"""One agent with modes on the call, never a chain of agents (design §7.1).

A mode change swaps the agent's instructions and tool list in place: no new agent and no new
AgentActivity, so the audio pipeline, the chat history and the backchannel filter carry on
untouched. The LLM asks for a change with `switch_mode`; code applies it through
`state.switch_mode`, which can refuse (a call in handoff stays in handoff).

LiveKit 1.8 behaviour this relies on (voice/agent_activity.py):
- The reply right after a tool call uses the refreshed instructions but the tool list from the
  start of the turn, so the new mode's tools arrive from the caller's next turn. A stale tool
  called in that reply is still refused by its guard.
- `update_tools` drops earlier tool calls and results for tools the new mode doesn't have. What
  the agent said aloud stays in the history.
"""

import json
import logging
from typing import Literal

from livekit.agents import Agent, RunContext, function_tool, llm

from agent import prompts, tools
from agent.state import ALLOWED, Action, CallState, Mode, Refused
from agent.state import switch_mode as apply_switch

logger = logging.getLogger("modes")

# The action each tool performs. Its guard checks the same action, so the list the LLM sees and
# the check code runs come from the same `ALLOWED` table.
TOOL_ACTIONS: list[tuple[llm.FunctionTool, Action]] = [
    (tools.clinic_info, Action.READ_CLINIC_INFO),
    (tools.find_slots, Action.READ_SLOTS),
    (tools.verify_caller, Action.VERIFY_CALLER),
    (tools.my_bookings, Action.READ_OWN_BOOKING),
    (tools.hold_slot, Action.HOLD_SLOT),
    (tools.confirm_booking, Action.CREATE_BOOKING),
    (tools.reschedule_booking, Action.RESCHEDULE_BOOKING),
    (tools.cancel_booking, Action.CANCEL_BOOKING),
    (tools.request_callback, Action.REQUEST_CALLBACK),
]


@function_tool
async def switch_mode(
    context: RunContext[CallState],
    mode: Literal["faq", "booking", "rescheduling", "cancelling", "handoff"],
) -> str:
    """Change what you are helping the caller with. Call it as soon as their task changes.

    Args:
        mode: "booking" for a new appointment, "rescheduling" to move an existing one,
            "cancelling" to cancel one, "faq" for clinic questions and free times, "handoff"
            when a person at the clinic must help (medical advice, an upset caller, or you keep
            misunderstanding each other).
    """
    state = context.userdata
    try:
        apply_switch(state, Mode(mode))
    except Refused as e:
        logger.warning("mode switch to %s refused: %s", mode, e)
        return json.dumps({"error": "not_allowed", "reason": str(e)})
    agent = context.session.current_agent
    if isinstance(agent, Receptionist):
        await agent.apply_mode(state.mode)
    logger.info("mode %s", state.history[-1] if state.history else state.mode)
    return json.dumps({"mode": state.mode})


def tools_for(mode: Mode) -> list[llm.FunctionTool]:
    """The tools the LLM sees in `mode`: only those the mode allows (design §7.1)."""
    allowed = [t for t, action in TOOL_ACTIONS if action in ALLOWED[mode]]
    # Handoff is final (state.switch_mode), so it has no way out to offer.
    return allowed if mode == Mode.HANDOFF else [*allowed, switch_mode]


class Receptionist(Agent):
    def __init__(self, language: str, mode: Mode = Mode.GREETING) -> None:
        self.language = language
        self.applied_mode = mode
        super().__init__(instructions=prompts.instructions(language, mode), tools=tools_for(mode))

    async def apply_mode(self, mode: Mode) -> None:
        """Bring instructions and tools in line with the call state's mode."""
        if mode == self.applied_mode:
            return
        await self.update_instructions(prompts.instructions(self.language, mode))
        await self.update_tools(tools_for(mode))
        self.applied_mode = mode
