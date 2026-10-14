"""A short filler line while the agent looks something up ("One moment, let me check.").

Most of a tool turn's wait is not the tool (DB 150-390 ms) but the second LLM call that turns the
tool result into a reply (LEARNING #14: tool turns p50 about 3.9 s vs 2.2 s without). Voice
platforms cover that gap with a short acknowledgement, the way a receptionist says "one moment"
before checking the diary.

Rules, all in code:
- once per caller turn, on the first lookup tool of that turn;
- not for write tools: they speak their own read-back (agent/readback.py);
- skipped if the agent is already speaking (the LLM said "let me check" itself);
- after `delay_s`, and only if nothing else started speaking in the meantime;
- kept out of the chat history, so it can never sit between a read-back and the caller's yes
  (agent/confirm.py checks the message right before the yes).

LiveKit queues a `say()` made while a tool runs before the tool's reply. Off by default
(`FILLER=off`); the voice session turns it on and compares voice-to-voice and time-to-first-audio.
"""

import asyncio
import logging
import os

from livekit.agents import AgentSession, ToolExecutionUpdatedEvent, UserInputTranscribedEvent

logger = logging.getLogger("filler")

# Write tools speak their own read-back; a filler before it would be noise.
NO_FILLER_TOOLS = frozenset({"confirm_booking", "reschedule_booking", "cancel_booking"})
LINES = {
    "en": ["One moment, let me check.", "Sure, just a second.", "Let me look that up."],
    "hinglish": ["Ek second, check karti hoon.", "Ji, ek minute.", "Main dekhti hoon."],
}


def enabled() -> bool:
    return os.environ.get("FILLER", "off") == "on"


def delay_s() -> float:
    value = float(os.environ.get("FILLER_DELAY_S", "0.3"))
    if not 0 <= value <= 2:
        raise ValueError(f"FILLER_DELAY_S={value} is outside 0..2")
    return value


class Filler:
    def __init__(self, session: AgentSession, *, language: str, delay: float) -> None:
        self._session = session
        self._lines = LINES.get(language, LINES["en"])
        self._delay = delay
        self._said_this_turn = False
        self._pending: asyncio.TimerHandle | None = None
        self.count = 0  # fillers spoken this call, for the call summary

    def on_transcript(self, ev: UserInputTranscribedEvent) -> None:
        if ev.is_final and ev.transcript.strip():
            self._said_this_turn = False  # a new caller turn may get one filler

    def on_tool(self, ev: ToolExecutionUpdatedEvent) -> None:
        update = ev.update
        if update.type != "tool_call_started" or self._said_this_turn:
            return
        if update.function_call.name in NO_FILLER_TOOLS:
            return
        if self._session.agent_state == "speaking":
            self._said_this_turn = True  # the LLM's own "let me check" is playing
            return
        self._said_this_turn = True
        self._pending = asyncio.get_running_loop().call_later(self._delay, self._speak)

    def _speak(self) -> None:
        self._pending = None
        if self._session.agent_state == "speaking":
            return  # the reply (or something else) beat the filler: say nothing
        line = self._lines[self.count % len(self._lines)]
        self.count += 1
        self._session.say(line, allow_interruptions=True, add_to_chat_ctx=False)
        logger.debug("filler spoken", extra={"line": line})


def attach(session: AgentSession, *, language: str) -> Filler:
    filler = Filler(session, language=language, delay=delay_s())
    session.on("tool_execution_updated", filler.on_tool)
    session.on("user_input_transcribed", filler.on_transcript)
    return filler
