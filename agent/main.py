"""LiveKit Agents worker: the real-time voice path (VAD -> turn detector -> STT -> LLM -> TTS)."""

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
from livekit.plugins import silero

from agent import prompts, providers

load_dotenv(".env")


class Receptionist(Agent):
    def __init__(self, language: str) -> None:
        super().__init__(instructions=prompts.RECEPTIONIST[language])


server = AgentServer()


def prewarm(proc: JobProcess) -> None:
    # Load VAD once per process so the first turn of a call does not pay for it.
    proc.userdata["vad"] = silero.VAD.load()


server.setup_fnc = prewarm


@server.rtc_session()
async def entrypoint(ctx: JobContext) -> None:
    language = providers.agent_language()
    # Every stage is passed explicitly so the provider choices are visible in code.
    session = AgentSession(
        vad=ctx.proc.userdata["vad"],
        # Pinned: the default picks cloud v1 in dev but local v1-mini in start, which would
        # make dev and deployed latency numbers incomparable. v1-mini runs locally (supports hi).
        turn_detection=inference.TurnDetector(version="v1-mini"),
        stt=providers.build_stt(),
        llm=providers.build_llm(),
        tts=providers.build_tts(),
    )

    await session.start(room=ctx.room, agent=Receptionist(language))
    session.generate_reply(instructions=prompts.GREETING[language])


if __name__ == "__main__":
    cli.run_app(server)
