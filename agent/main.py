"""LiveKit Agents worker: the real-time voice path (VAD -> turn detector -> STT -> LLM -> TTS)."""

from dotenv import load_dotenv
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    JobProcess,
    cli,
)
from livekit.plugins import silero

from agent import prompts, providers, telemetry

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
        turn_detection=providers.build_turn_detector(),
        stt=providers.build_stt(),
        llm=providers.build_llm(),
        tts=providers.build_tts(),
    )

    sink = telemetry.attach(
        session,
        call_id=ctx.job.id,
        # Browser only until the phone line arrives (week 4); then derive it from the
        # participant kind (SIP vs standard).
        channel="browser",
        language=language,
        providers=providers.describe(),
    )
    ctx.add_shutdown_callback(sink.aclose)

    await session.start(room=ctx.room, agent=Receptionist(language))
    session.generate_reply(instructions=prompts.GREETING[language])


if __name__ == "__main__":
    cli.run_app(server)
