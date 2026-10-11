# Progress

## Current status (2026-10-10, week 1 · day 1)

Done:
- Repo scaffold: `agent/`, `db/migrations/`, `scripts/`, `docs/`; `pyproject.toml` + `uv.lock` (Python 3.12, livekit-agents 1.8.6); `.env.example`; `CLAUDE.md`.
- Minimal worker `agent/main.py` with no tools and no telemetry: Silero VAD, turn detector `v1-mini` (pinned), Deepgram `nova-3` multi, Gemini `gemini-3.6-flash` from `AI_MODEL` (thinking minimal), Cartesia `sonic-3` Hindi. A short Hinglish receptionist prompt lives in `agent/prompts.py`.
- Verified: dependencies install, ruff is clean, the worker imports, model weights download, and every provider constructs.

- **Day 1 check passed (2026-10-10):** a live browser call through the Agents Playground worked. The Hinglish greeting played, the agent answered "Clinic कब खुलेगा?" (Deepgram tagged it `hi`), and the worker registered in LiveKit Cloud region India South.

Decision (2026-10-10): **English audio first.** `AGENT_LANGUAGE=en` uses Deepgram `en-IN`, Cartesia English and the English prompt. The reason: build latency timing, tools, interruption handling and evals without Hinglish recognition noise hiding real bugs. Hinglish code (`dates.py`, names, test scripts) is still built on schedule, Hinglish audio returns in week 4, and the week 3 V1 video should be Hinglish if at all possible. Sarvam stays wired but unused.

Sarvam samples: Bulbul v4-flash has Hinglish customer-service voices (`simran_enhi_customer`, `ishita_enhi_customer_expressive`), but first audio took about 1 s against Cartesia's 150–200 ms on the one-shot HTTP path. Re-measure on the streaming path in week 4.

English call (2026-10-10, 13:30): every turn was transcribed correctly with about 0.4–0.5 s of transcript delay. The interruption was detected 0.36 s after speech started, and history kept only the heard part ("Sure, I can help with that. May I"). On "your superior… name?" the turn detector scored the pause 0.25 (unfinished), waited 2.5 s, and took the whole question as one turn.

Prompt fixes after that call, checked with text tests:
- It invented "clinic manager is Mr. Sharma". Now no clinic facts or names of any kind.
- It asked for name and phone with nowhere to store them. Now it does not ask for personal details.
- It promised callbacks, holds and "let me check" that nothing performs. Now it says honestly it can't help on this line yet (design §9: no fake confirmation).
- Medical: no medicine advice; for urgent cases it says to call 108 or go to the nearest hospital.
Remove the "can't help on this line" lines as the real tools (`clinic_info`, `find_slots`, `request_callback`) arrive.

Known issues:
- The local worker log contains caller PII (`lk.pii.*` transcript fields, a real name in one test). Telemetry (day 3) must redact before writing turns anywhere, and only synthetic names should be used in test calls.
- **Speech recognition misses Hinglish (first call).** Deepgram `multi` turned about 2.8 s of speech into "Uh," (tagged `en`, 6.2 s late) and about 9 s into "Clinic कब खुलेगा?". Sarvam `saaras:v4` codemix is now wired behind `STT_PROVIDER` for an A/B.
- **The voice sounded rushed and flat** (one pace, no pauses). Cartesia speed is now 0.9, the prompt writes speech-style phrases with commas and openers like "Ji", and Sarvam `bulbul:v3` is wired behind `TTS_PROVIDER`. `scripts/tts_samples.py` renders the variants for listening.
- The agent invented clinic facts ("subah aath baje"). The prompt now tells it not to guess and to offer a staff callback instead, until `clinic_info` arrives on day 4.
- `turn_detection=` is deprecated in 1.8; move to `turn_handling=TurnHandlingOptions(...)`.
- Interruptions were not tested in the first call.

