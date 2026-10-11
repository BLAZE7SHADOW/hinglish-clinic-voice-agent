"""Read-backs written by code, spoken word for word (LEARNING #22).

The sentence the caller says yes to is built here from the database row: doctor, day, time and
patient name, nothing from the LLM. Code speaks it (`session.say`), and agent/confirm.py accepts
a yes only if the message right before it is this exact sentence, heard in full. So a yes is
always a yes to what will actually be written.

Times and dates are written as words, because the text goes straight to TTS (prompts.py speech
style): "five thirty in the evening", "shaam saade paanch baje".
"""

import re
from datetime import datetime

from agent.dates import CLINIC_TZ

_EN_UNITS = [
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
]
_EN_TENS = {20: "twenty", 30: "thirty", 40: "forty", 50: "fifty"}
_EN_ORDINALS = [
    "first",
    "second",
    "third",
    "fourth",
    "fifth",
    "sixth",
    "seventh",
    "eighth",
    "ninth",
    "tenth",
    "eleventh",
    "twelfth",
    "thirteenth",
    "fourteenth",
    "fifteenth",
    "sixteenth",
    "seventeenth",
    "eighteenth",
    "nineteenth",
    "twentieth",
]
_HI_NUMBERS = [
    "ek",
    "do",
    "teen",
    "char",
    "paanch",
    "chhe",
    "saat",
    "aath",
    "nau",
    "das",
    "gyarah",
    "barah",
    "terah",
    "chaudah",
    "pandrah",
    "solah",
    "satrah",
    "atharah",
    "unnees",
    "bees",
    "ikkees",
    "baees",
    "teyees",
    "chaubees",
    "pachchees",
    "chhabbees",
    "sattaees",
    "atthaees",
    "untees",
    "tees",
    "iktees",
]


def _en_number(n: int) -> str:
    if n < 20:
        return _EN_UNITS[n]
    tens, unit = divmod(n, 10)
    return _EN_TENS[tens * 10] + (f"-{_EN_UNITS[unit]}" if unit else "")


def _en_ordinal(n: int) -> str:
    if n <= 20:
        return _EN_ORDINALS[n - 1]
    tens, unit = divmod(n, 10)
    if not unit:
        return _EN_TENS[tens * 10][:-1] + "ieth"  # thirtieth
    return f"{_EN_TENS[tens * 10]}-{_EN_ORDINALS[unit - 1]}"


def _part_of_day(hour: int) -> tuple[str, str]:
    """(English, Hinglish) part of the day, the same boundaries as dates.PARTS_OF_DAY."""
    if hour < 12:
        return "in the morning", "subah"
    if hour < 16:
        return "in the afternoon", "dopahar"
    if hour < 20:
        return "in the evening", "shaam"
    return "at night", "raat"


def spoken_time(dt: datetime, language: str) -> str:
    t = dt.astimezone(CLINIC_TZ)
    h12 = t.hour % 12 or 12
    en_part, hi_part = _part_of_day(t.hour)
    if language == "hinglish":
        nxt = h12 % 12 + 1
        h, n = _HI_NUMBERS[h12 - 1], _HI_NUMBERS[nxt - 1]
        if t.minute == 0:
            clock = f"{h} baje"
        elif t.minute == 30:
            clock = {1: "dedh baje", 2: "dhaai baje"}.get(h12, f"saade {h} baje")
        elif t.minute == 15:
            clock = f"sawa {h} baje"
        elif t.minute == 45:
            clock = f"paune {n} baje"
        else:
            clock = f"{h} bajkar {t.minute} minute"
        return f"{hi_part} {clock}"
    minutes = "" if t.minute == 0 else f" {_en_number(t.minute)}"
    if 0 < t.minute < 10:
        minutes = f" oh {_en_number(t.minute)}"
    return f"{_en_number(h12)}{minutes} {en_part}"


def spoken_day(dt: datetime, language: str) -> str:
    t = dt.astimezone(CLINIC_TZ)
    if language == "hinglish":
        return f"{t.strftime('%A')}, {_HI_NUMBERS[t.day - 1]} {t.strftime('%B')}"
    return f"{t.strftime('%A')}, the {_en_ordinal(t.day)} of {t.strftime('%B')}"


def spoken_doctor(name: str) -> str:
    return re.sub(r"^Dr\.?\s+", "Doctor ", name)


def booking(doctor: str, starts_at: datetime, patient: str | None, language: str) -> str:
    day, time = spoken_day(starts_at, language), spoken_time(starts_at, language)
    doctor = spoken_doctor(doctor)
    if language == "hinglish":
        who = f", {patient} ke liye" if patient else ""
        return f"Confirm kar loon: {doctor}, {day}, {time}{who}. Book kar doon?"
    who = f", for {patient}" if patient else ""
    return f"Just to confirm: {doctor}, on {day}, at {time}{who}. Shall I book it?"


def reschedule(
    doctor: str, old_start: datetime, new_doctor: str, new_start: datetime, language: str
) -> str:
    at = "" if language == "hinglish" else "at "
    old = f"{spoken_day(old_start, language)}, {at}{spoken_time(old_start, language)}"
    new = f"{spoken_day(new_start, language)}, {at}{spoken_time(new_start, language)}"
    doctor, new_doctor = spoken_doctor(doctor), spoken_doctor(new_doctor)
    if language == "hinglish":
        with_new = "" if new_doctor == doctor else f", {new_doctor} ke saath"
        return (
            f"Confirm kar loon: {doctor} ke saath aapka appointment {old} se hata kar "
            f"{new}{with_new} kar doon?"
        )
    with_new = "" if new_doctor == doctor else f", with {new_doctor}"
    return (
        f"Just to confirm: I'll move your appointment with {doctor} from {old}, to "
        f"{new}{with_new}. Shall I go ahead?"
    )


def cancel(doctor: str, starts_at: datetime, language: str) -> str:
    day, time = spoken_day(starts_at, language), spoken_time(starts_at, language)
    doctor = spoken_doctor(doctor)
    if language == "hinglish":
        return (
            f"Confirm kar loon: {doctor} ke saath {day}, {time} wala appointment cancel kar doon?"
        )
    return (
        f"Just to confirm: you want to cancel your appointment with {doctor} on {day}, at {time}?"
    )
