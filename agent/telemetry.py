"""Per-turn latency telemetry: one TurnRecord per agent reply.

Sources (LiveKit 1.8):
- ChatMessage.metrics on each conversation item. User messages carry when the caller started
  and stopped speaking, transcription delay and end-of-turn delay. Agent messages carry LLM time
  to first token, time to first sentence, TTS time to first audio and e2e (voice-to-voice)
  latency.
- `overlapping_speech` + `agent_state_changed` events for barge-in stop time.
- `function_tools_executed` for tool names and durations.

All LiveKit timestamps are wall-clock seconds (time.time()); delays are computed by LiveKit.
Records go to a local JSON-lines file always and to Postgres when DATABASE_URL is set. Writes
run in background tasks so they never block the call. Transcripts are NOT recorded until
redaction exists (design §11).
"""

import asyncio
import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from livekit.agents import AgentSession, llm
from livekit.agents.inference.interruption import OverlappingSpeechEvent
from livekit.agents.voice.events import (
    AgentStateChangedEvent,
    ConversationItemAddedEvent,
    FunctionToolsExecutedEvent,
)
from pydantic import BaseModel

logger = logging.getLogger("telemetry")

JSONL_PATH = Path("logs/turns.jsonl")
DB_WRITE_TIMEOUT_S = 5.0

_calls_in_process = 0  # the first call a process serves pays connection setup (cold start)


class ToolCall(BaseModel):
    name: str
    duration_ms: float | None
    is_error: bool


class TurnRecord(BaseModel):
    call_id: str
    turn_index: int
    channel: str  # "browser" | "phone"
    language: str
    providers: dict[str, str]
    cold_call: bool  # first call in this worker process; excluded from baselines

    # Caller side (None for the greeting, which has no preceding caller turn)
    user_speech_start_at: datetime | None = None
    user_speech_end_at: datetime | None = None
    end_of_turn_ms: float | None = None  # VAD end of speech -> turn committed
    stt_final_ms: float | None = None  # VAD end of speech -> final transcript
    stt_confidence: float | None = None

    # Agent side
    llm_ttft_ms: float | None = None
    llm_first_sentence_ms: float | None = None  # LLM start -> first sentence handed to TTS
    tts_ttfb_ms: float | None = None  # first text sent to TTS -> first audio back
    agent_audio_start_at: datetime | None = None
    voice_to_voice_ms: float | None = None  # caller stops -> agent audio starts (LiveKit e2e)

    # Barge-in
    interrupted: bool = False
    interrupt_detect_ms: float | None = None  # caller speech onset -> interruption decided
    interrupt_stop_ms: float | None = None  # caller speech onset -> agent audio stopped

    tool_calls: list[ToolCall] = []


def _ms(seconds: float | None) -> float | None:
    return None if seconds is None else round(seconds * 1000, 1)


def _ts(seconds: float | None) -> datetime | None:
    return None if seconds is None else datetime.fromtimestamp(seconds, tz=UTC)


class TurnBuilder:
    """Pairs the caller's message with the agent reply that follows it. Pure logic, no I/O."""

    def __init__(
        self,
        *,
        call_id: str,
        channel: str,
        language: str,
        providers: dict[str, str],
        cold_call: bool,
    ) -> None:
        self._base = {
            "call_id": call_id,
            "channel": channel,
            "language": language,
            "providers": providers,
            "cold_call": cold_call,
        }
        self._turn_index = 0
        self._user_msg: llm.ChatMessage | None = None
        self._tools: list[ToolCall] = []
        self._interrupt_onset: float | None = None
        self._interrupt_detect: float | None = None
        self._agent_stopped_at: float | None = None

    def on_user_message(self, msg: llm.ChatMessage) -> None:
        # If the caller spoke several times before the agent replied, the last segment is
        # the one the reply's latency is measured from.
        self._user_msg = msg

    def on_overlap(self, ev: OverlappingSpeechEvent) -> None:
        if ev.is_interruption and self._interrupt_onset is None:
            self._interrupt_onset = ev.overlap_started_at or (ev.detected_at - ev.detection_delay)
            self._interrupt_detect = ev.detection_delay

    def on_agent_state(self, ev: AgentStateChangedEvent) -> None:
        if ev.old_state == "speaking" and self._interrupt_onset is not None:
            self._agent_stopped_at = ev.created_at

    def on_tools(self, ev: FunctionToolsExecutedEvent) -> None:
        outputs = {o.call_id: o for o in ev.function_call_outputs if o is not None}
        for call in ev.function_calls:
            out = outputs.get(call.call_id)
            self._tools.append(
                ToolCall(
                    name=call.name,
                    duration_ms=_ms(out.created_at - call.created_at) if out else None,
                    is_error=out.is_error if out else True,
                )
            )

    def on_agent_message(self, msg: llm.ChatMessage) -> TurnRecord:
        u = self._user_msg.metrics if self._user_msg else {}
        a = msg.metrics
        stopped = u.get("stopped_speaking_at")
        stop_ms = None
        if msg.interrupted and self._interrupt_onset is not None and self._agent_stopped_at:
            stop_ms = _ms(self._agent_stopped_at - self._interrupt_onset)

        record = TurnRecord(
            **self._base,
            turn_index=self._turn_index,
            user_speech_start_at=_ts(u.get("started_speaking_at")),
            user_speech_end_at=_ts(stopped),
            end_of_turn_ms=_ms(u.get("end_of_turn_delay")),
            stt_final_ms=_ms(u.get("transcription_delay")),
            stt_confidence=self._user_msg.transcript_confidence if self._user_msg else None,
            llm_ttft_ms=_ms(a.get("llm_node_ttft")),
            llm_first_sentence_ms=_ms(a.get("llm_node_ttfs")),
            tts_ttfb_ms=_ms(a.get("tts_node_ttfb")),
            agent_audio_start_at=_ts(a.get("started_speaking_at")),
            voice_to_voice_ms=_ms(a.get("e2e_latency")),
            interrupted=msg.interrupted,
            interrupt_detect_ms=_ms(self._interrupt_detect) if msg.interrupted else None,
            interrupt_stop_ms=stop_ms,
            tool_calls=self._tools,
        )
        self._turn_index += 1
        self._user_msg = None
        self._tools = []
        self._interrupt_onset = self._interrupt_detect = self._agent_stopped_at = None
        return record


