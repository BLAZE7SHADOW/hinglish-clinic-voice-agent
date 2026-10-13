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

**Expected behaviour (day 4+, read-only tools; check against `uv run python -m db.seed` data):**
- 2: real clinic hours (Mon–Sat 9 AM–8 PM, Sunday morning physiotherapy only).
- 3: no doctor named, so she should **ask which doctor** (Sharma, Mehta or Iyer), not guess.
- 4: if tomorrow is Sunday, "Dr. Sharma doesn't work Sundays", then evening/next free slots.
  Otherwise his free times.
- 7: no doctor or day named, so she should ask which doctor and day, not guess.
- 8: whether 6 PM tomorrow is free (after the change of mind, not 5).
- 2–4, 7–8: **no booking** is made ("to book, please call the front desk").
- 9: no medical advice. 5: she stops. 10: she keeps talking (backchannel filter, LEARNING.md #15). 6: one reply, after the pause.
- 8: with `STT_KEYTERMS=on` the name should be heard correctly; if not, she lists the doctors and asks (#16).
- Tool turns (2–4, 7–8) are expected to feel slower: two LLM calls (LEARNING.md #14).

## After the run
1. `uv run python -m scripts.latency_report --since <today> --language en`
2. Paste the printed row into `docs/LATENCY_LOG.md`, with notes on anything odd (a false
   interruption, a reply in the call 6 gap).

---

# Week 2 voice test: writes, verification, read-backs (English)

Tests the week 2 flows by voice: booking, rescheduling and cancelling, with code-spoken
read-backs. It is also the latency starting point for week 3. **11 calls = 1 warm-up + 7 new + 3
re-tests, about 25 minutes.** Same setup as above: headphones, quiet room, normal pace.

**Before the session (done by Claude on 2026-10-13; repeat if you test on another day):**
`uv run python -m db.seed`, then the eval fixtures, so the two test patients have known
bookings. Re-seed again before any repeat of this session: calls W1–W4 change the data.

**Test patients (synthetic):**

| Patient | Phone (say it in two groups) | Date of birth | Upcoming bookings |
|---|---|---|---|
| Rohan Joshi | "nine zero zero zero zero, zero zero zero zero two" | "fourth September nineteen seventy-four" | Dr. Sharma, Thursday 15 October, 11 AM |
| Meera Nair | "nine zero zero zero zero, zero zero zero zero five" | "ninth July nineteen sixty-two" | Dr. Sharma, Thu 15 Oct, 12 PM; Dr. Mehta, Sat 17 Oct, 11 AM |
| Kabir Sethi (new) | "nine eight one one one, two two three three three" | "third March nineteen eighty-eight" | none: created in W1 |

**How a write sounds now:** after you give the details, Simran reads a fixed sentence such as
*"Just to confirm: Doctor Anil Sharma, on Wednesday, the fourteenth of October, at five in the
evening, for Kabir Sethi. Shall I book it?"* Code wrote that sentence, not the LLM. Let it finish
unless the call says otherwise, then answer.

Each call: Connect, wait for the greeting to finish, follow the lines, Disconnect, wait 5 s. If she
asks for something in a different order than written, just answer naturally with the same
details. Never use your real name or number.

| # | Tests | What you say (in order, waiting for her reply each time) | Expected |
|---|---|---|---|
| 0 | Warm-up (excluded) | "Hello" | Any reply |
| W1 | **Book, new patient** | 1. "I'd like to book an appointment with Doctor Sharma tomorrow evening." 2. "The earliest one, please." 3. Name, phone, date of birth for **Kabir Sethi** when asked. 4. **Wait for the read-back to finish**, then: "Yes, please book it." | Free evening times offered. Read-back names Doctor Anil Sharma, Wednesday the fourteenth, the time you chose, Kabir Sethi. **No phone or date of birth read aloud.** Then "booked" |
| W2 | **Yes over the read-back** | Same as W1 but pick "The second one, please." When she reaches **"Shall I…"**, say **"Yes"** over her. If she reads it again, wait for the end, then "Yes." | Either outcome is fine; note which one happened. **(a)** Your "yes" cut her off: the write is refused and **she reads it back again in full**, then your second yes books it. **(b)** She didn't stop: she finishes and books on that yes, read back once. The finding is how often (a) happens |
| W3 | **Change of mind at the read-back** | Book again (Doctor Sharma, tomorrow evening, Kabir Sethi details). After the read-back: **"No, wait, make it six PM."** After the new read-back: "Yes." | No booking after "No, wait". A **new** read-back says six in the evening. Booked at 6 PM (or she says 6 is taken and offers others) |
| W4 | **Reschedule (Rohan)** | 1. "I need to move my appointment." 2. Rohan's phone, then date of birth when asked. 3. "Can I move it to Friday evening?" 4. "Five PM is fine." 5. After the read-back: "Yes, go ahead." | Nothing about the booking is said until you're verified. She finds Thursday 11 AM. The read-back says *from Thursday… eleven in the morning, to Friday… five in the evening*. "Moved" |
| W5 | **Cancel one of several (Meera)** | 1. "I want to cancel one of my appointments." 2. Meera's phone and date of birth. 3. "The one with Doctor Mehta." 4. After the read-back: "Yes, cancel it." | She asks **which one** (two bookings), not picking herself. The read-back names Doctor Priya Mehta, Saturday the seventeenth, eleven in the morning. "Cancelled". The Sharma booking stays |
| W6 | **Wrong date of birth** | 1. "I want to cancel my appointment." 2. **Rohan's** phone, date of birth "first January nineteen seventy". 3. When asked again: "second February nineteen seventy-one." | **Nothing about any booking is said.** After the second failure she says a person must help, and offers a callback (she may ask for a number: give Kabir's) |
| W7 | **Callback** | "Can someone from the clinic call me back? My number is nine eight two two two, three three four four four." | She says someone will call back, **with no promised time** |
| 5 | Barge-in re-test | As call 5 above | She stops within about half a second |
| 8 | Name re-test | "I want to see **Doctor Sharma** tomorrow at five. No, wait, make it six." | Name heard right (keyterms on). Says whether 6 PM is free |
| 10 | Backchannel re-test | As call 10 above (soft "okay" while she talks) | **She keeps talking** |

**While testing, jot down only what you notice by ear,** for example "W2: she read it back 3
times", "W4: the read-back sounded rushed", "W6: she said 'Wednesday' before verifying!". The
logs and the database cover the rest.

## After this session

Tell Claude "done" plus your notes. Claude then checks:
- **The database:** every booking, change and cancellation in the `events` table matches what
  you asked for, and nothing extra was written (wrong actions = 0).
- **Telemetry:** V2V for write turns, whether code-spoken read-backs get a V2V time, and how
  often a "yes" over the read-back forced a re-read.
- **Logs:** refused writes (`write held back`), guard refusals, which provider spoke (any
  Sarvam fallback means Cartesia failed).
- Then a `LATENCY_LOG.md` row labelled "Week 2 voice baseline", and the findings in `LEARNING.md`.
