"""Read-only tools for the call: clinic_info and find_slots (design §7.3).

Rules applied here:
- Typed inputs; `when` is the caller's own words, resolved in code (agent/dates.py). The model
  never computes dates.
- Data minimization: find_slots returns times only, never patient records (design §16.4).
- Tool output is data, not instructions: clinic text is wrapped under "clinic_data" and the
  prompt says to treat it as facts only (design §16.4, data-borne injection).
- Every DB read has a timeout and one retry (agent/db.py). On failure the tool returns an
  honest error for the agent to say, never a guess (design §9).
"""

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timedelta
from typing import Literal

import asyncpg
from livekit.agents import RunContext, function_tool

from agent import db
from agent.dates import CLINIC_TZ, PARTS_OF_DAY, resolve
from agent.schedule import Doctor, WorkingHours, fmt_fee, fmt_slot, free_slots, match_doctors

logger = logging.getLogger("tools")

CLINIC_NAME = os.environ.get("CLINIC_NAME", "Sharma Family Clinic")
MAX_SLOTS_SPOKEN = 5  # a voice reply can't list twenty times
NEXT_AVAILABLE_SEARCH_DAYS = 14
DOCTORS_CACHE_S = 60  # doctors don't change mid-call; saves a DB round trip per tool call

UNAVAILABLE = {
    "error": "schedule_system_unavailable",
    "say": "Say honestly that you can't check the schedule right now. Do not guess.",
}

Topic = Literal["hours", "address", "phone", "services", "payment", "parking", "fees", "doctors"]


@function_tool
async def clinic_info(context: RunContext, topic: Topic) -> str:
    """Look up a fact about the clinic. Use this instead of answering from memory.

    Args:
        topic: What the caller asked about. "fees" gives consultation fees per doctor;
            "doctors" lists the doctors and their specialties.
    """
    try:
        if topic in ("fees", "doctors"):
            doctors = await _doctors()
            facts = [
                {"doctor": d.full_name, "specialty": d.specialty}
                | ({"consultation_fee": fmt_fee(d.fee_paise)} if topic == "fees" else {})
                for d in doctors
            ]
        else:
            rows = await db.read(
                "select f.answer from clinic_facts f join clinics c on c.id = f.clinic_id"
                " where c.name = $1 and f.topic = $2",
                CLINIC_NAME,
                topic,
            )
            facts = rows[0]["answer"] if rows else "No information on file for this topic."
    except (TimeoutError, OSError, asyncpg.PostgresError):
        logger.exception("clinic_info failed")
        return json.dumps(UNAVAILABLE)
    return json.dumps({"clinic_data": facts})


