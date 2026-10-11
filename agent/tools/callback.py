"""request_callback: a person at the clinic calls the caller back (design §3 flow 4, §7.3).

The real-time path only appends an event row and moves on. Nothing in the turn waits on a
queue or a workflow; Temporal picks callbacks up from the event log in week 5 (CLAUDE.md hard
rule). The reason is a fixed list, never free text, so no caller words land in the event log.
"""

import asyncio
import json
import logging
from typing import Literal

from livekit.agents import RunContext, function_tool

from agent import db
from agent.identity import parse_phone
from agent.state import Action, CallState
from agent.tools.common import DB_ERRORS, UNCERTAIN, emit, error, guard

logger = logging.getLogger("tools")

Reason = Literal[
    "medical_question",
    "wants_a_person",
    "upset_caller",
    "not_understood",
    "system_problem",
    "other",
]


@function_tool
async def request_callback(context: RunContext[CallState], reason: Reason, phone: str = "") -> str:
    """Ask a person at the clinic to call the caller back. Only say a callback is arranged after
    this returns requested.

    Args:
        reason: Why a person needs to call back.
        phone: The number to call back, as the caller said it. "" if the caller is verified.
    """
    if refusal := guard(context, Action.REQUEST_CALLBACK):
        return refusal
    state = context.userdata
    if state.callback_requested:  # one per call; a repeat is not a second request
        return json.dumps({"requested": True})
    number = None
    if state.patient_id is None:
        number = parse_phone(phone)
        if number is None:
            return error("need_phone", say="Ask which number the clinic should call back.")
    try:
        async with asyncio.timeout(db.WRITE_TIMEOUT_S), db.transaction() as conn:
            if number is None:  # verified caller: the number on file, never read out
                number = await conn.fetchval(
                    "select phone from patients where id = $1", state.patient_id
                )
            # The number is in the payload because staff need it to call back; logs never get it.
            await emit(
                conn,
                state.call_id,
                "callback_requested",
                reason=reason,
                mode=state.mode,
                patient_id=state.patient_id,
                phone=number,
            )
    except TimeoutError:
        logger.warning("request_callback timed out; outcome unknown")
        return UNCERTAIN
    except DB_ERRORS:
        logger.exception("request_callback failed")
        return error(
            "callback_failed",
            say="Say sorry, you couldn't arrange a callback, and suggest they call the front desk.",
        )
    state.callback_requested = True
    logger.info("callback requested: %s", reason)
    return json.dumps(
        {"requested": True, "say": "Say someone from the clinic will call back. Promise no time."}
    )
