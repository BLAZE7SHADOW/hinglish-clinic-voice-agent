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

**Chosen.** Built early, on 2026-10-11, after the day 5 run showed it again (see #15). Originally planned for week 3 (design §5.6). Today's logs give the first
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

---

## 15. The backchannel filter (built, day 5)

**What happened.** Day 5, call 10: while Simran listed what she could do, a soft "okay" scored
**0.69** against the 0.656 threshold. She stopped at "…and fees. I can", and never continued.
The caller had to say "Continue. Continue." (#11 has the background.)

**How it works (traced in LiveKit 1.8.6 source, `voice/agent_activity.py`).** It's a
two-step failure:
1. The adaptive model flags the overlap as an interruption, so LiveKit **pauses** the agent's
   audio (it doesn't cancel it yet) and starts a 2 s "false interruption" timer.
2. Deepgram's **final transcript** "Okay." arrives. In `on_final_transcript`, LiveKit treats any
   final transcript as proof of a real interruption, so it **interrupts the paused speech**.
   After that, there's nothing left to resume.

So raising the audio threshold alone can't fix it, because the words stage kills the speech
anyway. The fix has to look at **what was said**.

**Options.** These are compared in #11: raising the threshold (would also block call 8's real
answer, which scored 0.68), `min_words=2` (blocks "wait" and "ruko"), or a word list.

**Chosen.** A word list in `agent/backchannel.py`, active only while agent speech is in
progress:
- **The words:** okay, hmm, right, yeah, haan, ji, achha, theek hai, plus Devanagari forms for
  Sarvam's codemix output. Words that ask her to stop (wait, no, nahi, ruko, sorry) are never
  on the list.
- **On the final transcript:** if it's only backchannel words, the pause is kept (not turned
  into an interruption), and the resume timer is shortened to 0.5 s.
- **At end of turn:** the turn is dropped and no reply is generated. This is the same path
  LiveKit's own `min_words` gate uses.
- **Then:** LiveKit's false-interruption timer resumes her speech **where it paused**.

LiveKit has no public hook for this; the hook it offers, `on_user_turn_completed`, runs *after*
the speech is already interrupted. So we wrap two internal methods. `tests/test_backchannel.py`
checks their names and parameters, so a LiveKit upgrade that changes them fails a test rather
than silently switching the filter off.

**Known limit (fix in week 2).** "Yes", "yeah", "haan" and "ji" are on the list. When booking
asks "shall I book 5 PM?" and the caller says "haan" while she's still finishing her sentence,
that "haan" is a **confirmation**, not a backchannel. When a read-back confirmation is pending
(`CallState.slot` set and not yet confirmed), confirmation words must be taken out of the
filter.

**Verification.**
- Unit tests (30 cases): backchannels in English, Hinglish and Devanagari are dropped. "Wait", "ruko",
  "no", "Okay, wait", "Sorry, just the address" and replies after she finishes all still go
  through.
- Re-test on voice: calls 5 and 10 (call 5 must still stop, call 10 must not).

**In industry.**
- **Vapi and Retell ship a backchannel/ignore word list as a setting,** applied only while the
  agent speaks. That's the same design as ours.
- **Contact-centre systems also make the list depend on the conversation state.** During a
  yes/no confirmation, "yes" is a real answer. This is our week 2 item.
- **Wrapping framework internals is a known trade-off:** teams do it when the framework lags
  behind a need (here, LiveKit GitHub issue #4450), and pin the version with a test exactly as
  we did. The longer-term fix is to upstream it, or move to the public hook once it exists.

---

## 16. STT misheard "Sharma" as "Harmer" and "Sharpa": fixed with per-clinic keyterms (E9)

**What happened.** Day 5, call 8: the caller said "Doctor Sharma" twice. Deepgram returned
"Doctor, Harmer", then "doctor Sharpa". `find_slots` returned `doctor_not_found` both times. The
LLM only recovered on the third try.

**How it works.** STT picks the most likely word sequence for the sound. Indian surnames are
rare in mostly-English training data, so nearby common words win ("Harmer"), or the name is
dropped entirely ("I want to see doctor tomorrow").

**First attempt, and why it was wrong.**
- **What I did:** fuzzy string matching in code, with a 0.65 similarity threshold.
- **The flaw (the user caught it):** the threshold was picked from the two mishearings in our
  own call. That's fitting a rule to two examples, not measuring it.
- **Evidence it didn't generalise:** it also matched "Iyengar" to Iyer and "karma" to Sharma.
  Plain character similarity knows nothing about how names *sound*.
- **Outcome:** removed. Lesson: **test calls are evidence and regression cases, never the
  tuning set.**

**Options (what production systems do).**
1. **Custom vocabulary at the STT, built from the business's own records.** The production
   standard: Deepgram keyterms, Google speech adaptation, Azure phrase lists.
2. **Let the caller choose:** when the name doesn't match, list the clinic's doctors and ask.
   This works for any clinic and can never pick the wrong doctor silently.
3. Sound-based (phonetic) matching in code. Only worth adding if it's measured on a labelled
   set and still confirmed aloud.

**Chosen.** 1 and 2.
- **Keyterms:** `agent/vocabulary.py` builds them from the DB (doctor full names, surnames, clinic
  name) after the call starts. They're applied with the public `session.update_options(keyterms=...)`,
  so the greeting isn't delayed, and the call works without them if the DB fails. Switched by
  `STT_KEYTERMS`, and recorded in every turn's `providers` label.
- **The prompt:** on `doctor_not_found` or `doctor_ambiguous`, ask which listed doctor they mean;
  never pick one.

**E9 measurement** (`scripts/name_stt_eval.py`, 2026-10-11). A synthetic A/B test that doesn't
use our calls:
- **Names:** 24 Indian surnames. 3 synthetic clinics, each with a random roster of 6 doctors.
  The other 18 names are callers asking for doctors *not* on the roster.
- **Audio:** 3 sentence templates, 2 TTS voices (Cartesia, Sarvam Indian English), browser
  16 kHz and phone 8 kHz.
- **STT:** Deepgram nova-3 en-IN, with and without that clinic's keyterms.

| Channel | Keyterms | Roster names right | Off-roster names right | Off-roster turned into a roster name |
|---|---|---|---|---|
| browser | off | 75.2% (n=101) | 77.2% (n=281) | 0.0% |
| browser | **on** | **99.0%** | 66.9% | 1.8% |
| phone | off | 61.4% | 70.1% | 0.4% |
| phone | **on** | **99.0%** | **39.1%** | **5.0%** |

**What it means.**
- **The gain is large:** for the clinic's own doctors (almost every real call), recognition
  goes from 75% to 99% in the browser, and from 61% to 99% on phone audio.
- **The cost is real too:**
  - **Names not on the roster get worse**, especially on the phone (70% → 39%). Often the name
    is dropped entirely, which is harmless: she asks "which doctor?".
  - **Sometimes a non-roster name becomes a roster name** ("Nair" → "Iyer"): 1.8% in the
    browser, 5% on the phone. This is the dangerous case.
- **Decision:** turn keyterms on, because the benefit is on the common case. The risk is
  covered by a rule that's already required: every slot answer names the doctor, and every
  booking reads back doctor, date and time before writing. So a hijacked name is heard and
  corrected, never silently booked.
- **Re-measure in week 4** on real phone audio. If the hijack rate holds at 5%, try fewer
  keyterms (surnames only), or ask a confirming question when the doctor came from a fuzzy
  context.

**Limits of this measurement.**
- TTS voices are cleaner than real callers.
- It used the prerecorded API, while live calls stream.
- 19 Cartesia sentences couldn't be rendered (the Cartesia account returned 402 Payment
  Required), so Sarvam's voice is over-represented.
- Use these numbers to rank the options. A short live check confirms them.

**In industry.**
- **Custom vocabulary is the main lever for names,** with the vocabulary built per tenant from
  their own data (contacts, products, staff). Fuzzy or phonetic matching is a second layer,
  used only with confirmation, and only after it's measured.
- **Teams measure "boosting" for both effects:** recall on the boosted terms *and* false
  substitutions on everything else. Over-boosting a vocabulary is a well-known failure, and the
  table above shows it.
- **Confirmation turns for critical entities** (names, dates, amounts) are standard in voice
  booking and payments flows. They make STT errors recoverable.

---

## 17. Fallback providers, and the timeout that would have broken every call

**What happened.** We added LiveKit `FallbackAdapter`s:
- **STT:** Deepgram, then Sarvam.
- **LLM:** Gemini 3.6 Flash, then Gemini 2.5 Flash.
- **TTS:** Cartesia, then Sarvam.

The first chaos test (a deliberately non-existent primary model) showed the switch happening,
and then **the backup failed too**, with a 400: *"Manually set deadline 5s is too short.
Minimum allowed deadline is 10s."*

**How it works.**
- **Why fallbacks:** without them, any one provider's outage, rate limit or 5xx means a dead
  call. The adapter tries providers in order and marks a failed one unavailable. It keeps
  checking that provider in the background and switches back when it recovers.
- **What went wrong:** the adapter's `attempt_timeout` isn't only a local timer. The Google
  plugin sends it to Gemini as the request **deadline**, and Gemini rejects deadlines under
  10 s. With `attempt_timeout=5.0`, **every** request through the adapter would have failed,
  primary included. A config meant to make calls safer would have broken all of them. Only the
  chaos test caught it: the unit test checked the wiring, not a real request.

**Options.** Set `attempt_timeout` to at least 10 s, or leave the LLM without an adapter.

**Chosen.** `LLM_ATTEMPT_TIMEOUT_S = 10.0`.
- **Hard errors** (4xx/5xx, connection refused) still fail over immediately. The chaos test
  switched in about 2.2 s, including the backup's own answer.
- **Only a request that hangs** waits the full 10 s.
- The TTS adapter retries once (`max_retry_per_tts=1`) before switching, because every retry
  is dead air.
- Each switch is logged as "provider failed, falling back: <provider/model>".

**Known limits.**
- The backup voice (Sarvam) sounds different from Cartesia.
- The LLM backup is the same company: it covers a model failure, not a Google-wide outage. A
  second provider's key would cover that.
- STT failover wasn't chaos-tested with live audio; it uses the same adapter mechanism, and the
  wiring is unit-tested.

**Verification.**
- **Real requests:** primary broken → backup answered in 2.2 s; normal path 1.8 s.
- **TTS:** Cartesia key broken → Sarvam produced the audio.
- **Unit tests:** `tests/test_providers.py` checks the order of providers in each chain and
  that a backup is never the same as the primary.

**In industry.**
- **Multi-provider fallback is standard** for production voice (LiveKit, Vapi and Retell all
  offer it). Teams also track "fallback activations" as a metric, since a silent failover can
  hide a degrading primary.
- **Chaos testing** (break a key or a model name on purpose) is how teams prove a fallback
  works. Config that only runs during outages is otherwise untested until the worst moment.
  This entry is a small example of why.
- **Timeouts are tuned per provider:** each API has its own minimums and limits, so a single
  global timeout is a common source of exactly this bug.


## 18. Modes on one agent: switching in place, not handing off to a new agent

**What happened.** Week 2 needs modes (greeting/FAQ, booking, rescheduling, cancelling,
handoff), each with its own short instructions and only its own tools (design §7.1). Until now
the agent had one prompt and both read tools in every state, and the typed call state in
`agent/state.py` wasn't used during a call.

**How it works (LiveKit 1.8, read from `voice/agent_activity.py` and `voice/agent.py`).**
- **The handoff path:** a tool returns a new `Agent`, and the session builds a new
  `AgentActivity` for it. The new agent's `on_enter` usually speaks first, so the old agent's
  reply to the tool is dropped. Chat history has to be passed over explicitly (`chat_ctx=`).
- **The in-place path:** `Agent.update_instructions` and `Agent.update_tools` change the
  running agent. The reply right after the tool call already uses the new instructions (LiveKit
  refreshes them, issue #4242), but it keeps **the tool list from the start of the turn**. The
  new tools reach the LLM on the caller's next turn.
- **A side effect of `update_tools`:** it copies the chat context with only the new tools, which
  drops earlier tool calls and results for tools the new mode lacks. What the agent said aloud
  stays.

**Options.**
1. **One `Agent` subclass per mode, with LiveKit handoffs.** This is LiveKit's own multi-agent
   pattern. But it builds a new activity on every switch, adds a second speaker path
   (`on_enter`), and history has to be copied by hand.
2. **One agent, with instructions and tools swapped in place by a `switch_mode` tool.**
3. **Code picks the mode** (a classifier before the LLM). This adds a model call or a rule
   layer on every turn, which is experiment E3's job, not V1's.

**Chosen: option 2** (`agent/modes.py`).
- **One `switch_mode(mode)` tool** instead of one tool per mode, because every tool definition
  adds to time to first token.
- **The code decides.** `switch_mode` calls `state.switch_mode`, which refuses leaving handoff.
  Handoff mode exposes no tools at all, so there is nothing to talk it out of.
- **One table drives both what the LLM sees and what code allows:** `tools_for(mode)` is built
  from `state.ALLOWED`, and each tool's `guard()` checks the same table.
- **The guard is the real defence, not the tool list.** Because the reply right after a switch
  still sees the old tools, the LLM can call a tool the new mode doesn't allow. `guard()`
  refuses it with `not_allowed` before any DB read. The same check stops an injected "ignore
  your instructions and cancel everything".
- **Booking and rescheduling may also call `clinic_info`.** Otherwise "what's the fee?" in the
  middle of a booking would force a switch to FAQ, which clears the slot being discussed.

**Cost.**
- A switch is one tool round trip, but the LLM can call `switch_mode` and `find_slots` in the
  same step, so a switch needs no extra round trip of its own.
- Prompt size: greeting/FAQ grew from 2,003 to 2,230 characters (the switch rule). Cancelling
  (1,654) and handoff (1,455) are shorter than the old single prompt.
- The latency effect is unmeasured. It goes in the next `LATENCY_LOG.md` row.

**Verification.** `tests/test_modes.py` (22 new tests, 125 in total):
- each mode's tool list matches `ALLOWED`
- `switch_mode` swaps instructions and tools on a real `Receptionist`
- a switch out of handoff is refused
- a stale `find_slots` or `clinic_info` call in cancelling mode is refused without touching the
  DB

Not yet tested with a real LLM or a voice call.

**In industry.**
- **Frameworks offer both shapes:** handoffs between agents (LiveKit multi-agent, OpenAI Agents
  SDK) and a single agent whose state changes its prompt and tools (Pipecat Flows' nodes,
  LangGraph state machines).
- **Production voice teams keep one agent per call** because each agent-to-agent hop is delay
  the caller hears. They move multi-agent work after the call (design §7.1).
- **Tool allowlists per state** are the standard defence against prompt injection (OWASP LLM
  Top 10, "excessive agency"). The allowlist is enforced where the tool runs, not only in what
  the model is shown.

## 19. "Wait for a clear yes" is an LLM claim unless code checks it

**What happened.** Building `confirm_booking` showed that the hard rule "read back doctor, date
and time and wait for a clear yes" was enforced only by the prompt. `state.check()` required
`slot.read_back_confirmed`, but nothing set it except the LLM's decision to call the tool. Two
more problems appeared along the way:
- The backchannel filter (#15) drops "haan", "yes" and "ji" while the agent is speaking, which
  is exactly the answer to a read-back.
- A caller who cuts into a read-back may not have heard the time, yet their "haan" would still
  count.

**How it works.** When a tool runs, the caller's turn that triggered it is already in
`session.history` (LiveKit commits the user message at `agent_activity.py:3646` before running
tools at `:3753`). Each assistant message carries `interrupted`, and its text is cut to what was
actually played.

**Options.**
1. Trust the LLM (prompt only).
2. A separate confirmation tool (`caller_said_yes()`): still the LLM's claim.
3. **Check the transcript in code** at the moment of the write.
4. An intent classifier for yes/no: more accurate on unusual phrasing, but it adds a model call
   on the write path. A candidate for E3.

**Chosen: option 3** (`agent/confirm.py`). The write goes ahead only if all of these hold:
- the caller's latest turn came after the hold
- the agent message just before it was not interrupted
- that turn is a clear yes: at least one yes word, no word meaning no, wait or change, and at
  most 12 words

The lists cover English, Romanized Hinglish and Devanagari. "na" is deliberately not a no word,
because "haan na" is an emphatic yes.

While a read-back is pending (`CallState.awaiting_yes`), the backchannel filter steps aside. An
early "haan" then interrupts the read-back, the check refuses the write, and the agent reads it
back again in full.

**Asymmetry is the design.** A missed yes costs one more "shall I book it?"; a false yes books the
wrong slot. So anything mixed is a no.

**Known limits.**
- Code knows a read-back played, not that it named the right time (`THREAT_MODEL.md` gaps).
- A "yes" spoken over the last word of the question counts as an interruption and forces a
  re-read. Voice testing must measure how often that happens.

**Verification.**
- `tests/test_confirm.py`: 21 cases, including interrupted read-backs, mixed answers and a yes
  that came before the hold.
- `test_write_tools.py`: confirm, cancel and reschedule refusals.
- `test_yes_words_are_answers_while_a_read_back_waits`.

**In industry.**
- IVR systems have always used constrained yes/no grammars at confirmation points, with
  re-prompts on no-match. This is the same idea applied to free speech.
- Voice platforms (Vapi, Retell, Bland) mostly leave confirmation to the prompt. Teams that care
  about wrong actions add a deterministic or classifier gate at the write, and log every refusal
  so they can tune the word lists from real calls (week 4, phone audio).

## 20. An expired hold still blocks the slot in the unique index

**What happened.** While writing `hold_slot`, the unique index `bookings_one_active_per_slot`
covers `status in ('held', 'confirmed')`, with no expiry condition. `find_slots` already treats
an expired hold as free. So it would offer the time, and the next caller's insert would hit the
index and fail as "slot taken". An abandoned hold would block a slot forever.

**How it works.** A partial index predicate can't use `now()`: index predicates must be
immutable, and time moves on. So expiry can never be part of the constraint itself.

**Options.**
1. A sweeper job that deletes expired holds every minute. This leaves a window, and needs a
   scheduler (Temporal arrives in week 5).
2. **Clear the expired hold for that exact slot inside the transaction that places the new
   hold.**
3. Redis holds with a TTL, and only confirmed rows in Postgres. That adds a second store and
   moves the double-booking guard out of the database.

**Chosen: option 2,** plus releasing a call's holds when it ends (`release_call_holds`, keyed by
the new `bookings.call_id`). This is cheap, and the index stays the final guard.
- **The race:** two callers clearing the same expired row both try the insert, and the index lets
  only one win. The other gets `slot_taken`.
- **Confirming is safe even after expiry:** a confirm accepts our own held row even if it has
  expired, as long as it still exists. If it exists, nobody else has taken the slot.

**Verification.** `test_an_expired_hold_frees_the_time_and_call_end_releases_holds` and
`test_two_calls_cannot_hold_the_same_time`, in `tests/test_booking_db.py`. These run against the
eval sandbox only and haven't run yet: no sandbox database exists.

**In industry.**
- Ticketing and booking systems (seat maps, restaurant booking) use short holds with expiry. The
  common designs are a TTL store (Redis `SET NX PX`) or a DB row with lazy cleanup on contention
  plus a background sweeper.
- Postgres shops often use an exclusion constraint on a time range for overlapping appointments.
  Here a unique index on the start time is enough, because slots have fixed lengths.

## 21. Evals need their own database and a frozen clock

**What happened.** The text eval runner has to make real bookings to test booking, but the hard
rule says evals never touch real systems. The schedule is also seeded relative to today, so
"tomorrow evening" means different slots every day.

**Options.**
- **Database:**
  - a separate schema in the same database (the Supabase pooler doesn't reliably carry
    `search_path`)
  - a test clinic in the same database (one missing `clinic_id` filter away from a real
    booking)
  - mocking the DB (no unique index, so the most important property goes untested)
  - **a separate database**
- **Clock:** real time, or **frozen time in eval mode only**.

**Chosen.**
- **Database:** `EVAL_DATABASE_URL`, a separate database. `evals/sandbox.enter()` swaps it in, and
  with `EVAL_MODE=1` every connection checks it isn't the real one (`db.assert_safe_target`).
  Seed and migrate check it too.
- **Clock:** `dates.now()` honours `AGENT_FAKE_NOW` only when `EVAL_MODE=1`, so a stray variable
  can't move a real caller's "kal".
- **Isolation:** the sandbox is re-seeded before every scenario, so scenarios don't affect each
  other.
- **Checks are on outcomes:** event rows per call and tool names and arguments, not reply wording
  (§16.2). Wrong actions and verification violations fail the run (exit code 1).
- **Intent labels:** split dev/hold-out per intent (stratified), by hash so the split never
  changes. A plain hash split had left "cancelling" with 1 dev example and 4 hold-out.

**Verification.** `tests/test_evals.py` covers the scoring rules, the refusal without a sandbox,
the connect-time check and the intent data. Running without `EVAL_DATABASE_URL` stops before any
LLM call. The scenarios themselves haven't run yet (they need the sandbox and an LLM key; testing
was deferred).

**In industry.** Separate test and staging databases are standard. Frozen clocks (freezegun,
time-travel fixtures) are how teams make date logic testable. Voice-agent eval tools (Coval,
Hamming, LiveKit's own `session.run` tests) run scripted or simulated callers against a sandbox,
and score tool calls and end state.

## 22. Code writes the read-back, so the yes is to what will be written

**What happened.** After #19, code knew that a read-back was spoken in full and that the caller
said a clear yes. It didn't know what the read-back said. If the LLM said "Wednesday at six" for
a Tuesday 5 PM hold, the caller's "yes" would still book Tuesday at 5 (`THREAT_MODEL.md` gap 1).

**How it works (LiveKit 1.8).**
- **A tool can speak and stay silent.** `session.say(text)` from inside a tool queues that exact
  text for TTS and adds it to the chat history as an assistant message. Returning
  `llm.ToolResult(output, reply_required=False)` keeps the tool's result in history but asks the
  LLM for no reply, so the said sentence is the agent's whole turn.
- **Evals see it too.** `say()` registers with the running `RunResult`, so text evals capture the
  spoken read-back.

**Options.**
1. Check the LLM's read-back for the right day and time. That means matching free-form spoken
   times ("five in the evening", "shaam paanch baje") against the hold: fragile, and it would be
   tuned to whatever phrasings we happen to see.
2. Give the LLM an exact sentence and refuse unless it was repeated verbatim. This still costs an
   LLM generation, and the model may paraphrase.
3. **Code builds the sentence from the database row and speaks it itself.**

**Chosen: option 3** (`agent/readback.py`, `tools/common.read_back_gate`).
- **Two calls per write.** The first call to `confirm_booking`, `reschedule_booking` or
  `cancel_booking` builds the read-back from stored data and speaks it, writing nothing. The
  second call writes only if the message just before the caller's yes contains that exact
  sentence, heard in full (`confirm.check_yes`, compared word by word, ignoring spacing and
  punctuation).
- **The read-back comes last.** It's spoken after the name and phone are collected, so it
  includes the patient's name (a misheard name gets caught too) and never the phone or birth
  date.
- **A yes covers exactly one version.** `PendingWrite.key` covers the hold or booking id plus the
  patient details. If anything changes, the old yes doesn't count and the new version is read
  back.
- **Re-reads are capped.** A paraphrase, an interruption, or a question in between means code
  reads it again, at most 3 times, then offers a callback.
- **An unclear answer gets no re-read.** "Hmm" or "yes but later" goes back to the LLM to ask what
  the caller wants.
- **Spoken form.** Times and dates come out as words in English and Hinglish ("five thirty in the
  evening", "shaam saade paanch baje", "paune das", "dedh baje"), because the text goes straight
  to TTS.

**Cost.**
- **Saved:** the read-back turn needs no LLM generation for its reply, since code wrote the
  sentence.
- **Added:** one more tool call, and the prompts grew by about 100 characters (booking 2,917,
  rescheduling 2,971).
- **To check in the voice test:** whether LiveKit sets `e2e_latency` on a `say()` message. If
  not, read-back turns would have no voice-to-voice number and the latency report would
  undercount tool turns.

**Verification.** 205 tests pass. New tests cover:
- the read-back is spoken and nothing is booked
- an exact yes reaches the write
- a paraphrase is replaced by the code read-back
- a mixed answer gets no re-read
- re-reads stop at three
- changed details need a new yes
- a yes to a booking read-back doesn't allow a cancel
- `tests/test_confirm.py`: paraphrased and interrupted read-backs, a question in between, and TTS
  punctuation

Not yet run with an LLM or in a voice call.

**In industry.**
- Templated confirmations for critical values are the norm wherever a mistake is costly: IVR
  ("You said Tuesday at 5 PM, press 1"), bank transfers, pharmacy refills, airline changes.
- LLM voice platforms increasingly let a tool speak a fixed message (`say` with a template,
  "static" tool messages) for exactly this, keeping generation for the conversational parts.
- Aviation's read-back/hear-back rule is the strict version: the controller checks the
  read-back against what was actually issued, which is what `check_yes` does against the stored
  row.

## 23. The first eval run: a stale cache and a fixture that lied

**What happened.** The first `pytest` run against the sandbox failed 2 of 3 DB tests: after the
first test, `find_slots` found no free times at all. Then the first dev eval run passed 10 of 12.
Rescheduling and cancelling never reached their write tools.

**Mechanism 1, the stale cache.** Each test (and each eval scenario) re-seeds the sandbox, which
deletes the clinic and recreates its doctors with new ids. `find_slots` caches the doctor list
for 60 s, and `clinic_id()` the clinic id for 5 minutes. So the next test looked up the working
hours of doctor ids that no longer existed, found none, and returned no free slots. In production
doctors are never deleted mid-call, so this only shows up when data is replaced under a running
process. But a cache with no way to invalidate it is a trap.

**Fix 1.** `sandbox.prepare()` clears both caches after re-seeding. The production TTLs stay.
When the staff view can edit doctors (week 7), a change must invalidate the cache too, or
expire it at most one call later.

**Mechanism 2, the fixture.** The scenarios assumed the eval patient has one upcoming booking.
The seed spreads 151 bookings over 20 random patients, so Rohan Joshi had 5. The agent listed
them and asked "which one?" every time, which is correct. The scripted caller can't answer that,
so the call went in circles, and in the cancel scenario the agent handed off with a callback.
That's also correct behaviour.

**Fix 2 (the test, not the agent).** The fixture now gives each eval patient exactly its stated
bookings (`evals/run.py FIXTURES`). The agent's "which one?" behaviour became its own scenario
(`cancel_one_of_several`), following the failure-to-eval rule (§16.2). Changing the agent to pass
would have been tuning to the test.

**Verification.**
- `pytest`: 208 pass, including the 3 sandbox tests (double hold refused, repeated
  hold/confirm = one booking, expired hold freed, holds released at call end).
- Dev evals: 13 of 13, with 0 wrong actions and 0 verification violations.
- The transcripts show the code read-back spoken word for word, and the write only on the second
  call, after "Yes".

**In industry.** A failing eval needs triage before a fix: an agent bug, a test bug, or a spec
gap. Teams keep the transcript for every eval run for exactly this reason. The runner now stores
it in the results file (synthetic data only, git-ignored). Scripted callers break when the agent
asks a reasonable question the script didn't expect. That's why voice-agent eval tools move to
simulated callers, an LLM playing the patient with a goal and a persona (planned for week 6).

## 24. Two silent bugs found by the first week 2 voice call

**What happened.** The warm-up call logged `STT keyterms not loaded: module 'agent.tools' has no
attribute 'CLINIC_NAME'`. In W1 the agent asked for the date of birth twice although the caller
said it clearly ("Third March nineteen eighty eight").

**Mechanism.**
- *Keyterms:* when `tools.py` became the `tools/` package, `CLINIC_NAME` moved to
  `tools/common.py`, but `vocabulary.py` still read `tools.CLINIC_NAME`. Keyterms fail open by
  design, so every call since the split worked without them. Telemetry still labelled the calls
  `stt_keyterms=on`, because the label comes from `.env`, not from what was applied.
- *Date of birth:* Deepgram writes spoken dates as words. `parse_birth_date` only read digits,
  so `confirm_booking` refused the real answer. The second try passed only because the LLM
  rewrote the words as "3 March 1988" itself: the LLM normalising data that code should.

**Options.** Re-export names from the package `__init__` (hides where they live) or import from
the module that owns them. For dates: ask the LLM to send digits (a prompt rule the LLM may
ignore), or parse spoken numbers in code (deterministic, testable).

**Choice.** Import `CLINIC_NAME` from `tools.common`. Spoken day, ordinal and year words
("twenty-first", "nineteen eighty eight", "two thousand and five", "nineteen oh nine") are turned
into digits in code before parsing.

**Verification.** A regression test loads keyterms through the real package. Eight spoken
date-of-birth cases pass. After the restart the log shows `STT keyterms applied: 7 terms`, and in
W4–W6 every spoken birth date was accepted on the first try. 217 tests pass.

**In industry.** "Fail open" is right for a quality feature on the hot path, but a fail-open
path needs a metric, or it fails open forever without anyone noticing. Telemetry should record
what was applied, not what was configured. Speech-to-structure normalisation (numbers, dates,
spelled letters) belongs in deterministic code with tests: voice platforms ship "inverse text
normalisation" exactly because LLMs are inconsistent at it.

## 25. "Kabir" heard as "Kavita": open-ended names don't survive STT

**What happened.** In W1 and W2 the caller said "Kabir" and spelled it ("K a b I r", "B for
ball"). STT heard "Kavita", "Kavir", "Kavita k a b I r". The agent wrote "Kavita Kabir" and read
it back, and it took 4–6 turns to get to "Kabir". The read-back stayed correct throughout: a yes
to "Kavita Kabir" would have created a patient with exactly that name.

**Mechanism.**
1. Keyterm bias: the doctor list (boosted since #24 was fixed) contains "Kavita Iyer", and
   keyterm prompting pulls similar-sounding speech toward boosted words. Deepgram users report
   the same over-matching ([discussion #1233](https://github.com/orgs/deepgram/discussions/1233)).
2. B and V are close in Indian English, so "Kabir" vs "Kavir" is a real acoustic ambiguity.
3. The spelling reached the LLM as fragments split across STT segments, and the LLM ignored or
   merged them. Nothing in code reads spelled letters.
4. The flow asks for the name first, as an open-ended field, before the phone number that would
   tell us which names are on file.
The same call shows the phone-number version of point 3: the LLM joined STT fragments into "two
twothree", which code rejected.

**Options.**
- Spell back every name (call-centre practice; one operator reports it catches errors in about
  15% of calls, [AnSer](https://www.anser.com/qa/allnames)). It costs a turn and TTS reads
  single letters poorly ([Retell](https://community.retellai.com/t/agent-confirming-name-and-email-are-garbling-slurring-words/1656)).
- Phone number first, then date of birth. If they match a patient, *confirm* the name on file
  ("Is this for Kabir?") instead of capturing it: a closed choice, not open recognition. Systems
  that narrow recognition to the caller's known records do this
  ([US6088429](https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/6088429)).
- Parse spelled letters ("K a b i r", "B for ball", NATO words) in code, like phone numbers.
- Scope keyterms to the moment: doctor names while choosing a doctor, not while the caller says
  their own name.
- Take the phone number and date of birth from the caller's transcript in code, not from the
  LLM's arguments.

**Choice (next step, not yet built).** Phone first, then date of birth, then confirm the name on
file; spell back only for a new patient; parse spelled letters in code; pause doctor keyterms
during name capture. One change per step, with a voice re-test (W1–W3).

**In industry.** Contact centres treat names as the least reliable field, and identify by
number plus a second factor (ANI/caller ID, date of birth, account number). Names are verified
against the record, not transcribed from scratch.

## 26. The backchannel filter drops "okay" but the speech doesn't resume

**What happened.** Call 10: a soft "Oh, okay." stopped the agent mid-sentence, and she didn't
continue until the caller said "Just continue". Later in the same call another "Okay." was
handled correctly: paused, then resumed.

**Mechanism.** From the worker log:
- The adaptive interruption model fired 1.32 s after the caller started speaking (probability
  0.747 > 0.656) and paused the audio.
- The filter worked: `backchannel ignored`, then `backchannel turn dropped`.
- But resuming paused speech is armed by the VAD end-of-speech event (`on_end_of_speech`), and
  only if speech is already paused at that moment. The detection was late, so the VAD
  end-of-speech had most likely already fired before the pause began. Nothing was left to arm the
  resume timer: the filter only arms it when `speaking is False` is passed, which this path didn't
  do. The speech stayed paused.
- The second "Okay." came after LiveKit's interruption quota ran out (`quota exceeded`, 429)
  and the session fell back to VAD interruption. There the pause starts at speech start, so
  end-of-speech armed the timer and the speech resumed (`resumed false interrupted speech`).

**Second finding: the adaptive interruption quota.** LiveKit Cloud emailed "100% of concurrent
barge-in requests per minute used" (limit 45 per minute on this project). The detector sends a
request every 0.1 s (`detection_interval`) while the caller and agent overlap, so one call with
several overlaps can use the whole minute's budget. After that the session silently uses VAD
interruption for the rest of the call, so behaviour changes mid-test.

**Options.**
- In the filter, arm the resume whenever speech is paused and the caller isn't speaking
  (`session.user_state != "speaking"`), instead of relying on `speaking is False`.
- Raise `detection_interval` to cut quota use (slower detection), or use VAD interruption with
  the filter only (no quota, more false pauses).
- A plan with a higher quota (cost).

**Choice (built 2026-10-14).** The filter now arms the resume itself whenever speech is
paused and the caller is already quiet (`session.user_state != "speaking"`), both on the
backchannel transcript and when the backchannel turn is dropped. Every telemetry row now records
`providers.interruption` = adaptive / vad, read from the live activity, so latency rows show
which detector each turn used. A test fails if the LiveKit internals this relies on change.
Voice re-test: call 10, several runs, in the end-of-step voice session. Quota decision after
measuring requests per call.

**In industry.** Barge-in systems separate "pause" from "stop": pause at the first sign of
speech, and stop only once the words show an intent to take the turn. Every path into "paused"
needs a way out. A stuck pause is the worst failure, because the caller hears silence and doesn't
know whose turn it is.

## 27. Half the week 2 turns waited the full 2.5 s end-of-turn delay

**What happened.** The voice test's end of turn was p50 2,500 ms (27 of 52 turns waited the
maximum), against 578 ms in the week 1 baseline. Voice-to-voice rose to p50 3,829 ms.

**Mechanism.** The turn detector scores the transcript text. Week 2 calls are mostly phone
numbers, dates of birth, names and short answers ("Doctor Sharma", "Five thirty is fine."). It
scores these 0.07–0.33, below the 0.36 "unlikely" threshold, so LiveKit waits
`max_endpointing_delay` (2.5 s). For a phone number said in groups that wait is useful: STT split
"nine zero zero zero zero … two" into 3–8 segments, and an early end of turn would cut the
number. For a complete short answer it's dead air.

**Options.** Phase-aware endpointing: when the agent has just asked for a number or a date,
allow a long wait; after a yes/no question or a slot choice, a short one. Or a lower
`max_endpointing_delay` everywhere (cuts digit sequences), or the audio-aware cloud model (no
gain in E7, #10).

**Choice (built 2026-10-14, `agent/endpointing.py`, `ENDPOINTING=phase`, off by default).**
After every agent message, code picks the phase for the caller's next turn and sets LiveKit's
`max_delay` with `session.update_options(endpointing_opts=…)` (checked against the installed
1.8.6 source: the default is 2.5 s, the value the waits hit):
- long (3.0 s): a phone number, date of birth or spelling was asked for, or a name check is out
  (a correction may be spelled, with pauses between letters)
- short (1.0 s): a read-back is waiting, or a choice / "anything else?" question
- default (2.5 s): everything else, unchanged
Each value is an `.env` setting with bounds (short ≤ default ≤ long). Every turn row records
`providers.endpointing` (the phase the caller's turn was judged under), so the voice session can
report end of turn by phase, with the flag off as the baseline and on as the change.

**In industry.** Production voice agents set the endpointing timeout per dialogue state: long
for "please say your account number", short after "is that right?". IVR systems have done this
for decades ("incomplete timeout" per grammar).

## 28. Capture a name like a person would: confirm it first, read the booking back once

**What happened.** In the R2 voice re-test (a new patient), every correction of the name
repeated the whole slot read-back ("Doctor Anil Sharma, Thursday the fifteenth, six thirty…, for
Kavita Kabir. Shall I book it?"). The caller's point: a receptionist gets the name right first
and confirms the booking once, at the end. Also, the caller said "Kabir" and spelled it in one
breath; STT heard "Kavita K a b I r", the LLM passed "Kavita Kabir", and the spelling only
replaced the word it matched exactly.

**Research.** Sierra's τ-Elicitation benchmark ([arXiv 2609.13602](https://arxiv.org/pdf/2609.13602),
4,800 voice calls, GPT/Gemini/Grok agents): person names pass exactly in only about 30% of
tasks under robust conditions. A prescribed ask → spell → read back → confirm → correct protocol
adds 14–31 points, and validating each field before moving on raises task accuracy from 0.64
to 0.82. LLM agents repairing errors on their own "often double down on an error or hallucinate
a replacement", the "Kavita Kabir" merge. Amazon Lex captures naturally first and switches to
spell-by-letter or spell-by-word on the retry, set by code
([docs](https://docs.aws.amazon.com/lexv2/latest/dg/spelling-styles.html)).

**Choice.** A new patient's name is confirmed on its own, spelled back by code ("I have the
patient's name as Kabir, that's K, A, B, I, R. Is that right?"), with the same code read-back
and yes check as writes. The booking read-back comes after the name is confirmed, once. A
spelling now replaces every word that sounds like it. Patients on file skip the name step.

**Verification.** 240 tests; dev evals 14 of 14 (book_happy shows the name check, then one
booking read-back). Voice S4: "Kavita" was spelled back as K, A, V, I, T, A; the caller said
"No, it's Kavir, k a b I r"; the next check was "Kabir, K, A, B, I, R"; then one booking read-back.
Only the name was repeated.

**Next (planned).** Escalate to spell-by-word after one failed name check, stop after two (book
under the heard name flagged for staff, or offer a callback), and log first-try / turns-to-fix /
gave-up per field. Then clear the doctor-name keyterms while the caller says their own name.

**In industry.** Validate each field as it's captured and confirm the whole task once. Never
let the LLM run the repair loop on its own.

## 29. Silence: re-engage, then hang up for everyone

**What happened.** R3: the caller was silent for about 15 s after a read-back and the agent said
nothing. Then S3 (silent the whole call): the agent left, but the caller stayed connected to an
empty room.

**Mechanism.** LiveKit marks the user "away" after `user_away_timeout` of mutual silence
(default 15 s, paused while tools run), but nothing in our code listened for it. Once a user is
away, LiveKit doesn't re-arm the timer by itself. `ctx.shutdown()` ends the agent's job, not the
call: the room and the caller remain.

**Research.** Google's Dialogflow CX voice design guidance: repeat the question after the first
no-input, rephrase it on the second, hand off at about the third
([guide](https://docs.cloud.google.com/dialogflow/cx/docs/concept/voice-agent-design?hl=it)).
Vapi and Cognigy document the same reprompt-then-end pattern.

**Choice.** `agent/silence.py`, behind `SILENCE_REPROMPT` (default on): after 8 s, "Are you still
there?" plus the last question; after the second, "No rush" plus the question in simpler words;
the third ends the call with a goodbye from code. A pending read-back is repeated word for word,
so a yes after it still counts. Each reprompt calls `reset_away_timer()`. After the agent's own
goodbye a silence just hangs up. Hanging up deletes the room, as LiveKit's `EndCallTool` does,
so every participant (phone callers too) is disconnected.

**Verification.** Voice: S1 reprompted at 8 s with the read-back and the "yes" after it booked;
S2 gave two reprompts (the second rephrased) and the caller came back; H2 hung up 8 s after the
agent's goodbye with no second goodbye; room deletion disconnected the caller (H1–H3).

**Two bugs the voice runs found in this handler.**
- H1 (first run) hung up at the first silence with no reprompt: the goodbye check matched "thank
  you for calling", which is how the greeting starts. Now only closing phrases count, and nothing
  counts before the caller has spoken.
- H1 (second run) reprompted 4 times instead of 2: the count reset on VAD "speaking", and room
  noise or echo triggered VAD between reprompts. Now only a final transcript with words resets
  it. 249 tests, each bug with a regression test from the call.

**In industry.** Every no-input path needs a bounded number of retries and a clean exit. The
worst failure is silence on both ends, with neither side knowing whose turn it is.

## 30. A "yes" heard by VAD but never transcribed

**What happened.** S4: the caller said "yes" a second after the booking read-back. The final
turn's `speech_start_time` is 13:47:06.6, one second after the read-back ended, but no
transcript came back for it. 8 s later the silence handler asked "are you still there?", and only
the caller's second "yes" (13:47:28) was transcribed. To the caller, the yes was ignored and the
transcript order looked wrong.

**Mechanism.** VAD detected speech; Deepgram returned no final (and no logged interim) words for
it. The cause isn't confirmed. A candidate from earlier calls is Deepgram's 25 ms
`endpointing_ms` splitting or dropping very short words (LEARNING E7 notes). LiveKit can detect
the symptom: `transcription_timeout` emits `user_transcription_timeout` when VAD speech gets no
final transcript in time.

**Choice.** Detect and recover now, measure the cause next: after 2 s without words for at least
0.25 s of speech, the agent says "Sorry, I didn't catch that" and repeats the read-back (or its
question). Lost transcripts are counted per call. Then test `endpointing_ms` on its own.

**Verification.** Tests for the read-back repeat and for ignoring sub-0.25 s blips. Voice: next
run.

**In industry.** No-match ("I heard you but didn't understand") and no-input ("I heard nothing")
are separate events in IVR design, with separate prompts. Treating a lost answer as silence makes
the agent sound like it ignored the caller.

## 31. Off-topic talk and distress: kind, brief, back to the task

**What happened.** Asked by the caller: what does the agent do when a caller talks about the
weather, their feelings, a friend who is angry, money trouble, or something worse?

**Research.** ElevenLabs' safety framework for voice agents: keep the agent within its scope,
decline personal advice, steer off-topic subjects back professionally, and exit cleanly when a
caller keeps pushing ([ElevenLabs](https://elevenlabs.io/blog/safety-framework-for-ai-voice-agents)).
High-stakes topics go to a person. India's national tele-mental-health line is Tele-MANAS,
14416 or 1-800-891-4416, free, 24×7 ([Vikaspedia, a Government of India portal](https://en.vikaspedia.in/viewcontent/health/mental-health/tele-manas);
the official telemanas.mohfw.gov.in page renders the number in JavaScript and couldn't be read).

**Choice.** Two short prompt rules for every mode (+396 characters, so about +100 prompt tokens):
- Off-topic: one kind sentence, no advice, back to the task; a held time stays held. Payment
  plans or discounts can't be arranged by the agent: offer a staff callback.
- Crisis or self-harm: stop the task, give Tele-MANAS and 108, offer a callback, don't counsel.

New eval field `reply_must` (a regex some reply must match) and 5 scenarios: off-topic in the
middle of a booking (the booking must still complete), personal advice (no "you should…"), money
worries, distress in English (dev) and Hinglish (hold-out).

**What the eval run caught.** The first run passed 18 of 18, but the money-worries transcript
said "full payment is required, we don't offer instalments". The agent had fetched only the fees;
the clinic's payment fact says nothing about instalments. An invented policy, and the regex only
checked for promises. The prompt now says to offer a callback, and the scenario requires one and
rejects invented refusals. Then 3 of 3 repeats of each new scenario passed, and the full dev set
passed 18 of 18. Passing a test isn't the same as being right: read the transcripts.

**In industry.** Scope rules plus an escalation path, tested with adversarial and emotional
scenarios. Crisis handling gives a real resource and a human route; it never tries to counsel.

## 32. A name-capture protocol in code, and an LLM that skips it

**What happened.** Layer 2 of the name plan (#25, #28): after a rejected name check, ask for
spell-by-word; after two failures, stop and offer a callback. The first eval run passed, but the
transcript showed the stop never fired: on "No, that's wrong" the LLM asked for a spelling
itself and never called the tool, so code never saw the rejection.

**Mechanism.** A protocol enforced in code runs only at the tool boundary. The tool's "next"
hint said "after a clear yes, call again", so the LLM treated a "no" as its own business, which
is the pattern Sierra's τ-Elicitation reports (agents "double down on an error or hallucinate a
replacement" instead of completing the repair).

**Choice.**
- Count failures from what code always sees: every name check that is replaced by another name,
  or answered without a clear yes, is one failure, however the LLM got there.
- The read-back hint now says to call the tool again after *any* answer, with any correction or
  spelling, so code decides what the answer means.
- A bare "no" (no new name, no spelling) gets the spell-by-word instruction ("K for kite"); a
  correction is spelled back as a new check; two failures return `name_not_captured`: apologise,
  don't book, offer a callback.
- Metric: `booking_created` carries `name_checks_failed` (0 = right first time) for new
  patients, so name capture can be measured from the events table.

**Verification.** 253 tests (bare no → spell-by-word, counted once; correction counts; second
failure stops). New eval `name_not_captured` (caller rejects twice): 2 of 2 pass. One run
followed the protocol exactly; in the other the LLM still handled one "no" itself, so the stop
came a turn later. Either way no unconfirmed name can be booked: every path to a booking goes
through the name gate and needs a yes.

**In industry.** Deterministic dialogue state for exact-entity capture, the LLM for wording.
Where the LLM can bypass a step, enforce the outcome (no write without confirmation) rather than
trusting it to follow the steps.

## 33. A yes over "Shall I book it?": count it only if every fact was heard

**What happened.** W2 and H3: callers said yes as the read-back ended. Any interruption forced
a full re-read; in W2 that meant three read-backs. The safety rule (#22) is "a yes to the exact
sentence, heard in full", because a caller who cut in may not have heard the time.

**Mechanism.** LiveKit keeps only the played part of an interrupted message in history (H3's
read-back is stored as "…the fifteenth of October, at seven"). So code can see exactly what the
caller heard. In H3 the cut was inside the facts ("forty-five in the evening, for Kabir" was
never heard), so the re-read was right. The cases worth relaxing are cuts inside the closing
question only.

**Choice (`READBACK_YES_AFTER_FACTS`, off by default).** `confirm.facts_of()` splits the
read-back into its facts and its closing question. With the flag on, a clear yes counts when the
read-back was interrupted but every fact is in the heard text. A cut inside any fact still means
a re-read. One-sentence read-backs (cancel) have no separate question, so nothing changes there.

**Verification.** Tests: yes over "Shall…" counts only with the flag on; H3's real cut (inside
the time) is refused either way; the cancel read-back stays whole. Voice: count re-reads with the
flag off and on in the end-of-step session.

**In industry.** Confirmation is about what the caller heard, not what the agent sent. Systems
with word-level playback timing (TTS-aligned transcripts) use it to decide whether an
interrupted prompt counts as delivered.

## 34. A filler line during lookups, without breaking the read-back rule

**Problem.** Tool turns cost two LLM calls (#14): about 3.9 s p50 against 2.2 s. "Book me with
Doctor Sharma tomorrow evening" took 13–15 s to the first answer in the R3 and H3 calls (switch
mode, find slots, then the reply). The caller hears nothing in between.

**Options.** (a) An LLM preamble ("let me check") in the prompt: free in code, but not
guaranteed and adds prompt tokens. (b) LiveKit's `RunContext.with_filler()`: built for a slow
step *inside* a tool; it waits for the session to be idle, and the agent is "thinking" during
the second LLM call, so it would not fire there. (c) A code-driven filler at tool start.

**Choice (c), `agent/filler.py`, `FILLER=off` by default.** On the first lookup tool of a
caller turn, after `FILLER_DELAY_S` (0.3 s) and only if nothing is playing, say one short line.
Never for write tools (they speak their own read-back), never twice in a turn, and with
`add_to_chat_ctx=False` so it can't become "the message before the yes" that `confirm.py`
checks. Lines rotate; Hinglish lines exist for week 4.

**Verification.** Unit tests: once per turn, out of history, none before a read-back, none if
the LLM already speaks, cancelled if the reply starts first. Not verified yet: LiveKit's playout
order (filler before the tool reply) in a real call; the voice session checks it from log
timestamps, and compares time to first audio and V2V with the flag off and on.

**In industry.** Acknowledge immediately, answer when ready. Contact-centre agents say "one
moment" for the same reason; voice platforms ship "filler" or "thinking" audio for tool calls.