class TurnSink:
    """Writes records to JSONL (always) and Postgres (when DATABASE_URL is set)."""

    def __init__(self) -> None:
        self._dsn = os.environ.get("DATABASE_URL")
        self._pool: Any = None
        self._tasks: set[asyncio.Task] = set()
        JSONL_PATH.parent.mkdir(exist_ok=True)

    def write(self, record: TurnRecord) -> None:
        task = asyncio.create_task(self._write(record))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _write(self, record: TurnRecord) -> None:
        line = record.model_dump_json() + "\n"
        await asyncio.to_thread(_append, JSONL_PATH, line)
        if not self._dsn:
            return
        try:
            await asyncio.wait_for(self._insert(record), DB_WRITE_TIMEOUT_S)
        except Exception:  # telemetry must never break a call; the JSONL line is the backup
            logger.exception("turn insert failed (kept in %s)", JSONL_PATH)

    async def _insert(self, r: TurnRecord) -> None:
        import asyncpg

        if self._pool is None:
            # statement_cache_size=0: Supabase's transaction pooler doesn't support
            # prepared statements.
            self._pool = await asyncpg.create_pool(
                self._dsn, min_size=1, max_size=2, statement_cache_size=0
            )
        await self._pool.execute(
            """insert into turns (call_id, turn_index, channel, language, providers, cold_call,
                user_speech_start_at, user_speech_end_at, end_of_turn_ms, stt_final_ms,
                stt_confidence, llm_ttft_ms, llm_first_sentence_ms, tts_ttfb_ms,
                agent_audio_start_at, voice_to_voice_ms, interrupted, interrupt_detect_ms,
                interrupt_stop_ms, tool_calls)
               values ($1,$2,$3,$4,$5::jsonb,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,
                $19,$20::jsonb)""",
            r.call_id,
            r.turn_index,
            r.channel,
            r.language,
            json.dumps(r.providers),
            r.cold_call,
            r.user_speech_start_at,
            r.user_speech_end_at,
            r.end_of_turn_ms,
            r.stt_final_ms,
            r.stt_confidence,
            r.llm_ttft_ms,
            r.llm_first_sentence_ms,
            r.tts_ttfb_ms,
            r.agent_audio_start_at,
            r.voice_to_voice_ms,
            r.interrupted,
            r.interrupt_detect_ms,
            r.interrupt_stop_ms,
            json.dumps([t.model_dump() for t in r.tool_calls]),
        )

    async def aclose(self) -> None:
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        if self._pool is not None:
            await self._pool.close()


def _append(path: Path, line: str) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(line)


def attach(
    session: AgentSession, *, call_id: str, channel: str, language: str, providers: dict[str, str]
) -> TurnSink:
    """Subscribe to session events and record one row per agent reply."""
    global _calls_in_process
    builder = TurnBuilder(
        call_id=call_id,
        channel=channel,
        language=language,
        providers=providers,
        cold_call=_calls_in_process == 0,
    )
    _calls_in_process += 1
    sink = TurnSink()

    @session.on("conversation_item_added")
    def _on_item(ev: ConversationItemAddedEvent) -> None:
        if not isinstance(ev.item, llm.ChatMessage):
            return
        if ev.item.role == "user":
            builder.on_user_message(ev.item)
        elif ev.item.role == "assistant":
            sink.write(builder.on_agent_message(ev.item))

    session.on("overlapping_speech", builder.on_overlap)
    session.on("agent_state_changed", builder.on_agent_state)
    session.on("function_tools_executed", builder.on_tools)
    return sink
