"""Free-slot computation and doctor matching. Pure functions, no I/O, so they're cheap to test."""

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from agent.dates import CLINIC_TZ

MIN_LEAD = timedelta(minutes=30)  # can't book a slot that starts in the next half hour


@dataclass(frozen=True)
class Doctor:
    id: int
    full_name: str  # "Dr. Anil Sharma"
    specialty: str
    fee_paise: int
    slot_minutes: int


@dataclass(frozen=True)
class WorkingHours:
    weekday: int  # 0 = Monday
    starts: time
    ends: time


def free_slots(
    hours: list[WorkingHours],
    days_off: set[date],
    taken: set[datetime],
    slot_minutes: int,
    window_start: datetime,
    window_end: datetime,
    now: datetime,
) -> list[datetime]:
    """Slot start times in [window_start, window_end) that are bookable.

    Bookable = inside working hours, not on a day off, not taken by an active booking, and at
    least MIN_LEAD from now. The database's unique index stays the final guard (design §9).
    """
    earliest = max(window_start, now + MIN_LEAD)
    step = timedelta(minutes=slot_minutes)
    out: list[datetime] = []
    day = window_start.astimezone(CLINIC_TZ).date()
    last_day = window_end.astimezone(CLINIC_TZ).date()
    while day <= last_day:
        if day not in days_off:
            for h in sorted(
                (h for h in hours if h.weekday == day.weekday()), key=lambda h: h.starts
            ):
                slot = datetime.combine(day, h.starts, CLINIC_TZ)
                end = datetime.combine(day, h.ends, CLINIC_TZ)
                while slot + step <= end:
                    if earliest <= slot < window_end and slot not in taken:
                        out.append(slot)
                    slot += step
        day += timedelta(days=1)
    return out


_SPECIALTY_WORDS = {
    "dentist": "Dentist",
    "dental": "Dentist",
    "teeth": "Dentist",
    "tooth": "Dentist",
    "daant": "Dentist",
    "dant": "Dentist",
    "physio": "Physiotherapist",
    "physiotherapist": "Physiotherapist",
    "physiotherapy": "Physiotherapist",
    "general": "General physician",
    "physician": "General physician",
    "gp": "General physician",
    "family": "General physician",
}
_FILLER = {
    "dr",
    "doctor",
    "doc",
    "ji",
    "sahab",
    "saab",
    "madam",
    "sir",
    "mr",
    "mrs",
    "ms",
    "the",
    "with",
    "se",
    "ko",
    "ka",
    "ke",
    "ki",
}


def match_doctors(query: str, doctors: list[Doctor]) -> list[Doctor]:
    """Doctors matching a spoken name or specialty ("Dr. Sharma", "Sharma ji", "dentist")."""
    words = [w for w in re.sub(r"[^a-z ]+", " ", query.lower()).split() if w not in _FILLER]
    if not words:
        return []
    by_name = [d for d in doctors if any(w in _name_words(d) for w in words)]
    if by_name:
        return by_name
    specialties = {_SPECIALTY_WORDS[w] for w in words if w in _SPECIALTY_WORDS}
    return [d for d in doctors if d.specialty in specialties]


def _name_words(d: Doctor) -> set[str]:
    return set(re.sub(r"[^a-z ]+", " ", d.full_name.lower()).split()) - _FILLER


def fmt_slot(dt: datetime) -> str:
    return dt.astimezone(CLINIC_TZ).strftime("%I:%M %p").lstrip("0").replace(":00 ", " ")


def fmt_fee(paise: int) -> str:
    rupees, rest = divmod(paise, 100)
    return f"{rupees} rupees" + (f" {rest} paise" if rest else "")
