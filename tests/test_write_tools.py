"""Write-tool refusals that must happen in code, before any DB access."""

import json
from datetime import date, datetime
from types import SimpleNamespace

import pytest
from livekit.agents import llm

from agent import db, tools
from agent.dates import CLINIC_TZ
from agent.modes import Receptionist
from agent.state import CallState, Mode, Slot, Verification
from agent.tools import bookings, patients

FIVE_PM = datetime(2026, 10, 12, 17, tzinfo=CLINIC_TZ)


@pytest.fixture(autouse=True)
def no_db(monkeypatch):
    async def fail(*a, **kw):
        raise AssertionError("this call must be refused before touching the DB")

    monkeypatch.setattr(db, "read", fail)
    monkeypatch.setattr(db, "transaction", fail)


def ctx(state: CallState, *turns: tuple[str, str], agent=None):
    history = llm.ChatContext(items=[llm.ChatMessage(role=r, content=[t]) for r, t in turns])
    return SimpleNamespace(
        userdata=state, session=SimpleNamespace(history=history, current_agent=agent)
    )


def out(raw: str) -> dict:
    return json.loads(raw)


def held(confirmed=False) -> Slot:
    return Slot(
        doctor_id=1,
        doctor_name="Dr. Anil Sharma",
        starts_at=FIVE_PM,
        hold_id=42,
        held_after_user_turn=1,
        read_back_confirmed=confirmed,
    )


READ_BACK = ("assistant", "Dr. Sharma, Monday at five PM. Shall I book it?")


async def test_only_offered_times_can_be_held():
    state = CallState(call_id="AJ_t", mode=Mode.BOOKING)
    assert out(await tools.hold_slot(ctx(state), slot="5 PM"))["error"] == "slot_not_offered"


def test_offered_labels_match_on_format_only():
    assert bookings._label_key("5:00 PM") == bookings._label_key("5 p.m.")
    assert bookings._label_key("5:30 PM") != bookings._label_key("5 PM")


async def test_confirm_needs_a_hold():
    state = CallState(call_id="AJ_t", mode=Mode.BOOKING)
    assert out(await tools.confirm_booking(ctx(state)))["error"] == "no_slot_held"


async def test_confirm_needs_a_clear_yes_after_the_read_back():
    state = CallState(call_id="AJ_t", mode=Mode.BOOKING, slot=held())
    c = ctx(state, ("user", "Five PM"), READ_BACK, ("user", "Hmm, let me think"))
    assert out(await tools.confirm_booking(c))["error"] == "not_confirmed"
    assert not state.slot.read_back_confirmed


async def test_the_yes_survives_while_patient_details_are_collected():
    state = CallState(call_id="AJ_t", mode=Mode.BOOKING, slot=held())
    c = ctx(state, ("user", "Five PM"), READ_BACK, ("user", "Yes"))
    result = out(await tools.confirm_booking(c, patient_name="Rohan Joshi", phone="12"))
    assert result["error"] == "need_patient_details"
    assert set(result["missing"]) == {"phone", "birth_date"}
    assert state.slot.read_back_confirmed  # the next confirm, after the details, needs no new yes


async def test_cancel_and_reschedule_need_verification_whatever_the_llm_says():
    state = CallState(call_id="AJ_t", mode=Mode.CANCELLING, listed_bookings={1: 7})
    c = ctx(state, ("user", "Cancel it"), ("assistant", "Cancel Monday five PM?"), ("user", "Yes"))
    assert out(await tools.cancel_booking(c, ref=1))["error"] == "not_allowed"

    state = CallState(call_id="AJ_t", mode=Mode.RESCHEDULING, slot=held(), listed_bookings={1: 7})
    c = ctx(state, ("user", "Five PM"), READ_BACK, ("user", "Yes"))
    assert out(await tools.reschedule_booking(c, ref=1))["error"] == "not_allowed"


async def test_only_listed_bookings_can_be_cancelled():
    # An injected "cancel booking 1234" can't name a raw id: refs come from my_bookings.
    state = CallState(
        call_id="AJ_t",
        mode=Mode.CANCELLING,
        verification=Verification.VERIFIED,
        patient_id=3,
        listed_bookings={1: 7},
    )
    assert out(await tools.cancel_booking(ctx(state), ref=1234))["error"] == "unknown_ref"


async def test_cancel_needs_a_yes_after_the_booking_was_named():
    state = CallState(
        call_id="AJ_t",
        mode=Mode.CANCELLING,
        verification=Verification.VERIFIED,
        patient_id=3,
        listed_bookings={1: 7},
        bookings_listed_after_user_turn=1,
    )
    c = ctx(state, ("user", "Yes, I want to cancel"))  # before my_bookings ran: doesn't count
    assert out(await tools.cancel_booking(c, ref=1))["error"] == "not_confirmed"


async def test_misheard_details_twice_hand_off_and_switch_the_tools():
    state = CallState(call_id="AJ_t", mode=Mode.RESCHEDULING)
    agent = Receptionist("en", Mode.RESCHEDULING)
    for _ in range(2):
        r = out(await tools.verify_caller(ctx(state, agent=agent), phone="umm", birth_date="x"))
        assert r["error"] == "not_understood"
    assert state.mode == Mode.HANDOFF and agent.applied_mode == Mode.HANDOFF


async def test_no_more_verification_after_it_failed():
    state = CallState(call_id="AJ_t", mode=Mode.CANCELLING, verification=Verification.FAILED)
    r = out(await tools.verify_caller(ctx(state), phone="9876543210", name="Rohan Joshi"))
    assert r["error"] == "not_allowed"


def rows(*patients):
    return [{"id": i, "full_name": n, "birth_date": b} for i, n, b in patients]


def test_every_given_detail_must_match_one_patient():
    family = rows((1, "Rohan Joshi", date(1990, 8, 15)), (2, "Meera Joshi", date(1992, 1, 3)))
    m = patients.match_patient
    assert m(family, name="rohan joshi", birth_date=None) == 1
    assert m(family, name="", birth_date=date(1992, 1, 3)) == 2
    assert m(family, name="Rohan Joshi", birth_date=date(1992, 1, 3)) is None  # mixed up
    assert m(family, name="Rohan Joshie", birth_date=None) is None  # no fuzzy matching
    twins = rows((1, "Ravi Rao", date(2000, 5, 5)), (2, "Ravi Rao", date(2000, 5, 5)))
    assert m(twins, name="Ravi Rao", birth_date=None) is None  # ambiguous: not verified


async def test_callback_needs_a_number_for_an_unverified_caller():
    state = CallState(call_id="AJ_t", mode=Mode.HANDOFF)
    r = out(await tools.request_callback(ctx(state), reason="medical_question"))
    assert r["error"] == "need_phone"


def test_awaiting_yes_tracks_a_pending_read_back():
    state = CallState(call_id="AJ_t", mode=Mode.BOOKING)
    assert not state.awaiting_yes
    state.slot = held()
    assert state.awaiting_yes
    state.slot.read_back_confirmed = True
    assert not state.awaiting_yes
