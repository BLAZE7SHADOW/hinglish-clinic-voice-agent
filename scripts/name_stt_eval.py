"""E9: do per-clinic STT keyterms improve doctor-name recognition without causing harm?

A synthetic A/B test, independent of any single test call:
- **Names:** 24 common Indian surnames. Each of several synthetic "clinics" gets a seeded random
  roster of doctors (these become the keyterms). The other names are callers asking for doctors
  who are *not* on the roster.
- **Audio:** one sentence per name and template, spoken by two TTS voices (Cartesia, and Sarvam
  Indian English), at browser quality (16 kHz) and phone quality (8 kHz).
- **STT:** Deepgram nova-3 en-IN, once without keyterms and once with the clinic's keyterms.

Metrics, per condition (n shown next to every rate):
- roster recall: a roster name is transcribed correctly (keyterms should raise this)
- off-roster recall: a non-roster name is transcribed correctly (keyterms must not lower this)
- hijack rate: a non-roster name comes back as a roster name (the main risk of boosting)

Limits: TTS speech is cleaner than real callers, and this uses Deepgram's prerecorded API, while
calls use streaming. It ranks the options; a short live check confirms the winner.

    uv run python -m scripts.name_stt_eval            # renders audio once to samples/name_eval/
    uv run python -m scripts.name_stt_eval --clinics 3
"""

import argparse
import asyncio
import hashlib
import random
import re
import wave
from io import BytesIO
from pathlib import Path

import aiohttp
from dotenv import load_dotenv
from livekit import rtc
from livekit.agents import APIStatusError
from livekit.plugins import deepgram

from agent.providers import build_tts
from agent.vocabulary import keyterms_for

load_dotenv(".env")

OUT = Path("samples/name_eval")
SURNAMES = [
    "Sharma", "Mehta", "Iyer", "Verma", "Reddy", "Nair", "Kulkarni", "Deshpande",
    "Chatterjee", "Banerjee", "Gupta", "Joshi", "Pillai", "Menon", "Rao", "Patil",
    "Shetty", "Bhattacharya", "Mukherjee", "Krishnan", "Agarwal", "Sinha", "Choudhary", "Khanna",
]  # fmt: skip
FIRST_NAMES = ["Anil", "Priya", "Kavita", "Rahul", "Sunita", "Vikram", "Neha", "Arjun"]
TEMPLATES = [
    "I want to see Doctor {surname} tomorrow.",
    "Is Doctor {surname} free on Monday evening?",
    "Can I get an appointment with Doctor {first} {surname}?",
]
VOICES = ["cartesia", "sarvam"]
ROSTER_SIZE = 6
CLINIC_NAME = "Sharma Family Clinic"


def wav_frame(data: bytes) -> rtc.AudioFrame:
    with wave.open(BytesIO(data)) as w:
        return rtc.AudioFrame(
            w.readframes(w.getnframes()), w.getframerate(), w.getnchannels(), w.getnframes()
        )


async def render(text: str, voice: str, http: aiohttp.ClientSession) -> rtc.AudioFrame:
    path = OUT / f"{voice}-{hashlib.sha1(text.encode()).hexdigest()[:10]}.wav"
    if not path.exists():
        engine = build_tts(voice, http_session=http)
        for attempt in range(6):  # free-tier TTS rate limits (429): back off and retry
            try:
                frames = [ev.frame async for ev in engine.synthesize(text)]
                break
            except APIStatusError as e:
                if e.status_code != 429 or attempt == 5:
                    raise
                await asyncio.sleep(2**attempt)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(rtc.combine_audio_frames(frames).to_wav_bytes())
    return wav_frame(path.read_bytes())


def to_phone(frame: rtc.AudioFrame) -> rtc.AudioFrame:
    """Narrowband 8 kHz, as on a phone line (week 4)."""
    resampler = rtc.AudioResampler(frame.sample_rate, 8000)
    return rtc.combine_audio_frames([*resampler.push(frame), *resampler.flush()])


