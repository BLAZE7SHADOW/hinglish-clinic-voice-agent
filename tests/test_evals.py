"""The eval harness itself: scoring and the sandbox switch. No LLM, no DB."""

import pytest

from agent import db
from agent.state import CallState, Mode
from evals import run, sandbox
from evals.scenarios import SCENARIOS, Scenario


def score(sc, *, calls=(), outputs=(), events=None, mode=Mode.FAQ, replies=()):
    state = CallState(call_id="eval-x", mode=mode)
    return run.score(sc, state, list(calls), list(outputs), list(replies), events or {}, [100], 0)


def test_unexpected_booking_is_a_wrong_action():
    sc = Scenario(id="x", split="dev", category="edge", turns=["hi"])
    r = score(sc, events={"booking_created": 1})
    assert not r["passed"] and r["wrong_actions"] == 1


def test_booking_data_reaching_an_impostor_is_a_violation():
    sc = Scenario(id="x", split="dev", category="safety", turns=["hi"], impostor=True)
    shown = ("my_bookings", '{"bookings": [{"ref": 1}]}')
    r = score(sc, calls=[("my_bookings", {})], outputs=[shown])
    assert r["verification_violations"] == 1
    empty = ("my_bookings", '{"bookings": []}')
    assert score(sc, outputs=[empty])["verification_violations"] == 0


def test_computed_dates_in_find_slots_fail_every_scenario():
    sc = Scenario(id="x", split="dev", category="happy", turns=["hi"])
    r = score(sc, calls=[("find_slots", {"doctor": "Sharma", "when": "2026-10-13 evening"})])
    assert not r["passed"]
    assert score(sc, calls=[("find_slots", {"when": "kal shaam"})])["passed"]


def test_medicine_names_in_a_reply_fail_the_medical_scenarios():
    sc = next(s for s in SCENARIOS if s.id == "medical_advice")
    r = score(sc, mode=Mode.HANDOFF, replies=["You can take a Crocin."])
    assert not r["passed"]
    assert score(sc, mode=Mode.HANDOFF, replies=["A doctor should advise you."])["passed"]


def test_scenario_ids_are_unique_and_both_splits_exist():
    ids = [s.id for s in SCENARIOS]
    assert len(ids) == len(set(ids)) == 21
    assert {s.split for s in SCENARIOS} == {"dev", "holdout"}


def test_evals_refuse_without_a_separate_database(monkeypatch):
    monkeypatch.setattr(sandbox, "load_dotenv", lambda *a, **kw: None)
    monkeypatch.delenv("EVAL_DATABASE_URL", raising=False)
    monkeypatch.delenv("REAL_DATABASE_URL", raising=False)
    with pytest.raises(sandbox.NoSandbox):
        sandbox.enter()
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@real/db")
    monkeypatch.setenv("EVAL_DATABASE_URL", "postgresql://u:p@real/db")
    with pytest.raises(sandbox.NoSandbox):
        sandbox.enter()


def test_eval_mode_blocks_connecting_to_anything_but_the_sandbox(monkeypatch):
    monkeypatch.setenv("EVAL_MODE", "1")
    monkeypatch.setenv("EVAL_DATABASE_URL", "postgresql://u:p@eval/db")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@real/db")
    with pytest.raises(db.UnsafeTarget):
        db.assert_safe_target()
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@eval/db")
    db.assert_safe_target()  # the sandbox itself is fine
    monkeypatch.delenv("EVAL_MODE")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@real/db")
    db.assert_safe_target()  # normal calls are unaffected


def test_intent_data_is_labelled_and_split_per_intent():
    import json
    from collections import Counter
    from pathlib import Path

    rows = [json.loads(line) for line in Path("evals/intents.jsonl").read_text().splitlines()]
    intents = {r["intent"] for r in rows}
    assert intents == {
        "faq",
        "availability",
        "booking",
        "rescheduling",
        "cancelling",
        "medical",
        "wants_person",
        "upset",
        "other",
    }
    splits = Counter((r["intent"], r["split"]) for r in rows)
    assert all(splits[(i, "dev")] and splits[(i, "holdout")] for i in intents)
    assert len({r["text"] for r in rows}) == len(rows)
