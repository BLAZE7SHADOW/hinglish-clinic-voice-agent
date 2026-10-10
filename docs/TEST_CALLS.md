# Scripted Test Calls

The same 10 calls, run the same way, every time latency is measured (design §6: fixed set
before and after each change). Use synthetic names only; real names end up in logs.

**How to run a measurement**
1. Start the worker and make one throwaway call first. The first call a worker process serves
   is marked `cold_call` and excluded from reports (connection setup).
2. Run calls 1–10 in order, one Playground connection per call. Speak normally, in a quiet room,
   using the same mic every time.
3. `uv run python -m scripts.latency_report --since <today> --language <en|hinglish>`
4. Paste the printed row into `docs/LATENCY_LOG.md`.

| # | Purpose | English line (AGENT_LANGUAGE=en) | Hinglish line (week 4) |
|---|---|---|---|
| 1 | Greeting | "Hello, good morning." | "Hello ji, namaste." |
| 2 | FAQ | "What time does the clinic open?" | "Clinic kitne baje khulta hai?" |
| 3 | Availability with a loose date | "Is the doctor free day after tomorrow in the evening?" | "Doctor parson shaam ko free hain?" |
| 4 | Doctor's name | "I want to see Doctor Sharma tomorrow." | "Mujhe kal Dr. Sharma se milna hai." |
| 5 | Interrupt mid-reply | Ask "Can you tell me about the clinic?", then cut in after 2 seconds: "Sorry, just the address." | "Clinic ke baare mein batao…" then cut in: "Bas address bata do." |
| 6 | Long pause mid-sentence | "I need an appointment for… (pause 2 s) …next Monday." | "Mera appointment chahiye… (2 s) …agle Monday." |
| 7 | Mixed: book + time of day | "Can I come in the morning, around ten?" | "Subah das baje aa sakte hain kya?" |
| 8 | Mixed: change of mind | "Tomorrow at five. No, wait, make it six." | "Kal paanch baje. Nahi, chhe baje." |
| 9 | Mixed: medical question (must not advise) | "I have a headache, which tablet should I take?" | "Sar dard hai, kaunsi tablet lun?" |
| 10 | Mixed: backchannel while agent speaks | Say "okay… okay" softly while the agent is talking, without trying to interrupt | "Haan… haan" while the agent talks |

Expected behaviour today (no tools yet): honest "can't help with that on this line yet" for 2–4
and 7–8, no medical advice for 9, the agent stops for 5 and keeps talking for 10.
