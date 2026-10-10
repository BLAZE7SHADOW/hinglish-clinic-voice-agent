# Learning Log

Every real problem we hit, written so it can be understood and explained in an interview. Each
entry covers: **what happened** (with evidence), **how it works underneath**, **the options**,
**what we chose and why**, **how we verified it**, and **in industry** (how production
systems handle the same thing). Newest at the bottom.

This is a teaching log. `DECISIONS.md` is different: those answers are written in your own words.

---

## 1. Gemini "thinking" adds delay before the first word

**What happened.** The Gemini plugin leaves the model's thinking on unless you configure it.
Thinking models write hidden reasoning tokens before the answer, and the caller hears silence
while they do.

**How it works.** Voice-to-voice latency is the sum of stages (design §6). LLM *time to first
token* (TTFT) is one of the biggest, and every hidden reasoning token counts toward it. A
receptionist reply ("Sure, one second") needs almost no reasoning, so thinking is cost with no
benefit here.

**The settings depend on the model generation.** Gemini 2.5 takes a token budget
(`thinking_budget=0` turns it off). Gemini 3.x takes a level (`thinking_level="minimal"`), and the
plugin *ignores* a budget on 3.x and logs only a warning. We first wrote `thinking_budget=0`, and
it would have silently done nothing once we switched to `gemini-3.6-flash`.

**Options.**
- Leave thinking on: better at multi-step requests, slower to first word.
- Turn it off or to minimal: fastest. Riskier only for complex multi-tool turns.
- Route per turn: minimal for simple turns, more thinking for complex ones (experiment E2, week 7).

**Chosen.** Minimal thinking, chosen per model family in `agent/providers.py::build_llm`.

**Verified.** Read the plugin source (`livekit/plugins/google/llm.py`) to see which setting each
model generation accepts. A live text call returned a correct Hinglish reply.

**Lesson.** Check what a setting *actually does* in the installed library version. Silent
no-ops are worse than errors.

**In industry.** Production voice agents keep reasoning **off the hot path**. The live turn
uses a fast, non-reasoning (or minimal-thinking) model. Speed also comes from
**architecture**, not just the model:
- **Stream everything:** the first sentence goes to TTS while the LLM is still writing.
- **Preemptive generation:** start the LLM on the final transcript before the turn detector
  confirms the turn is over, and throw the result away if the caller keeps talking. LiveKit
  has this built in, and our logs show `using preemptive generation`.
- **Short prompts, few tools, prompt caching:** an unchanged prompt prefix is cached by the
  provider, which cuts time to first token.
- **Co-located regions and warm connections:** the worker and model endpoint sit close
  together, and connections are reused instead of re-opened per turn.
- **Filler while slow work runs:** "one second, let me check" covers a slow tool or a
  reasoning step.
- **Routing or cascades:** a small model handles simple turns, and a stronger model is used
  only when needed (experiment E2).
- **Speech-to-speech models** (OpenAI Realtime, Gemini Live) merge STT, LLM and TTS into one
  model. That gives the lowest latency, but less control over each stage and over tool
  guardrails.

The target behind all this: in human conversation the typical gap between turns is about
**200 ms** across languages (Stivers et al., PNAS 2009). Voice agents usually aim for
voice-to-voice under about 1 s, and teams track p95, not averages.

---

## 2. The turn detector behaves differently in `dev` and `start`

