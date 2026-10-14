"""Phase-aware endpointing (LEARNING #27): the wait depends on what the agent just asked."""

from types import SimpleNamespace

import pytest
from livekit.agents import llm

from agent import endpointing
from agent.endpointing import phase
from agent.state import Action, CallState, PendingWrite


def waiting(key: str) -> CallState:
    return CallState(
        call_id="AJ_t",
        pending=PendingWrite(action=Action.CREATE_BOOKING, key=key, text="...", after_user_turn=1),
    )


IDLE = CallState(call_id="AJ_t")


@pytest.mark.parametrize(
    ("said", "expected"),
    [  # agent lines from the 14 October voice calls
        ("Great, I have held six fifteen PM for you. May I please have your phone number?", "long"),
        ("And what is your date of birth, please?", "long"),
        ("Could you please spell your name letter by letter, like K for kite?", "long"),
        (
            "Dr. Anil Sharma has six PM, six fifteen PM, and six thirty PM. Which time works for you?",
            "short",
        ),
        ("Is there anything else I can help you with today?", "short"),
        ("Would that work for you, or would you prefer Friday?", "short"),
        ("How can I help you today?", "default"),
        (
            "We are open Monday to Saturday, from nine in the morning to eight in the evening.",
            "default",
        ),
    ],
)
def test_phase_follows_the_question(said, expected):
    assert phase(said, IDLE) == expected


def test_a_read_back_waits_short_but_a_name_check_waits_long():
    assert phase("Just to confirm: ... Shall I book it?", waiting("42|7")) == "short"
    # "No, it's K... A... B..." pauses between letters: don't cut it off.
    assert (
        phase("I have the patient's name as Kabir, that's K, A, B, I, R.", waiting("name|Kabir"))
        == "long"
    )


def test_the_max_delay_changes_only_when_the_phase_does():
    calls = []
    session = SimpleNamespace(userdata=IDLE, update_options=lambda **kw: calls.append(kw))
    handler = endpointing.PhaseEndpointing(session, endpointing.Settings())

    def agent(text):
        msg = llm.ChatMessage(role="assistant", content=[text])
        handler.on_item(SimpleNamespace(item=msg))

    agent("May I please have your phone number?")
    agent("Thank you. And your date of birth?")  # still long: no update
    agent("Which time works for you?")
    handler.on_item(SimpleNamespace(item=llm.ChatMessage(role="user", content=["Six"])))
    assert calls == [
        {"endpointing_opts": {"max_delay": 3.0}},
        {"endpointing_opts": {"max_delay": 1.0}},
    ]
    assert handler.current == "short"


def test_off_by_default_and_settings_are_bounded(monkeypatch):
    monkeypatch.delenv("ENDPOINTING", raising=False)
    assert not endpointing.enabled()
    monkeypatch.setenv("ENDPOINTING", "phase")
    assert endpointing.enabled()
    monkeypatch.setenv("ENDPOINT_SHORT_MAX_S", "3")  # longer than the default wait
    with pytest.raises(ValueError):
        endpointing.Settings.from_env()
