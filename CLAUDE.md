# Clinic Voice Agent

Hinglish voice receptionist for small Indian clinics. Full design: `docs/design.md`. Current week's plan: `docs/week1.md`. Current status and next task: `docs/PROGRESS.md` (update it at the end of every work session). Every real problem gets an entry in `docs/LEARNING.md`: what happened, mechanism, options, choice, verification, and how production systems handle it ("In industry").

## Stack

- Python 3.12, `uv` (commit `uv.lock`)
- LiveKit Agents 1.8 on LiveKit Cloud (Build plan), using `AgentServer` + `@server.rtc_session`
- Silero VAD + `inference.TurnDetector(version="v1-mini")` (pinned, runs locally)
- Language and providers chosen in `.env`, wired and tuned in `agent/providers.py`:
  - `AGENT_LANGUAGE=en|hinglish` sets the language for STT, TTS and the prompt at once. English while the engineering is built; Hinglish audio returns in week 4, and the week 3 V1 demo should be Hinglish if at all possible.
  - STT (`STT_PROVIDER`): Deepgram `nova-3` (`en-IN` / `multi`), or Sarvam `saaras:v4`
  - LLM (`AI_PROVIDER`/`AI_MODEL`): Gemini, default `gemini-3.6-flash`, thinking minimal
  - TTS (`TTS_PROVIDER`): Cartesia `sonic-3` (default), or Sarvam `bulbul:v3` / `v4-flash`
- Telemetry: `agent/telemetry.py` writes one row per agent reply to `logs/turns.jsonl` and, when `DATABASE_URL` is set, the `turns` table. It never blocks a call, and stores no transcript text until redaction exists.
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
- **Hinglish stays in the code even while audio is English:** `dates.py`, doctor names and the Hinglish test scripts are built on schedule.
- **Latency:** label every run with `AGENT_LANGUAGE`; English numbers do not carry over to Hinglish. Use the metric definitions in design §6. Report p50/p95, never averages alone. Skip the cold first call. Every change gets a row in `docs/LATENCY_LOG.md` next to task success.
- **Evals never touch real systems:** no real bookings, messages or calls.
- **Keep the prompt and per-mode tool list short;** both add to time to first token.

## How to run

```bash
uv sync
cp .env.example .env                              # fill in keys; set spending caps
uv run python -m agent.main download-files        # VAD + turn-detector weights (once)
caffeinate -i uv run python -m agent.main dev     # worker connects to LiveKit Cloud; caffeinate stops
                                                  # macOS idle sleep, which kills the worker's connection
```

Then open https://agents-playground.livekit.io, sign in to the same LiveKit Cloud project and connect. `uv run python -m agent.main console` talks to the agent from the terminal with no browser.

```bash
uv run python -m scripts.latency_report         # p50/p95 per stage (DB, or logs/turns.jsonl)
uv run python -m scripts.migrate                # apply db/migrations/*.sql (needs DATABASE_URL)
uv run python -m db.seed                        # synthetic clinic, doctors, patients, bookings (relative to today)
uv run python -m scripts.tts_samples           # render voice variants to samples/ to compare by ear
uv run python -m evals.run --split dev          # text evals in the sandbox (needs EVAL_DATABASE_URL; costs LLM tokens)
uv run pytest
uv run ruff check && uv run ruff format --check
```

## Conventions

- Commit messages: descriptive subject plus a body that explains why. No AI co-author or attribution lines.

- Create folders only when their week arrives (see the repo structure in `docs/week1.md`).
- LiveKit APIs change between versions: check the installed source in `.venv/lib/python3.12/site-packages/livekit/` or the docs before using an API. Don't guess.
- Pass every pipeline stage explicitly to `AgentSession` so provider choices are visible in code.
