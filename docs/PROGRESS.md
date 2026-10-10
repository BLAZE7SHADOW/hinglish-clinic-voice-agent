# Progress

## Current status (2026-10-10, week 1 · day 1)

Done:
- Repo scaffold: `agent/`, `db/migrations/`, `scripts/`, `docs/`; `pyproject.toml` + `uv.lock` (Python 3.12, livekit-agents 1.8.6); `.env.example`; `CLAUDE.md`.
- Minimal worker `agent/main.py` with no tools and no telemetry: Silero VAD, turn detector `v1-mini` (pinned), Deepgram `nova-3` multi, Gemini `gemini-2.5-flash` (thinking off), Cartesia `sonic-3` Hindi. A short Hinglish receptionist prompt lives in `agent/prompts.py`.
- Verified: dependencies install, ruff is clean, the worker imports, model weights download, and every provider constructs.

Not yet verified:
- A live browser call. It needs real keys in `.env.local`.

Notes for `DECISIONS.md` (day 2):
- The turn detector is pinned to `v1-mini`. The 1.8 default picks the cloud `v1` model in `dev` but the local `v1-mini` in `start`, so latency numbers would not be comparable. The cloud `v1` model (audio-aware) is a candidate for the E7 turn-detection experiment.
- Gemini thinking is off for time to first token. `gemini-3.5-flash` is a candidate for the E1 model comparison.
- No `CARTESIA_VOICE_ID` has been chosen yet; the plugin default voice is used.

## Next task

1. **Day 1 check:** fill in `.env.local`, run `uv run python -m agent.main dev`, and talk to the agent from the Agents Playground. Done when it greets you and answers.
2. **Day 2:** pick a Cartesia voice that handles Hinglish, refine the receptionist prompt, test interruptions with default settings and note what happens, then start `docs/DECISIONS.md` ("Why LiveKit", "Why these providers").