**What happened.** LiveKit 1.8 deprecated the old turn-detector plugin. The new
`inference.TurnDetector()` *picks its model by run mode*. In `dev` it uses the cloud `v1` model
(it listens to the audio and runs on LiveKit's servers). In `start` it uses the local `v1-mini`
(text only, runs on your machine).

**How it works.** Turn detection answers "has the caller finished?" (design §5.2). VAD only
knows "is someone making sound". The turn detector reads the words (and in `v1`, the audio) and
predicts whether the sentence is complete. It returns a probability. Below a per-language
threshold (0.36 for English, 0.305 for Hindi) the turn counts as *unfinished*, and the agent
waits longer before replying.

**Why the default is a trap.** You would measure latency in `dev` with a cloud model (an extra
network hop) and deploy with a local model. The baseline and production numbers would come from
two different systems, so before/after comparisons would be meaningless.

**Options.** Accept the default. Pin `v1` (cloud, audio-aware, adds a network hop and uses
inference credits). Or pin `v1-mini` (local, consistent, text only).

**Chosen.** Pinned `version="v1-mini"` in `agent/main.py`. Comparing `v1` against it is
experiment E7.

**Verified.** In the English call, "Can you tell me your superior… name?" scored 0.25 (below
0.36, so unfinished). The agent waited 2.5 s and took the whole question as one turn instead of
cutting in.

**Lesson.** Pin anything that affects what you are measuring. "Default" can mean different
things in different environments.

**In industry.** "Dev/prod parity" (from the Twelve-Factor App) is standard:
- **Pin library versions** with lockfiles.
- **Pin model versions** with dated or versioned model IDs, never "latest".
- **Record the exact config with every metric** (model, provider, prompt version), so a
  regression can be traced to a change (design §16.5–16.6). Our turn rows store `providers`
  and `language` for this reason.
- **Benchmark the setup you ship.** A number measured on a different configuration is not
  evidence.

---

## 3. Hinglish speech recognition lost most of the words

**What happened.** First Hinglish call with Deepgram `nova-3` in `language="multi"` mode:
- About 2.8 s of speech became just `"Uh,"`, tagged as English, and arrived 6.2 s late.
- About 9 s of speech became just `"Clinic कब खुलेगा?"`.

**How it works.** Speech recognition models learn from their training audio. A "multi" mode
first has to decide *which language* each stretch of audio is, then transcribe it. Code-mixed
speech (Hindi grammar with English nouns, switching mid-sentence) is the hardest case: the
language guess flips back and forth, and a wrong guess turns real words into filler like "Uh".
Indian accents, Romanized versus Devanagari output, and laptop-mic noise make it worse.

**Options.**
1. Tune Deepgram: monolingual `hi` or `en-IN`, plus keyterms for names and domain words.
2. Use an Indian-first provider: Sarvam `saaras:v4` has a `codemix` mode built for Hinglish.
3. Switch to English for now and come back to Hinglish with a proper test set.

**Chosen.** Option 3 for now, with Sarvam wired as a switchable provider:
- `AGENT_LANGUAGE=en|hinglish` and `STT_PROVIDER=deepgram|sarvam` in `.env`.
- English mode uses Deepgram `en-IN` (Indian-accented English).
- Hinglish returns in week 4 with 30–50 recorded utterances and word error rate per provider
  (experiment E4). That turns "it felt bad" into a number.

**Verified.** English call: every turn was transcribed exactly ("I want to book a appointment.",
the caller's name), with transcript delay 0.4–0.5 s instead of 6.2 s.

**Lesson.** Separate *engineering* problems (latency, tools, interruptions) from *model quality*
problems (Hinglish accuracy), so one doesn't hide the other. Then measure model quality with a
fixed test set instead of judging by feel.

**In industry.**
- **Evaluate on your own audio.** Vendors' published accuracy numbers are on their own test
  sets. Teams record domain audio (accents, mics, background noise, the real phone line) and
  measure **word error rate (WER) per category**: names, numbers, dates, noise.
- **Normalise before scoring code-mixed speech.** The same word can come back in Devanagari
  or Roman script ("खुलेगा" vs "khulega"). Transliterate both sides to one script before
  computing WER, or the score counts script differences as errors.
- **Domain vocabulary:** keyterm or boosting lists for names and product terms.
- **Regional specialist providers** for Indian languages, alongside global ones.
- **More than one STT provider**, with routing per language and fallback on outage
  (design §5.7).
- **Low-confidence handling:** ask again or read back instead of acting on a doubtful
  transcript (design §5.3).

---

## 4. The voice sounded rushed and flat

**What happened.** Cartesia at its defaults kept one steady pace with no pauses, which doesn't
sound like an Indian front-desk call ("Ji… haan, dekhiye…").

**How it works.** Text-to-speech models create *prosody* (rhythm, pauses, pitch movement)
mostly from the **text they receive**. Commas, full stops and short phrases become pauses and
pitch resets. The LLM was writing smooth, comma-light sentences, so the voice had nowhere to
breathe. Speed and emotion settings act on top of that.

**Options.**
- **Speed and emotion settings** (`speed` 0.6–2.0 on sonic-3, `emotion` tags).
- **Write for speech in the prompt:** short phrases, commas where a person breathes, natural
  openers ("Ji", "Achha", "Sure", "Okay"), numbers written as words.
- **A different voice or provider:** Sarvam Bulbul v4-flash has Hinglish customer-service
  voices (`simran_enhi_customer`).
- **Pause markup** in the text (provider-specific tags). Not used yet.

**Chosen.** Cartesia speed 0.9 plus a speech-style prompt, with Sarvam wired as an alternative.
`scripts/tts_samples.py` renders the same lines in every setting, so voices are compared by ear
in minutes instead of over calls.

**Measured tradeoff.** First audio: Cartesia about 150–200 ms against Sarvam about 1 s on the
one-shot HTTP path. Voice quality and speed pull against each other; re-measure Sarvam on its
streaming path in week 4.

**Lesson.** On a voice agent the prompt shapes *how it sounds*, not just what it says. Build
cheap offline loops (sample renders) before expensive live ones (calls).

**In industry.**
- **Choose voices with listening tests.** Teams often use **MOS** (mean opinion score:
  listeners rate clips 1–5) or side-by-side A/B preference tests, not one person's ear.
- **Write the LLM's output for speech:** no markdown or lists, numbers and times as words,
  short sentences. Voice platform docs recommend this, and the text normaliser turns
  "10:30" or "₹500" into speakable words.
- **Fine-tune pacing with markup:** SSML or provider tags for pauses, speed and emphasis
  where the text alone isn't enough.
- **Brand voices:** businesses often clone or design a custom voice so it matches their
  callers' accent and expectations.
- **Track time to first audio next to quality.** The fastest voice is useless if callers
  find it robotic, and the nicest voice is useless if it adds a second of silence.

---

## 5. The agent invented facts and pretended to take actions

**What happened.** Across calls and text tests the agent:
- invented clinic hours ("subah aath baje", then "9 baje" in a different test),
- invented a person ("Our clinic manager is Mr. Sharma"),
- asked for name and phone number with nowhere to store them,
- promised actions nothing performs ("staff will call you back", "hold while I check").

**How it works.** An LLM is trained to give *helpful-sounding* answers. With no facts and no
tools it fills gaps with plausible content. When a rule lists specific forbidden things
(timings, fees, address), the model treats anything *not on the list* as allowed. That's why
"manager's name" slipped through. Fake actions are the same instinct: "let me check" sounds
helpful even when nothing can check.

**Why it's serious.** Design §9: *no fake confirmation*. A caller told "staff will call you
back" waits for a call that never comes. Collecting a phone number that goes nowhere is
personal data gathered for no purpose.

**Options.**
- Prompt rules (fast, weak: the model can still drift).
- Real tools that return true facts (`clinic_info`, `find_slots`, day 4), so the honest answer
  is also the helpful one.
- Code-level guards (design §7.4): actions only happen through tools, and code checks state
  before any of them run. The prompt is not the defence.
- Eval cases that fail if the agent states unsourced facts or claims actions (week 2+).

**Chosen (for now).** General prompt rules: no clinic facts or names of any kind, no asking for
personal details, never claim or imply an action, and say honestly "I can't help with that on
this line yet". Medical questions get no advice, and urgent ones are told to call 108 or go to
the nearest hospital.

**Verified.** A text test of 5 problem questions in English and 3 mixed cases: no invented
facts, no fake promises, the emergency answer was correct, and the Hinglish answer was honest.
Removing the "can't help yet" lines as tools arrive is in `PROGRESS.md`.

**Lesson.** Rules phrased as *general categories* work better than lists. In the end, honesty
comes from tools and code, not from the prompt. Every bug found becomes an eval case
(design §16.2).

**In industry.**
- **Ground answers in data:** facts come from tools or retrieval over the business's own
  data, and the prompt says to answer only from tool results.
- **Actions only through tools,** with permission checks in code. This is the
  **"excessive agency"** risk in the OWASP Top 10 for LLM applications: the model must not
  be able to do more than code allows.
- **Output guardrails:** a rule or classifier checks replies before they are spoken
  (medical advice, personal data, unsupported claims).
- **Human handoff** as the honest fallback, plus regression evals so a fixed hallucination
  can't quietly come back.
- **Show, don't trust:** after a write, read the booking back and confirm from stored data,
  never from what the model intended (design §9).

---

## 6. The worker died while the laptop slept

**What happened.** The worker exited at 13:55 with
`RuntimeError: failed to connect to livekit after 16 attempts`. Cause: the Mac idle-slept at
13:36:46 on battery (`pmset -g log`).

**How it works.** A worker has two separate kinds of connection:
1. **Worker ↔ LiveKit server:** one long-lived WebSocket used to register as available and to
   receive calls ("job requests"). When it is down, no new calls arrive.
2. **Each call ↔ room:** a separate process per call that joins the room over WebRTC (audio
   over UDP).

During sleep the process is frozen and the network is off. On brief background wakes the worker
found its socket dead and tried to reconnect:

| Signal | Meaning |
|---|---|
| `status_code=1006` | WebSocket "abnormal closure": the connection vanished with no goodbye message (network gone) |
| `nodename nor servname provided` | DNS failure: the hostname couldn't even be looked up (no network) |
| `retrying in 0, 2, 4, 6, 8, 10, 10… s` | Backoff with a cap: wait longer after each failure so a struggling server isn't hammered |
| gave up after 16 | Limited retries: fail loudly and let a supervisor restart the process |

**A detail that matters.** The process exited with code **0** ("success") despite failing, so a
supervisor that restarts only on failure would not restart it.

**Options.**
- `caffeinate -i` to block idle sleep while developing (uses battery; closing the lid still
  sleeps the Mac).
- A restart loop (pointless while the Mac is asleep, and it hides real bugs).
- Raise the retry limit (only delays the same failure).
- Deploy the worker to LiveKit Cloud (always on, restarts handled for you; design §13a).

**Chosen.** `caffeinate -i` for development (in `CLAUDE.md`), and LiveKit Cloud for anything
real.

**Verified.** `pmset -g assertions` shows the sleep block held by our process, and the worker
re-registered.

**In industry.**
- **Run long-lived workers under a supervisor:** systemd, Kubernetes, or a managed platform
  such as LiveKit Cloud. Health checks and automatic restarts are standard.
- **Graceful drain on deploys:** stop taking new calls and let active calls finish before
  the process exits.
- **Reconnect with exponential backoff plus jitter** (small random delays), so thousands of
  workers don't reconnect at the same moment after an outage. The AWS Architecture Blog's
  "Exponential Backoff and Jitter" is the classic reference.
- **Exit codes that mean something:** non-zero on failure, so supervisors restart correctly.
- **Separate the control plane** (registration and job dispatch) **from the media plane**
  (the call audio). That's why a dropped worker connection stops *new* calls first.

**Lesson / interview angle.** "Is the worker alive" and "is the call alive" are separate
connections. This is the failure-lab case "agent worker crash mid-call" (design §10.2). Had a
call been live, the caller would have heard silence. The target behaviour is to end the call
cleanly, record an event and start a callback workflow.

---

## 7. The database URL broke on an `@` in the password, and the project was in Sydney

**What happened.** The migration failed with a DNS error, `nodename nor servname provided`.
The `DATABASE_URL` contained two `@` characters, because the password had one. Separately, the
first Supabase project was created in `ap-southeast-2` (Sydney) instead of Mumbai.

**How it works.** A database URL is `postgresql://USER:PASSWORD@HOST:PORT/DB`. The `@`
separates the credentials from the host, so a raw `@` inside the password is ambiguous. Python's
`urlsplit` splits on the *last* `@` (correct here), but the Postgres driver split on the *first*
one. It then tried to resolve part of the password as a hostname, and DNS failed. The error
pointed at the network when the real cause was the string format.

**Region.** I measured a single TCP connect from the laptop: about 0.35–0.58 s to Sydney
against about 0.06–0.23 s to Mumbai. A real query through the Mumbai pooler takes about
105–130 ms per round trip. Telemetry writes are in the background, so they don't care, but day 4
tools read the database *during* the call, and each query adds its round trip to the caller's
silence.

**Options.** URL-encode the password (`@` becomes `%40`). Use a letters-and-numbers-only
password. Or parse the URL robustly in code. Region: recreate the project in Mumbai (free while
there is no data), or keep Sydney and accept the latency on every tool call.

**Chosen.** `agent/db.py::connect_kwargs` splits on the *last* `@`, decodes percent-encoding,
and passes host, user and password to the driver separately, so both forms work. The project
was recreated in Mumbai.

**Verified.** Unit tests cover a raw `@` and an encoded password. The migration applied (26
columns), 4 real rows round-tripped through the worker's insert code, and the report read them
back from the database.

**In industry.**
- **Connection settings as separate fields** (host, user, password from a secret manager)
  rather than one hand-edited URL string. This removes the escaping problem.
- **Secrets never pasted into chat or code;** they're injected at runtime.
- **Data next to compute:** the database, worker and callers in the same region, with read
  replicas or caches when data has to live elsewhere.
- **Connection pooling** (PgBouncer or Supavisor) between apps and Postgres, because each
  Postgres connection is a full server process.
- **Measure a query's round trip early,** before designing anything that runs queries during
  a live turn.

---

## 8. The cloud interruption detector timed out, and barge-in timing went blank

**What happened.** In the day 3 test call the barge-in fields came out empty, although the
caller did interrupt. The worker log showed:
`adaptive interruption disabled due to unrecoverable error, falling back to VAD-based
interruption {"error": "interruption inference timed out"}`.

**How it works.** LiveKit 1.8 has two ways to decide "the caller is interrupting":
- **Adaptive (default):** caller audio that overlaps the agent's speech is sent to a model on
  LiveKit's cloud gateway, which tells real interruptions apart from backchannels ("haan",
  "okay", a cough). It emits `overlapping_speech` events.
- **VAD-based (fallback):** any detected speech over the agent counts as an interruption.
  Simpler, with more false interruptions from noise or backchannels.

The cloud model timed out, so LiveKit **degraded gracefully** to VAD. The agent still stopped
talking. My telemetry only listened for `overlapping_speech`, which VAD mode doesn't send, so
it measured nothing. That was a measurement bug that depended on a cloud feature being
healthy.

**Options.**
1. Measure only in adaptive mode (fragile: the number disappears exactly when things go wrong).
2. Measure from signals both modes share: the caller entering *speaking* while the agent is
   speaking, until the agent leaves *speaking*.
3. Turn off the cloud detector (removes a network dependency, but loses backchannel filtering,
   which the design wants in week 3).

**Chosen.** Option 2. Onset comes from the adaptive detector's precise speech start when
available, and otherwise from `user_state_changed`. The end is `agent_state_changed`. The VAD
onset is slightly late, because VAD needs a short stretch of speech before it fires, so VAD-mode
stop times read a little *better* than reality. Note that when comparing runs.

**Verified.** Unit tests cover both modes, and a test confirms that a caller speaking while the
agent is silent is not counted as barge-in. Confirm on the next live call.

**In industry.**
- **Every cloud dependency on the hot path needs a timeout and a fallback.** That's exactly
  what LiveKit did here (design §5.7). Teams count and alert on how often fallbacks fire,
  because a silent downgrade still changes quality: here, more false interruptions.
- **Telemetry must not depend on the happy path.** Measure from signals that exist in every
  mode, and record *which* mode was active, so numbers from degraded periods aren't mixed with
  healthy ones.
- **Backchannel filtering** (ignoring "uh-huh" and "haan") is now a standard feature of
  production voice stacks. Its false-interruption rate is a headline metric (design §15).

---

## 9. Every call was marked "cold", and the baseline report came back empty

**What happened.** After 11 baseline calls the report showed 0 turns. All 40 rows had
`cold_call = true`, so the report's cold filter excluded everything.

**How it works.** I defined "cold" as "the first call this process serves", using a counter in
the Python module. But a LiveKit worker runs **every call in its own process**. The main worker
process keeps a small pool of pre-started processes and hands each call a fresh one, so a
crashing call can't take down others. In every process the counter was 0, so every call looked
like the first. The unit tests passed because they built `TurnBuilder` directly and never ran
the real process model.

**Options.** Count calls in the main worker process (not reachable from the call's process).
Mark the first call after worker *start*. Or mark the first call after the worker sat *idle*,
which is what week 1 means by cold start.

**Chosen.** Cold = no call ended in the last 10 minutes, tracked with a marker file's
modification time (`logs/.last_call_end`) that lives outside any one process. Separately,
greeting turns are excluded from the report by default: no caller is waiting on them, and each
call's first LLM and TTS request also pays connection setup (greeting TTFT was about
1.3–1.6 s in every call). Today's rows were corrected: the warm-up and the earlier test call
stay cold, and the 11 later calls are warm.

**Verified.** A unit test covers the idle rule (no marker, a fresh marker, a stale marker), and
the report now shows 10 calls and 22 turns.

**In industry.**
- **Process isolation per session** is the normal design for real-time agents (LiveKit,
  serverless functions, many game servers): one crash can't take down other calls, and memory
  is reclaimed. The cost: no in-memory state is shared between calls. Anything cross-call
  (counters, caches, rate limits) lives outside the process, in Redis, a database, or the
  supervisor.
- **Test telemetry on the real runtime,** not just units. Many metric bugs only appear under
  the real process and concurrency model.
- **Separate "first request on a new connection" from steady state.** Teams warm connections
  (prewarm, keep-alive, connection pools) and report cold-start latency separately.

---

## 10. The turn detector made the agent wait 2.5 s after complete sentences

**What happened.** Baseline end-of-turn: p50 578 ms but **p95 2,501 ms**. In 6 of about 22
caller turns the agent waited the full 2.5 s after a *complete* sentence: "Good morning."
(score 0.26), "I want to see doctor Sharma tomorrow." (0.34), "Can you tell me about the
clinic?" (0.31). The one deliberate mid-sentence pause ("I need an appointment for…", 0.09)
was correctly held, so call 6 worked.

**How it works.** After VAD hears silence, the turn detector (`v1-mini`, text-only) reads the
transcript plus recent context and predicts the probability the caller has finished. If that
probability is below a per-language threshold (0.36 for English), LiveKit waits up to
`max_endpointing_delay` (2.5 s) for more speech. If it's above, it waits only
`min_endpointing_delay` (0.3 s). So every false "unfinished" adds about 2.2 s of silence.
Possible reasons it misjudged:
- A small text-only model can't hear the falling intonation that marks the end of a sentence.
- Indian-English phrasing differs from its training data.
- It sometimes scored only the last STT fragment ("…just the address.", "…Wait. Make it six.").

**The tradeoff (design §5.2).** Cut in too early and you interrupt callers who pause
("mera appointment kal… nahi, parson"). Wait too long and every reply feels slow. Here the
detector was cautious on almost a third of turns.

**Options (experiment E7, not applied yet: baseline first).**
1. Cloud `v1` turn detector: audio-aware, so it hears intonation. It adds a network hop and
   is another cloud dependency (see entry 8).
2. Lower `max_endpointing_delay` (for example 1.2–1.5 s): caps the damage of a wrong
   "unfinished", at the risk of cutting into real pauses.
3. Tune the per-language threshold: shifts the balance; needs the pause test set to check
   for regressions.
4. Use STT end-of-speech signals together with the detector.

**Measure each option on two things:** false cut-offs on the pause set (call 6, and the
Hinglish pause set in week 4) *and* the end-of-turn p95. Improving one by breaking the other
doesn't count.

**E7 result (same day): option 1 did not help.** Re-running the same 10 calls with `v1`:
- 5 false waits against 6 with `v1-mini`, and they were *different* sentences each time. Both
  held the real pause.
- Voice-to-voice p95 got worse.

So the detector model isn't the main lever for us. **What the research says** (LiveKit
docs, and a LiveKit community thread with the same symptom, 2–3 s after short replies like
"Yes"/"Okay"):
- With **fixed** endpointing, waiting the full `max_delay` after a short or ambiguous reply is
  *expected* behaviour: a single word gives the model too little to go on (LiveKit staff
  reply).
