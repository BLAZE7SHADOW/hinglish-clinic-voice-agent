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

## Next task

1. **Voice check:** pick the Cartesia speed from `samples/en/`, and optionally an Indian-English `CARTESIA_VOICE_ID`.
2. **Day 2:** pick a Cartesia voice that handles Hinglish, refine the receptionist prompt, test interruptions with default settings and note what happens, then start `docs/DECISIONS.md` ("Why LiveKit", "Why these providers").
