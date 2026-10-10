# Scripted Test Calls

The same calls, run the same way, every time latency is measured (design §6: a fixed set before
and after each change). Use made-up names only; real names end up in logs.

## Setup (keep identical across runs)
- Same mic, quiet room. **Use headphones.** With laptop speakers, the agent's own voice can
  leak into the mic and trigger false interruptions.
- Worker running: `caffeinate -i uv run python -m agent.main dev`
- Playground: https://agents-playground.livekit.io, signed in to the LiveKit project.

## Procedure: 11 calls = 1 warm-up + 10 measured
Every call has the same shape, so each gives **2 measured caller turns** (about 20 in total,
enough for a usable p95):

1. Click **Connect**. Wait until Simran's greeting has **finished**.
2. Say the call's **line** (table below), at a normal pace. Then **stop talking and wait** for
   her full reply. Calls 5, 6 and 10 have special instructions.
3. Say the closing line: **"Okay, thank you."** Wait for her reply to finish.
4. Click **Disconnect**. Wait about 5 seconds before the next call.

**Call 0 (warm-up):** connect, wait for the greeting, say "Hello", wait for the reply,
disconnect. It is marked `cold_call` and excluded.

| # | Tests | English line (AGENT_LANGUAGE=en) | Special instruction | Hinglish line (week 4) |
|---|---|---|---|---|
| 1 | Greeting reply | "Hello, good morning." | none | "Hello ji, namaste." |
| 2 | FAQ | "What time does the clinic open?" | none | "Clinic kitne baje khulta hai?" |
| 3 | Loose date | "Is the doctor free day after tomorrow in the evening?" | none | "Doctor parson shaam ko free hain?" |
| 4 | Doctor's name | "I want to see Doctor Sharma tomorrow." | none | "Mujhe kal Dr. Sharma se milna hai." |
| 5 | **Barge-in** | "Can you tell me about the clinic?" | When she has said **3–4 words** of her reply, cut in: **"Sorry, just the address."** Then wait for her reply. | "Clinic ke baare mein batao…" then "Bas address bata do." |
| 6 | **Pause mid-sentence** | "I need an appointment for…" **(stay silent 2 seconds)** "…next Monday." | Count "one, two" silently in the gap. It must be one turn: she should **not** reply in the gap. | "Mera appointment chahiye… (2 s) …agle Monday." |
| 7 | Time of day | "Can I come in the morning, around ten?" | none | "Subah das baje aa sakte hain kya?" |
| 8 | Change of mind | "Tomorrow at five. No, wait, make it six." | Say it as one sentence. | "Kal paanch baje. Nahi, chhe baje." |
| 9 | Medical (must not advise) | "I have a headache, which tablet should I take?" | none | "Sar dard hai, kaunsi tablet lun?" |
| 10 | **Backchannel** | "What all can you help me with?" | While she replies, say **"okay"** softly once, as if listening. Don't try to stop her. | Same, with "haan…" |

**Expected behaviour today (no tools yet):** an honest "can't help with that on this line yet"
for 2–4 and 7–8. No medical advice for 9. She stops for 5. She ideally keeps talking for 10
(VAD fallback mode may stop her, which is useful to record). One reply for 6.

## After the run
1. `uv run python -m scripts.latency_report --since <today> --language en`
2. Paste the printed row into `docs/LATENCY_LOG.md`, with notes on anything odd (a false
   interruption, a reply in the call 6 gap).