@function_tool
async def find_slots(context: RunContext, doctor: str, when: str) -> str:
    """Check which appointment times are free. Read-only: it does not book anything.

    Args:
        doctor: The doctor's name or specialty exactly as the caller said it, e.g.
            "Dr. Sharma", "Sharma ji", "dentist", "daant ke doctor".
        when: The caller's own words for the day and time, copied as spoken, e.g.
            "kal shaam", "tomorrow evening", "next Monday at ten", "parson subah".
            Never convert it to a date yourself. Use "" if the caller gave no day.
    """
    now = datetime.now(CLINIC_TZ)
    w = resolve(when, now)
    try:
        doctors = await _doctors()
        matches = match_doctors(doctor, doctors)
        if len(matches) != 1:
            return json.dumps(
                {
                    "error": "doctor_not_found" if not matches else "doctor_ambiguous",
                    "doctors": [
                        {"name": d.full_name, "specialty": d.specialty}
                        for d in (matches or doctors)
                    ],
                }
            )
        d = matches[0]
        search_end = max(w.end, now + timedelta(days=NEXT_AVAILABLE_SEARCH_DAYS))
        hours, days_off, taken = await _schedule(d.id, w.start, search_end)
    except (TimeoutError, OSError, asyncpg.PostgresError):
        logger.exception("find_slots failed")
        return json.dumps(UNAVAILABLE)

    slots = free_slots(hours, days_off, taken, d.slot_minutes, w.start, w.end, now)
    multi_day = w.start.date() != (w.end - timedelta(minutes=1)).date()
    label = (lambda s: f"{s.strftime('%A %-d %B')} {fmt_slot(s)}") if multi_day else fmt_slot

    result: dict = {
        "doctor": d.full_name,
        "specialty": d.specialty,
        "when_understood": w.description,
        "assumptions_to_confirm": w.assumptions,
    }
    if w.exact_time:
        result["requested_time_free"] = w.exact_time in set(
            free_slots(
                hours,
                days_off,
                taken,
                d.slot_minutes,
                w.exact_time,
                w.exact_time + timedelta(minutes=1),
                now,
            )
        )
        day_start = w.exact_time.replace(hour=0, minute=0)
        same_day = free_slots(
            hours, days_off, taken, d.slot_minutes, day_start, day_start + timedelta(days=1), now
        )
        result["nearest_free_that_day"] = [
            fmt_slot(s) for s in sorted(same_day, key=lambda s: abs(s - w.exact_time))
        ][:3]
    else:
        result["free"] = [label(s) for s in slots[:MAX_SLOTS_SPOKEN]]
        result["more_free_not_listed"] = max(0, len(slots) - MAX_SLOTS_SPOKEN)
    if not multi_day:
        day = w.start.date()
        if day in days_off or not any(h.weekday == day.weekday() for h in hours):
            result["doctor_not_working_that_day"] = True
    if not slots and not w.exact_time:
        later = free_slots(hours, days_off, taken, d.slot_minutes, w.end, search_end, now)
        if w.part:  # asked for evenings: offer evenings first
            lo, hi = PARTS_OF_DAY[w.part]
            later = [s for s in later if lo <= s.hour < hi] or later
        result["next_free_after_that"] = [
            f"{s.strftime('%A %-d %B')} {fmt_slot(s)}" for s in later[:3]
        ]
    return json.dumps(result)


_doctors_cache: tuple[float, list[Doctor]] | None = None


async def _doctors() -> list[Doctor]:
    global _doctors_cache
    if _doctors_cache and time.monotonic() - _doctors_cache[0] < DOCTORS_CACHE_S:
        return _doctors_cache[1]
    rows = await db.read(
        "select d.id, d.full_name, d.specialty, d.consultation_fee_paise, d.slot_minutes"
        " from doctors d join clinics c on c.id = d.clinic_id"
        " where c.name = $1 and d.active order by d.id",
        CLINIC_NAME,
    )
    doctors = [
        Doctor(
            r["id"], r["full_name"], r["specialty"], r["consultation_fee_paise"], r["slot_minutes"]
        )
        for r in rows
    ]
    _doctors_cache = (time.monotonic(), doctors)
    return doctors


async def _schedule(doctor_id: int, start: datetime, end: datetime):
    # Three independent reads in parallel: one network round trip instead of three.
    hours_rows, off_rows, taken_rows = await asyncio.gather(
        db.read("select weekday, starts, ends from doctor_hours where doctor_id = $1", doctor_id),
        db.read(
            "select day from doctor_days_off where doctor_id = $1 and day between $2 and $3",
            doctor_id,
            start.date(),
            end.date(),
        ),
        # Active = confirmed, or held with a hold that hasn't expired yet.
        db.read(
            "select starts_at from bookings where doctor_id = $1 and starts_at >= $2"
            " and starts_at < $3 and (status = 'confirmed'"
            " or (status = 'held' and hold_expires_at > now()))",
            doctor_id,
            start,
            end,
        ),
    )
    hours = [WorkingHours(r["weekday"], r["starts"], r["ends"]) for r in hours_rows]
    return (
        hours,
        {r["day"] for r in off_rows},
        {r["starts_at"].astimezone(CLINIC_TZ) for r in taken_rows},
    )
