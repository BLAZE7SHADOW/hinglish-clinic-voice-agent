# Audio Path: Fact Sheet (reference for writing AUDIO_PATH.md yourself)

This is **not** `AUDIO_PATH.md`. The design asks you to write that in your own words (week 1,
day 5) and to be able to explain it out loud without notes. These are the facts for *this*
project's setup, to check your understanding against. Items marked **(verified)** were checked
in this repo's installed code or our own logs; items marked **(check)** are general WebRTC
facts to confirm in the LiveKit/WebRTC docs.

## The path of one packet, caller → agent → caller (browser call)

| # | Where | What happens | Facts for our setup |
|---|---|---|---|
| 1 | **Browser mic** | `getUserMedia` captures audio. The browser's WebRTC audio processing runs **echo cancellation (AEC)**, noise suppression and auto gain. | AEC is why headphones matter less in browsers, but leaked speaker sound still caused false interruptions in our tests. LiveKit logs "aec warmup… disabling interruptions for 3 s" at call start **(verified, our logs)**. |
| 2 | **Encoder** | PCM is compressed with **Opus**, the WebRTC voice codec, usually in **20 ms frames** **(check)**. | Opus adapts its bitrate to the network and has forward error correction for lost packets **(check)**. |
| 3 | **Packetise + encrypt** | Each frame goes into an **RTP** packet, encrypted as **SRTP** (keys exchanged with DTLS) and sent over **UDP** **(check)**. | UDP, not TCP: a late voice packet is useless, so it's better to drop it than wait for a retransmit. |
| 4 | **Network → LiveKit Cloud** | **ICE/STUN/TURN** find a route through NATs and firewalls. Packets reach LiveKit's **SFU** (Selective Forwarding Unit) **(check)**. | Our project's SFU region is **India South** **(verified, worker registration log)**. An SFU *forwards* packets; it doesn't decode or mix them. |
| 5 | **SFU → worker** | The worker joins the same room as a participant and subscribes to the caller's audio track. | Each call runs in its **own worker process** **(verified, LEARNING.md #9)**. The Python SDK wraps a Rust core through FFI (the `FfiHandle` lines in our logs) **(verified)**. |
| 6 | **Jitter buffer → decode** | Packets arrive unevenly; the **jitter buffer** reorders them and smooths timing, then Opus decodes back to **PCM** frames **(check)**. | The jitter buffer trades a little delay for smooth audio. |
| 7 | **Resample** | PCM is resampled for the speech models. | Deepgram plugin sends **16 kHz** audio (`sample_rate=16000`) **(verified, plugin source)**. |
| 8 | **VAD** | **Silero VAD** (local, in the worker) decides "is someone speaking?" frame by frame. | Loaded once per process in `prewarm` (`agent/main.py`) **(verified)**. |
| 9 | **STT** | Audio streams over a WebSocket to **Deepgram nova-3** (`en-IN` or `multi`), which returns interim and final transcripts. | Final transcript p50 **516 ms** after you stop **(verified, baseline)**. Plugin `endpointing_ms=25` (LEARNING.md, "thank you" word drop) **(verified)**. |
| 10 | **Turn detector** | `v1-mini` (local, text) scores "has the caller finished?". Below threshold it waits up to 2.5 s, otherwise 0.3 s. | End of turn p50 578 / p95 2,501 ms; English threshold 0.36 **(verified, LEARNING.md #10)**. |
| 11 | **LLM** | The transcript and history go to **Gemini 3.6 Flash** over HTTPS, and the reply streams back. Tool turns call the LLM twice. | Time to first token p50 **1,313 ms**, 0 reasoning tokens **(verified, baseline)**. Preemptive generation is on **(verified, logs)**. |
| 12 | **Sentence → TTS** | The first complete sentence goes to **Cartesia sonic-3** over a WebSocket, which returns PCM. | Cartesia outputs **24 kHz** (`sample_rate=24000`) **(verified, plugin source)**. First audio p50 **205 ms** **(verified, baseline)**. |
| 13 | **Worker → caller** | The agent's PCM is Opus-encoded and goes through RTP/SRTP → SFU → browser → jitter buffer → decode → speakers (steps 2–6 in reverse). | The caller's AEC uses this playback as a reference, to remove it from the mic signal **(check)**. |
| 14 | **Barge-in** | If the caller speaks over the agent, the adaptive model (cloud) or VAD decides whether it's an interruption. The worker stops publishing audio and drops the frames still queued. | Stop time 365 ms in the baseline. The cloud detector timed out twice, with fallback to VAD **(verified, LEARNING.md #8, #11)**. |

## Network legs to know (latency budget)
- Browser ↔ LiveKit India South, and worker (your Mac) ↔ LiveKit India South.
- Worker ↔ **Deepgram**, worker ↔ **Gemini**, worker ↔ **Cartesia**: these provider regions
  were **not** chosen by us **(check where each serves from)**. That's design §6 optimization
  step 2 ("region placement").
- Worker ↔ **Supabase Mumbai** (about 110–170 ms per query from the laptop) **(verified,
  LEARNING.md #13)**.

## Phone calls differ (week 4)
PSTN → SIP trunk (Exotel/Twilio) → LiveKit SIP → the room. Phone audio is usually **G.711 at
8 kHz** (narrowband) and is transcoded at the SIP edge **(check)**. That means less audio detail
for STT, so expect lower accuracy than in the browser (design §5.1), and measure the two
separately.

## Questions your AUDIO_PATH.md should answer (in your own words)
1. Why is audio sent over UDP rather than TCP, and what happens to a lost packet?
2. What does the jitter buffer trade off?
3. What's the difference between an SFU and an MCU, and why does LiveKit use an SFU?
4. Why are VAD, the turn detector and STT three separate things?
5. Where exactly does each of our measured delays (STT, end of turn, TTFT, TTS) happen on this
   path?
6. What happens, step by step, between the caller saying "wait" and the agent going silent?
7. What changes on a phone call, and why does it hurt STT accuracy?
8. If you had to cut 500 ms, which legs would you look at first, and why?
