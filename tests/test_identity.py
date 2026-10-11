from datetime import date, datetime, timedelta

import pytest

from agent.dates import CLINIC_TZ, now, parse_birth_date
from agent.identity import normalize_name, parse_phone


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
