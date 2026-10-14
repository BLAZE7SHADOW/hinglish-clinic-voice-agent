"""Text-level eval runner (design §10.1): scripted caller turns straight to the agent, no audio.

    uv run python -m evals.run --split dev            # while working
    uv run python -m evals.run --split holdout        # only to report
    uv run python -m evals.run --only book_happy --repeat 3

Runs the real agent (prompts, tools, guards, LLM) against the eval sandbox: a separate
database, re-seeded before every scenario, with a frozen clock (evals/sandbox.py). Never the
real database. Calls the configured LLM, so it costs tokens.

Scores, from state and the database, not from reply wording:
- task success: every check of the scenario passed
- wrong actions: booking events the scenario didn't expect (the metric that matters most)
- verification violations: booking data shown to, or changed by, an impostor (must be 0)
- invariants on every scenario: find_slots gets the caller's words, never a computed date
Exit code 1 if any wrong action or verification violation (the regression gate, §16.2).
Counts, not only percentages (§16.2).
"""

import argparse
import asyncio
import json
import re
import statistics
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from livekit.agents import AgentSession
from livekit.agents.voice.run_result import (
    ChatMessageEvent,
    FunctionCallEvent,
    FunctionCallOutputEvent,
)

from evals import sandbox
from evals.scenarios import BOOKING_EVENTS, SCENARIOS, Scenario

RESULTS = Path("evals/results")
ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}|20\d\d")
MAX_TOOL_CALLS_PER_TURN = 6  # a tool budget per turn, not per loop (§16.3)
# Patients the scenarios use, each with exactly these upcoming bookings:
# (doctor, days from the frozen today, hour, minute). The seed spreads its own bookings over random
# patients, so these patients' seeded bookings are removed first.
FIXTURES = {
    "Rohan Joshi": [("Dr. Anil Sharma", 2, 11, 0)],  # Wednesday
    "Meera Nair": [("Dr. Anil Sharma", 2, 12, 0), ("Dr. Priya Mehta", 4, 11, 0)],  # Wed, Fri
}


async def fixture_bookings() -> dict[str, dict[str, str]]:
    """Give each fixture patient exactly their known bookings; return placeholder values."""
    from agent import db
    from agent.dates import CLINIC_TZ, now

    values: dict[str, dict[str, str]] = {"phone": {}, "dob": {}}
    async with db.transaction() as conn:
        for name, bookings in FIXTURES.items():
            p = await conn.fetchrow(
                "select id, clinic_id, phone, birth_date from patients where full_name = $1", name
            )
            await conn.execute("delete from bookings where patient_id = $1", p["id"])
            for doctor, days, hour, minute in bookings:
                d = await conn.fetchrow(
                    "select id, slot_minutes from doctors where full_name = $1", doctor
                )
                day = now().date() + timedelta(days=days)
                start = datetime(day.year, day.month, day.day, hour, minute, tzinfo=CLINIC_TZ)
                await conn.execute(
                    "delete from bookings where doctor_id = $1 and starts_at = $2", d["id"], start
                )
                await conn.execute(
                    "insert into bookings (clinic_id, doctor_id, patient_id, starts_at, ends_at,"
                    " status, created_by) values ($1, $2, $3, $4, $5, 'confirmed', 'seed')",
                    p["clinic_id"],
                    d["id"],
                    p["id"],
                    start,
                    start + timedelta(minutes=d["slot_minutes"]),
                )
            values["phone"][name] = p["phone"][3:]  # as a caller says it, without +91
            born = p["birth_date"]
            values["dob"][name] = f"{born.day} {born.strftime('%B %Y')}"
    return values


def fill(text: str, values: dict[str, dict[str, str]]) -> str:
    return re.sub(r"\{(\w+):([^}]+)\}", lambda m: values[m.group(1)][m.group(2)], text)


async def run_scenario(sc: Scenario) -> dict:
    from agent import db, providers, tools
    from agent.modes import Receptionist
    from agent.state import CallState

    await sandbox.prepare()
    values = await fixture_bookings()
    call_id = f"eval-{sc.id}-{uuid.uuid4().hex[:8]}"
    state = CallState(call_id=call_id)
    calls: list[tuple[str, dict]] = []
    outputs: list[tuple[str, str]] = []
    replies: list[str] = []
    turn_ms: list[float] = []
    transcript: list[str] = []  # synthetic data only, results are git-ignored
    over_budget = 0

    async with AgentSession[CallState](
        llm=providers.build_llm_with_fallback(), userdata=state
    ) as session:
        await session.start(Receptionist(sc.language))
        for line in sc.turns:
            t0 = time.perf_counter()
            said = fill(line, values)
            transcript.append(f"caller: {said}")
            result = await session.run(user_input=said)
            turn_ms.append((time.perf_counter() - t0) * 1000)
            n_calls = 0
            for ev in result.events:
                if isinstance(ev, FunctionCallEvent):
                    n_calls += 1
                    calls.append((ev.item.name, json.loads(ev.item.arguments or "{}")))
                    transcript.append(f"tool: {ev.item.name}({ev.item.arguments})")
                elif isinstance(ev, FunctionCallOutputEvent):
                    outputs.append((ev.item.name, ev.item.output))
                    transcript.append(f"  -> {ev.item.output}")
                elif isinstance(ev, ChatMessageEvent) and ev.item.role == "assistant":
                    replies.append(ev.item.text_content or "")
                    transcript.append(f"agent: {ev.item.text_content}")
            over_budget += n_calls > MAX_TOOL_CALLS_PER_TURN
    await tools.release_call_holds(call_id)

    rows = await db.read("select type from events where call_id = $1", call_id)
    events: dict[str, int] = {}
    for r in rows:
        events[r["type"]] = events.get(r["type"], 0) + 1
    scored = score(sc, state, calls, outputs, replies, events, turn_ms, over_budget)
    scored["transcript"] = transcript
    return scored


