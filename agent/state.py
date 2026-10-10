"""Typed call state: what the code knows about the call, independent of what the LLM says.

The LLM proposes actions; code checks this state before any action runs (design §7.2). Day 5
defines the state and its rules. Week 2 wires it to modes and write tools (create, reschedule,
cancel), whose guards call `check()`.
"""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class Mode(StrEnum):
    GREETING = "greeting"
    FAQ = "faq"
    BOOKING = "booking"
    RESCHEDULING = "rescheduling"
    CANCELLING = "cancelling"
    HANDOFF = "handoff"


class Verification(StrEnum):
    UNVERIFIED = "unverified"
    VERIFIED = "verified"  # phone number plus name or date of birth matched a patient (§3)
    FAILED = "failed"  # too many wrong attempts: hand off, never keep guessing


class Action(StrEnum):
    READ_SLOTS = "read_slots"
    READ_CLINIC_INFO = "read_clinic_info"
    READ_OWN_BOOKING = "read_own_booking"
    CREATE_BOOKING = "create_booking"
    RESCHEDULE_BOOKING = "reschedule_booking"
    CANCEL_BOOKING = "cancel_booking"
    REQUEST_CALLBACK = "request_callback"


class Slot(BaseModel):
    doctor_id: int
    doctor_name: str
    starts_at: datetime
    read_back_confirmed: bool = False  # caller said a clear "yes" to doctor, date and time


class CallState(BaseModel):
    call_id: str
    mode: Mode = Mode.GREETING
    verification: Verification = Verification.UNVERIFIED
    verification_attempts: int = 0
    patient_id: int | None = None  # set only by successful verification
    slot: Slot | None = None  # the slot under discussion
    booking_id: int | None = None  # the booking being changed (reschedule / cancel)
    misunderstandings: int = 0  # repeated "sorry?" leads to handoff (design §7.4)
    history: list[str] = Field(default_factory=list)  # mode changes, for the event log


MAX_VERIFICATION_ATTEMPTS = 2
MAX_MISUNDERSTANDINGS = 2


class Refused(Exception):
    """An action the code won't allow in the current state, whatever the LLM asked for."""


# Which actions each mode allows: a per-mode tool allowlist (design §7.1, §7.4).
ALLOWED: dict[Mode, set[Action]] = {
    Mode.GREETING: {Action.READ_CLINIC_INFO, Action.READ_SLOTS, Action.REQUEST_CALLBACK},
    Mode.FAQ: {Action.READ_CLINIC_INFO, Action.READ_SLOTS, Action.REQUEST_CALLBACK},
    Mode.BOOKING: {Action.READ_SLOTS, Action.CREATE_BOOKING, Action.REQUEST_CALLBACK},
    Mode.RESCHEDULING: {
        Action.READ_OWN_BOOKING,
        Action.READ_SLOTS,
        Action.RESCHEDULE_BOOKING,
        Action.REQUEST_CALLBACK,
    },
    Mode.CANCELLING: {Action.READ_OWN_BOOKING, Action.CANCEL_BOOKING, Action.REQUEST_CALLBACK},
    Mode.HANDOFF: {Action.REQUEST_CALLBACK},
}
NEEDS_VERIFICATION = {Action.READ_OWN_BOOKING, Action.RESCHEDULE_BOOKING, Action.CANCEL_BOOKING}
NEEDS_READ_BACK = {Action.CREATE_BOOKING, Action.RESCHEDULE_BOOKING}


def check(state: CallState, action: Action) -> None:
    """Raise Refused unless `action` is allowed right now. Called by every tool before it acts."""
    if action not in ALLOWED[state.mode]:
        raise Refused(f"{action} is not allowed in {state.mode} mode")
    if action in NEEDS_VERIFICATION and state.verification != Verification.VERIFIED:
        raise Refused(f"{action} needs a verified caller")
    if action in NEEDS_READ_BACK and not (state.slot and state.slot.read_back_confirmed):
        raise Refused(f"{action} needs the caller to confirm doctor, date and time first")


def switch_mode(state: CallState, mode: Mode) -> None:
    """Change mode. A cheap state change, not another LLM round trip (design §7.1)."""
    if state.mode == Mode.HANDOFF and mode != Mode.HANDOFF:
        raise Refused("a call in handoff stays in handoff")
    if mode != state.mode:
        state.history.append(f"{state.mode}->{mode}")
        state.mode = mode
        state.slot = None if mode in (Mode.FAQ, Mode.HANDOFF) else state.slot


def record_verification(state: CallState, *, matched_patient_id: int | None) -> None:
    if matched_patient_id is not None:
        state.verification, state.patient_id = Verification.VERIFIED, matched_patient_id
        return
    state.verification_attempts += 1
    if state.verification_attempts >= MAX_VERIFICATION_ATTEMPTS:
        state.verification = Verification.FAILED
        switch_mode(state, Mode.HANDOFF)


def record_misunderstanding(state: CallState) -> None:
    state.misunderstandings += 1
    if state.misunderstandings >= MAX_MISUNDERSTANDINGS:
        switch_mode(state, Mode.HANDOFF)
