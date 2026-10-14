"""Silence handling: when the caller goes quiet, re-engage, then end the call politely.

The pattern voice platforms document (Dialogflow CX voice design, Vapi, Cognigy; LEARNING #29):
repeat the question after the first silence, rephrase it after the second, and stop after the
third instead of waiting forever. Here:

1. "Are you still there?" plus the last question. If a read-back is waiting for a yes, code
   speaks the read-back again in full, so a yes after it still counts (agent/confirm.py needs
   the exact sentence right before the yes).
2. "No rush" plus the question again, simpler.
3. A goodbye from code, then the call ends. Holds are released by the call-end hook (main.py).

LiveKit decides when the caller is "away": both sides silent for `user_away_timeout` seconds,
paused while a tool runs. Once a caller is away, LiveKit doesn't restart that timer by itself,
so each reprompt calls `reset_away_timer()` to arm the next one. Any speech from the caller
resets the count.

A second case looks like silence but isn't: the caller spoke, VAD heard it, and STT returned no
words (S4 re-test: a "yes" right after the read-back was lost). LiveKit reports it as
`user_transcription_timeout`; the agent then says it didn't catch that and asks again within
about 2 s, instead of waiting for the silence timer and asking "are you still there?".
"""

import logging
import os
import re
from collections.abc import Callable

from livekit.agents import AgentSession, UserStateChangedEvent, UserTranscriptionTimeoutEvent

from agent.state import CallState

logger = logging.getLogger("silence")

SILENCE_S = 8.0  # after the agent stops talking; long enough to think, short enough to notice
MAX_REPROMPTS = 2  # then the third silence ends the call
TRANSCRIPTION_TIMEOUT_S = 2.0  # caller spoke but no words came back from STT
MIN_LOST_SPEECH_S = 0.25  # shorter VAD blips are noise (a cough, a click), not a lost answer

_STILL_THERE = {"en": "Are you still there?", "hinglish": "Hello, kya aap line par hain?"}
_NO_RUSH = {"en": "No rush.", "hinglish": "Koi jaldi nahi hai."}
_NOT_CAUGHT = {"en": "Sorry, I didn't catch that.", "hinglish": "Maaf kijiye, sun nahi paayi."}
_GOODBYE = {
    "en": "I'll end the call for now. Please call us again whenever you're ready. Goodbye!",
    "hinglish": "Abhi ke liye call band kar rahi hoon. Jab bhi aap ready hon, phir se call "
    "kijiye. Dhanyavaad!",
}
_REPROMPT = {
    1: "The caller has been silent. In one short sentence, ask if they are still there, then "
    "repeat your last question.",
    "lost": "The caller said something but it wasn't heard clearly. In one short sentence say "
    "you didn't catch that, then repeat your last question.",
    2: "The caller is still silent. Say kindly that there is no rush, then ask your last "
    "question again in simpler words. One or two short sentences.",
}
# The call is already over in all but name: don't ask "are you still there?" after a goodbye.
# Closing phrases only: "thank you for calling" is also how greetings start (H1 re-test hung up
# on the greeting), so it doesn't count, and nothing counts before the caller has spoken.
_FAREWELL = re.compile(
    r"(?i)\b(have a (good|great|nice|wonderful|lovely) (day|evening)|good ?bye|bye|take care)\b"
)


def enabled() -> bool:
    return os.environ.get("SILENCE_REPROMPT", "on") == "on"


class SilenceHandler:
    def __init__(
        self, session: AgentSession[CallState], *, language: str, end_call: Callable[[], None]
    ) -> None:
        self._session = session
        self._language = language
        self._end_call = end_call
        self.silences = 0  # in a row, since the caller last spoke
        self.reprompts = 0  # over the whole call, for the call summary
        self.lost_transcripts = 0  # speech with no words from STT, for the call summary

    def on_user_state(self, ev: UserStateChangedEvent) -> None:
        if ev.new_state == "speaking":
            self.silences = 0
        elif ev.new_state == "away":
            self.silences += 1
            self._handle()

    def on_transcription_timeout(self, ev: UserTranscriptionTimeoutEvent) -> None:
        if ev.speech_duration < MIN_LOST_SPEECH_S:
            return
        self.lost_transcripts += 1
        logger.info(
            "caller spoke but STT returned nothing: asking again",
            extra={"speech_s": round(ev.speech_duration, 2)},
        )
        self._ask_again(_NOT_CAUGHT, "lost")

    def _handle(self) -> None:
        session, lang = self._session, self._language
        if _said_goodbye(session):
            # She already said goodbye: just hang up, no second goodbye.
            logger.info("caller silent after the goodbye: ending the call")
            self._end_call()
            return
        if self.silences > MAX_REPROMPTS:
            logger.info("caller silent: ending the call", extra={"silences": self.silences})
            handle = session.say(_GOODBYE[lang], allow_interruptions=False)
            handle.add_done_callback(lambda _: self._end_call())
            return
        self.reprompts += 1
        logger.info(
            "caller silent: reprompt",
            extra={"silences": self.silences, "awaiting_yes": session.userdata.awaiting_yes},
        )
        self._ask_again(_STILL_THERE if self.silences == 1 else _NO_RUSH, self.silences)
        # LiveKit doesn't re-arm the away timer while the caller is "away"; this does, and it
        # starts counting once the reprompt has been spoken.
        session.reset_away_timer()

    def _ask_again(self, opener: dict[str, str], reprompt: int | str) -> None:
        session = self._session
        pending = session.userdata.pending
        if pending is not None and not pending.confirmed:
            # The read-back again, word for word, so the caller's yes still counts.
            session.say(f"{opener[self._language]} {pending.text}")
        else:
            session.generate_reply(instructions=_REPROMPT[reprompt], tool_choice="none")


def _said_goodbye(session: AgentSession) -> bool:
    """The agent's last line closed the call, after the caller had said something."""
    messages = [i for i in session.history.items if i.type == "message"]
    if not any(m.role == "user" for m in messages):
        return False  # only the greeting so far
    last_agent = next((m for m in reversed(messages) if m.role == "assistant"), None)
    return last_agent is not None and bool(_FAREWELL.search(last_agent.text_content or ""))


def attach(
    session: AgentSession[CallState], *, language: str, end_call: Callable[[], None]
) -> SilenceHandler:
    handler = SilenceHandler(session, language=language, end_call=end_call)
    session.on("user_state_changed", handler.on_user_state)
    session.on("user_transcription_timeout", handler.on_transcription_timeout)
    return handler
