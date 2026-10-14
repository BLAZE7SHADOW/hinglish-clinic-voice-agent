from datetime import date, datetime, timedelta

import pytest

from agent.dates import CLINIC_TZ, now, parse_birth_date
from agent.identity import apply_spelling, normalize_name, parse_phone, parse_spelled_name


@pytest.mark.parametrize(
    "spoken",
    [
        "9876543210",
        "+91 98765 43210",
        "098765 43210",
        "91-9876543210",
        "nine eight seven six five, four three two one zero",
        "nine eight seven six five four three two one oh",
        "nau aath saat chhe paanch chaar teen do ek shunya",
        "98765 four three two one zero",
    ],
)
def test_phone_numbers_read_out_in_parts(spoken):
    assert parse_phone(spoken) == "+919876543210"


def test_double_and_triple():
    assert parse_phone("nine double eight seven triple six one two three") == "+919887666123"


@pytest.mark.parametrize("spoken", ["", "12345", "1234567890", "98765432100", "my number"])
def test_not_an_indian_mobile(spoken):
    assert parse_phone(spoken) is None


def test_names_compare_exactly_after_normalizing():
    assert normalize_name("Mr. Rohan  Joshi ji") == "rohan joshi"
    assert normalize_name("rohan joshi") != normalize_name("Rohan Joshie")  # no fuzzy matching


TODAY = date(2026, 10, 12)


@pytest.mark.parametrize(
    "spoken",
    [
        "15 August 1990",
        "August 15th, 1990",
        "15th of Aug 1990",
        "15/08/1990",
        "1990-08-15",
        "born on 15 aug 1990",
    ],
)
def test_birth_dates(spoken):
    assert parse_birth_date(spoken, TODAY) == date(1990, 8, 15)


@pytest.mark.parametrize(
    ("spoken", "born"),
    [
        ("Third March nineteen eighty eight", date(1988, 3, 3)),
        ("fourth September nineteen seventy-four", date(1974, 9, 4)),
        ("ninth July nineteen sixty-two", date(1962, 7, 9)),
        ("the twenty-first of June nineteen ninety", date(1990, 6, 21)),
        ("thirty first December two thousand and five", date(2005, 12, 31)),
        ("first January nineteen oh nine", date(1909, 1, 1)),
        ("second February two thousand", date(2000, 2, 2)),
        ("twelve August nineteen ninety nine", date(1999, 8, 12)),
    ],
)
def test_spoken_birth_dates(spoken, born):
    """STT writes spoken dates as words (week 2 voice test: "Third March nineteen eighty eight")."""
    assert parse_birth_date(spoken, TODAY) == born


@pytest.mark.parametrize(
    "spoken",
    ["15 August 90", "31/02/1990", "5 March 2030", "15 August 1890", "fifteenth August", ""],
)
def test_unclear_or_impossible_birth_dates_are_refused(spoken):
    assert parse_birth_date(spoken, TODAY) is None


def test_fake_clock_only_in_eval_mode(monkeypatch):
    monkeypatch.setenv("AGENT_FAKE_NOW", "2026-10-12T10:00:00+05:30")
    monkeypatch.delenv("EVAL_MODE", raising=False)
    assert abs(now() - datetime.now(CLINIC_TZ)) < timedelta(minutes=1)  # ignored: real time
    monkeypatch.setenv("EVAL_MODE", "1")
    assert now().isoformat() == "2026-10-12T10:00:00+05:30"


@pytest.mark.parametrize(
    ("said", "spelled"),
    [
        ("K a b I r, Kavir.", "Kabir"),
        ("k-a-b-i-r", "Kabir"),
        ("K. A. B. I. R.", "Kabir"),
        ("kilo alpha bravo india romeo", "Kabir"),
        ("S for sun, E, T, H, I", "Sethi"),
        ("No. My name is only Kavita, k a b I r. Kavir.", "Kabir"),
        ("B I r. B I r. B for ball.", "Bir"),  # a partial spelling, fixed by apply_spelling
        ("I am a doctor", None),
        ("My name is Kabir", None),
    ],
)
def test_spelled_names(said, spelled):
    assert parse_spelled_name(said) == spelled


@pytest.mark.parametrize(
    ("name", "spelled", "result"),
    [
        ("Kavir", "Kabir", "Kabir"),
        ("Kavir", "Bir", "Kabir"),  # only the end was wrong
        ("Kavir Sethi", "Kabir", "Kabir Sethi"),
        ("Kabir Seti", "Sethi", "Kabir Sethi"),
        ("Kavita Sethi", "Kabir", "Kabir Sethi"),
        ("Kavita Kabir", "Kabir", "Kabir"),  # R2: one name heard twice, then spelled
        ("", "Kabir", "Kabir"),
        ("Kabir Sethi", None, "Kabir Sethi"),
    ],
)
def test_a_spelling_replaces_the_word_it_spells(name, spelled, result):
    assert apply_spelling(name, spelled) == result
