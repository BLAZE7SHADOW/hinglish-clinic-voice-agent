"""Provider wiring for STT, LLM and TTS.

Which provider runs is config (`STT_PROVIDER`, `AI_PROVIDER`, `TTS_PROVIDER` in `.env`), so
comparisons swap providers without code changes. How each one is tuned stays here in code.
`AGENT_LANGUAGE` picks the language settings for every stage at once (see LANGUAGES).
"""

import os

import aiohttp
from livekit.agents import inference, stt, tts
from livekit.plugins import cartesia, deepgram, google, sarvam

# Per-language settings for each provider. English first while the engineering is built;
# Hinglish audio returns in week 4 (design doc §14). Same keys in every entry.
LANGUAGES = {
    "en": {
        "deepgram": "en-IN",  # Indian-accented English
        "sarvam_stt": ("en-IN", "transcribe"),
        "cartesia": "en",
        "sarvam_tts": "en-IN",
    },
    "hinglish": {
        "deepgram": "multi",  # follows Hindi/English code-switching within a turn
        # codemix: built for Hinglish, keeps English words in Latin and Hindi in Devanagari.
        "sarvam_stt": ("hi-IN", "codemix"),
        "cartesia": "hi",
        "sarvam_tts": "hi-IN",
    },
}


def agent_language() -> str:
    lang = os.environ.get("AGENT_LANGUAGE", "en")
    if lang not in LANGUAGES:
        raise ValueError(f"AGENT_LANGUAGE={lang!r} is not supported; use one of {list(LANGUAGES)}")
    return lang


def build_stt() -> stt.STT:
    provider = os.environ.get("STT_PROVIDER", "deepgram")
    lang = LANGUAGES[agent_language()]
    if provider == "deepgram":
        return deepgram.STT(model="nova-3", language=lang["deepgram"])
    if provider == "sarvam":
        language, mode = lang["sarvam_stt"]
        return sarvam.STT(model="saaras:v4", language=language, mode=mode)
    raise ValueError(f"STT_PROVIDER={provider!r} is not supported; use 'deepgram' or 'sarvam'")


def build_llm() -> google.LLM:
    provider = os.environ.get("AI_PROVIDER", "gemini")
    if provider != "gemini":
        raise ValueError(f"AI_PROVIDER={provider!r} is not wired up yet; only 'gemini' is")
    model = os.environ.get("AI_MODEL", "gemini-3.6-flash")
    # Thinking as low as the model allows: it adds directly to time to first token.
    # Gemini 3 takes a level, 2.5 and earlier take a token budget.
    thinking = {"thinking_level": "minimal"} if "gemini-3" in model else {"thinking_budget": 0}
    return google.LLM(model=model, api_key=os.environ["GEMINI_API_KEY"], thinking_config=thinking)


# Voice tuning. Defaults at 1.0 sounded rushed and flat for a front-desk call, so pace is a
# little slower. Change these by ear with scripts/tts_samples.py, then confirm on a real call.
CARTESIA_SPEED = 0.9
SARVAM_PACE = 0.95


def build_tts(
    provider: str | None = None,
    *,
    http_session: aiohttp.ClientSession | None = None,
    **overrides,
) -> tts.TTS:
    """Build the TTS. `provider` and `overrides` exist for scripts/tts_samples.py."""
    provider = provider or os.environ.get("TTS_PROVIDER", "cartesia")
    lang = LANGUAGES[agent_language()]
    if provider == "cartesia":
        opts = {"model": "sonic-3", "language": lang["cartesia"], "speed": CARTESIA_SPEED}
        if voice := os.environ.get("CARTESIA_VOICE_ID"):
            opts["voice"] = voice
        return cartesia.TTS(**(opts | overrides), http_session=http_session)
    if provider == "sarvam":
        opts = {
            "model": "bulbul:v3",
            "target_language_code": lang["sarvam_tts"],
            "speaker": os.environ.get("SARVAM_SPEAKER") or "simran",
            "pace": SARVAM_PACE,
        }
        return sarvam.TTS(**(opts | overrides), http_session=http_session)
    raise ValueError(f"TTS_PROVIDER={provider!r} is not supported; use 'cartesia' or 'sarvam'")


def build_turn_detector() -> inference.TurnDetector:
    """TURN_DETECTOR=v1-mini (local, text only) or v1 (LiveKit cloud, also hears audio).

    Always pinned: LiveKit's default picks v1 in `dev` and v1-mini in `start`, which would make
    dev and deployed numbers incomparable (LEARNING.md #2). For v1, local_fallback is off so a
    gateway failure can't silently swap in v1-mini mid-experiment (LEARNING.md #8); turns then
    commit on the endpointing delay and the failure shows up in the log.
    """
    version = os.environ.get("TURN_DETECTOR", "v1-mini")
    if version == "v1-mini":
        return inference.TurnDetector(version="v1-mini")
    if version == "v1":
        return inference.TurnDetector(version="v1", local_fallback=False)
    raise ValueError(f"TURN_DETECTOR={version!r} is not supported; use 'v1-mini' or 'v1'")


def describe() -> dict[str, str]:
    """Provider/model labels recorded on every turn, so each number traces to a config."""
    return {
        "stt": os.environ.get("STT_PROVIDER", "deepgram"),
        "llm": f"{os.environ.get('AI_PROVIDER', 'gemini')}/"
        f"{os.environ.get('AI_MODEL', 'gemini-3.6-flash')}",
        "tts": os.environ.get("TTS_PROVIDER", "cartesia"),
        "turn": os.environ.get("TURN_DETECTOR", "v1-mini"),
    }