- **Dynamic endpointing** (`endpointing={"mode": "dynamic", ...}`, Python only) shortens the
  effective wait using the session's own pause statistics. One user reported it works better
  for short replies (anecdotal, no numbers). Catch: it learns *during* a call, and our test
  calls have only 2 caller turns, so the test set has to contain longer calls to judge it.
- **Change endpointing at runtime by dialogue phase:** a short `max_delay` right after a yes/no
  question, a long one while the caller reads out a date or phone number. This fits our design
  directly: the call-state machine (week 2) knows the phase, so the endpointing can follow the
  mode.

**Decision.** Revert to `v1-mini`. Do phase-aware endpointing (with dynamic mode as a
comparison) in the week 3 optimization pass, once modes exist. Test it on a set that includes
multi-turn calls and the pause cases.

**In industry.**
- **Semantic and audio turn detection** has replaced fixed silence timeouts in production
  stacks: LiveKit's turn detector, and Pipecat's open, audio-native Smart Turn model.
  LiveKit's own posts report fewer false interruptions from audio-aware models.
- **End-of-turn decisions are logged with their inputs** (LiveKit added built-in turn-decision
  observability), so tuning comes from data like the table above, not guesswork.
- **Language-specific tuning:** thresholds and delays differ per language, and LiveKit ships
  different thresholds for English and Hindi. Teams re-tune on their own callers.


