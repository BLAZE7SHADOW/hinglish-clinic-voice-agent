"""Render the same Hinglish lines with several TTS settings, to compare voices by ear.

    uv run python -m scripts.tts_samples            # all variants
    uv run python -m scripts.tts_samples sarvam     # only variants whose name contains "sarvam"

Writes one WAV per variant to samples/<language>/ (git-ignored). Lines follow AGENT_LANGUAGE. Sarvam variants need SARVAM_API_KEY.
"""

import asyncio
import os
import sys
import time
from pathlib import Path

import aiohttp
from dotenv import load_dotenv
from livekit import rtc
from livekit.agents import APIError

from agent.providers import CARTESIA_SPEED, SARVAM_PACE, agent_language, build_tts

LINES_BY_LANGUAGE = {
    "en": [
        "Hello, this is Simran from Sharma Family Clinic. How can I help you today?",
        "Okay, you'd like to see Doctor Sharma. One second, let me check.",
        "So, there's a slot tomorrow evening at five. Would that work for you?",
        "Sure. Let me confirm, Doctor Sharma, tomorrow at five in the evening. Is that right?",
    ],
    "hinglish": [
        "Namaste ji, Sharma Family Clinic se Simran bol rahi hoon. Bataiye, main aapki kya madad kar sakti hoon?",
        "Achha, aapko Doctor Sharma se milna hai. Ek second, main dekhti hoon.",
        "Dekhiye, kal shaam ko paanch baje ka slot khaali hai. Kya woh aapke liye theek rahega?",
        "Ji, bilkul. Toh main confirm kar deti hoon, Doctor Sharma, kal shaam paanch baje. Sahi hai?",
    ],
}

# (name, provider, overrides). The first entry of each provider is the current default.
VARIANTS = [
    ("cartesia-current", "cartesia", {}),
    ("cartesia-speed-1.0", "cartesia", {"speed": 1.0}),
    ("cartesia-speed-0.8", "cartesia", {"speed": 0.8}),
    ("cartesia-content", "cartesia", {"emotion": ["Content"]}),
    ("sarvam-v3-simran", "sarvam", {"speaker": "simran"}),
    (
        "sarvam-v4-simran-enhi-customer",
        "sarvam",
        {"model": "bulbul:v4-flash", "speaker": "simran_enhi_customer"},
    ),
    (
        "sarvam-v4-ishita-enhi-expressive",
        "sarvam",
        {"model": "bulbul:v4-flash", "speaker": "ishita_enhi_customer_expressive"},
    ),
    (
        "sarvam-v4-ritu-hi-customer-warm",
        "sarvam",
        {"model": "bulbul:v4-flash", "speaker": "ritu_hi_customer_warm"},
    ),
]

OUT = Path("samples")
GAP_S = 0.8  # silence between lines in the combined file


async def render(name: str, provider: str, overrides: dict, session: aiohttp.ClientSession) -> None:
    engine = build_tts(provider, http_session=session, **overrides)
    frames: list[rtc.AudioFrame] = []
    first_audio_ms = []
    for line in LINES_BY_LANGUAGE[agent_language()]:
        t0 = time.perf_counter()
        first = None
        async for ev in engine.synthesize(line):
            first = first or time.perf_counter()
            frames.append(ev.frame)
        first_audio_ms.append((first - t0) * 1000)
        silence = int(engine.sample_rate * GAP_S)
        frames.append(
            rtc.AudioFrame(bytes(silence * 2), engine.sample_rate, engine.num_channels, silence)
        )
    (OUT / agent_language() / f"{name}.wav").write_bytes(
        rtc.combine_audio_frames(frames).to_wav_bytes()
    )
    ttfa = ", ".join(f"{ms:.0f}" for ms in first_audio_ms)
    print(f"{name:34s} first-audio ms per line: {ttfa}  (first includes connection setup)")


async def main() -> None:
    load_dotenv(".env")
    (OUT / agent_language()).mkdir(parents=True, exist_ok=True)
    only = sys.argv[1] if len(sys.argv) > 1 else ""
    print(f"AGENT_LANGUAGE={agent_language()}")
    print(f"defaults: CARTESIA_SPEED={CARTESIA_SPEED} SARVAM_PACE={SARVAM_PACE}")
    async with aiohttp.ClientSession() as session:
        for name, provider, overrides in VARIANTS:
            if only not in name:
                continue
            if provider == "sarvam" and not os.environ.get("SARVAM_API_KEY"):
                print(f"{name:34s} skipped: SARVAM_API_KEY not set")
                continue
            try:
                await render(name, provider, overrides, session)
            # keep going so one bad variant doesn't hide the rest
            except (APIError, aiohttp.ClientError, ValueError) as e:
                print(f"{name:34s} FAILED: {type(e).__name__}: {e}")
    print(f"\nListen: open {OUT.resolve()}")


if __name__ == "__main__":
    asyncio.run(main())
