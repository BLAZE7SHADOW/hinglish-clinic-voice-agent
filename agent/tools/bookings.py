"""Booking writes: hold, confirm, reschedule, cancel (design §9, "Preventing double bookings").

The sequence for a new booking:
1. hold_slot: the caller chose one of the times find_slots offered. A `held` row with a short
   expiry goes in, so another caller can't take the time while this one confirms. The partial
   unique index on (doctor, start) for held and confirmed rows is the final guard: two holds on
   one slot can't both exist, whatever the application does.
2. confirm_booking, first call: code speaks the read-back it wrote (agent/readback.py) and
   writes nothing.
3. confirm_booking, second call: only if the caller's answer is a clear yes to that exact
   sentence, heard in full (agent/confirm.py). It turns the held row into a confirmed one and
   records the event in the same transaction, then reads the stored row back so the agent
   confirms what is stored, not what it intended.
Reschedule and cancel use the same two-call read-back (`read_back_gate`).

Idempotency: a hold's key is `hold:<call id>:<doctor id>:<start>`, full length. Repeating a hold
returns the same row; confirming updates that row, so repeating a confirm can't create a second
booking. Writes never retry on their own. A timeout returns "outcome unknown", and the agent
calls the same tool again, which is safe for the reasons above (design §16.3).
"""

import asyncio
import json
import logging
import re

import asyncpg
from livekit.agents import RunContext, function_tool

from agent import db, readback
from agent.dates import CLINIC_TZ, now, parse_birth_date
from agent.identity import apply_spelling, normalize_name, parse_phone, parse_spelled_name
from agent.schedule import fmt_slot
from agent.state import Action, CallState, Mode, Slot, Verification, record_verification
from agent.tools.common import (
    DB_ERRORS,
    UNAVAILABLE,
    UNCERTAIN,
    caller_words,
    clinic_id,
    emit,
    error,
    from_caller,
    guard,
    language,
    read_back_gate,
)

logger = logging.getLogger("tools")

# Long enough to read back and hear "yes", short enough that an abandoned call doesn't block the
# slot for long. Holds are also released when the call ends. Measure expiry rate, then adjust.
HOLD_MINUTES = 3


def _label_key(text: str) -> str:
    """Compare time labels loosely on format only: "5:00 PM" = "5 pm" = "5 P.M."."""
    return re.sub(r"[\s.]|:00", "", text.lower())


def _day(dt) -> str:
    return dt.astimezone(CLINIC_TZ).strftime("%A %-d %B")


