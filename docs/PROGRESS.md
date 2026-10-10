# Progress

## Current status (2026-10-10, week 1 · day 1)

Done:
- Repo scaffold: `agent/`, `db/migrations/`, `scripts/`, `docs/`; `pyproject.toml` + `uv.lock` (Python 3.12, livekit-agents 1.8.6); `.env.example`; `CLAUDE.md`.
- Minimal worker `agent/main.py` with no tools and no telemetry: Silero VAD, turn detector `v1-mini` (pinned), Deepgram `nova-3` multi, Gemini `gemini-3.6-flash` from `AI_MODEL` (thinking minimal), Cartesia `sonic-3` Hindi. A short Hinglish receptionist prompt lives in `agent/prompts.py`.
- Verified: dependencies install, ruff is clean, the worker imports, model weights download, and every provider constructs.

- **Day 1 check passed (2026-10-10):** a live browser call through the Agents Playground worked. The Hinglish greeting played, the agent answered "Clinic कब खुलेगा?" (Deepgram tagged it `hi`), and the worker registered in LiveKit Cloud region India South.

Known issues:
- The agent invents clinic facts. It said "subah aath baje", and a text test earlier said 9 baje. This is expected until `clinic_info` is built on day 4; until then the prompt could tell it to offer a callback instead of guessing.
- `turn_detection=` is deprecated in 1.8; move to `turn_handling=TurnHandlingOptions(...)`.
- Interruptions were not tested in the first call.

Notes for `DECISIONS.md` (day 2):
- The turn detector is pinned to `v1-mini`. The 1.8 default picks the cloud `v1` model in `dev` but the local `v1-mini` in `start`, so latency numbers would not be comparable. The cloud `v1` model (audio-aware) is a candidate for the E7 turn-detection experiment.
- Gemini thinking is set to the lowest level for time to first token. The model is chosen by `AI_MODEL`; `gemini-3.7-flash` and `gemini-3.8-flash` are also available to the key and are candidates for the E1 model comparison.
- A Cartesia voice ID is set in `.env`; judge it on Hinglish in the day 2 test.
- The adaptive interruption detector calls the LiveKit Cloud gateway, which is one more network dependency on the hot path.
- Google's SDK has its own automatic function calling (AFC), and it is on by default. Before adding tools on day 4, check how it interacts with LiveKit's tool loop.
- The first call after idle waited about 3 s for a worker process ("no warmed process available"). Exclude it from latency runs.

## Next task

1. **Day 2:** pick a Cartesia voice that handles Hinglish, refine the receptionist prompt, test interruptions with default settings and note what happens, then start `docs/DECISIONS.md` ("Why LiveKit", "Why these providers").
