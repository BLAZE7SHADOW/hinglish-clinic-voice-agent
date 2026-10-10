# Latency Log

One row per measured change (design §6). p50 / p95 in milliseconds over the
[scripted test calls](TEST_CALLS.md), cold calls excluded. Always record language, channel and
providers, and report task success next to latency once evals exist (week 2).

Columns: **EoT** end of turn · **STT** final transcript · **TTFT** LLM first token ·
**1st sent.** LLM first sentence to TTS · **TTS** first audio · **V2V** voice-to-voice ·
**Stop** barge-in stop time.

| Date | Change | Setup | Sample | EoT | STT | TTFT | 1st sent. | TTS | V2V | Stop | Notes |
|---|---|---|---|---|---|---|---|---|---|---|---|
