"""Per-turn latency telemetry: one TurnRecord per agent reply.

Sources (LiveKit 1.8):
- ChatMessage.metrics on each conversation item. User messages carry when the caller started
  and stopped speaking, transcription delay and end-of-turn delay. Agent messages carry LLM time
  to first token, time to first sentence, TTS time to first audio and e2e (voice-to-voice)
  latency.
- Barge-in stop time: onset from the adaptive interruption detector's `overlapping_speech`
  event when it runs, else from `user_state_changed` -> speaking while the agent is speaking
  (VAD-based interruption, which LiveKit falls back to if the cloud detector fails); the end
  is `agent_state_changed` away from speaking.
- `function_tools_executed` for tool names and durations.
- `metrics_collected` LLMMetrics for token counts, to check thinking and prompt caching.

All LiveKit timestamps are wall-clock seconds (time.time()); delays are computed by LiveKit.
Records go to a local JSON-lines file always and to Postgres when DATABASE_URL is set. Writes
run in background tasks so they never block the call. Transcripts are NOT recorded until
redaction exists (design §11).
"""

import asyncio
import json
import logging
import os
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from livekit.agents import AgentSession, llm, metrics
from livekit.agents.inference.interruption import OverlappingSpeechEvent
from livekit.agents.voice.events import (
    AgentStateChangedEvent,
    ConversationItemAddedEvent,
    FunctionToolsExecutedEvent,
    MetricsCollectedEvent,
    UserStateChangedEvent,
)
from pydantic import BaseModel

from agent.db import connect_kwargs

logger = logging.getLogger("telemetry")

JSONL_PATH = Path("logs/turns.jsonl")
USAGE_PATH = Path("logs/usage.jsonl")  # one line per call: tokens, audio seconds, characters
DB_WRITE_TIMEOUT_S = 5.0

# A call is "cold" if no call started or ended in the last IDLE_COLD_S seconds (week 1: skip the
# first call after the agent has been idle). LiveKit runs each call in a fresh process, so this
# is tracked outside the process: the marker file's mtime is the last call start or end. Touching
# it at start matters: the previous call's process may still be shutting down when the next
# call arrives.
LAST_CALL_MARKER = Path("logs/.last_call_end")
IDLE_COLD_S = 600


def _is_cold_call() -> bool:
    try:
        return time.time() - LAST_CALL_MARKER.stat().st_mtime > IDLE_COLD_S
    except FileNotFoundError:
        return True


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

    # LLM tokens for this reply: is "minimal" thinking really zero, is prompt caching working?
    llm_prompt_tokens: int | None = None
    llm_cached_tokens: int | None = None
    llm_reasoning_tokens: int | None = None
    llm_completion_tokens: int | None = None

    # Barge-in
    interrupted: bool = False
    interrupt_detect_ms: float | None = None  # caller speech onset -> interruption decided
    interrupt_stop_ms: float | None = None  # caller speech onset -> agent audio stopped

    tool_calls: list[ToolCall] = []


def _ms(seconds: float | None) -> float | None:
    return None if seconds is None else round(seconds * 1000, 1)