@function_tool
async def hold_slot(context: RunContext[CallState], slot: str) -> str:
    """Hold the time the caller chose so nobody else takes it while you confirm. Not a booking.

    Args:
        slot: One of the times from your latest find_slots result, copied exactly, e.g.
            "5:30 PM" or "Monday 12 October 5 PM".
    """
    if refusal := guard(context, Action.HOLD_SLOT):
        return refusal
    state = context.userdata
    chosen = next((o for o in state.offered if _label_key(o.label) == _label_key(slot)), None)
    if chosen is None:
        return error(
            "slot_not_offered",
            offered=[o.label for o in state.offered],
            say="Only a time from find_slots can be held. Call find_slots first if needed.",
        )
    previous = state.slot.hold_id if state.slot else None
    key = f"hold:{state.call_id}:{chosen.doctor_id}:{chosen.starts_at.isoformat()}"
    try:
        cid = await clinic_id()
        async with asyncio.timeout(db.WRITE_TIMEOUT_S), db.transaction() as conn:
            if previous is not None:  # the caller changed their mind: free the old time
                await _release(conn, previous)
            # An expired hold still sits in the unique index; clear it so the time can be taken.
            await conn.execute(
                "delete from bookings where doctor_id = $1 and starts_at = $2"
                " and status = 'held' and hold_expires_at <= now()",
                chosen.doctor_id,
                chosen.starts_at,
            )
            hold_id = await conn.fetchval(
                "insert into bookings (clinic_id, doctor_id, starts_at, ends_at, status,"
                " hold_expires_at, idempotency_key, created_by, call_id)"
                " values ($1, $2, $3, $4, 'held', now() + make_interval(mins => $5), $6,"
                " 'agent', $7)"
                " on conflict (idempotency_key) do update set status = 'held',"
                " hold_expires_at = excluded.hold_expires_at, cancelled_at = null"
                " where bookings.status in ('held', 'cancelled')"
                " returning id",
                cid,
                chosen.doctor_id,
                chosen.starts_at,
                chosen.ends_at,
                HOLD_MINUTES,
                key,
                state.call_id,
            )
            if hold_id is None:  # this call already booked exactly this time
                return error("already_booked_on_this_call")
            await emit(
                conn,
                state.call_id,
                "slot_held",
                booking_id=hold_id,
                doctor_id=chosen.doctor_id,
                starts_at=chosen.starts_at,
            )
    except asyncpg.UniqueViolationError:
        state.offered = [o for o in state.offered if o is not chosen]
        return error(
            "slot_taken",
            say="Someone else just took that time. Say so and offer another free time.",
        )
    except TimeoutError:
        logger.warning("hold_slot timed out; outcome unknown")
        return UNCERTAIN
    except DB_ERRORS + (LookupError,):
        logger.exception("hold_slot failed")
        return UNAVAILABLE

    state.slot = Slot(
        doctor_id=chosen.doctor_id,
        doctor_name=chosen.doctor_name,
        starts_at=chosen.starts_at,
        hold_id=hold_id,
    )
    state.pending = None  # a new time: any earlier read-back no longer applies
    return json.dumps(
        {
            "held": True,
            "doctor": chosen.doctor_name,
            "day": _day(chosen.starts_at),
            "time": fmt_slot(chosen.starts_at),
            "hold_minutes": HOLD_MINUTES,
            "next": "Call reschedule_booking with the booking's ref."
            if state.mode == Mode.RESCHEDULING
            else "Unless the caller is verified, get the phone number, then the date of birth, "
            "and call confirm_booking.",
        }
    )


