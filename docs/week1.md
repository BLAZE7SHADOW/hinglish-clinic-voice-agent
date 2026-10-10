# Week 1 — Day-by-Day Plan

## Goal for the week

By the end of week 1 you can talk to the agent in Hinglish from a browser, every turn is timed by stage, and you have a baseline latency table. You also have the clinic schedule in Supabase with read-only tools working, so week 2 can start straight on bookings.

**Definition of done for the week:**

- [ ] A browser conversation in Hinglish works end to end, including interrupting the agent.
- [ ] Every turn writes stage timings to the database.
- [ ] `LATENCY_LOG.md` has a baseline row: p50 and p95 for each stage, from at least 10 scripted test calls.
- [ ] The agent answers "Dr. Sharma kal free hain?" correctly from the database.
- [ ] `DECISIONS.md` and `AUDIO_PATH.md` exist with your first written answers.
- [ ] The Indian phone number process has started, and the resume rewrite is done.

**Daily rhythm (6-8 hours):** about 5-6 hours building, then 1-2 hours on job search: 3-5 targeted applications and one referral message.

## Repo structure

```
clinic-voice-agent/
├── agent/                  # LiveKit Agents worker (the real-time path)
│   ├── main.py             # entrypoint, session setup, provider wiring
│   ├── prompts.py          # per-mode instructions, kept short
│   ├── state.py            # typed call state: mode, verification, slot
│   ├── tools/              # find_slots, clinic_info (week 1), writes later
│   ├── dates.py            # Hinglish date/time resolution in code
│   └── telemetry.py        # per-stage timing, turn records
├── db/
│   ├── migrations/         # SQL for Supabase
│   └── seed.py             # synthetic clinic, doctors, patients
├── workflows/              # Temporal (from week 5)
├── evals/                  # scenarios and runner (from week 2)
├── dashboard/              # Next.js staff view on Vercel (from week 7)
├── scripts/
│   └── latency_report.py   # p50/p95 per stage from the turns table
├── docs/
│   ├── DECISIONS.md
│   ├── AUDIO_PATH.md
│   ├── LATENCY_LOG.md
│   └── FAILURE_LAB.md
├── .env.example            # never commit real keys
├── pyproject.toml
└── README.md
```

Create only `agent/`, `db/`, `scripts/` and `docs/` this week. The other folders appear when their week comes.

## Libraries

| Need | Library | Notes |
| --- | --- | --- |
| Python and packages | Python 3.12, `uv` | `uv` keeps installs fast and the lockfile pins exact versions; commit the lockfile |
| Voice agent | `livekit-agents` with plugins for your providers | Install the plugins you use (VAD, turn detector, STT, LLM, TTS). The turn detector's mini model ships with recent SDK versions ([LiveKit](https://livekit.com/blog/solving-end-of-turn-detection)) |
| VAD | Silero plugin | Default pairing with the LiveKit turn detector |
| STT | Deepgram and Sarvam (compare both) | Start with one, add the second in week 4 for the Hinglish comparison |
| LLM | One fast model with tool calling | Pick for speed first; measure task success before switching |
| TTS | ElevenLabs or Cartesia | Choose a voice that handles Hinglish naturally |
| Database | `asyncpg` or `psycopg` (async) | Talk to Supabase Postgres directly; no ORM needed |
| Validation | `pydantic` | Tool inputs, call state, turn records |
| Tests | `pytest`, `pytest-asyncio` | Date parsing tests start in week 1 |
| Browser client | LiveKit's sample web frontend or Agents Playground | Check the LiveKit docs for the current starter; no custom UI this week |

Check each provider's plugin name and setup in the LiveKit Agents docs on day 1, since package names change between versions.

## Latency instrumentation from the first commit

If timing is added later, you lose the baseline. Build it on day 3, before any optimization.

### What to record for every turn

| Field | Meaning |
| --- | --- |
| `call_id`, `turn_index` | Which call and which turn |
| `user_speech_end_at` | VAD says the caller stopped speaking |
| `turn_committed_at` | Turn detector decides the turn is over |
| `stt_final_at` | Final transcript arrives |
| `llm_request_at`, `llm_first_token_at` | LLM request sent, first token received |
| `tts_request_at`, `tts_first_audio_at` | First text sent to TTS, first audio back |
| `agent_audio_start_at` | First agent audio played to the caller |
| `interrupted`, `interrupt_stop_ms` | Whether the caller cut in, and how fast audio stopped |
| `tool_calls` | Name and duration of each tool call |
| `channel`, `providers` | Browser or phone; which STT, LLM and TTS were used |

From these, compute the metrics defined in section 6 of the design doc, with voice-to-voice latency = `agent_audio_start_at` minus `user_speech_end_at`.

