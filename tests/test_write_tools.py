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


@pytest.fixture(autouse=True)
def new_patient(monkeypatch):
    """By default nobody is on file with the caller's phone and date of birth."""
    found: list[dict] = []

    async def lookup(phone, born):
        return found

    monkeypatch.setattr(bookings, "_patients_with", lookup)
    return found


class Session(SimpleNamespace):
    """The parts of AgentSession the tools use. `said` records what code spoke."""

    def say(self, text):
        self.said.append(text)


def ctx(state: CallState, *turns: tuple[str, str], agent=None):
    history = llm.ChatContext(items=[llm.ChatMessage(role=r, content=[t]) for r, t in turns])
    return SimpleNamespace(
        userdata=state, session=Session(history=history, current_agent=agent, said=[])
    )


class ReachedTheWrite(Exception):
    pass


@pytest.fixture
def write_allowed(monkeypatch):
    """Let a call through to the write and stop it there: proof the gate opened."""

    def reached(*a, **kw):
        raise ReachedTheWrite

    monkeypatch.setattr(db, "transaction", reached)

    async def clinic(*a, **kw):
        return [{"id": 1}]

    monkeypatch.setattr(db, "read", clinic)


def out(raw: str) -> dict:
    return json.loads(raw)


def held() -> Slot:
    return Slot(doctor_id=1, doctor_name="Dr. Anil Sharma", starts_at=FIVE_PM, hold_id=42)


DETAILS = {"patient_name": "Kabir Sethi", "phone": "98111 22333", "birth_date": "3 March 1988"}


async def test_only_offered_times_can_be_held():
    state = CallState(call_id="AJ_t", mode=Mode.BOOKING)
    assert out(await tools.hold_slot(ctx(state), slot="5 PM"))["error"] == "slot_not_offered"


def test_offered_labels_match_on_format_only():
    assert bookings._label_key("5:00 PM") == bookings._label_key("5 p.m.")
    assert bookings._label_key("5:30 PM") != bookings._label_key("5 PM")


async def test_confirm_needs_a_hold():
    state = CallState(call_id="AJ_t", mode=Mode.BOOKING)
    assert out(await tools.confirm_booking(ctx(state)))["error"] == "no_slot_held"


async def test_first_confirm_speaks_the_code_read_back_and_books_nothing():
    state = CallState(call_id="AJ_t", mode=Mode.BOOKING, slot=held())
    c = ctx(state, ("user", "Five PM. I'm Kabir Sethi, 98111 22333, born 3 March 1988."))
    result = await tools.confirm_booking(c, **DETAILS)

    assert isinstance(result, llm.ToolResult) and not result.reply_required  # no LLM reply
    (text,) = c.session.said
    assert text == state.pending.text
    assert "Doctor Anil Sharma" in text and "five in the evening" in text and "Kabir Sethi" in text
    assert "98111" not in text and "1988" not in text  # never read out phone or birth date
    assert state.awaiting_yes


async def test_yes_to_the_exact_read_back_reaches_the_write(write_allowed):
    state = CallState(call_id="AJ_t", mode=Mode.BOOKING, slot=held())
    first = ctx(state, ("user", "Five PM, Kabir Sethi..."))
    await tools.confirm_booking(first, **DETAILS)
    spoken = first.session.said[0]

    second = ctx(state, ("user", "Five PM, Kabir Sethi..."), ("assistant", spoken), ("user", "Yes"))
    with pytest.raises(ReachedTheWrite):
        await tools.confirm_booking(second, **DETAILS)
    assert state.pending.confirmed


async def test_unclear_answer_writes_nothing_and_does_not_reread():
    state = CallState(call_id="AJ_t", mode=Mode.BOOKING, slot=held())
    first = ctx(state, ("user", "Five PM"))
    await tools.confirm_booking(first, **DETAILS)
    second = ctx(
        state, ("user", "Five PM"), ("assistant", first.session.said[0]), ("user", "Hmm, wait")
    )
    assert out(await tools.confirm_booking(second, **DETAILS))["error"] == "not_confirmed"
    assert second.session.said == []


async def test_a_paraphrased_read_back_is_replaced_by_the_code_one():
    state = CallState(call_id="AJ_t", mode=Mode.BOOKING, slot=held())
    await tools.confirm_booking(ctx(state, ("user", "Five PM")), **DETAILS)
    llm_version = ("assistant", "So that's Wednesday at six, right?")
    second = ctx(state, ("user", "Five PM"), llm_version, ("user", "Yes"))
    result = await tools.confirm_booking(second, **DETAILS)
    assert isinstance(result, llm.ToolResult)  # spoken again, not booked
    assert second.session.said == [state.pending.text] and state.pending.times_spoken == 2


async def test_rereading_stops_after_three_tries():
    state = CallState(call_id="AJ_t", mode=Mode.BOOKING, slot=held())
    turns = [("user", "Five PM")]
    await tools.confirm_booking(ctx(state, *turns), **DETAILS)
    for _ in range(2):  # each time the caller answers something that isn't the read-back
        turns += [("assistant", "Sorry, what?"), ("user", "Yes")]
        await tools.confirm_booking(ctx(state, *turns), **DETAILS)
    turns += [("assistant", "Sorry, what?"), ("user", "Yes")]
    last = out(await tools.confirm_booking(ctx(state, *turns), **DETAILS))
    assert last["error"] == "not_confirmed" and "callback" in last["say"]