def _sum(items: list[metrics.LLMMetrics], field: str) -> int | None:
    return sum(getattr(m, field) for m in items) if items else None


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
        # Which barge-in detector this turn used. The adaptive one silently falls back to VAD
        # for the rest of the call when its quota runs out (LEARNING #26), which changes
        # interruption behaviour mid-test, so every row says which one was live.
        self.interruption_mode: Callable[[], str] = lambda: "unknown"
        # The endpointing phase the caller's turn was judged under (agent/endpointing.py):
        # "off" unless ENDPOINTING=phase. Captured when the caller's message lands.
        self.endpointing_phase: Callable[[], str] = lambda: "off"
        self._turn_phase = "off"
        self._user_msg: llm.ChatMessage | None = None
        self._tools: list[ToolCall] = []
        self._agent_state = "initializing"
        self._interrupt_onset: float | None = None  # adaptive detector: true speech onset
        self._vad_onset: float | None = None  # VAD: caller started speaking over the agent
        self._interrupt_detect: float | None = None
        self._agent_stopped_at: float | None = None
        self._llm: list[metrics.LLMMetrics] = []

    def on_metrics(self, ev: MetricsCollectedEvent) -> None:
        # Cancelled requests are preemptive generations thrown away when the caller kept
        # talking, or replies cut off by an interruption; they don't describe this reply.
        if isinstance(ev.metrics, metrics.LLMMetrics) and not ev.metrics.cancelled:
            self._llm.append(ev.metrics)

    def on_user_message(self, msg: llm.ChatMessage) -> None:
        # If the caller spoke several times before the agent replied, the last segment is
        # the one the reply's latency is measured from.
        self._user_msg = msg
        self._turn_phase = self.endpointing_phase()

    def on_overlap(self, ev: OverlappingSpeechEvent) -> None:
        if ev.is_interruption and self._interrupt_onset is None:
            self._interrupt_onset = ev.overlap_started_at or (ev.detected_at - ev.detection_delay)
            self._interrupt_detect = ev.detection_delay

    def on_user_state(self, ev: UserStateChangedEvent) -> None:
        if ev.new_state == "speaking" and self._agent_state == "speaking" and not self._vad_onset:
            self._vad_onset = ev.created_at

    def on_agent_state(self, ev: AgentStateChangedEvent) -> None:
        self._agent_state = ev.new_state
        if ev.old_state == "speaking" and (self._interrupt_onset or self._vad_onset):
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
        onset = self._interrupt_onset or self._vad_onset
        if msg.interrupted and onset is not None and self._agent_stopped_at:
            stop_ms = _ms(self._agent_stopped_at - onset)

        record = TurnRecord(
            **(
                self._base
                | {
                    "providers": self._base["providers"]
                    | {"interruption": self.interruption_mode(), "endpointing": self._turn_phase}
                }
            ),
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
            llm_prompt_tokens=_sum(self._llm, "prompt_tokens"),
            llm_cached_tokens=_sum(self._llm, "prompt_cached_tokens"),
            llm_reasoning_tokens=_sum(self._llm, "reasoning_tokens"),
            llm_completion_tokens=_sum(self._llm, "completion_tokens"),
            interrupted=msg.interrupted,
            interrupt_detect_ms=_ms(self._interrupt_detect) if msg.interrupted else None,
            interrupt_stop_ms=stop_ms,
            tool_calls=self._tools,
        )
        self._turn_index += 1
        self._user_msg = None
        self._tools = []
        self._llm = []
        self._interrupt_onset = self._vad_onset = self._interrupt_detect = None
        self._agent_stopped_at = None
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
            self._pool = await asyncpg.create_pool(
                min_size=1, max_size=2, **connect_kwargs(self._dsn)
            )
        await self._pool.execute(
            """insert into turns (call_id, turn_index, channel, language, providers, cold_call,
                user_speech_start_at, user_speech_end_at, end_of_turn_ms, stt_final_ms,
                stt_confidence, llm_ttft_ms, llm_first_sentence_ms, tts_ttfb_ms,
                agent_audio_start_at, voice_to_voice_ms, llm_prompt_tokens, llm_cached_tokens,
                llm_reasoning_tokens, llm_completion_tokens, interrupted, interrupt_detect_ms,
                interrupt_stop_ms, tool_calls)
               values ($1,$2,$3,$4,$5::jsonb,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,
                $19,$20,$21,$22,$23,$24::jsonb)""",
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
            r.llm_prompt_tokens,
            r.llm_cached_tokens,
            r.llm_reasoning_tokens,
            r.llm_completion_tokens,
            r.interrupted,
            r.interrupt_detect_ms,
            r.interrupt_stop_ms,
            json.dumps([t.model_dump() for t in r.tool_calls]),
        )

    async def aclose(self) -> None:
        LAST_CALL_MARKER.touch()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        if self._pool is not None:
            await self._pool.close()


def write_usage(session: AgentSession, *, call_id: str) -> None:
    """Per-call usage from LiveKit's own collector (session.usage), for cost per call.

    LiveKit's UsageCollector (in older tutorials) is deprecated; AgentSession now collects the
    same numbers itself, per provider/model. Called at shutdown, when the call is complete.
    """
    usage = [u.model_dump() | {"type": type(u).__name__} for u in session.usage.model_usage]
    line = json.dumps({"call_id": call_id, "at": datetime.now(UTC).isoformat(), "usage": usage})
    JSONL_PATH.parent.mkdir(parents=True, exist_ok=True)
    _append(USAGE_PATH, line + "\n")
    logger.info("call usage: %s", line)


def _append(path: Path, line: str) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(line)


def interruption_mode(session: AgentSession) -> str:
    """ "adaptive" (LiveKit's model), "vad" (fallback, or never enabled), or "unknown"."""
    activity = getattr(session, "_activity", None)
    enabled = getattr(activity, "_interruption_detection_enabled", None)
    return "unknown" if enabled is None else "adaptive" if enabled else "vad"


def attach(
    session: AgentSession,
    *,
    call_id: str,
    channel: str,
    language: str,
    providers: dict[str, str],
    endpointing_phase: Callable[[], str] | None = None,
) -> TurnSink:
    """Subscribe to session events and record one row per agent reply."""
    builder = TurnBuilder(
        call_id=call_id,
        channel=channel,
        language=language,
        providers=providers,
        cold_call=_is_cold_call(),
    )
    builder.interruption_mode = lambda: interruption_mode(session)
    if endpointing_phase is not None:
        builder.endpointing_phase = endpointing_phase
    sink = TurnSink()
    LAST_CALL_MARKER.touch()

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
    session.on("user_state_changed", builder.on_user_state)
    session.on("function_tools_executed", builder.on_tools)
    session.on("metrics_collected", builder.on_metrics)
    return sink