@function_tool
async def confirm_booking(
    context: RunContext[CallState],
    phone: str = "",
    birth_date: str = "",
    patient_name: str = "",
    name_spelling: str = "",
) -> str:
    """Book the held time. The first call reads the booking back to the caller and books
    nothing; call it again with the same arguments after the caller's clear yes.

    Args:
        phone: The patient's phone number as the caller said it. "" if already verified.
        birth_date: The patient's date of birth in the caller's own words. "" if verified.
        patient_name: The patient's full name, only if this tool asked for it (new patient).
        name_spelling: The letters exactly as the caller spelled the name, if they did.
    """
    state = context.userdata
    slot = state.slot
    if refusal := guard(context, Action.CREATE_BOOKING, before_read_back=True):
        return refusal
    if slot is None or slot.hold_id is None:
        return error("no_slot_held", say="Hold a time with hold_slot first.")

    details = None
    if state.verification != Verification.VERIFIED:
        # Phone and date of birth first (LEARNING #25): they find a patient on file, whose name
        # is then read back instead of being recognised from speech.
        number = from_caller(context, phone, parse_phone)
        born = from_caller(context, birth_date, lambda t: parse_birth_date(t, now().date()))
        if missing := [f for f, v in (("phone", number), ("birth_date", born)) if not v]:
            return error(
                "need_patient_details",
                missing=missing,
                say="Ask for the first of these, one at a time, then call confirm_booking "
                "again. The time stays held.",
            )
        try:
            on_file = await _patients_with(number, born)
        except DB_ERRORS + (LookupError,):
            logger.exception("confirm_booking failed")
            return UNAVAILABLE
        spelled = parse_spelled_name(name_spelling) or _spelled_by_caller(context)
        name = apply_spelling(patient_name, spelled)
        if len(on_file) > 1 and name:  # e.g. twins sharing a phone: the name decides
            on_file = [r for r in on_file if normalize_name(r["full_name"]) == normalize_name(name)]
        if len(on_file) == 1:
            # Phone plus date of birth is this clinic's verification (§3), so the name on file
            # may be said, and the patient's later changes on this call need no new check.
            record_verification(state, matched_patient_id=on_file[0]["id"])
            logger.info("booking caller found on file as patient %s", state.patient_id)
        elif not name:
            return error(
                "new_patient",
                say="No patient with this phone and date of birth. Ask for the patient's full "
                "name, then call confirm_booking again with all three.",
            )
        else:
            details = (name, number, born)

    try:
        if details is None:  # on file: read back the name on file
            rows = await db.read("select full_name from patients where id = $1", state.patient_id)
            name = rows[0]["full_name"]
        else:
            name = details[0]
    except DB_ERRORS:
        logger.exception("confirm_booking failed")
        return UNAVAILABLE
    if details is not None and state.confirmed_name != name:
        # A new patient's name is checked on its own first, spelled back: a correction then
        # repeats only the name, and the booking is read back once, at the end (LEARNING #28).
        check = readback.name_check(name, language(context))
        if gate := read_back_gate(
            context, Action.CREATE_BOOKING, f"name|{name}", check, "confirm_booking"
        ):
            return gate
        state.confirmed_name = name
    text = readback.booking(slot.doctor_name, slot.starts_at, name, language(context))
    key = f"{slot.hold_id}|{details or state.patient_id}"
    if gate := read_back_gate(context, Action.CREATE_BOOKING, key, text, "confirm_booking"):
        return gate
    if refusal := guard(context, Action.CREATE_BOOKING):
        return refusal

    try:
        cid = await clinic_id()
        async with asyncio.timeout(db.WRITE_TIMEOUT_S), db.transaction() as conn:
            status = await conn.fetchval(
                "select status from bookings where id = $1 for update", slot.hold_id
            )
            if status is None or status == "cancelled":
                state.slot, state.pending = None, None
                return error("hold_lost", say="Say the time was released; check times again.")
            if status == "held":
                patient_id = state.patient_id if details is None else None
                if details is not None:
                    patient_id, existing = await _find_or_add_patient(conn, cid, *details)
                    if patient_id is None:
                        return error(
                            "details_mismatch",
                            say="Say the details don't match the clinic's records and ask them "
                            "to check the date of birth, or offer a callback.",
                        )
                    if existing:  # phone, name and birth date all matched: that is verification
                        record_verification(state, matched_patient_id=patient_id)
                    else:
                        state.patient_id = patient_id
                await conn.execute(
                    "update bookings set status = 'confirmed', patient_id = $2,"
                    " hold_expires_at = null where id = $1",
                    slot.hold_id,
                    patient_id,
                )
                await emit(
                    conn,
                    state.call_id,
                    "booking_created",
                    booking_id=slot.hold_id,
                    patient_id=patient_id,
                    doctor_id=slot.doctor_id,
                    starts_at=slot.starts_at,
                )
            # status == "confirmed": a repeat of a confirm that already went through. Same row.
            stored = await _stored(conn, slot.hold_id)
        return _done(state, slot.hold_id, "booked", stored)
    except TimeoutError:
        logger.warning("confirm_booking timed out; outcome unknown")
        return UNCERTAIN
    except DB_ERRORS + (LookupError,):
        logger.exception("confirm_booking failed")
        return UNAVAILABLE