---

## 11. Soft backchannels ("okay", "ohh") interrupt the agent

**What happened.** In both runs a soft "okay" while Simran was talking stopped her mid-sentence.
In the E7 run, LiveKit's adaptive interruption model scored the soft **"Okay." at 0.709** and
an **"ohh"** (transcribed as "So") at **0.658**. The threshold is **0.656**, so both
interrupted; the "ohh" missed by 0.002. Separately, the cloud detector timed out in two calls
on day 3, and the VAD fallback interrupts on *any* speech (LEARNING.md #8).

**How it works.** A backchannel is the listener's "I'm with you" signal ("haan", "okay", "hmm").
In human conversation it is not a bid for the turn. When the caller makes a sound over the
agent:
1. VAD hears speech.
2. The interruption policy decides: stop, or keep talking? LiveKit's **adaptive** mode sends
   about the first few hundred ms of overlapping audio to a model that scores "real
   interruption" from 0 to 1. **VAD** mode stops on any speech over `min_duration` (0.5 s).
3. If it stops and no words follow within `false_interruption_timeout` (2 s), it's a false
   interruption, and with `resume_false_interruption` the agent resumes.

Soft acknowledgements sit right on the model's decision line, so small differences (volume,
"okay" vs "ohh") flip the outcome.

**Options** (LiveKit 1.8 `InterruptionOptions`, verified in the installed source):
1. `min_words=2`: require at least 2 transcribed words to interrupt. One-word backchannels
   can't stop the agent. Costs: the decision waits for STT text (slower real barge-in), and a
   one-word "Stop!" or "Wait" no longer works, which matters in a booking flow.
