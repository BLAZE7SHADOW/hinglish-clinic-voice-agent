from datetime import datetime

import pytest

from agent.dates import CLINIC_TZ, resolve

NOW = datetime(2026, 10, 10, 11, 0, tzinfo=CLINIC_TZ)  # Saturday


def at(day, hour, minute=0):
    return datetime(2026, 10, day, hour, minute, tzinfo=CLINIC_TZ)


@pytest.mark.parametrize(
    "phrase, day",
    [
        ("kal", 11),
        ("tomorrow", 11),
        ("Kal shaam", 11),
        ("parson", 12),
        ("day after tomorrow", 12),
        ("parso subah", 12),
        ("aaj", 10),
        ("today evening", 10),
        ("agle Monday", 12),
        ("next monday", 12),
        ("Monday", 12),
        ("somvaar", 12),
        ("is shukravar", 16),
        ("friday", 16),
        ("saturday", 17),  # bare weekday never means today
        ("15 tarikh", 15),
        ("15th", 15),
        ("15 October", 15),
        ("15th october", 15),
    ],
)
def test_days(phrase, day):
    w = resolve(phrase, NOW)
    assert w.day_given and w.start.date().day == day, w


@pytest.mark.parametrize(
    "phrase, start, end",
    [
        ("kal shaam", at(11, 16), at(11, 20)),
        ("kal subah", at(11, 8), at(11, 12)),
        ("parson dopahar", at(12, 12), at(12, 16)),
        ("tomorrow night", at(11, 20), at(11, 23)),
    ],
)
def test_part_of_day_window(phrase, start, end):
    w = resolve(phrase, NOW)
    assert (w.start, w.end, w.exact_time) == (start, end, None)


@pytest.mark.parametrize(
    "phrase, exact",
    [
        ("kal shaam paanch baje", at(11, 17)),
        ("kal 5 baje", at(11, 17)),  # no AM/PM -> PM in clinic hours
        ("kal das baje", at(11, 10)),
        ("tomorrow at ten", at(11, 10)),
        ("tomorrow around 10 am", at(11, 10)),
        ("tomorrow 5 pm", at(11, 17)),
        ("kal 5:30", at(11, 17, 30)),
        ("parson saade das baje", at(12, 10, 30)),
        ("kal sawa chaar baje", at(11, 16, 15)),
        ("kal paune paanch baje", at(11, 16, 45)),
        ("kal dedh baje", at(11, 13, 30)),
        ("next monday at 11", at(12, 11)),
        ("Can I come in the morning tomorrow around ten?", at(11, 10)),
    ],
)
def test_exact_times(phrase, exact):
    assert resolve(phrase, NOW).exact_time == exact


def test_ambiguities_are_recorded_for_read_back():
    w = resolve("kal paanch baje", NOW)
    assert any("kal" in a for a in w.assumptions)
    assert any("5 PM assumed" in a for a in w.assumptions)
    assert any("coming monday" in a for a in resolve("agle monday", NOW).assumptions)


def test_no_day_means_default_horizon_from_now():
    w = resolve("evening", NOW)
    assert not w.day_given
    assert w.start == at(10, 16) and w.end == at(16, 20)


def test_today_window_starts_now_not_in_the_past():
    w = resolve("aaj", datetime(2026, 10, 10, 15, 30, tzinfo=CLINIC_TZ))
    assert w.start == at(10, 15, 30)


def test_weeks():
    nxt = resolve("agle hafte", NOW)
    assert (nxt.start.date().day, nxt.end.date().day) == (12, 19)
    this = resolve("is hafte", NOW)
    assert this.start == NOW and this.end.date().day == 12


def test_english_do_is_not_a_time():
    assert resolve("what do you have tomorrow", NOW).exact_time is None


def test_description_reads_back_naturally():
    assert resolve("kal shaam", NOW).description == "Sunday 11 October, evening (4 PM to 8 PM)"
    assert resolve("kal shaam paanch baje", NOW).description == "Sunday 11 October at 5 PM"
