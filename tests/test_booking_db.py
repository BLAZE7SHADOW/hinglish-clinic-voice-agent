"""Booking writes against a real Postgres: the eval sandbox only. Skipped without one.

These check what only the database can: the unique index, idempotent repeats, expired holds.
"""

import json
import os
from types import SimpleNamespace

import pytest
from dotenv import load_dotenv
from livekit.agents import llm

from agent import db, tools
from agent.state import CallState, Mode, Verification
from evals import sandbox

load_dotenv(".env")  # EVAL_DATABASE_URL lives there; the skip check runs before any fixture
pytestmark = pytest.mark.skipif(
    not os.environ.get("EVAL_DATABASE_URL"), reason="needs EVAL_DATABASE_URL (eval sandbox)"
)


@pytest.fixture(scope="module", autouse=True)
def sandboxed():
    sandbox.enter()


@pytest.fixture(autouse=True)
async def fresh_pool():
    # pytest-asyncio gives each test its own event loop; a pool can't cross loops.
    db._pool, db._pool_lock = None, None
    await sandbox.prepare()
    yield
    if db._pool is not None:
        await db._pool.close()
        db._pool = None


class Session(SimpleNamespace):
    def say(self, text):
        self.said.append(text)


def ctx(state, *turns):
    history = llm.ChatContext(items=[llm.ChatMessage(role=r, content=[t]) for r, t in turns])
    return SimpleNamespace(
        userdata=state, session=Session(history=history, current_agent=None, said=[])
    )


async def offered_slot(state) -> str:
    r = json.loads(await tools.find_slots(ctx(state), doctor="Dr. Sharma", when="kal shaam"))
    return r["free"][0]


async def test_two_calls_cannot_hold_the_same_time():
    a = CallState(call_id="eval-a", mode=Mode.BOOKING)
    b = CallState(call_id="eval-b", mode=Mode.BOOKING)
    label = await offered_slot(a)
    await offered_slot(b)
    assert json.loads(await tools.hold_slot(ctx(a), slot=label))["held"]
    assert json.loads(await tools.hold_slot(ctx(b), slot=label))["error"] == "slot_taken"


async def test_repeated_hold_and_confirm_make_one_booking():
    s = CallState(
        call_id="eval-c", mode=Mode.BOOKING, verification=Verification.VERIFIED, patient_id=None
    )
    rows = await db.read("select id from patients order by id limit 1")
    s.patient_id = rows[0]["id"]
    label = await offered_slot(s)
    first = json.loads(await tools.hold_slot(ctx(s), slot=label))
    hold_id = s.slot.hold_id
    again = json.loads(await tools.hold_slot(ctx(s), slot=label))
    assert first["held"] and again["held"] and s.slot.hold_id == hold_id

    first = ctx(s, ("user", label))
    await tools.confirm_booking(first)  # speaks the read-back, books nothing
    turns = (("user", label), ("assistant", first.session.said[0]), ("user", "Yes"))
    slot, pending = s.slot, s.pending
    assert json.loads(await tools.confirm_booking(ctx(s, *turns)))["booked"]
    s.slot, s.pending = slot, pending  # the LLM repeats the call after a timeout
    assert json.loads(await tools.confirm_booking(ctx(s, *turns)))["booked"]
    n = await db.read("select count(*) from bookings where call_id = $1", "eval-c")
    assert n[0]["count"] == 1


async def test_an_expired_hold_frees_the_time_and_call_end_releases_holds():
    a = CallState(call_id="eval-d", mode=Mode.BOOKING)
    b = CallState(call_id="eval-e", mode=Mode.BOOKING)
    label = await offered_slot(a)
    await tools.hold_slot(ctx(a), slot=label)
    async with db.transaction() as conn:
        await conn.execute(
            "update bookings set hold_expires_at = now() - interval '1 minute' where id = $1",
            a.slot.hold_id,
        )
    await offered_slot(b)
    assert json.loads(await tools.hold_slot(ctx(b), slot=label))["held"]
    await tools.release_call_holds("eval-e")
    left = await db.read(
        "select count(*) from bookings where call_id = $1 and status = 'held'", "eval-e"
    )
    assert left[0]["count"] == 0