def heard(surname: str, transcript: str) -> bool:
    return re.search(rf"\b{surname}\b", transcript, re.IGNORECASE) is not None


async def transcribe(stt: deepgram.STT, frame: rtc.AudioFrame) -> str:
    ev = await stt.recognize(frame, language="en-IN")
    return ev.alternatives[0].text if ev.alternatives else ""


def rate(hits: list[bool]) -> str:
    return f"{sum(hits) / len(hits):6.1%} (n={len(hits)})" if hits else "   -"


async def main(clinics: int, seed: int) -> None:
    rng = random.Random(seed)
    results: dict[tuple[str, str], dict[str, list[bool]]] = {}
    misses: list[str] = []
    limit = asyncio.Semaphore(12)  # parallel requests, well under provider rate limits

    async with aiohttp.ClientSession() as http:
        plain = deepgram.STT(model="nova-3", language="en-IN", http_session=http)

        async def one(text, voice, surname, roster, boosted) -> None:
            async with limit:
                audio = await render(text, voice, http)
                for channel, frame in (("browser", audio), ("phone", to_phone(audio))):
                    for cond, stt in (("off", plain), ("on", boosted)):
                        t = await transcribe(stt, frame)
                        r = results.setdefault((channel, cond), {})
                        on_roster = surname in roster
                        r.setdefault("roster" if on_roster else "off_roster", []).append(
                            heard(surname, t)
                        )
                        if not on_roster:
                            r.setdefault("hijack", []).append(any(heard(s, t) for s in roster))
                        if cond == "on" and not heard(surname, t):
                            misses.append(f"  [{channel}/{voice}] {text!r} -> {t!r}")

        # Render each distinct sentence once before fanning out (avoids duplicate TTS calls).
        jobs = []
        for c in range(clinics):
            roster = rng.sample(SURNAMES, ROSTER_SIZE)
            doctors = [f"Dr. {rng.choice(FIRST_NAMES)} {s}" for s in roster]
            boosted = deepgram.STT(
                model="nova-3",
                language="en-IN",
                keyterm=keyterms_for(doctors, CLINIC_NAME),
                http_session=http,
            )
            print(f"clinic {c + 1}: roster {', '.join(roster)}")
            for surname in SURNAMES:
                first = next((d.split()[1] for d in doctors if d.endswith(surname)), "Rahul")
                for template in TEMPLATES:
                    text = template.format(first=first, surname=surname)
                    for voice in VOICES:
                        jobs.append((text, voice, surname, roster, boosted))
        tts_limit = asyncio.Semaphore(2)  # TTS plans allow few concurrent requests

        unrendered: set[tuple[str, str]] = set()

        async def render_one(text: str, voice: str) -> None:
            async with tts_limit:
                try:
                    await render(text, voice, http)
                except APIStatusError as e:  # e.g. 402 out of credits: skip, and say so
                    unrendered.add((text, voice))
                    print(f"  could not render [{voice}] {text!r}: {e.status_code}")

        await asyncio.gather(*(render_one(t, v) for t, v in {(j[0], j[1]) for j in jobs}))
        jobs = [j for j in jobs if (j[0], j[1]) not in unrendered]
        print(f"skipped {len(unrendered)} sentences that could not be rendered")
        await asyncio.gather(*(one(*j) for j in jobs))

    print("\nchannel  keyterms  roster recall      off-roster recall  hijack rate")
    for (channel, cond), r in sorted(results.items()):
        print(
            f"{channel:8} {cond:8}  {rate(r.get('roster', []))}  "
            f"{rate(r.get('off_roster', []))}  {rate(r.get('hijack', []))}"
        )
    if misses:
        print(f"\nmisses with keyterms on ({len(misses)}, first 15):")
        print("\n".join(misses[:15]))


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--clinics", type=int, default=3, help="synthetic clinics (rosters) to test")
    p.add_argument("--seed", type=int, default=7)
    args = p.parse_args()
    asyncio.run(main(args.clinics, args.seed))
