# Learning Log

Every real problem we hit, written so it can be understood and explained in an interview. Each
entry covers: **what happened** (with evidence), **how it works underneath**, **the options**,
**what we chose and why**, and **how we verified it**. Newest at the bottom.

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

**Lesson / interview angle.** "Is the worker alive" and "is the call alive" are separate
connections. This is the failure-lab case "agent worker crash mid-call" (design §10.2). Had a
call been live, the caller would have heard silence. The target behaviour is to end the call
cleanly, record an event and start a callback workflow.
