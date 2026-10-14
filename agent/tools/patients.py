"""Caller verification and the verified caller's own bookings (design §3, §7.3, §7.4).

- Verified = the phone number plus the name or the date of birth match one patient. Both are
  parsed and compared in code (agent/identity.py, agent/dates.py), exactly, never fuzzily.
- A failed attempt says only "not verified": never which detail was wrong or whether the phone
  number is on file, so the tool can't be used to find out who is a patient here.
- Two failed attempts end verification for the call and hand off (state.record_verification).
- my_bookings returns short refs (1, 2, ...), never raw ids or another patient's data. Only
  those refs can be rescheduled or cancelled.
"""

import json
import logging

from livekit.agents import RunContext, function_tool

from agent import db
from agent.dates import now, parse_birth_date
from agent.identity import normalize_name, parse_phone
from agent.state import (
    MAX_VERIFICATION_ATTEMPTS,
    Action,
    CallState,
    Verification,
    record_misunderstanding,
    record_verification,
)
from agent.tools.common import (
    DB_ERRORS,
    UNAVAILABLE,
    clinic_id,
    error,
    from_caller,
    guard,
    sync_mode,
)
from agent.tools.read import fmt_day_slot

logger = logging.getLogger("tools")

MAX_LISTED_BOOKINGS = 5


def match_patient(rows, *, name: str, birth_date) -> int | None:
    """The one patient on this phone matching every detail the caller gave, else None.

    At least one of name and date of birth must be given (the caller checks). If both are given,
    both must match: a right name doesn't excuse a wrong date of birth.
    """
    wanted = normalize_name(name)
    hits = {
        r["id"]
        for r in rows
        if (birth_date is None or r["birth_date"] == birth_date)
        and (not wanted or normalize_name(r["full_name"]) == wanted)
    }
    return hits.pop() if len(hits) == 1 else None  # two family members match: not verified


@function_tool
async def verify_caller(
    context: RunContext[CallState], phone: str, name: str = "", birth_date: str = ""
) -> str:
    """Verify the caller before talking about or changing their appointments.

    Args:
        phone: The phone number the booking was made with, as the caller said it.
        name: The patient's full name as the caller said it, or "".
        birth_date: The patient's date of birth in the caller's own words, or "". Prefer this
            over the name when the caller gives both.
    """
    if refusal := guard(context, Action.VERIFY_CALLER):
        return refusal
    state = context.userdata
    if state.verification == Verification.VERIFIED:
        return json.dumps({"verified": True})
    number = from_caller(context, phone, parse_phone)
    born = from_caller(context, birth_date, lambda t: parse_birth_date(t, now().date()))
    if number is None or (not name.strip() and born is None):
        # Not a wrong answer, a misheard one: ask again, but don't loop forever.
        record_misunderstanding(state)
        await sync_mode(context)
        missing = "phone" if number is None else "name_or_birth_date"
        return error("not_understood", missing=missing, say="Ask for it once more, slowly.")
    try:
        rows = await db.read(
            "select id, full_name, birth_date from patients where clinic_id = $1 and phone = $2",
            await clinic_id(),
            number,
        )
    except DB_ERRORS + (LookupError,):
        logger.exception("verify_caller failed")
        return UNAVAILABLE
    matched = match_patient(rows, name=name, birth_date=born)
    record_verification(state, matched_patient_id=matched)
    await sync_mode(context)
    if matched is not None:
        logger.info("caller verified as patient %s", matched)
        return json.dumps({"verified": True})
    logger.info("verification failed, attempt %d", state.verification_attempts)
    if state.verification == Verification.FAILED:
        return json.dumps(
            {
                "verified": False,
                "say": "Say you can't verify the details on this call and a person at the "
                "clinic will need to help.",
            }
        )
    return json.dumps(
        {
            "verified": False,
            "attempts_left": MAX_VERIFICATION_ATTEMPTS - state.verification_attempts,
            "say": "Say the details didn't match and ask them to check and say them again.",
        }
    )


@function_tool
async def my_bookings(context: RunContext[CallState]) -> str:
    """List the verified caller's upcoming appointments."""
    if refusal := guard(context, Action.READ_OWN_BOOKING):
        return refusal
    state = context.userdata
    try:
        rows = await db.read(
            "select b.id, d.full_name, b.starts_at from bookings b"
            " join doctors d on d.id = b.doctor_id"
            " where b.patient_id = $1 and b.status = 'confirmed' and b.starts_at >= $2"
            " order by b.starts_at limit $3",
            state.patient_id,
            now(),
            MAX_LISTED_BOOKINGS,
        )
    except DB_ERRORS:
        logger.exception("my_bookings failed")
        return UNAVAILABLE
    state.listed_bookings = {i: r["id"] for i, r in enumerate(rows, start=1)}
    return json.dumps(
        {
            "bookings": [
                {"ref": i, "doctor": r["full_name"], "when": fmt_day_slot(r["starts_at"])}
                for i, r in enumerate(rows, start=1)
            ]
        }
    )
