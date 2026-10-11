"""Resolve the caller's own words for a day and time into concrete datetimes.

The LLM never computes dates (design §7.3). It passes the caller's phrase ("kal shaam",
"next Monday at ten", "parson subah saade das baje") and this module turns it into a window in
the clinic's timezone, plus the assumptions it made, so the agent can read them back.

Covered: aaj/today, kal/tomorrow, parson/day after tomorrow, weekdays in English and Hindi
(with is/this, agle/next), is hafte/this week, agle hafte/next week, "15 tarikh" / "15th" /
"15 October", parts of the day (subah, dopahar, shaam, raat), and times with digits or
number words (English and Hindi) including saade/sawa/paune/dedh/dhai.
"""

import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

CLINIC_TZ = ZoneInfo("Asia/Kolkata")
DEFAULT_HORIZON_DAYS = 7  # no day mentioned: look this far ahead

PARTS_OF_DAY = {  # (start hour, end hour)
    "morning": (8, 12),
    "afternoon": (12, 16),
    "evening": (16, 20),
    "night": (20, 23),
}
PART_WORDS = {
    "subah": "morning",
    "morning": "morning",
    "dopahar": "afternoon",
    "dophar": "afternoon",
    "afternoon": "afternoon",
    "shaam": "evening",
    "sham": "evening",
    "evening": "evening",
    "raat": "night",
    "rat": "night",
    "night": "night",
}
WEEKDAYS = {
    "monday": 0,
    "somvar": 0,
    "somvaar": 0,
    "tuesday": 1,
    "mangalvar": 1,
    "mangalvaar": 1,
    "wednesday": 2,
    "budhvar": 2,
    "budhvaar": 2,
    "thursday": 3,
    "guruvar": 3,
    "guruvaar": 3,
    "brihaspativar": 3,
    "friday": 4,
    "shukravar": 4,
    "shukravaar": 4,
    "saturday": 5,
    "shanivar": 5,
    "shanivaar": 5,
    "sunday": 6,
    "ravivar": 6,
    "ravivaar": 6,
    "itvaar": 6,
    "itwar": 6,
}
MONTHS = {
    m: i
    for i, m in enumerate(
        [
            "january",
            "february",
            "march",
            "april",
            "may",
            "june",
            "july",
            "august",
            "september",
            "october",
            "november",
            "december",
        ],
        start=1,
    )
}
NUMBER_WORDS = {
    # English
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    # Hindi (romanized; common spellings)
    "ek": 1,
    "do": 2,
    "teen": 3,
    "char": 4,
    "chaar": 4,
    "paanch": 5,
    "panch": 5,
    "chhe": 6,
    "che": 6,
    "chheh": 6,
    "saat": 7,
    "aath": 8,
    "ath": 8,
    "nau": 9,
    "das": 10,
    "dus": 10,
    "gyarah": 11,
    "gyaarah": 11,
    "barah": 12,
    "baarah": 12,
}
FRACTION_WORDS = {"dedh": (1, 30), "dhai": (2, 30), "dhaai": (2, 30)}  # whole time words
MODIFIERS = {"saade": 30, "sade": 30, "sawa": 15, "sava": 15, "paune": -15, "pone": -15}

_NUM = r"(\d{1,2}|" + "|".join(NUMBER_WORDS) + r")"


def now() -> datetime:
    """The clinic's current time. Evals freeze it so a scenario sees the same schedule every run.

    AGENT_FAKE_NOW is honoured only with EVAL_MODE=1, so a stray value can't move a real call's
    "kal" to another day.
    """
    fake = os.environ.get("AGENT_FAKE_NOW")
    if fake and os.environ.get("EVAL_MODE") == "1":
        return datetime.fromisoformat(fake).astimezone(CLINIC_TZ)
    return datetime.now(CLINIC_TZ)


