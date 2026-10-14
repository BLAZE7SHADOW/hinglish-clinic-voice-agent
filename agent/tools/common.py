"""What every tool shares: the state guard, honest errors, the clinic id and the event log."""

import json
import logging
import os
import time
from collections.abc import Callable

import asyncpg
from livekit.agents import RunContext, llm

from agent import db
from agent.confirm import check_yes, messages, user_turns
from agent.state import Action, CallState, PendingWrite, Refused, check

logger = logging.getLogger("tools")

CLINIC_NAME = os.environ.get("CLINIC_NAME", "Sharma Family Clinic")
DB_ERRORS = (TimeoutError, OSError, asyncpg.PostgresError)

UNAVAILABLE = json.dumps(
    {
        "error": "schedule_system_unavailable",
        "say": "Say honestly that you can't check the schedule right now. Do not guess.",
    }
)
# A write that timed out may still have committed (design §16.3), so the agent must not say it
# failed, and must not say it worked.
UNCERTAIN = json.dumps(
    {
        "error": "write_outcome_unknown",
        "say": "Say the system is slow and you are checking. Call the same tool again once; it "
        "is safe to repeat. If it fails again, offer a callback.",
    }
)


def guard(
    context: RunContext[CallState], action: Action, *, before_read_back: bool = False
) -> str | None:
    """None if `action` is allowed now, else the refusal for the LLM to act on."""
    try:
        check(context.userdata, action, before_read_back=before_read_back)
    except Refused as e:
        logger.warning("refused %s in %s mode: %s", action, context.userdata.mode, e)
        return json.dumps({"error": "not_allowed", "reason": str(e)})
    return None


def error(code: str, **details) -> str:
    return json.dumps({"error": code, **details})


def turns(context: RunContext[CallState]) -> int:
    return user_turns(context.session.history)


CALLER_TURNS_TO_SEARCH = 4  # a number said in parts spans at most a few STT segments


def caller_words(context: RunContext[CallState]) -> list[str]:
    """The caller's latest turns, newest last."""
    return [m.text_content or "" for m in messages(context.session.history) if m.role == "user"][
        -CALLER_TURNS_TO_SEARCH:
    ]


def from_caller[T](
    context: RunContext[CallState], value: str, parse: Callable[[str], T | None]
) -> T | None:
    """`parse(value)`, or else the same parse over the caller's own last turns.

    The LLM copies numbers badly when STT split them into segments ("two twothree", LEARNING
    #25). So if its version doesn't parse, code reads what the caller actually said: every run
    of consecutive recent turns, the newest and shortest first. A number said in parts parses
    only when exactly its parts are joined; a run that also takes in other numbers ("nineteen
    eighty eight") makes it too long and fails instead of matching.
    """
    if value and (parsed := parse(value)) is not None:
        return parsed
    turns = caller_words(context)
    for end in range(len(turns), 0, -1):
        for start in range(end - 1, -1, -1):
            if (parsed := parse(" ".join(turns[start:end]))) is not None:
                return parsed
    return None


def language(context: RunContext[CallState]) -> str:
    return getattr(context.session.current_agent, "language", "en")


MAX_READ_BACKS = 3  # then stop: the caller and agent aren't understanding each other


def read_back_gate(
    context: RunContext[CallState], action: Action, key: str, text: str, tool: str
) -> str | llm.ToolResult | None:
    """None once the caller has said a clear yes to this exact read-back; until then, what the
    write tool must return instead of writing (LEARNING #22).

    First call for a write: code speaks `text` word for word and the tool returns without asking
    the LLM for a reply. Second call: the caller's answer is checked against that sentence.
    `key` is everything the read-back covers (ids, patient details); if any of it changed, the
    old yes doesn't count and the new version is read back.
    """
    state = context.userdata
    p = state.pending
    if p is None or p.action != action or p.key != key:
        return _speak(
            context,
            PendingWrite(action=action, key=key, text=text, after_user_turn=turns(context)),
            tool,
        )
    if p.confirmed:
        return None
    result = check_yes(
        context.session.history,
        after_user_turn=p.after_user_turn,
        read_back=p.text,
        yes_after_facts=os.environ.get("READBACK_YES_AFTER_FACTS", "off") == "on",
    )
    if result.ok:
        p.confirmed = True
        return None
    logger.info("write held back: %s", result.reason)
    if not result.read_back_again:
        return error("not_confirmed", reason=result.reason)
    if p.times_spoken >= MAX_READ_BACKS:
        return error(
            "not_confirmed",
            reason="the read-back was not answered after several tries",
            say="Offer a callback from the clinic instead.",
        )
    p.times_spoken += 1
    p.after_user_turn = turns(context)
    return _speak(context, p, tool)


def _speak(context: RunContext[CallState], pending: PendingWrite, tool: str) -> llm.ToolResult:
    context.userdata.pending = pending
    context.session.say(pending.text)
    # No LLM reply: the read-back is the whole of the agent's turn, then the caller answers.
    return llm.ToolResult(
        json.dumps(
            {
                "read_back_spoken": pending.text,
                # Code decides what an answer means, so every answer comes back to the tool:
                # a "no" handled by the LLM alone skips the capture protocol (LEARNING #32).
                "next": f"Wait for the caller's answer, then call {tool} again with the same "
                "arguments plus any correction or spelling they gave, whatever they answered. "
                "Never repeat the read-back yourself.",
            }
        ),
        reply_required=False,
    )


async def sync_mode(context: RunContext[CallState]) -> None:
    """Bring the running agent's tools and instructions in line with the state's mode.

    Needed whenever code changes the mode itself, e.g. failed verification -> handoff.
    """
    apply_mode = getattr(context.session.current_agent, "apply_mode", None)
    if apply_mode is not None:
        await apply_mode(context.userdata.mode)


async def emit(conn: asyncpg.Connection, call_id: str, type_: str, **payload) -> None:
    """Append to the event log in the same transaction as the change it records.

    Ids and times only, never names or phone numbers, except where staff need the number to
    act (a callback).
    """
    await conn.execute(
        "insert into events (call_id, type, payload) values ($1, $2, $3::jsonb)",
        call_id,
        type_,
        json.dumps(payload, default=str),
    )


_clinic_cache: tuple[float, int] | None = None
CLINIC_CACHE_S = 300


async def clinic_id() -> int:
    global _clinic_cache
    if _clinic_cache and time.monotonic() - _clinic_cache[0] < CLINIC_CACHE_S:
        return _clinic_cache[1]
    rows = await db.read("select id from clinics where name = $1", CLINIC_NAME)
    if not rows:
        raise LookupError(f"clinic {CLINIC_NAME!r} not found")
    _clinic_cache = (time.monotonic(), rows[0]["id"])
    return _clinic_cache[1]
