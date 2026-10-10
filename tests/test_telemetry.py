from livekit.agents import llm
from livekit.agents.inference.interruption import OverlappingSpeechEvent
from livekit.agents.voice.events import AgentStateChangedEvent, FunctionToolsExecutedEvent

from agent.telemetry import TurnBuilder


def builder() -> TurnBuilder:
    return TurnBuilder(
        call_id="AJ_test",
        channel="browser",
        language="en",
        providers={"stt": "deepgram", "llm": "gemini/x", "tts": "cartesia"},
        cold_call=False,
    )


def user(**metrics) -> llm.ChatMessage:
    return llm.ChatMessage(role="user", content=["hello"], metrics=metrics)


def agent(interrupted: bool = False, **metrics) -> llm.ChatMessage:
    return llm.ChatMessage(
        role="assistant", content=["hi"], interrupted=interrupted, metrics=metrics
    )


def test_greeting_has_no_caller_side():
    r = builder().on_agent_message(agent(llm_node_ttft=0.4, started_speaking_at=100.0))
    assert r.turn_index == 0
    assert r.user_speech_end_at is None and r.voice_to_voice_ms is None
    assert r.llm_ttft_ms == 400.0


def test_pairs_caller_with_reply_and_converts_to_ms():
    b = builder()
    b.on_agent_message(agent())  # greeting
    b.on_user_message(
        user(
            started_speaking_at=10.0,
            stopped_speaking_at=12.0,
            end_of_turn_delay=0.31,
            transcription_delay=0.48,
        )
    )
    r = b.on_agent_message(
        agent(
            llm_node_ttft=0.62,
            llm_node_ttfs=0.9,
            tts_node_ttfb=0.18,
            e2e_latency=1.41,
            started_speaking_at=13.41,
        )
    )
    assert r.turn_index == 1
    assert (r.end_of_turn_ms, r.stt_final_ms) == (310.0, 480.0)
    assert (r.llm_ttft_ms, r.llm_first_sentence_ms, r.tts_ttfb_ms) == (620.0, 900.0, 180.0)
    assert r.voice_to_voice_ms == 1410.0
    assert r.user_speech_end_at.timestamp() == 12.0


def test_uses_last_caller_segment_and_resets_between_turns():
    b = builder()
    b.on_user_message(user(stopped_speaking_at=5.0))
    b.on_user_message(user(stopped_speaking_at=8.0))
    assert b.on_agent_message(agent()).user_speech_end_at.timestamp() == 8.0
    assert b.on_agent_message(agent()).user_speech_end_at is None


def test_barge_in_stop_time_from_speech_onset_to_agent_silence():
    b = builder()
    b.on_overlap(
        OverlappingSpeechEvent(
            is_interruption=True, overlap_started_at=20.0, detected_at=20.36, detection_delay=0.36
        )
    )
    b.on_agent_state(
        AgentStateChangedEvent(old_state="speaking", new_state="listening", created_at=20.45)
    )
    r = b.on_agent_message(agent(interrupted=True))
    assert r.interrupted
    assert r.interrupt_detect_ms == 360.0
    assert r.interrupt_stop_ms == 450.0


def test_backchannel_is_not_an_interruption():
    b = builder()
    b.on_overlap(OverlappingSpeechEvent(is_interruption=False, overlap_started_at=20.0))
    b.on_agent_state(AgentStateChangedEvent(old_state="speaking", new_state="listening"))
    r = b.on_agent_message(agent())
    assert not r.interrupted and r.interrupt_stop_ms is None


def test_tool_durations():
    b = builder()
    b.on_tools(
        FunctionToolsExecutedEvent(
            function_calls=[
                llm.FunctionCall(call_id="c1", name="find_slots", arguments="{}", created_at=1.0)
            ],
            function_call_outputs=[
                llm.FunctionCallOutput(call_id="c1", output="[]", is_error=False, created_at=1.25)
            ],
        )
    )
    [tool] = b.on_agent_message(agent()).tool_calls
    assert (tool.name, tool.duration_ms, tool.is_error) == ("find_slots", 250.0, False)
