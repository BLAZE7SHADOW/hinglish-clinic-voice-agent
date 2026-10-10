# Clinic Voice Agent

Hinglish voice receptionist for small Indian clinics. Full design: `docs/design.md`. Current week's plan: `docs/week1.md`. Current status and next task: `docs/PROGRESS.md` (update it at the end of every work session).

## Stack

- Python 3.12, `uv` (commit `uv.lock`)
- LiveKit Agents 1.8 on LiveKit Cloud (Build plan), using `AgentServer` + `@server.rtc_session`
- Silero VAD + `inference.TurnDetector(version="v1-mini")` (pinned, runs locally)
- STT Deepgram `nova-3` (`language="multi"`), LLM Gemini `gemini-2.5-flash` (thinking off), TTS Cartesia `sonic-3` (`hi`)
- Supabase Postgres via `asyncpg` (no ORM), `pydantic` for tool inputs, call state and turn records, `pytest` + `pytest-asyncio`, `ruff`
- Later: Temporal (week 5), Next.js staff view on Vercel (week 7), OpenTelemetry (week 7)

## Hard rules

- **Real-time path stays small.** Nothing in a turn waits on a queue or a workflow engine. Never call Temporal synchronously during a turn; emit an event and move on.
- **One agent with modes on the call**, never a chain of agents. Multi-agent work runs only on the durable path.
- **The LLM never computes dates or times.** Code (`agent/dates.py`) resolves "kal", "parson shaam" and similar phrases, then passes the result back as a fact.
- **Guards live in code, not the prompt.** Code checks typed call state before any tool runs, and each mode exposes only the tools it needs.
- **No personal data before verification.** Read back doctor, date and time and wait for a clear yes before any write. No medical advice; send those calls to handoff.
- **Writes are idempotent.** Use full-length idempotency keys and never retry blindly. Reads retry once. Every external call has a timeout.
- **The DB unique constraint on (doctor, start time)** is the final guard against double booking, not application code.
- **Synthetic data only.** Redact phones, names and DOB in logs and traces. No secrets in the repo; everything `.env*` except `.env.example` is git-ignored.
- **Latency:** use the metric definitions in design §6. Report p50/p95, never averages alone. Skip the cold first call. Every change gets a row in `docs/LATENCY_LOG.md` next to task success.
- **Evals never touch real systems:** no real bookings, messages or calls.
- **Keep the prompt and per-mode tool list short;** both add to time to first token.

## How to run

```bash
uv sync
cp .env.example .env.local                        # fill in keys; set spending caps
uv run python -m agent.main download-files        # VAD + turn-detector weights (once)
uv run python -m agent.main dev                   # worker connects to LiveKit Cloud
```

Then open https://agents-playground.livekit.io, sign in to the same LiveKit Cloud project and connect. `uv run python -m agent.main console` talks to the agent from the terminal with no browser.

```bash
uv run pytest
uv run ruff check && uv run ruff format --check
```

## Conventions

- Create folders only when their week arrives (see the repo structure in `docs/week1.md`).
- LiveKit APIs change between versions: check the installed source in `.venv/lib/python3.12/site-packages/livekit/` or the docs before using an API. Don't guess.
- Pass every pipeline stage explicitly to `AgentSession` so provider choices are visible in code.