async def test_changed_details_need_a_new_yes(write_allowed):
    state = CallState(call_id="AJ_t", mode=Mode.BOOKING, slot=held())
    first = ctx(state, ("user", "Five PM"))
    await tools.confirm_booking(first, **DETAILS)
    yes = ctx(state, ("user", "Five PM"), ("assistant", first.session.said[0]), ("user", "Yes"))
    with pytest.raises(ReachedTheWrite):
        await tools.confirm_booking(yes, **DETAILS)
    # The caller corrects the name: the old yes doesn't cover the new booking.
    fixed = DETAILS | {"patient_name": "Kabir Sethia"}
    again = ctx(state, ("user", "Sorry, it's Sethia"))
    result = await tools.confirm_booking(again, **fixed)
    assert isinstance(result, llm.ToolResult) and "Kabir Sethia" in again.session.said[0]


async def test_missing_details_are_asked_for_before_any_read_back():
    state = CallState(call_id="AJ_t", mode=Mode.BOOKING, slot=held())
    c = ctx(state, ("user", "Five PM"))
    result = out(await tools.confirm_booking(c, patient_name="Rohan Joshi", phone="12"))
    assert result["error"] == "need_patient_details"
    assert set(result["missing"]) == {"phone", "birth_date"}
    assert c.session.said == [] and state.pending is None


async def test_cancel_and_reschedule_need_verification_whatever_the_llm_says():
    state = CallState(call_id="AJ_t", mode=Mode.CANCELLING, listed_bookings={1: 7})
    c = ctx(state, ("user", "Cancel it"), ("assistant", "Cancel Monday five PM?"), ("user", "Yes"))
    assert out(await tools.cancel_booking(c, ref=1))["error"] == "not_allowed"
    assert c.session.said == []  # not even the read-back: nothing about the booking is said

    state = CallState(call_id="AJ_t", mode=Mode.RESCHEDULING, slot=held(), listed_bookings={1: 7})
    c = ctx(state, ("user", "Five PM"), ("user", "Yes"))
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


async def test_cancel_reads_back_the_stored_booking_first(monkeypatch):
    async def booking_row(*a, **kw):
        return [{"full_name": "Dr. Priya Mehta", "starts_at": FIVE_PM}]

    monkeypatch.setattr(db, "read", booking_row)
    state = CallState(
        call_id="AJ_t",
        mode=Mode.CANCELLING,
        verification=Verification.VERIFIED,
        patient_id=3,
        listed_bookings={1: 7},
    )
    c = ctx(state, ("user", "Yes, cancel it"))  # a yes before any read-back doesn't count
    result = await tools.cancel_booking(c, ref=1)
    assert isinstance(result, llm.ToolResult)
    assert "cancel your appointment with Doctor Priya Mehta" in c.session.said[0]


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


async def test_awaiting_yes_tracks_a_pending_read_back():
    state = CallState(call_id="AJ_t", mode=Mode.BOOKING, slot=held())
    assert not state.awaiting_yes  # held, but nothing read back yet
    await tools.confirm_booking(ctx(state, ("user", "Five PM")), **DETAILS)
    assert state.awaiting_yes
    state.pending.confirmed = True
    assert not state.awaiting_yes


async def test_a_patient_on_file_is_read_back_by_the_name_on_file(monkeypatch, new_patient):
    """Phone and date of birth find the patient; no name is recognised from speech (#25)."""
    new_patient.append({"id": 7, "full_name": "Kabir"})

    async def name_on_file(query, *args):
        return [{"full_name": "Kabir"}]

    monkeypatch.setattr(db, "read", name_on_file)
    state = CallState(call_id="AJ_t", mode=Mode.BOOKING, slot=held())
    c = ctx(state, ("user", "Nine eight one one one, two two three three three"))
    await tools.confirm_booking(c, phone="98111 22333", birth_date="3 March 1988")
    assert "for Kabir." in c.session.said[0]
    assert state.verification == Verification.VERIFIED and state.patient_id == 7


async def test_a_new_patient_is_asked_for_a_name_before_any_read_back():
    state = CallState(call_id="AJ_t", mode=Mode.BOOKING, slot=held())
    c = ctx(state, ("user", "Third March nineteen eighty eight"))
    result = out(await tools.confirm_booking(c, phone="98111 22333", birth_date="3 March 1988"))
    assert result["error"] == "new_patient"
    assert c.session.said == [] and state.verification == Verification.UNVERIFIED


async def test_a_number_split_across_turns_is_read_from_the_caller_not_the_llm():
    """W2: STT split the number and the LLM sent "two twothree"; code reads the turns."""
    state = CallState(call_id="AJ_t", mode=Mode.BOOKING, slot=held())
    c = ctx(
        state,
        ("user", "Nine eight one one one two two"),
        ("user", "three three three."),
        ("user", "Third March nineteen eighty eight."),
    )
    garbled = "Nine eight one one one two twothree three three"
    result = await tools.confirm_booking(
        c, phone=garbled, birth_date="Third March nineteen eighty eight", patient_name="Kabir"
    )
    assert isinstance(result, llm.ToolResult)  # read back, so the number parsed
    assert "+919811122333" in state.pending.key


async def test_a_spelling_in_the_callers_words_fixes_the_name():
    """W1/W2: STT heard "Kavir"; the caller spelled it, and code applies the spelling."""
    state = CallState(call_id="AJ_t", mode=Mode.BOOKING, slot=held())
    c = ctx(state, ("user", "It's Kavir, K a b I r."), ("user", "98111 22333, 3 March 1988"))
    await tools.confirm_booking(
        c, phone="98111 22333", birth_date="3 March 1988", patient_name="Kavir Sethi"
    )
    assert "for Kabir Sethi." in c.session.said[0]