@function_tool
async def reschedule_booking(context: RunContext[CallState], ref: int) -> str:
    """Move the caller's booking to the held time. The first call reads the change back to the
    caller and changes nothing; call it again with the same ref after the caller's clear yes.

    Args:
        ref: The ref of the booking to move, from my_bookings.
    """
    state = context.userdata
    slot = state.slot
    if refusal := guard(context, Action.RESCHEDULE_BOOKING, before_read_back=True):
        return refusal
    old_id = state.listed_bookings.get(ref)
    if old_id is None:
        return error("unknown_ref", say="Call my_bookings and use one of its refs.")
    if slot is None or slot.hold_id is None:
        return error("no_slot_held", say="Hold the new time with hold_slot first.")
    try:
        old_row = await _own_booking(state, old_id)
    except DB_ERRORS:
        logger.exception("reschedule_booking failed")
        return UNAVAILABLE
    if old_row is None:
        return error("booking_not_active", say="Say that booking is no longer active.")
    text = readback.reschedule(
        old_row["full_name"],
        old_row["starts_at"],
        slot.doctor_name,
        slot.starts_at,
        language(context),
    )
    key = f"{old_id}|{slot.hold_id}"
    if gate := read_back_gate(context, Action.RESCHEDULE_BOOKING, key, text, "reschedule_booking"):
        return gate
    if refusal := guard(context, Action.RESCHEDULE_BOOKING):
        return refusal
    try:
        async with asyncio.timeout(db.WRITE_TIMEOUT_S), db.transaction() as conn:
            old = await conn.fetchval(
                "select status from bookings where id = $1 and patient_id = $2 for update",
                old_id,
                state.patient_id,
            )
            new = await conn.fetchrow(
                "select status, replaces_booking_id from bookings where id = $1 for update",
                slot.hold_id,
            )
            repeat = new and new["status"] == "confirmed" and new["replaces_booking_id"] == old_id
            if not repeat:
                if old != "confirmed":
                    return error("booking_not_active", say="Say that booking is no longer active.")
                if new is None or new["status"] != "held":
                    state.slot = None
                    return error("hold_lost", say="Say the new time was released; check again.")
                await conn.execute(
                    "update bookings set status = 'confirmed', patient_id = $2,"
                    " hold_expires_at = null, replaces_booking_id = $3 where id = $1",
                    slot.hold_id,
                    state.patient_id,
                    old_id,
                )
                await conn.execute(
                    "update bookings set status = 'cancelled', cancelled_at = now() where id = $1",
                    old_id,
                )
                await emit(
                    conn,
                    state.call_id,
                    "booking_rescheduled",
                    booking_id=slot.hold_id,
                    replaces_booking_id=old_id,
                    patient_id=state.patient_id,
                )
            stored = await _stored(conn, slot.hold_id)
        state.listed_bookings.pop(ref, None)
        return _done(state, slot.hold_id, "rescheduled", stored)
    except TimeoutError:
        logger.warning("reschedule_booking timed out; outcome unknown")
        return UNCERTAIN
    except DB_ERRORS:
        logger.exception("reschedule_booking failed")
        return UNAVAILABLE


@function_tool
async def cancel_booking(context: RunContext[CallState], ref: int) -> str:
    """Cancel one of the caller's bookings. The first call reads it back to the caller and
    cancels nothing; call it again with the same ref after the caller's clear yes.

    Args:
        ref: The ref of the booking to cancel, from my_bookings.
    """
    if refusal := guard(context, Action.CANCEL_BOOKING, before_read_back=True):
        return refusal
    state = context.userdata
    booking_id = state.listed_bookings.get(ref)
    if booking_id is None:
        return error("unknown_ref", say="Call my_bookings and use one of its refs.")
    try:
        row = await _own_booking(state, booking_id)
    except DB_ERRORS:
        logger.exception("cancel_booking failed")
        return UNAVAILABLE
    if row is None:
        return error("booking_not_active", say="Say that booking is no longer active.")
    text = readback.cancel(row["full_name"], row["starts_at"], language(context))
    if gate := read_back_gate(
        context, Action.CANCEL_BOOKING, str(booking_id), text, "cancel_booking"
    ):
        return gate
    if refusal := guard(context, Action.CANCEL_BOOKING):
        return refusal
    try:
        async with asyncio.timeout(db.WRITE_TIMEOUT_S), db.transaction() as conn:
            status = await conn.fetchval(
                "select status from bookings where id = $1 and patient_id = $2 for update",
                booking_id,
                state.patient_id,
            )
            if status == "confirmed":
                await conn.execute(
                    "update bookings set status = 'cancelled', cancelled_at = now() where id = $1",
                    booking_id,
                )
                await emit(
                    conn,
                    state.call_id,
                    "booking_cancelled",
                    booking_id=booking_id,
                    patient_id=state.patient_id,
                )
            elif status != "cancelled":  # cancelled already = a repeat of this same cancel
                return error("booking_not_active", say="Say that booking is no longer active.")
            stored = await _stored(conn, booking_id)
        state.listed_bookings.pop(ref, None)
        return _done(state, booking_id, "cancelled", stored)
    except TimeoutError:
        logger.warning("cancel_booking timed out; outcome unknown")
        return UNCERTAIN
    except DB_ERRORS:
        logger.exception("cancel_booking failed")
        return UNAVAILABLE


