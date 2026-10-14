import inspect
from types import SimpleNamespace

import pytest
from livekit.agents.voice.agent_activity import AgentActivity

from agent import backchannel
from agent.backchannel import is_backchannel


@pytest.mark.parametrize(
    "text",
    [
        "Okay.",
        "okay",
        "Hmm.",
        "Haan ji.",
        "Theek hai.",
        "Achha",
        "Okay. Okay.",
        "Mhm",
        "So.",
        "Yeah, right.",
        "हाँ।",
        "ठीक है",
        "uh-huh",
    ],
)
def test_backchannels(text):
    assert is_backchannel(text)


@pytest.mark.parametrize(
    "text",
    [
        "Wait.",
        "Ruko ruko",
        "Stop",
        "No.",
        "Nahi",
        "Sorry, just the address.",
        "Okay, wait.",
        "Okay, what about Monday?",
        "Doctor Sharma.",
        "Continue.",
        "Hello?",
        "",
        "...",
        "okay " * 9,
    ],
)
def test_real_interruptions_and_replies(text):
    assert not is_backchannel(text)


def test_patched_framework_methods_still_exist():
    # The filter wraps internal LiveKit methods; fail loudly if an upgrade changes them.
    params = inspect.signature(backchannel._original_on_final_transcript).parameters
    assert list(params)[:2] == ["self", "ev"] and "speaking" in params
    assert list(inspect.signature(backchannel._original_on_end_of_turn).parameters) == [
        "self",
        "info",
    ]
    for name in ("_current_speech", "_paused_speech"):
        assert name in inspect.getsource(AgentActivity.__init__)
    for name in ("_start_false_interruption_timer", "_cancel_preemptive_generation"):
        assert callable(getattr(AgentActivity, name))
    assert isinstance(AgentActivity.session, property)  # read for the call state
    from livekit.agents import AgentSession

    assert isinstance(AgentSession.user_state, property)  # read to arm a late resume (#26)
    assert "_interruption_detection_enabled" in inspect.getsource(AgentActivity.__init__)


class FakeActivity:
    def __init__(
        self,
        *,
        speaking_agent: bool,
        paused: bool = False,
        awaiting_yes=False,
        user_state="speaking",
    ):
        self.session = SimpleNamespace(
            userdata=SimpleNamespace(awaiting_yes=awaiting_yes), user_state=user_state
        )
        self._current_speech = (
            SimpleNamespace(interrupted=False, allow_interruptions=True) if speaking_agent else None
        )
        self._paused_speech = SimpleNamespace(timeout=2.0) if paused else None
        self.timer = None
        self.cancelled_preemptive = False

    def _start_false_interruption_timer(self, timeout):
        self.timer = timeout

    def _cancel_preemptive_generation(self):
        self.cancelled_preemptive = True


def final(text):
    return SimpleNamespace(alternatives=[SimpleNamespace(text=text)])


def test_backchannel_while_agent_talks_is_dropped(monkeypatch):
    calls = []
    monkeypatch.setattr(backchannel, "_original_on_end_of_turn", lambda s, i: calls.append(i))
    monkeypatch.setattr(
        backchannel, "_original_on_final_transcript", lambda s, ev, speaking: calls.append(ev)
    )
    act = FakeActivity(speaking_agent=True, paused=True)

    backchannel._on_final_transcript(act, final("Okay."), speaking=False)
    assert calls == [] and act._paused_speech.timeout == backchannel.RESUME_AFTER_S
    assert act.timer == backchannel.RESUME_AFTER_S  # resume armed, pause not cancelled

    info = SimpleNamespace(new_transcript="Okay.")
    assert backchannel._on_end_of_turn(act, info) is False
    assert calls == [] and act.cancelled_preemptive


def test_real_words_and_idle_agent_go_to_livekit(monkeypatch):
    calls = []
    monkeypatch.setattr(backchannel, "_original_on_end_of_turn", lambda s, i: calls.append(i))
    monkeypatch.setattr(
        backchannel, "_original_on_final_transcript", lambda s, ev, speaking: calls.append(ev)
    )
    talking = FakeActivity(speaking_agent=True)
    backchannel._on_final_transcript(talking, final("Wait."), speaking=None)
    backchannel._on_end_of_turn(talking, SimpleNamespace(new_transcript="Wait."))
    # Agent silent: "Okay" is a normal reply ("okay, thank you" at the end of a call).
    idle = FakeActivity(speaking_agent=False)
    backchannel._on_end_of_turn(idle, SimpleNamespace(new_transcript="Okay."))
    assert len(calls) == 3


def test_yes_words_are_answers_while_a_read_back_waits(monkeypatch):
    # "Shall I book Monday five PM?" ... "haan": that is the answer, not a backchannel.
    calls = []
    monkeypatch.setattr(backchannel, "_original_on_end_of_turn", lambda s, i: calls.append(i))
    monkeypatch.setattr(
        backchannel, "_original_on_final_transcript", lambda s, ev, speaking: calls.append(ev)
    )
    act = FakeActivity(speaking_agent=True, awaiting_yes=True)
    backchannel._on_final_transcript(act, final("Haan."), speaking=False)
    backchannel._on_end_of_turn(act, SimpleNamespace(new_transcript="Haan."))
    assert len(calls) == 2


def test_a_late_pause_still_resumes(monkeypatch):
    """Call 10 (#26): the detector paused her after the caller's speech had ended, so LiveKit
    never armed the resume. The filter must arm it when the caller is already quiet."""
    monkeypatch.setattr(backchannel, "_original_on_final_transcript", lambda *a, **k: None)
    act = FakeActivity(speaking_agent=True, paused=True, user_state="listening")
    backchannel._on_final_transcript(act, final("Oh, okay."), speaking=None)
    assert act.timer == backchannel.RESUME_AFTER_S

    act = FakeActivity(speaking_agent=True, paused=True, user_state="listening")
    assert backchannel._on_end_of_turn(act, SimpleNamespace(new_transcript="Okay.")) is False
    assert act.timer == backchannel.RESUME_AFTER_S


def test_no_resume_while_the_caller_is_still_talking(monkeypatch):
    monkeypatch.setattr(backchannel, "_original_on_final_transcript", lambda *a, **k: None)
    act = FakeActivity(speaking_agent=True, paused=True, user_state="speaking")
    backchannel._on_final_transcript(act, final("Okay."), speaking=None)
    assert act.timer is None and act._paused_speech.timeout == backchannel.RESUME_AFTER_S
