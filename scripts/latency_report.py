"""p50/p95 per latency stage from the turns table (or the local JSONL backup).

    uv run python -m scripts.latency_report                       # DB if DATABASE_URL, else JSONL
    uv run python -m scripts.latency_report --jsonl --since 2026-10-10 --language en

Excluded unless asked for:
- cold calls (--include-cold): the first call after the worker sat idle (cold start);
- greeting turns (--include-greeting): no caller is waiting on them, and each call's first LLM
  and TTS requests also pay connection setup.
"""

import argparse
import asyncio
import json
import math
import os
from datetime import UTC, date, datetime
from pathlib import Path

from dotenv import load_dotenv

from agent.db import connect_kwargs

JSONL_PATH = Path("logs/turns.jsonl")

# (column, label) in pipeline order. Definitions: design doc §6.
STAGES = [
    ("end_of_turn_ms", "End of turn"),
    ("stt_final_ms", "STT final"),
    ("llm_ttft_ms", "LLM TTFT"),
    ("llm_first_sentence_ms", "LLM first sentence"),
    ("tts_ttfb_ms", "TTS first audio"),
    ("voice_to_voice_ms", "Voice-to-voice"),
    ("interrupt_stop_ms", "Barge-in stop"),
]


def percentile(values: list[float], p: float) -> float | None:
    """Linear interpolation between closest ranks (same as numpy's default)."""
    if not values:
        return None
    s = sorted(values)
    k = (len(s) - 1) * p / 100
    lo, hi = math.floor(k), math.ceil(k)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def summarize(rows: list[dict]) -> list[tuple[str, int, float | None, float | None, float | None]]:
    out = []
    for col, label in STAGES:
        vals = [r[col] for r in rows if r.get(col) is not None]
        out.append(
            (
                label,
                len(vals),
                percentile(vals, 50),
                percentile(vals, 95),
                max(vals) if vals else None,
            )
        )
    return out


def load_jsonl() -> list[dict]:
    if not JSONL_PATH.exists():
        return []
    return [json.loads(line) for line in JSONL_PATH.read_text().splitlines() if line.strip()]


async def load_db(dsn: str) -> list[dict]:
    import asyncpg

    conn = await asyncpg.connect(**connect_kwargs(dsn))
    try:
        rows = await conn.fetch("select * from turns")
    finally:
        await conn.close()
    return [
        {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in r.items()} for r in rows
    ]


def keep(row: dict, args: argparse.Namespace) -> bool:
    if not args.include_cold and row.get("cold_call"):
        return False
    if (
        not args.include_greeting
        and row.get("turn_index") == 0
        and not row.get("user_speech_end_at")
    ):
        return False
    if args.turn:
        p = row.get("providers") or {}
        p = json.loads(p) if isinstance(p, str) else p
        if p.get("turn", "v1-mini") != args.turn:  # rows from before E7 have no label
            return False
    if args.channel and row.get("channel") != args.channel:
        return False
    if args.language and row.get("language") != args.language:
        return False
    if args.since:
        at = row.get("agent_audio_start_at") or row.get("user_speech_end_at")
        if not at or datetime.fromisoformat(at).astimezone(UTC).date() < args.since:
            return False
    return True


def setup_label(row: dict) -> str:
    p = row.get("providers") or {}
    if isinstance(p, str):  # asyncpg returns jsonb as text
        p = json.loads(p)
    return (
        f"{row.get('language')}, {row.get('channel')}, {p.get('stt')}/{p.get('llm')}/{p.get('tts')}"
    )


def fmt(v: float | None) -> str:
    return "-" if v is None else f"{v:.0f}"


def main() -> None:
    load_dotenv(".env")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--jsonl", action="store_true", help="read logs/turns.jsonl, not the DB")
    ap.add_argument("--channel", choices=["browser", "phone"])
    ap.add_argument("--language", help="en | hinglish")
    ap.add_argument("--since", type=date.fromisoformat, help="YYYY-MM-DD (UTC)")
    ap.add_argument("--include-cold", action="store_true")
    ap.add_argument("--include-greeting", action="store_true")
    ap.add_argument("--turn", help="turn detector: v1-mini | v1 (rows before E7 count as v1-mini)")
    args = ap.parse_args()

    dsn = os.environ.get("DATABASE_URL")
    use_db = dsn and not args.jsonl
    rows = asyncio.run(load_db(dsn)) if use_db else load_jsonl()
    rows = [r for r in rows if keep(r, args)]
    calls = {r["call_id"] for r in rows}

    print(
        f"source: {'database' if use_db else JSONL_PATH}  |  "
        f"{len(rows)} turns from {len(calls)} calls"
    )
    print(f"\n{'Stage':20s} {'n':>4s} {'p50 ms':>8s} {'p95 ms':>8s} {'max ms':>8s}")
    summary = summarize(rows)
    for label, n, p50, p95, mx in summary:
        print(f"{label:20s} {n:4d} {fmt(p50):>8s} {fmt(p95):>8s} {fmt(mx):>8s}")

    tok = [r for r in rows if r.get("llm_prompt_tokens") is not None]
    if tok:
        reasoning = [r["llm_reasoning_tokens"] or 0 for r in tok]
        prompt = sum(r["llm_prompt_tokens"] for r in tok)
        cached = sum(r["llm_cached_tokens"] or 0 for r in tok)
        print(
            f"\nLLM tokens over {len(tok)} replies: reasoning p50 {fmt(percentile(reasoning, 50))}"
            f" / max {max(reasoning)} (0 = no hidden thinking); "
            f"prompt tokens served from cache {cached}/{prompt} ({100 * cached / prompt:.0f}%)"
        )

    small = [label for label, n, *_ in summary if 0 < n < 20]
    if small:
        print(
            f"\nNote: under 20 samples for {', '.join(small)}; p95 there is close to the max "
            "and moves a lot with one slow turn. Report counts next to percentiles."
        )

    cells = " | ".join(f"{fmt(p50)} / {fmt(p95)}" for _, _, p50, p95, _ in summary)
    setups = sorted({setup_label(r) for r in rows})
    setup = "; ".join(setups) if setups else "-"
    print("\nLATENCY_LOG.md row (p50 / p95 ms, columns in the order above):")
    print(
        f"| {date.today()} | <change> | {setup} | {len(calls)} calls, {len(rows)} turns "
        f"| {cells} | <notes> |"
    )


if __name__ == "__main__":
    main()