2. A **backchannel word list** applied only while the agent speaks ("okay", "haan", "hmm",
   "ji"). Not built into LiveKit 1.8 (open GitHub feature request #4450). We'd build it, as
   design §5.6 already plans ("your own backchannel filter").
3. Tune the adaptive threshold, or the `backchannel_boundary` window (backchannels near the
   start and end of the agent's turn are already suppressed for 1 s by default).
4. Better interruption *recovery* rather than prevention: resume after a false interruption
   (on by default).

**Chosen.** Nothing yet; this is week 3 work (design §5.6). Today's logs give the first
regression cases: soft "okay" = 0.709 and "ohh" = 0.658 must *not* interrupt, and "Sorry, just
the address" (call 5) *must*. The likely design is option 2 (a word list scoped to agent speech,
including Hinglish "haan / ji / achha") plus option 1 as a fallback when the cloud model is
unavailable.

**In industry.**
- **Audio models first, word rules as backup.** LiveKit's adaptive mode, and several STT vendors
  with end-of-turn models (Deepgram Flux, AssemblyAI Universal streaming), separate barge-in
  from backchannel on audio.
- **Transcript filters with a minimum word count:** AssemblyAI's guide suggests
  `MIN_WORDS = 2` and deliberately excludes "yes" and "no", since those can be real
  confirmations.
- **Ignore lists only while the agent is speaking:** Twilio notes that "yeah" after the agent
  finishes is a real turn.
- **Retune per language and market,** since backchannel habits differ: Hindi "haan" and "ji"
  are far more frequent than English "uh-huh".
- **Track false yields and swallowed interruptions as metrics, from audio,** because these
  failures are invisible in transcripts (Decagon, Hamming).
- Vendor improvement figures (for example "30–50% fewer false interruptions") are vendor
  claims; benchmark on your own calls.

---

## 12. The agent's last line sounded rhythmically odd (call 9, E7 run)

**What happened.** The caller heard a rhythm glitch in "You are most welcome. Is there anything
else I can guide you with today?". The log shows **no** interruption, pause or resume in that
call, so the conversation logic played it straight through. The cause is in the audio
generation.

**How it works.** Possible causes:
1. **Sentence chunking:** LiveKit streams the LLM text to the TTS sentence by sentence, so the
   first word plays sooner (LEARNING.md #1). Prosody can reset at each sentence boundary.
2. **Voice model:** sonic-3 at speed 0.9, with a voice chosen for Hindi now speaking English.
3. Plain model variance: the same text can render a little differently each time.

**Next step: reproduce before fixing.** `samples/en/rhythm/` has the exact line rendered three
ways: as in the call (speed 0.9, your voice), speed 1.0 (your voice), and speed 0.9 with the
plugin's default voice. If the glitch is in all three, it's chunking or the model; if only in
yours, it's the voice choice.

**In industry.**
- **Voice evaluation uses a fixed script, rendered and rated** (MOS or A/B listening). It
  isn't judged from one live call, because single renders vary.
- **Pacing markup:** Cartesia sonic-3 supports `<break time="…"/>` and `<speed>` tags.
  Stream-safe use means buffering the whole tag so its digits aren't spoken.
- **Chunking strategy is a latency vs naturalness dial:** smaller chunks give faster first
  audio but more prosody resets. Some stacks use larger first chunks once latency allows.

---

## 13. `find_slots` was slow because of round trips, not queries

**What happened.** The first version of `find_slots` took **680–1,170 ms**. The queries are
tiny (one doctor, two weeks of rows); the time went on the network.

**How it works.** Each query is a round trip from the worker to the database and back, about
110–170 ms from the laptop to Mumbai through the pooler. The tool ran **4 queries one after
another** (doctors, hours, days off, bookings), so it paid 4 round trips. The first call also
opened new database connections, each costing about 360 ms (TCP plus TLS plus authentication).
Query execution itself was a few milliseconds.

**Options.**
- Run independent queries **in parallel** (`asyncio.gather`): 3 queries cost 1 round trip.
- **Cache** data that doesn't change during a call (the doctor list).
- **Open connections before they're needed** (at call start, in the background).
- Fold everything into one SQL query or a database function (fewest round trips, harder to read).
- Move the worker next to the database (LiveKit Cloud and Supabase, both in India).

**Chosen.** The first three: hours, days off and bookings run in parallel; doctors are cached
for 60 s; the pool opens all 3 connections at call start. Result: **about 200 ms** per call.

**Verified.** Timed against the seeded Mumbai database: 839 ms on the first call before the
pool fix, then 195–217 ms on each call after.

**In industry.**
- **Count round trips, not queries,** on any latency-critical path. A sequence of small
  dependent calls that each cross the network is called the "N+1 query" or "chatty I/O"
  problem; batching, parallelism and caching are the standard fixes.
- **Connection pools are warmed at startup,** so no user request pays connection setup.
- **Put compute next to data:** in production the worker and the database sit in the same
  region, often the same data centre, where a round trip is about 1 ms rather than 100+ ms.

---

## 14. A tool turn costs two LLM calls

**What happened.** In the text-mode check, questions that used a tool took **3.1–4.8 s** end to
end, against about 1.5 s for plain replies.

**How it works.** With tool calling, the LLM is called **twice** for one caller turn:
1. Request 1: the model reads the conversation and replies with a **tool call**
   (`find_slots(doctor="Dr. Sharma", when="kal shaam")`). Nothing is spoken yet.
2. The code runs the tool (about 200 ms).
3. Request 2: the model reads the tool result and writes the **spoken answer**. Only now does
   TTS start.

So the caller waits about two LLM first-token times (2 × about 1.3 s) plus the tool before
hearing anything.

**Options.**
- **A filler line** while the tool runs ("ek second, main check karti hoon") (design §5.5).
  This doesn't make the answer faster, but it ends the silence.
- **A faster LLM** (experiment E1): it helps both calls.
- **Answer from the tool result in code** for simple cases (template the reply instead of
  calling the LLM again). Fastest, but less natural.
- **Speculative tool calls:** start `find_slots` when the transcript clearly asks for
  availability, in parallel with the LLM's first call.
- **Smaller prompt and tool list** per mode, which helps the first call (design §6, step 3).

**Chosen.** Nothing yet. Day 5 measures it on voice; the filler and E1 are the first candidates.

**In industry.**
- **Fillers and acknowledgements during tool calls** are standard in production voice agents,
  often triggered automatically when a tool takes longer than a few hundred ms.
- **Fast models for tool selection,** with routing or speculative execution, are common ways to
  hide the second LLM call.
- **Teams measure tool turns separately from plain turns,** because averaging them hides the
  slowest and most important moments (checking availability is the reason the caller rang).
