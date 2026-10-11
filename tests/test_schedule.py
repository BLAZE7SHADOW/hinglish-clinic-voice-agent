from datetime import date, datetime, time

from agent.dates import CLINIC_TZ
from agent.schedule import (
    Doctor,
    WorkingHours,
    fmt_fee,
    fmt_slot,
    free_slots,
    fuzzy_match_doctor,
    match_doctors,
)

SAT = date(2026, 10, 10)
SUN = date(2026, 10, 11)


def at(d, h, m=0):
    return datetime.combine(d, time(h, m), CLINIC_TZ)


HOURS = [WorkingHours(6, time(17), time(18)), WorkingHours(6, time(10), time(11))]  # Sunday


def test_slots_follow_hours_and_skip_taken():
    slots = free_slots(
        HOURS, set(), {at(SUN, 17, 15)}, 15, at(SUN, 16), at(SUN, 20), now=at(SAT, 11)
    )
    assert slots == [at(SUN, 17), at(SUN, 17, 30), at(SUN, 17, 45)]


def test_last_slot_must_fit_before_closing():
    slots = free_slots(HOURS, set(), set(), 45, at(SUN, 17), at(SUN, 18), now=at(SAT, 11))
    assert slots == [at(SUN, 17)]  # 17:45 + 45 min would end after 18:00


def test_day_off_and_lead_time():
    assert free_slots(HOURS, {SUN}, set(), 15, at(SUN, 0), at(SUN, 23), now=at(SAT, 11)) == []
    slots = free_slots(HOURS, set(), set(), 15, at(SUN, 0), at(SUN, 23), now=at(SUN, 10, 10))
    assert slots[0] == at(SUN, 10, 45)  # 10:15 and 10:30 are inside the 30-minute lead


DOCTORS = [
    Doctor(1, "Dr. Anil Sharma", "General physician", 50000, 15),
    Doctor(2, "Dr. Priya Mehta", "Dentist", 70000, 30),
    Doctor(3, "Dr. Kavita Iyer", "Physiotherapist", 60000, 45),
]


def names(q):
    return [d.full_name for d in match_doctors(q, DOCTORS)]


def test_match_by_name_variants():
    assert names("Dr. Sharma") == ["Dr. Anil Sharma"]
    assert names("sharma ji") == ["Dr. Anil Sharma"]
    assert names("Priya madam") == ["Dr. Priya Mehta"]
    assert names("doctor") == []


def test_match_by_specialty_in_english_and_hindi():
    assert names("dentist") == ["Dr. Priya Mehta"]
    assert names("daant ke doctor") == ["Dr. Priya Mehta"]
    assert names("physio") == ["Dr. Kavita Iyer"]
    assert names("cardiologist") == []


def test_formatting():
    assert fmt_slot(at(SUN, 17, 30)) == "5:30 PM" and fmt_slot(at(SUN, 10)) == "10 AM"
    assert fmt_fee(50000) == "500 rupees" and fmt_fee(50050) == "500 rupees 50 paise"


def fuzzy(q):
    d = fuzzy_match_doctor(q, DOCTORS)
    return d and d.full_name


def test_fuzzy_match_catches_stt_mishearings():
    # Real mishearings from the 2026-10-11 calls (LEARNING.md #16), plus Indian spellings.
    assert fuzzy("Doctor, Harmer") == "Dr. Anil Sharma"
    assert fuzzy("doctor Sharpa") == "Dr. Anil Sharma"
    assert fuzzy("Sarma") == "Dr. Anil Sharma"
    assert fuzzy("Metha") == "Dr. Priya Mehta"
    assert fuzzy("Kavitha") == "Dr. Kavita Iyer"


def test_fuzzy_match_refuses_to_guess_unrelated_words():
    for q in ["doctor", "tomorrow", "general", "Gupta", "Mr Verma", ""]:
        assert fuzzy(q) is None, q
