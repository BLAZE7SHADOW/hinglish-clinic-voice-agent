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
| 2026-10-10 | **E7: turn detector v1 (cloud, audio-aware)** instead of v1-mini; nothing else changed | en, browser, same providers; turn v1, local fallback off | 9 calls, 22 turns (call 2 never connected) | 578 / 2501 | 468 / 676 | 1375 / 1970 | 1535 / 2146 | 188 / 217 | 2401 / 3906 | 495 / 574 (n=4) | **No improvement → reverted.** False "unfinished" waits on complete scripted sentences: 5 (v1) vs 6 (v1-mini). The models fail on *different* sentences (v1 fixed "…just the address", "…ten?", "…six"; broke "…next Monday", "Which tablet should I take?"). v1's English threshold is 0.56; "Can you tell me about the clinic?" scored 0.552. The real pause (call 6) was held by both. V2V p95 worse. No gateway errors. Backchannels: soft "okay" scored 0.709 and "ohh" (heard as "So") 0.658 against the 0.656 interruption threshold; both interrupted. |
