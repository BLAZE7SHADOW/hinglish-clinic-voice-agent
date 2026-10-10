# Latency Log

One row per measured change (design §6). p50 / p95 in milliseconds over the
[scripted test calls](TEST_CALLS.md), cold calls excluded. Always record language, channel and
providers, and report task success next to latency once evals exist (week 2).

Columns: **EoT** end of turn · **STT** final transcript · **TTFT** LLM first token ·
**1st sent.** LLM first sentence to TTS · **TTS** first audio · **V2V** voice-to-voice ·
**Stop** barge-in stop time.

| Date | Change | Setup | Sample | EoT | STT | TTFT | 1st sent. | TTS | V2V | Stop | Notes |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 2026-10-10 | **Baseline** (framework defaults, no tools) | en, browser, Deepgram nova-3 en-IN / Gemini 3.6 Flash (thinking minimal) / Cartesia sonic-3 0.9; turn detector v1-mini | 10 calls, 22 turns | 578 / 2501 | 516 / 600 | 1313 / 1867 | 1513 / 2037 | 205 / 241 | 2251 / 2747 | 365 / 500 (n=3) | **EoT p95 = turn detector false "unfinished":** 6 of about 22 complete sentences scored below 0.36 and waited the 2.5 s max ("Good morning." 0.26, "Can you tell me about the clinic?" 0.31). The one real pause (call 6) was correctly held. **LLM TTFT is the biggest p50 stage** (about 58% of V2V). Reasoning tokens 0; prompt cache 0% (prompt about 390 tokens). Barge-in: call 5 real, 365 ms (adaptive). Call 9 a false interruption after the cloud detector timed out (VAD fallback, 515 ms). Call 10's backchannel "okay" was not filtered: she stopped and replied to it. Greetings excluded (TTFT about 1.3–1.6 s, includes connection setup). |
