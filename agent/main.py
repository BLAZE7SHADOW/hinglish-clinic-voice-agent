"""LiveKit Agents worker: the real-time voice path (VAD -> turn detector -> STT -> LLM -> TTS)."""

import asyncio
import logging

from dotenv import load_dotenv
from livekit.agents import (
    AgentServer,
    AgentSession,
    JobContext,
    JobProcess,
    cli,
)
from livekit.plugins import silero

from agent import backchannel, db, prompts, providers, telemetry, vocabulary
from agent.modes import Receptionist
from agent.state import CallState

load_dotenv(".env")
backchannel.install()  # "okay"/"haan" while she talks must not stop her (agent/backchannel.py)

logger = logging.getLogger("agent")


server = AgentServer()


def prewarm(proc: JobProcess) -> None:
    # Load VAD once per process so the first turn of a call does not pay for it.
    proc.userdata["vad"] = silero.VAD.load()


server.setup_fnc = prewarm


@server.rtc_session()
async def entrypoint(ctx: JobContext) -> None:
    language = providers.agent_language()
    # Open DB connections now, in the background, so the first tool call doesn't pay for it.
    pool_warmup = asyncio.create_task(db.get_pool())
    pool_warmup.add_done_callback(
        lambda t: t.exception() and logger.warning("DB pool warm-up failed: %s", t.exception())
    )
    # Every stage is passed explicitly so the provider choices are visible in code. STT, LLM and
    # TTS each have a backup provider (providers.py) so one provider's outage doesn't end the call.
    session = AgentSession[CallState](
        # Typed call state that every tool's guard checks (agent/state.py).
        userdata=CallState(call_id=ctx.job.id),
        vad=ctx.proc.userdata["vad"],
        turn_detection=providers.build_turn_detector(),
        stt=providers.build_stt_with_fallback(),
        llm=providers.build_llm_with_fallback(),
        tts=providers.build_tts_with_fallback(),
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

    async def _usage() -> None:
        telemetry.write_usage(session, call_id=ctx.job.id)

    ctx.add_shutdown_callback(_usage)

    await session.start(room=ctx.room, agent=Receptionist(language))
    if vocabulary.enabled():  # E9: per-clinic names as STT keyterms, loaded without blocking
        keyterms = asyncio.create_task(vocabulary.clinic_keyterms())
        keyterms.add_done_callback(lambda t: _apply_keyterms(session, t))
    session.generate_reply(instructions=prompts.GREETING[language])


def _apply_keyterms(session: AgentSession, task: asyncio.Task[list[str]]) -> None:
    if task.cancelled() or task.exception():
        # Fail open: the call works without keyterms, names are just recognized less well.
        logger.warning("STT keyterms not loaded: %s", task.exception())
        return
    session.update_options(keyterms=task.result())
    logger.info("STT keyterms applied: %d terms", len(task.result()))


if __name__ == "__main__":
    cli.run_app(server)