Notes for `DECISIONS.md` (day 2):
- The turn detector is pinned to `v1-mini`. The 1.8 default picks the cloud `v1` model in `dev` but the local `v1-mini` in `start`, so latency numbers would not be comparable. The cloud `v1` model (audio-aware) is a candidate for the E7 turn-detection experiment.
- Gemini thinking is set to the lowest level for time to first token. The model is chosen by `AI_MODEL`; `gemini-3.7-flash` and `gemini-3.8-flash` are also available to the key and are candidates for the E1 model comparison.
- A Cartesia voice ID is set in `.env`; judge it on Hinglish in the day 2 test.
- The adaptive interruption detector calls the LiveKit Cloud gateway, which is one more network dependency on the hot path.
- Google's SDK has its own automatic function calling (AFC), and it is on by default. Before adding tools on day 4, check how it interacts with LiveKit's tool loop.
- The first call after idle waited about 3 s for a worker process ("no warmed process available"). Exclude it from latency runs.

## Day 3 (2026-10-10): latency telemetry

Built:
- `agent/telemetry.py`: one `TurnRecord` per agent reply. It is built from LiveKit 1.8's per-message `MetricsReport` (end of turn, STT, LLM first token and first sentence, TTS first audio, e2e voice-to-voice), plus the `overlapping_speech` and `agent_state_changed` events for barge-in stop time, plus tool durations. Rows go to `logs/turns.jsonl` always and to Postgres when `DATABASE_URL` is set, from background tasks with a 5 s DB timeout.
- `db/migrations/001_turns.sql`, `scripts/migrate.py`, `scripts/latency_report.py` (p50/p95/max per stage, cold calls excluded, prints a `LATENCY_LOG.md` row).
- `docs/TEST_CALLS.md` (10 scripted calls) and the `docs/LATENCY_LOG.md` template.
- 8 unit tests (`uv run pytest`).

Done (later on day 3):
- Supabase project in Mumbai, `turns` table migrated, rows written by the worker.
- **Baseline recorded** in `docs/LATENCY_LOG.md`: V2V p50 2,251 / p95 2,747 ms over 10 calls and 22 turns.

Optimization candidates from the baseline (not applied; the design puts the optimization pass in week 3):
1. **Turn detector false "unfinished"** (EoT p95 2.5 s, about 6 of 22 turns): E7 (cloud `v1` vs a lower `max_endpointing_delay` vs threshold).
2. **LLM TTFT** about 1.3 s p50, the biggest p50 stage: E1 (model and region).
3. **Backchannel "okay" stopped the agent** (call 10): the week 3 backchannel filter.
4. The **cloud interruption detector timed out** twice today: count fallbacks (week 7 observability).

## E7 (2026-10-10): turn detector v1 vs v1-mini

