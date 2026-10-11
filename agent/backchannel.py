"""Backchannel filter: "okay", "haan", "hmm" while the agent talks must not stop her.

LiveKit 1.8 has no setting for this. Its adaptive interruption model scores a soft "okay" above
its threshold (0.69 and 0.71 against 0.656 in our calls), pauses the agent, and then the final
transcript "Okay" turns the pause into a real interruption (LEARNING.md #11, #15).
`min_words=2` would block it, but it also blocks "wait", "stop" and "ruko".

So we filter on what the words are. While agent speech is in progress (playing, or paused by a
suspected interruption), a transcript made only of backchannel words:
  - does not end the pause (the framework would otherwise interrupt the paused speech), and
  - does not commit a user turn (same path as LiveKit's own `min_words` gate),
so LiveKit's false-interruption timer resumes the speech where it stopped.

This wraps two methods of the framework's internal `AgentActivity`. They are not public API, so
`tests/test_backchannel.py` checks they still exist with the expected parameters; a LiveKit
upgrade that changes them fails a test instead of silently disabling the filter.
"""

import logging
import re

from livekit.agents.voice.agent_activity import AgentActivity

logger = logging.getLogger("backchannel")

# Acknowledgements only. Never add words that ask her to stop or change course
# (wait, stop, no, nahi, ruko, sorry, hello, excuse me): those must still interrupt.
_WORD_GROUPS = (
    # English ("so" is how STT heard a soft "ohh")
    "ok okay okey k yeah yes yep yup right alright sure fine great cool",
    "hmm hm mm mmm mhm uh huh um ah aha oh ohh oho so got it i see",
    # Hinglish (Roman script, as Deepgram/Sarvam spell it)
    "haan han haa ha ji jee achha acha accha theek thik hai hain sahi",
    # Hindi in Devanagari (Sarvam codemix writes Hindi words this way)
    "हाँ हां हा जी अच्छा ठीक है हैं सही हम्म",
)
BACKCHANNEL_WORDS = frozenset(w for group in _WORD_GROUPS for w in group.split())
# Dropped backchannels stay in LiveKit's pending transcript, so several in one agent reply add up
# ("haan ... haan ji ... achha"). The rule is "only these words", with a cap for safety.
MAX_BACKCHANNEL_WORDS = 8
RESUME_AFTER_S = 0.5  # resume sooner than the 2 s default once we know it was a backchannel


def is_backchannel(text: str) -> bool:
    # Devanagari letters and marks, minus the danda "।" punctuation (U+0964/5)
    words = re.findall(r"[a-z\u0900-\u0963\u0966-\u097f]+", text.lower())
    return 0 < len(words) <= MAX_BACKCHANNEL_WORDS and all(w in BACKCHANNEL_WORDS for w in words)


def _agent_turn_active(activity: AgentActivity) -> bool:
    speech = activity._current_speech
    return speech is not None and not speech.interrupted and speech.allow_interruptions


_original_on_final_transcript = AgentActivity.on_final_transcript
_original_on_end_of_turn = AgentActivity.on_end_of_turn


def _on_final_transcript(self: AgentActivity, ev, *, speaking: bool | None = None) -> None:
    if _agent_turn_active(self) and is_backchannel(ev.alternatives[0].text):
        logger.debug("backchannel ignored", extra={"lk.pii.text": ev.alternatives[0].text})
        if (paused := self._paused_speech) is not None:
            paused.timeout = RESUME_AFTER_S  # used when the caller's speech ends
            if speaking is False:  # it already ended: arm the resume now
                self._start_false_interruption_timer(RESUME_AFTER_S)
        return
    _original_on_final_transcript(self, ev, speaking=speaking)


def _on_end_of_turn(self: AgentActivity, info) -> bool:
    if _agent_turn_active(self) and is_backchannel(info.new_transcript):
        # Same handling as LiveKit's min_words gate: drop the turn, keep the agent's speech.
        self._cancel_preemptive_generation()
        logger.info("backchannel turn dropped; agent keeps talking")
        return False
    return _original_on_end_of_turn(self, info)


def install() -> None:
    """Patch once per process. Idempotent."""
    AgentActivity.on_final_transcript = _on_final_transcript
    AgentActivity.on_end_of_turn = _on_end_of_turn