async def _own_booking(state: CallState, booking_id: int):
    """The verified caller's own active booking (doctor, start), or None."""
    rows = await db.read(
        "select d.full_name, b.starts_at from bookings b join doctors d on d.id = b.doctor_id"
        " where b.id = $1 and b.patient_id = $2 and b.status = 'confirmed'",
        booking_id,
        state.patient_id,
    )
    return rows[0] if rows else None


async def _find_or_add_patient(conn, cid: int, name: str, phone: str, born) -> tuple:
    """(patient id, existed) for these details. (None, True) if the name is on file for this
    phone with a different date of birth: don't book against someone else's record.
    """
    rows = await conn.fetch(
        "select id, full_name, birth_date from patients where clinic_id = $1 and phone = $2",
        cid,
        phone,
    )
    same_name = [r for r in rows if normalize_name(r["full_name"]) == normalize_name(name)]
    if same_name:
        r = same_name[0]
        return (r["id"], True) if r["birth_date"] == born else (None, True)
    new_id = await conn.fetchval(
        "insert into patients (clinic_id, full_name, phone, birth_date) values ($1, $2, $3, $4)"
        " returning id",
        cid,
        name,
        phone,
        born,
    )
    return new_id, False


async def _patients_with(phone: str, born) -> list:
    """Patients on file with this phone and date of birth (a family can share a phone)."""
    return await db.read(
        "select id, full_name from patients where clinic_id = $1 and phone = $2"
        " and birth_date = $3",
        await clinic_id(),
        phone,
        born,
    )


def _spelled_by_caller(context: RunContext[CallState]) -> str | None:
    """A spelling in the caller's recent turns, read by code, not by the LLM."""
    return parse_spelled_name(" ".join(caller_words(context)))


async def _stored(conn, booking_id: int):
    """The booking as stored, read in the write's own transaction (design §9, step 4): the
    agent confirms what the database holds, not what it meant to write.
    """
    return await conn.fetchrow(
        "select b.status, d.full_name, b.starts_at from bookings b"
        " join doctors d on d.id = b.doctor_id where b.id = $1",
        booking_id,
    )


def _done(state: CallState, booking_id: int, outcome: str, r) -> str:
    state.slot, state.booking_id, state.pending = None, booking_id, None
    return json.dumps(
        {
            outcome: True,
            "status": r["status"],
            "doctor": r["full_name"],
            "day": _day(r["starts_at"]),
            "time": fmt_slot(r["starts_at"]),
            "say": "Confirm to the caller from these stored details.",
        }
    )


async def _release(conn, hold_id: int) -> None:
    await conn.execute(
        "update bookings set status = 'cancelled', cancelled_at = now()"
        " where id = $1 and status = 'held'",
        hold_id,
    )


async def release_call_holds(call_id: str) -> None:
    """At call end: free any time this call still holds, instead of waiting for expiry."""
    try:
        async with asyncio.timeout(db.WRITE_TIMEOUT_S), db.transaction() as conn:
            released = await conn.fetchval(
                "with r as (update bookings set status = 'cancelled', cancelled_at = now()"
                " where call_id = $1 and status = 'held' returning 1) select count(*) from r",
                call_id,
            )
        if released:
            logger.info("released %d hold(s) at call end", released)
    except DB_ERRORS:
        logger.exception("releasing holds failed; they expire on their own")