@dataclass(frozen=True)
class When:
    start: datetime  # inclusive, clinic timezone
    end: datetime  # exclusive
    exact_time: datetime | None  # set when the caller named a time
    day_given: bool  # False: no day was mentioned, so start/end is the default horizon
    description: str  # for reading back: "Sunday 11 October, evening (4 PM to 8 PM)"
    assumptions: list[str] = field(default_factory=list)
    part: str | None = None  # "morning" | "afternoon" | "evening" | "night"


def resolve(phrase: str, now: datetime) -> When:
    """Resolve `phrase` relative to `now` (any tz-aware datetime)."""
    now = now.astimezone(CLINIC_TZ)
    text = _normalize(phrase)
    assumptions: list[str] = []

    day_from, day_to, day_label = _resolve_day(text, now.date(), assumptions)
    day_given = day_from is not None
    if not day_given:
        day_from, day_to = now.date(), now.date() + timedelta(days=DEFAULT_HORIZON_DAYS - 1)
        day_label = f"the next {DEFAULT_HORIZON_DAYS} days"

    part = next((PART_WORDS[w] for w in text.split() if w in PART_WORDS), None)
    hm = _resolve_time(text, part, assumptions)

    if hm and day_from == day_to:
        exact = datetime.combine(day_from, time(*hm), CLINIC_TZ)
        start, end = exact, exact + timedelta(minutes=1)
        description = f"{_fmt_day(day_from)} at {_fmt_time(time(*hm))}"
    else:
        lo, hi = PARTS_OF_DAY[part] if part else (0, 24)
        start = datetime.combine(day_from, time(lo), CLINIC_TZ)
        end = datetime.combine(day_to, time(0), CLINIC_TZ) + timedelta(hours=hi)
        exact = None
        description = day_label
        if part:
            description += f", {part} ({_fmt_hour(lo)} to {_fmt_hour(hi)})"
        if hm:
            assumptions.append("a time was given without a single day; time ignored")

    if start < now and end > now:  # today's window already partly over
        start = now
    return When(start, end, exact, day_given, description, assumptions, part)


def _normalize(phrase: str) -> str:
    text = phrase.lower().replace("'", "").replace("’", "")
    text = re.sub(r"[^a-z0-9:]+", " ", text)
    text = re.sub(r"\bday after tomorrow\b", "parson", text)
    text = re.sub(r"\bo ?clock\b", "baje", text)
    return " ".join(text.split())


def _resolve_day(text: str, today: date, assumptions: list[str]) -> tuple:
    words = text.split()
    if "parson" in words or "parso" in words:
        d = today + timedelta(days=2)
        return d, d, _fmt_day(d)
    if "kal" in words:
        assumptions.append("'kal' read as tomorrow (it can also mean yesterday)")
        d = today + timedelta(days=1)
        return d, d, _fmt_day(d)
    if "tomorrow" in words or "tmrw" in words:
        d = today + timedelta(days=1)
        return d, d, _fmt_day(d)
    if "aaj" in words or "today" in words:
        return today, today, _fmt_day(today)

    if re.search(r"\b(agle|next) (hafte|week)\b", text):
        monday = today + timedelta(days=7 - today.weekday())
        return monday, monday + timedelta(days=6), f"next week ({_fmt_day(monday)} onward)"
    if re.search(r"\b(is|this) (hafte|week)\b", text):
        sunday = today + timedelta(days=6 - today.weekday())
        return today, sunday, "this week"

    for w in words:
        if w in WEEKDAYS:
            ahead = (WEEKDAYS[w] - today.weekday()) % 7 or 7  # coming one, never today
            d = today + timedelta(days=ahead)
            if re.search(rf"\b(agle|next) {w}\b", text):
                assumptions.append(f"'next {w}' read as the coming {w}, {_fmt_day(d)}")
            return d, d, _fmt_day(d)

    m = re.search(
        r"\b(\d{1,2}) ?(?:st|nd|rd|th)?\s(" + "|".join(MONTHS) + r")\b", text
    ) or re.search(r"\b(\d{1,2}) ?(?:tarikh|tareekh|st|nd|rd|th)\b", text)
    if m:
        day_num = int(m.group(1))
        month = MONTHS.get(m.group(2)) if m.lastindex == 2 else None
        d = _next_date_with_day(today, day_num, month)
        if d:
            return d, d, _fmt_day(d)
    return None, None, None