- No improvement (5 vs 6 false waits, different sentences), so reverted to `v1-mini`. Row in `LATENCY_LOG.md`, analysis and research in `LEARNING.md` #10.
- New findings: soft backchannels interrupt (#11), and a rhythm glitch in one TTS line (#12; samples in `samples/en/rhythm/` to listen to).
- Week 3 plan: phase-aware endpointing (dynamic as a comparison) and a backchannel filter, tested on a set that includes multi-turn calls.
- An STT word drop ("Okay, [thank] you") is probably Deepgram's 25 ms `endpointing_ms` splitting segments. Test `endpointing_ms` alone later (one variable at a time).

## Candidate: TypeSafe Jev ("System One" typed classifier)

Returns typed Choice/Score/Noul answers with confidence instead of text. Vendor claims 70–500 ms and $0.042 per million input tokens; about 68% on its own 4-workflow benchmark; early access (launched September 2026). Nothing independent verified yet.
- **Not** for the live turn: it can't generate replies, and end-of-turn and barge-in decisions need about 10–50 ms locally, not a network round trip.
- **Add as a contender in E3** (intent / mode routing: Gemini vs embeddings classifier vs Jev) on our own labelled Hinglish data, judged on accuracy, latency from India and cost.
- **Add as a contender for post-call QA and guardrail checks** (week 7), validated against hand labels (design §16.2).
- Before any use: check Hinglish accuracy, hosting region and latency, and redact transcripts (third-party data, design §11).

## Day 4 (2026-10-10): clinic data and read-only tools

Built:
- `db/migrations/002_clinic.sql`: clinics, clinic_facts, doctors, doctor_hours (a weekly template), doctor_days_off, patients, bookings (partial unique index = the final double-booking guard, **verified**: a duplicate active booking is rejected), calls, events.
- `db/seed.py`: 3 doctors, 20 synthetic patients, about 35% of 2 weeks' slots booked, relative to today, deterministic.
- `agent/dates.py`: the caller's words become a date window plus assumptions (English and Hindi days, parts of the day, times including saade/sawa/paune/dedh/dhai), with 42 tests.
- `agent/schedule.py`: free-slot computation and doctor matching (pure, tested). `agent/tools.py`: `clinic_info` and `find_slots` (typed, data-minimal, 2 s timeout plus one retry, honest error).
- `find_slots` latency: 680–1,170 ms down to about 200 ms (3 parallel queries, cached doctors, pool opened at call start).

**End-of-day check passed** (text mode, real LLM, tools and DB): "Dr. Sharma kal shaam free hain?" got "kal matlab Sunday… Dr. Sharma ki clinic nahi hoti… Monday shaam paanch baje…".

Finding: a tool turn needs two LLM calls (decide, then phrase), 3.1–4.8 s in text mode. Day 5 re-measures this on voice.

## Day 5 (2026-10-10)

- `agent/state.py`: typed CallState plus `check()` guards (per-mode allowlist, verification, read-back). 6 tests. Not wired to write tools yet (week 2).
- `docs/AUDIO_PATH_FACTS.md`: a verified fact sheet for this setup. **`AUDIO_PATH.md` is yours to write.**
- Still to do: the 10-call re-run with tools, plus a `LATENCY_LOG.md` row.

## Day 5 (2026-10-11): voice run with tools, then three fixes

Measured (row in `LATENCY_LOG.md`; DB re-seeded for today):
- **Task success:** 9 of 10 calls correct from the DB, nothing invented, no booking offers, the medical refusal correct, the call 6 pause held.
- **Tool turns** p50 about 3.9 s vs about 2.2 s without tools (two LLM calls, #14). EoT false waits still 6 of 27.

Built after the run (103 tests pass):
- **Backchannel filter** (`agent/backchannel.py`, LEARNING #15): "okay", "haan", "hmm" while she talks no longer stop her. "Wait", "ruko" and "sorry…" still do.
- **Fuzzy doctor matching** (LEARNING #16): STT's "Harmer" or "Sharpa" maps to Dr. Sharma, and she confirms the guess aloud.
- **Fallback providers** (LEARNING #17):
  - Chains: STT Deepgram → Sarvam, LLM Gemini 3.6 Flash → 2.5 Flash, TTS Cartesia → Sarvam.
  - The chaos test found a bug: Gemini rejects deadlines under 10 s, which would have failed every call. Fixed, and re-tested.
- **Per-call usage:** `logs/usage.jsonl` gets one line per call from LiveKit's `session.usage` (tokens, STT audio seconds, TTS characters), for cost per call.

Known follow-ups:
- **Week 2:** when a read-back confirmation is pending, "yes", "haan" and "ji" must not be treated as backchannels.
- **Experiment:** Deepgram `keyterm` for doctor names, measured on its own.
- **E8:** BVC noise cancellation, quiet room vs TV in the background.
- **Week 3:** a filler line during tool calls; E1 (a faster LLM).

## Next task

1. **Short voice re-test of the fixes:** calls 5 (must still stop), 8 (say "Doctor Sharma") and 10 (soft "okay": she must keep talking). Add a `LATENCY_LOG.md` note.
2. **Write `docs/AUDIO_PATH.md`** in your own words, using `docs/AUDIO_PATH_FACTS.md`.
3. **Start `docs/DECISIONS.md`** ("Why LiveKit", "Why these providers", and now "why fallbacks").
4. **Day 6:** resume, LinkedIn post, the 60 s recording (Hinglish when you're ready; the user deferred Hinglish on 2026-10-11).
