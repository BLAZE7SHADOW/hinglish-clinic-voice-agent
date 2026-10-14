"""Silence handling: reprompt twice, then end the call; a pending read-back is repeated whole."""

from types import SimpleNamespace

import pytest
from livekit.agents import (
    UserInputTranscribedEvent,
    UserStateChangedEvent,
    UserTranscriptionTimeoutEvent,
    llm,
)

from agent import silence
from agent.state import Action, CallState, PendingWrite

READ_BACK = (
    "Just to confirm: Doctor Anil Sharma, on Thursday, at six in the evening. Shall I book it?"
)


class FakeSession:
    def __init__(
        self,
        *,
        pending: PendingWrite | None = None,
        last_agent: str = "Which time?",
        caller_spoke: bool = True,
    ):
        self.userdata = CallState(call_id="AJ_t", pending=pending)
        items = [llm.ChatMessage(role="assistant", content=["Namaste, how can I help?"])]
        if caller_spoke:
            items.append(llm.ChatMessage(role="user", content=["Book me with Doctor Sharma."]))
        items.append(llm.ChatMessage(role="assistant", content=[last_agent]))
        self.history = llm.ChatContext(items=items)
        self.said: list[str] = []
        self.replies: list[dict] = []
        self.away_resets = 0
        self.handles: list[SimpleNamespace] = []

    def say(self, text, **kw):
        self.said.append(text)
        handle = SimpleNamespace(callbacks=[])
        handle.add_done_callback = handle.callbacks.append
        self.handles.append(handle)
        return handle

    def generate_reply(self, **kw):
        self.replies.append(kw)

    def reset_away_timer(self):
        self.away_resets += 1


def away() -> UserStateChangedEvent:
    return UserStateChangedEvent(old_state="listening", new_state="away")


def speaking() -> UserStateChangedEvent:
    return UserStateChangedEvent(old_state="listening", new_state="speaking")


def handler(session: FakeSession) -> tuple[silence.SilenceHandler, list[str]]:
    ended: list[str] = []
    h = silence.SilenceHandler(session, language="en", end_call=lambda: ended.append("end"))
    return h, ended


def test_two_reprompts_then_a_goodbye_ends_the_call():
    s = FakeSession()
    h, ended = handler(s)
    h.on_user_state(away())
    h.on_user_state(away())
    assert [r["tool_choice"] for r in s.replies] == ["none", "none"]  # never calls tools
    assert s.away_resets == 2  # LiveKit won't re-arm the timer while the caller is away
    h.on_user_state(away())
    assert "Goodbye" in s.said[-1] and ended == []  # ends only after the goodbye is spoken
    for callback in s.handles[-1].callbacks:
        callback(s.handles[-1])
    assert ended == ["end"]


def words(text: str) -> UserInputTranscribedEvent:
    return UserInputTranscribedEvent(transcript=text, is_final=True)


def test_caller_words_reset_the_count():
    s = FakeSession()
    h, _ = handler(s)
    h.on_user_state(away())
    h.on_user_state(away())
    h.on_transcript(words("Sorry, I'm here."))
    h.on_user_state(away())
    assert len(s.replies) == 3 and s.said == []  # a fresh first reprompt, no goodbye


def test_noise_without_words_does_not_reset_the_count():
    """H1: VAD fired on noise between reprompts, and the caller got 4 reprompts, not 2."""
    s = FakeSession()
    h, _ = handler(s)
    h.on_user_state(away())
    h.on_user_state(speaking())  # VAD only
    h.on_transcript(words(""))
    h.on_user_state(away())
    h.on_user_state(speaking())
    h.on_user_state(away())
    assert len(s.replies) == 2 and "Goodbye" in s.said[-1]


def test_a_pending_read_back_is_repeated_word_for_word():
    """R3: silence before the yes. The yes must still count, so the full read-back is spoken."""
    pending = PendingWrite(action=Action.CREATE_BOOKING, key="k", text=READ_BACK, after_user_turn=3)
    s = FakeSession(pending=pending)
    h, _ = handler(s)
    h.on_user_state(away())
    assert s.said == [f"Are you still there? {READ_BACK}"] and s.replies == []
    h.on_user_state(away())
    assert s.said[-1] == f"No rush. {READ_BACK}"
    assert pending.times_spoken == 1  # silence isn't a misunderstanding


def test_silence_after_the_goodbye_just_hangs_up():
    """S1: she already said goodbye; a second goodbye would be odd."""
    s = FakeSession(last_agent="You're welcome! Have a wonderful day.")
    h, ended = handler(s)
    h.on_user_state(away())
    assert s.replies == [] and s.said == [] and ended == ["end"]


def lost(seconds: float) -> UserTranscriptionTimeoutEvent:
    return UserTranscriptionTimeoutEvent(speech_duration=seconds, vad_speech_started_at=0.0)


def test_speech_without_words_is_asked_again_with_the_read_back():
    """S4: VAD heard "yes" but STT returned nothing. Ask again at once, read-back included."""
    pending = PendingWrite(action=Action.CREATE_BOOKING, key="k", text=READ_BACK, after_user_turn=3)
    s = FakeSession(pending=pending)
    h, _ = handler(s)
    h.on_transcription_timeout(lost(0.6))
    assert s.said == [f"Sorry, I didn't catch that. {READ_BACK}"]
    assert h.lost_transcripts == 1 and h.reprompts == 0  # not counted as silence


def test_a_short_noise_without_words_is_ignored():
    s = FakeSession()
    h, _ = handler(s)
    h.on_transcription_timeout(lost(0.1))
    assert s.said == [] and s.replies == [] and h.lost_transcripts == 0


def test_silence_handling_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("SILENCE_REPROMPT", "off")
    assert not silence.enabled()
    monkeypatch.delenv("SILENCE_REPROMPT")
    assert silence.enabled()


def test_the_greeting_is_never_taken_for_a_goodbye():
    """H1: "thank you for calling" opens the greeting; silence after it must reprompt."""
    greeting = "Hello, thank you for calling Sharma Family Clinic, this is Simran."
    s = FakeSession(last_agent=greeting, caller_spoke=False)
    h, ended = handler(s)
    h.on_user_state(away())
    assert len(s.replies) == 1 and ended == []
    s = FakeSession(last_agent="Thank you for calling Sharma Family Clinic, how can I help?")
    h, ended = handler(s)
    h.on_user_state(away())
    assert len(s.replies) == 1 and ended == []


def test_the_reprompt_count_is_a_setting():
    s = FakeSession()
    h = silence.SilenceHandler(
        s, language="en", end_call=lambda: None, settings=silence.Settings(max_reprompts=4)
    )
    for _ in range(4):
        h.on_user_state(away())
    assert len(s.replies) == 4 and s.said == []  # still reprompting, later ones rephrased
    assert "simpler" in s.replies[-1]["instructions"]
    h.on_user_state(away())
    assert "Goodbye" in s.said[-1]


def test_settings_come_from_env_and_are_bounded(monkeypatch):
    monkeypatch.setenv("SILENCE_TIMEOUT_S", "12")
    monkeypatch.setenv("SILENCE_MAX_REPROMPTS", "3")
    monkeypatch.setenv("TRANSCRIPTION_TIMEOUT_S", "2.5")
    assert silence.Settings.from_env() == silence.Settings(12.0, 3, 2.5)
    monkeypatch.setenv("SILENCE_TIMEOUT_S", "1")  # would nag every second
    with pytest.raises(ValueError, match="SILENCE_TIMEOUT_S"):
        silence.Settings.from_env()
