import json
from types import SimpleNamespace

import pytest

from agent import modes, prompts, tools
from agent.modes import Receptionist, switch_mode, tools_for
from agent.state import ALLOWED, CallState, Mode


def context(state: CallState, agent: Receptionist | None = None):
    # The two RunContext attributes the tools use.
    return SimpleNamespace(userdata=state, session=SimpleNamespace(current_agent=agent))


def names(tool_list) -> set[str]:
    return {t.id for t in tool_list}


@pytest.mark.parametrize("mode", list(Mode))
def test_each_mode_exposes_only_the_tools_it_allows(mode):
    for tool, action in modes.TOOL_ACTIONS:
        assert (tool in tools_for(mode)) == (action in ALLOWED[mode])


def test_handoff_has_no_tools_and_no_way_out():
    assert tools_for(Mode.HANDOFF) == []
    assert "switch_mode" not in prompts.instructions("en", Mode.HANDOFF)


async def test_switch_mode_swaps_instructions_and_tools_in_place():
    state = CallState(call_id="AJ_test")
    agent = Receptionist("en")
    assert "switch_mode" in names(agent.tools)

    out = json.loads(await switch_mode(context(state, agent), mode="cancelling"))

    assert out == {"mode": "cancelling"} and state.mode == Mode.CANCELLING
    assert agent.applied_mode == Mode.CANCELLING
    assert names(agent.tools) == {"switch_mode"}
    assert agent.instructions == prompts.instructions("en", Mode.CANCELLING)


async def test_switch_out_of_handoff_is_refused_and_tools_stay_empty():
    state = CallState(call_id="AJ_test", mode=Mode.HANDOFF)
    agent = Receptionist("en", Mode.HANDOFF)

    out = json.loads(await switch_mode(context(state, agent), mode="booking"))

    assert out["error"] == "not_allowed"
    assert state.mode == Mode.HANDOFF and agent.tools == []


async def test_guard_refuses_a_stale_tool_before_touching_the_db(monkeypatch):
    # After a switch, the reply in the same turn still sees the old tool list (modes.py). The
    # guard is what stops the call.
    async def no_db(*a, **kw):
        raise AssertionError("the DB must not be read for a refused call")

    monkeypatch.setattr(tools.db, "read", no_db)
    state = CallState(call_id="AJ_test", mode=Mode.CANCELLING)

    out = json.loads(await tools.find_slots(context(state), doctor="Dr. Sharma", when="kal"))
    assert out["error"] == "not_allowed"
    out = json.loads(await tools.clinic_info(context(state), topic="hours"))
    assert out["error"] == "not_allowed"


@pytest.mark.parametrize("language", ["en", "hinglish"])
@pytest.mark.parametrize("mode", list(Mode))
def test_every_mode_has_instructions_in_every_language(language, mode):
    text = prompts.instructions(language, mode)
    assert "Your task now" in text and "medical advice" in text
