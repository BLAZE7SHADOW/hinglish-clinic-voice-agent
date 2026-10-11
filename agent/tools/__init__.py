"""The agent's tools (design §7.3). Each checks the typed call state before it acts."""

from agent.tools.bookings import (
    cancel_booking,
    confirm_booking,
    hold_slot,
    release_call_holds,
    reschedule_booking,
)
from agent.tools.callback import request_callback
from agent.tools.patients import my_bookings, verify_caller
from agent.tools.read import clinic_info, find_slots, load_doctors

__all__ = [
    "cancel_booking",
    "clinic_info",
    "confirm_booking",
    "find_slots",
    "hold_slot",
    "load_doctors",
    "my_bookings",
    "release_call_holds",
    "request_callback",
    "reschedule_booking",
    "verify_caller",
]
