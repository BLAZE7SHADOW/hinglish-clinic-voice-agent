"""Filler line during lookups: once per caller turn, never before a read-back (LEARNING #34)."""

import asyncio
from types import SimpleNamespace

import pytest
from livekit.agents import UserInputTranscribedEvent

from agent import filler


class FakeSession:
    def __init__(self):
        self.agent_state = "thinking"
        self.said: list[tuple[str, dict]] = []

    def say(self, text, **kw):
        self.said.append((text, kw))


def started(name: str):
    return SimpleNamespace(
        update=SimpleNamespace(type="tool_call_started", function_call=SimpleNamespace(name=name))
    )


def turn():
    return UserInputTranscribedEvent(transcript="Doctor Sharma tomorrow evening", is_final=True)


async def test_one_filler_per_turn_and_kept_out_of_history():
    s = FakeSession()
    f = filler.Filler(s, language="en", delay=0)
    f.on_tool(started("switch_mode"))
    f.on_tool(started("find_slots"))  # same caller turn: no second filler
    await asyncio.sleep(0.01)
    assert [t for t, _ in s.said] == ["One moment, let me check."]
    assert s.said[0][1]["add_to_chat_ctx"] is False  # can't sit between a read-back and a yes
    f.on_transcript(turn())
    f.on_tool(started("clinic_info"))
    await asyncio.sleep(0.01)
    assert len(s.said) == 2 and s.said[1][0] == "Sure, just a second."


async def test_no_filler_before_a_read_back_or_over_speech():
    s = FakeSession()
    f = filler.Filler(s, language="en", delay=0)
    f.on_tool(started("confirm_booking"))  # speaks its own read-back
    s.agent_state = "speaking"
    f.on_tool(started("find_slots"))  # the LLM is already saying "let me check"
    await asyncio.sleep(0.01)
    assert s.said == []


async def test_a_reply_that_starts_first_cancels_the_filler():
    s = FakeSession()
    f = filler.Filler(s, language="en", delay=0.05)
    f.on_tool(started("find_slots"))
    s.agent_state = "speaking"
    await asyncio.sleep(0.08)
    assert s.said == []


def test_off_by_default_and_delay_bounded(monkeypatch):
    monkeypatch.delenv("FILLER", raising=False)
    assert not filler.enabled()
    monkeypatch.setenv("FILLER_DELAY_S", "5")
    with pytest.raises(ValueError):
        filler.delay_s()