### How to collect it

1. **Use the framework's metrics first.** LiveKit Agents emits per-stage metrics events during a session (for example LLM time to first token and TTS time to first audio). Subscribe to them in `telemetry.py`. Check the current docs for the event and field names.
2. **Add your own timestamps** where the framework does not give you what you need, especially `user_speech_end_at` and `agent_audio_start_at`. Use one monotonic clock inside the worker.
3. **Write one row per turn** to a `turns` table in Supabase, asynchronously, so writing never slows the call.
4. **Also log each row as one JSON line** locally. If the database is down during testing, you still have the data.
5. **`scripts/latency_report.py`** reads the table and prints p50 and p95 per stage, filtered by channel and date range.

### The scripted test set

Write 10 short test scripts now (you will reuse them all project long): a greeting, an FAQ, an availability question with a date in Hinglish, one with a doctor's name, one where you interrupt mid-reply, one with a long pause mid-sentence, and four mixed ones. Run all 10 the same way each time you measure. Skip the first call after the agent has been idle, because it includes cold-start time.

OpenTelemetry export and a dashboard come in week 7. This week, the database table and the report script are enough.

## Day by day

### Day 1: Accounts and a talking agent

- [ ] Create accounts: LiveKit Cloud (Build plan), Supabase, Vercel, and your STT, LLM and TTS providers. Set a spending cap on every key.
- [ ] Start the Indian phone number process with Exotel or Twilio; note what documents they need.
- [ ] Create the repo, `pyproject.toml` with `uv`, `.env.example`, and the four week-1 folders.
- [ ] Follow the LiveKit Agents voice quickstart and talk to the sample agent from the browser.

**End-of-day check:** you can speak to an agent in the browser and it answers.

### Day 2: Your providers and Hinglish

- [ ] Swap in your chosen STT, LLM and TTS.
- [ ] Enable Silero VAD and the turn detector explicitly in your session config, so the choice is visible in code.
- [ ] Write a short receptionist prompt for a fictional clinic: Hinglish, polite, short replies, no medical advice.
- [ ] Try interrupting the agent mid-sentence and note what happens with the default settings.
- [ ] Start `DECISIONS.md` with "Why LiveKit" and "Why these providers" in your own words.

**End-of-day check:** a 2-minute Hinglish conversation works, and interruptions stop the agent.

### Day 3: Instrumentation and baseline

- [ ] Create the `turns` table in Supabase.
- [ ] Build `telemetry.py`: subscribe to framework metrics, add your own timestamps, write one row per turn asynchronously and as a JSON line.
- [ ] Write `scripts/latency_report.py` for p50 and p95 per stage.
- [ ] Write the 10 test scripts and run each one, skipping the first cold call.
- [ ] Add the baseline row to `LATENCY_LOG.md`. Do not optimize anything yet.

**End-of-day check:** a table of p50 and p95 per stage from 10 calls.

### Day 4: Clinic data and read-only tools

- [ ] Migrations for clinics, doctors, schedules, patients, bookings (with the unique constraint on doctor and start time), calls, turns and events.
- [ ] `db/seed.py`: one clinic, three doctors, two weeks of schedules, 20 synthetic patients.
- [ ] `dates.py`: turn "kal", "parson", "agle Monday", "shaam ko" into concrete dates and times, with `pytest` tests.
- [ ] Tools `find_slots` and `clinic_info`, with typed inputs and timeouts.

**End-of-day check:** "Dr. Sharma kal shaam free hain?" gets a correct answer from the database.

### Day 5: Understanding and state

- [ ] Write `AUDIO_PATH.md`: the path of one audio packet from the caller's mic to the agent and back. Use the LiveKit and WebRTC docs; write it in your own words.
- [ ] Write `state.py`: call state with mode, verification status, and the slot being discussed. Not wired to write tools yet.
- [ ] Re-run the 10 test calls to check nothing slowed down after adding tools; add a row to `LATENCY_LOG.md`.

**End-of-day check:** you can explain the audio path out loud without notes, and the latency report has two rows.

### Day 6: Catch-up and job push

- [ ] Finish anything left from days 1-5.
- [ ] Record a 60-second screen recording of a Hinglish availability question being answered (for your own progress log).
- [ ] Finish the resume rewrite with PayOps first; update the LinkedIn headline; publish the first PayOps post.

**End-of-day check:** every item in the week's definition of done is ticked.

### Day 7: Rest and review

Take most of the day off. Spend 30 minutes reading the week 2 plan in the design doc and noting questions. A sustainable pace over eight weeks matters more than one heroic week.

### If you fall behind

Cut in this order: the day 6 recording, then `dates.py` coverage beyond the common phrases, then the second latency run. Never cut the baseline measurement or the resume work.
