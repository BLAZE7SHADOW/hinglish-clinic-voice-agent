"""What every tool shares: the state guard, honest errors, the clinic id and the event log."""

import json
import logging
import os
import time

import asyncpg
from livekit.agents import RunContext

from agent import db
from agent.confirm import user_turns
from agent.state import Action, CallState, Refused, check

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


def guard(context: RunContext[CallState], action: Action) -> str | None:
    """None if `action` is allowed now, else the refusal for the LLM to act on."""
    try:
        check(context.userdata, action)
    except Refused as e:
        logger.warning("refused %s in %s mode: %s", action, context.userdata.mode, e)
        return json.dumps({"error": "not_allowed", "reason": str(e)})
    return None


def error(code: str, **details) -> str:
    return json.dumps({"error": code, **details})


def turns(context: RunContext[CallState]) -> int:
    return user_turns(context.session.history)


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
