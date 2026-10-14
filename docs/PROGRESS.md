# Progress

## Current status (2026-10-14, week 2 · built and voice-tested; week 3 next)

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
- **Fuzzy doctor matching: added, then removed.** The threshold was fitted to our own two mishearings; the user rejected it as not production-grade. Replaced by:
  - **Per-clinic STT keyterms** from the DB (`agent/vocabulary.py`, `STT_KEYTERMS=on`).
  - A **prompt rule** to ask which listed doctor when the name doesn't match.
  - **E9 results** (`scripts/name_stt_eval.py`): the clinic's own names went from 75% to 99% correct (browser) and from 61% to 99% (phone). Names not on the roster got worse, and on phone audio 5% became a roster name; the read-back confirmation covers that (LEARNING #16).
- **Fallback providers** (LEARNING #17):
  - Chains: STT Deepgram → Sarvam, LLM Gemini 3.6 Flash → 2.5 Flash, TTS Cartesia → Sarvam.
  - The chaos test found a bug: Gemini rejects deadlines under 10 s, which would have failed every call. Fixed, and re-tested.
- **Per-call usage:** `logs/usage.jsonl` gets one line per call from LiveKit's `session.usage` (tokens, STT audio seconds, TTS characters), for cost per call.

Known follow-ups:
- **Week 2:** when a read-back confirmation is pending, "yes", "haan" and "ji" must not be treated as backchannels.
- **Week 4:** re-measure keyterms on real phone audio (the 5% hijack rate).
- **Cartesia returned 402 Payment Required during E9:** top up credits or set `TTS_PROVIDER=sarvam`. Until then, live calls fail over to Sarvam's voice.
- **E8:** BVC noise cancellation, quiet room vs TV in the background.
- **Week 3:** a filler line during tool calls; E1 (a faster LLM).

## Week 2, step 1 (2026-10-11): modes wired into the call

- **One agent with modes** (`agent/modes.py`, LEARNING #18): a `switch_mode` tool swaps the
  agent's instructions and tool list in place. The call state lives in `session.userdata`.
  Handoff mode has no tools and can't be left.
- **Per-mode prompts** (`prompts.instructions(language, mode)`): a shared base plus one short
  task section per mode.
- **Guards:** every tool runs `guard()` first, against the same `ALLOWED` table that builds each
  mode's tool list. Booking and rescheduling can also read clinic facts.
- 125 tests pass. Not yet tried with a real LLM or a voice call (testing skipped on request).
- The booking, rescheduling and cancelling modes still say honestly that they can't do it yet,
  until steps 2–4.

## Week 2, steps 2–6 (2026-10-12): verification, writes, callbacks, evals

Built (196 tests pass, 3 DB tests skip without a sandbox):
- **`agent/tools/` package** (the design's layout): `read.py`, `patients.py`, `bookings.py`,
  `callback.py`, `common.py`.
- **Verification** (`verify_caller`): phone plus name or birth date, parsed in code
  (`identity.py`, `dates.parse_birth_date`), compared exactly. Every detail given must match one
  patient. Failures don't say which detail was wrong; two failures hand off. **`my_bookings`**
  returns short refs; only those can be changed.
- **Booking writes** (`bookings.py`):
  - `hold_slot` accepts only times `find_slots` offered, with a 3-minute hold.
  - `confirm_booking` runs the code-checked yes (`confirm.py`, LEARNING #19), then confirms the
    held row, writes the event and reads back in one transaction.
  - `reschedule_booking` and `cancel_booking`.
  - Idempotent repeats and a "write outcome unknown" path. Expired holds are cleared on contention
    (#20), and holds are released at call end.
- **Backchannel filter** steps aside while a read-back waits for its answer.
- **`request_callback`:** one `events` row, a fixed reason list, one per call.
- **Migration `003_booking_writes.sql`:** `bookings.call_id`, `cancelled_at`,
  `replaces_booking_id`. **Not applied yet:** run `uv run python -m scripts.migrate` before the
  next voice call, or `hold_slot` fails.
- **Evals** (`evals/`, #21):
  - a sandbox DB plus a frozen clock
  - 20 scenarios (12 dev / 8 hold-out)
  - outcome-based scoring, with a gate on wrong actions and verification violations
  - `intents.jsonl`: 52 labelled lines with a stratified split, for E3
- **`docs/THREAT_MODEL.md`:** a draft with an OWASP LLM mapping and known gaps.

Not run yet (testing deferred by the user): the evals, the sandbox DB tests, any voice call.
Prompt sizes grew (greeting 2,003 → 2,490 characters, booking 2,826), so a `LATENCY_LOG.md`
row is owed at the next measured run.

## Read-back written by code (2026-10-12)

- **`agent/readback.py`:** read-back sentences built from the stored row, with times and dates as
  spoken words (English and Hinglish).
- **`tools/common.read_back_gate`:** the first call to `confirm_booking`, `reschedule_booking` or
  `cancel_booking` speaks the read-back with `session.say` and writes nothing. The second call
  writes only after a clear yes to that exact sentence, heard in full.
- **`CallState.pending`** (`PendingWrite`) replaces the per-slot flags. A changed detail needs a
  new yes, and re-reads are capped at 3. `THREAT_MODEL.md` gap 1 is closed (LEARNING #22).
- 205 tests pass. Prompts are ~100 characters longer (booking 2,917, rescheduling 2,971).
- **Check in the voice test:**
  - does a `say()` read-back get `e2e_latency` in telemetry?
  - how often does a yes over the end of a read-back force a re-read?

## First test and eval run (2026-10-12)

- **`pytest`: 208 pass**, including the 3 sandbox DB tests. They first failed on stale
  doctor/clinic caches after re-seeding, now cleared in `sandbox.prepare()` (LEARNING #23).
- **Dev evals: 13 of 13**, with 0 wrong actions, 0 verification violations and 0 refused tool
  calls. Text turn p50 3,052 / p95 5,178 ms, which is text mode, not voice (`LATENCY_LOG.md` row).
  The first run was 10 of 12 because of a fixture bug (the patient had 5 bookings, not 1). The
  agent was right to ask which one. The fixture was fixed and `cancel_one_of_several` added.
- The eval results now keep the transcript per scenario (`evals/results/`, git-ignored).
- The `hold_slot` hint now names `reschedule_booking` in rescheduling mode.
- **Not run:** the hold-out split (kept for reporting) and the voice test.

## Week 2 voice test (2026-10-14)

- README added for the resume (`README.md`).
- 10 of 11 calls run (W3 skipped until the name fix). **W1, W2, W4, W5, W6, W7 correct; wrong
  actions 0; nothing revealed before verification; no Sarvam fallback.** Every write in `events`
  matches the request: 2 bookings, 1 reschedule (linked by `replaces_booking_id`), 1 cancel of the
  right one of two, 2 callbacks.
- Fixed during the session (LEARNING #24): STT keyterms never loaded since the tools split
  (stale `tools.CLINIC_NAME`), and spoken dates of birth weren't parsed. 217 tests pass.
- `LATENCY_LOG.md` row "Week 2 voice baseline": V2V p50 3,829 / p95 6,358 ms. **EoT p50 2.5 s:
  27 of 52 turns waited the full delay** (#27).
- Findings for week 3:
  - **Names:** "Kabir" heard as "Kavita" (keyterm bias + B/V + spelling not parsed) (#25).
  - **Read-back re-reads:** a yes over the read-back forced a re-read, twice in W2.
  - **Backchannel:** the filter drops "okay" but paused speech may not resume when the
    adaptive detector fires late (#26). LiveKit's adaptive interruption quota (45/min) ran out.
  - **Phone numbers:** the LLM joins STT fragments badly ("two twothree"); parse from the
    transcript in code.
  - W6: the callback goes to the number the unverified caller claimed (the patient's own):
    staff must verify again on that call (THREAT_MODEL).
  - W7: "shortly" sounds like a time promise.
- Worker log: `logs/worker-2026-10-14.log` (git-ignored, contains transcripts).

## Week 3 step 1 (2026-10-14): name and number capture

- `confirm_booking` asks phone, then date of birth, and looks the patient up. On file: the name
  on file is read back and the caller is verified (phone + date of birth). Only a new patient is
  asked for a name.
- Phone numbers and dates of birth are parsed from the caller's own turns when the LLM's copy
  doesn't parse (`common.from_caller`). Spelled names ("K a b I r", "B for ball", NATO) are
  turned into words in code and replace the word they spell (`identity.parse_spelled_name`,
  `apply_spelling`).
- 237 tests pass (+20). Dev evals 14 of 14 (new `book_existing_patient`). LATENCY_LOG row.
- Keyterm scoping is not in this step.
- **Next:** voice re-test R1–R3 (`TEST_CALLS.md`, "Week 3 step 1 re-test").

## Next task

1. ~~Create the eval sandbox~~ (done 2026-10-12): Docker container `clinic-eval-db` (Postgres
   17.11, the same version as Supabase), `127.0.0.1:54329/clinic_eval`, data in the volume
   `clinic-eval-pgdata`, restarts with Docker. Migrated and seeded. The URL is in `.env` as
   `EVAL_DATABASE_URL`.
2. ~~Run pytest and dev evals~~ (done 2026-10-12: 208 pass, 13 of 13).
3. ~~Apply migration 003~~ (done 2026-10-12 on Supabase). Next: a short voice test of book,
   reschedule and cancel, and the backchannel calls 5, 8 and 10.
4. ~~Voice test~~ (done 2026-10-14, above).
5. **Week 3, one change at a time (flag → text evals → voice calls → LATENCY_LOG row):**
   1. Name and number capture (#25): phone first, confirm the name on file, parse spelled
      letters and numbers from the transcript in code, scope keyterms. Re-test W1–W3.
   2. Backchannel resume fix (#26) and an interruption-mode field in telemetry. Re-test call 10.
   3. Phase-aware endpointing (#27).
   4. Read-back re-reads: measure, then decide whether a yes in the last words counts.
   5. The filler line during tool calls; E1 (LLM comparison).

Still open from week 1: `AUDIO_PATH.md` (yours to write), `DECISIONS.md`, and the day 6 items.