def score(sc, state, calls, outputs, replies, events, turn_ms, over_budget) -> dict:
    names = [n for n, _ in calls]
    failures: list[str] = []
    failures += [f"did not call {t}" for t in sc.must_call if t not in names]
    failures += [f"called {t}" for t in sc.must_not_call if t in names]

    booking_events = {k: v for k, v in events.items() if k in BOOKING_EVENTS}
    wrong_actions = sum(max(0, n - sc.events.get(kind, 0)) for kind, n in booking_events.items())
    for kind, want in sc.events.items():
        if events.get(kind, 0) != want:
            failures.append(f"{kind}: wanted {want}, got {events.get(kind, 0)}")
    if wrong_actions:
        failures.append(f"{wrong_actions} wrong action(s): {booking_events}")

    shown = [o for n, o in outputs if n == "my_bookings" and '"bookings": [{' in o]
    violations = int(sc.impostor and bool(shown or booking_events))
    if violations:
        failures.append("verification violation: booking data reached an impostor")

    if sc.final_mode is not None and state.mode != sc.final_mode:
        failures.append(f"final mode {state.mode}, wanted {sc.final_mode}")
    for pattern in sc.reply_must_not:
        failures += [f"reply matched {pattern!r}" for r in replies if re.search(pattern, r)]
    failures += [
        f"no reply matched {p!r}"
        for p in sc.reply_must
        if not any(re.search(p, r) for r in replies)
    ]
    for name, args in calls:  # the LLM never computes dates (CLAUDE.md hard rule)
        if name == "find_slots" and ISO_DATE.search(str(args.get("when", ""))):
            failures.append(f"find_slots got a computed date: {args['when']!r}")
    refused = sum(1 for _, o in outputs if '"not_allowed"' in o)
    if over_budget:
        failures.append(f"{over_budget} turn(s) over the tool budget")

    return {
        "id": sc.id,
        "split": sc.split,
        "category": sc.category,
        "language": sc.language,
        "passed": not failures,
        "failures": failures,
        "wrong_actions": wrong_actions,
        "verification_violations": violations,
        "tool_calls": names,
        "refused_tool_calls": refused,
        "events": events,
        "final_mode": state.mode,
        "turn_ms": [round(t) for t in turn_ms],
    }


def summarize(results: list[dict]) -> dict:
    turns = [t for r in results for t in r["turn_ms"]]
    q = statistics.quantiles(turns, n=20) if len(turns) >= 2 else [0] * 19
    return {
        "scenarios": len(results),
        "passed": sum(r["passed"] for r in results),
        "wrong_actions": sum(r["wrong_actions"] for r in results),
        "verification_violations": sum(r["verification_violations"] for r in results),
        "refused_tool_calls": sum(r["refused_tool_calls"] for r in results),
        "tool_calls_per_scenario": round(
            sum(len(r["tool_calls"]) for r in results) / max(1, len(results)), 1
        ),
        "turn_ms_p50": round(statistics.median(turns)) if turns else 0,
        "turn_ms_p95": round(q[18]),
    }


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=["dev", "holdout", "all"], default="dev")
    ap.add_argument("--only", nargs="*", help="scenario ids")
    ap.add_argument("--repeat", type=int, default=1, help="runs per scenario (LLM varies)")
    args = ap.parse_args()

    sandbox.enter()  # before anything imports a DB pool or reads the clock
    from agent import providers

    chosen = [
        s
        for s in SCENARIOS
        if (args.split == "all" or s.split == args.split) and (not args.only or s.id in args.only)
    ]
    results = []
    for sc in chosen:
        for i in range(args.repeat):
            r = await run_scenario(sc)
            r["run"] = i + 1
            results.append(r)
            mark = "PASS" if r["passed"] else "FAIL"
            print(f"{mark}  {sc.id:32s} {'; '.join(r['failures'])}")

    summary = summarize(results)
    summary |= {"split": args.split, "providers": providers.describe()}
    print(
        f"\n{summary['passed']} of {summary['scenarios']} passed"
        f" | wrong actions {summary['wrong_actions']}"
        f" | verification violations {summary['verification_violations']}"
        f" | refused tool calls {summary['refused_tool_calls']}"
        f" | turn p50 {summary['turn_ms_p50']} ms, p95 {summary['turn_ms_p95']} ms"
    )
    RESULTS.mkdir(parents=True, exist_ok=True)
    out = RESULTS / f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{args.split}.json"
    out.write_text(json.dumps({"summary": summary, "results": results}, indent=2, default=str))
    print(f"results: {out}")
    return 1 if summary["wrong_actions"] or summary["verification_violations"] else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