def _next_date_with_day(today: date, day_num: int, month: int | None) -> date | None:
    for months_ahead in range(13):
        y = today.year + (today.month - 1 + months_ahead) // 12
        mth = (today.month - 1 + months_ahead) % 12 + 1
        if month and mth != month:
            continue
        try:
            d = date(y, mth, day_num)
        except ValueError:
            continue
        if d >= today:
            return d
    return None


def _resolve_time(text: str, part: str | None, assumptions: list[str]) -> tuple[int, int] | None:
    words = text.split()
    hour = minute = None
    if m := re.search(r"\b(\d{1,2}):(\d{2})\b", text):
        hour, minute = int(m.group(1)), int(m.group(2))
    elif w := next((w for w in words if w in FRACTION_WORDS), None):
        hour, minute = FRACTION_WORDS[w]
    elif m := (
        re.search(rf"\b{_NUM} ?(baje|am|pm)\b", text)
        or re.search(rf"\b(?:at|around|by|about) {_NUM}\b", text)
    ):
        hour, minute = _num(m.group(1)), 0
        mod = re.search(rf"\b({'|'.join(MODIFIERS)}) {re.escape(m.group(1))}\b", text)
        if mod:
            delta = MODIFIERS[mod.group(1)]
            hour, minute = (hour - 1, 60 + delta) if delta < 0 else (hour, delta)
    if hour is None or not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None

    if re.search(r"\bpm\b", text) or part in ("afternoon", "evening", "night"):
        if hour < 12:
            hour += 12
    elif re.search(r"\bam\b", text) or part == "morning":
        pass
    elif 1 <= hour <= 7:  # a clinic call: "paanch baje" means 5 PM, not 5 AM
        hour += 12
        assumptions.append(f"no AM/PM given; {_fmt_time(time(hour, minute))} assumed")
    return hour, minute


def _num(token: str) -> int:
    return int(token) if token.isdigit() else NUMBER_WORDS[token]


def _fmt_day(d: date) -> str:
    return f"{d.strftime('%A')} {d.day} {d.strftime('%B')}"


def _fmt_time(t: time) -> str:
    return t.strftime("%I:%M %p").lstrip("0").replace(":00 ", " ")


def _fmt_hour(h: int) -> str:
    return "midnight" if h in (0, 24) else _fmt_time(time(h))


_MONTH_WORDS = MONTHS | {m[:3]: i for m, i in MONTHS.items()} | {"sept": 9}
_DOB_NUMERIC = re.compile(r"\b(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})\b")  # Indian order: D/M/Y
_DOB_ISO = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")
MAX_AGE_YEARS = 120


def parse_birth_date(text: str, today: date) -> date | None:
    """A date of birth from the caller's words, or None if it isn't clear.

    Accepts "15 August 1990", "August 15th, 1990", "15th of Aug 1990", "15/08/1990" (day first,
    as Indians write it) and "1990-08-15". Needs a four-digit year: "15 August 90" is ambiguous
    and returns None, so the agent asks again instead of guessing a century.
    """
    t = text.lower()
    day = month = year = None
    if m := _DOB_ISO.search(t):
        year, month, day = (int(g) for g in m.groups())
    elif m := _DOB_NUMERIC.search(t):
        day, month, year = (int(g) for g in m.groups())
    else:
        words = re.findall(r"[a-z]+|\d+", t)
        months = [_MONTH_WORDS[w] for w in words if w in _MONTH_WORDS]
        years = [int(w) for w in words if w.isdigit() and len(w) == 4]
        days = [int(w) for w in words if w.isdigit() and len(w) <= 2]
        if len(months) == 1 and len(years) == 1 and len(days) == 1:
            day, month, year = days[0], months[0], years[0]
    if day is None:
        return None
    try:
        born = date(year, month, day)
    except ValueError:
        return None
    if born > today or born.year < today.year - MAX_AGE_YEARS:
        return None
    return born
