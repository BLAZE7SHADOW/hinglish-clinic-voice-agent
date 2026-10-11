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


class FakeActivity:
    def __init__(self, *, speaking_agent: bool, paused: bool = False):
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
