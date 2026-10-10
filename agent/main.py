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

load_dotenv(".env.local")


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
        # Thinking off: it adds directly to time to first token.
        llm=google.LLM(model="gemini-2.5-flash", thinking_config={"thinking_budget": 0}),
        tts=cartesia.TTS(model="sonic-3", language="hi", **_cartesia_voice()),
    )

    await session.start(room=ctx.room, agent=Receptionist())
    session.generate_reply(instructions=prompts.GREETING)


def _cartesia_voice() -> dict[str, str]:
    voice = os.environ.get("CARTESIA_VOICE_ID")
    return {"voice": voice} if voice else {}


if __name__ == "__main__":
    cli.run_app(server)
