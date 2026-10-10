"""LiveKit Agents worker: the real-time voice path (VAD -> turn detector -> STT -> LLM -> TTS)."""

import os

from dotenv import load_dotenv
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    JobProcess,
    cli,
    inference,
)
from livekit.plugins import cartesia, deepgram, google, silero

from agent import prompts

load_dotenv(".env")


class Receptionist(Agent):
    def __init__(self) -> None:
        super().__init__(instructions=prompts.RECEPTIONIST)


server = AgentServer()


def prewarm(proc: JobProcess) -> None:
    # Load VAD once per process so the first turn of a call does not pay for it.
    proc.userdata["vad"] = silero.VAD.load()


server.setup_fnc = prewarm


@server.rtc_session()
async def entrypoint(ctx: JobContext) -> None:
    # Every stage is passed explicitly so the provider choices are visible in code.
    session = AgentSession(
        vad=ctx.proc.userdata["vad"],
        # Pinned: the default picks cloud v1 in dev but local v1-mini in start, which would
        # make dev and deployed latency numbers incomparable. v1-mini runs locally (supports hi).
        turn_detection=inference.TurnDetector(version="v1-mini"),
        # "multi" lets nova-3 follow Hindi/English code-switching within a turn.
        stt=deepgram.STT(model="nova-3", language="multi"),
        llm=_llm(),
        tts=cartesia.TTS(model="sonic-3", language="hi", **_cartesia_voice()),
    )

    await session.start(room=ctx.room, agent=Receptionist())
    session.generate_reply(instructions=prompts.GREETING)


def _llm() -> google.LLM:
    provider = os.environ.get("AI_PROVIDER", "gemini")
    if provider != "gemini":
        raise ValueError(f"AI_PROVIDER={provider!r} is not wired up yet; only 'gemini' is")
    model = os.environ.get("AI_MODEL", "gemini-3.6-flash")
    # Thinking as low as the model allows: it adds directly to time to first token.
    # Gemini 3 takes a level, 2.5 and earlier take a token budget.
    thinking = {"thinking_level": "minimal"} if "gemini-3" in model else {"thinking_budget": 0}
    return google.LLM(model=model, api_key=os.environ["GEMINI_API_KEY"], thinking_config=thinking)


def _cartesia_voice() -> dict[str, str]:
    voice = os.environ.get("CARTESIA_VOICE_ID")
    return {"voice": voice} if voice else {}


if __name__ == "__main__":
    cli.run_app(server)
