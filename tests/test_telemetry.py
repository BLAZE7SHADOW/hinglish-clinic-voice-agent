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


def llm_metrics(cancelled=False, **tokens):
    from livekit.agents import metrics

    base = {
        "label": "x",
        "request_id": "r",
        "timestamp": 0.0,
        "duration": 0.5,
        "ttft": 0.3,
        "cancelled": cancelled,
        "completion_tokens": 10,
        "prompt_tokens": 300,
        "prompt_cached_tokens": 0,
        "total_tokens": 310,
        "tokens_per_second": 20.0,
    }
    return metrics.LLMMetrics(**(base | tokens))


def test_token_counts_skip_cancelled_requests():
    from livekit.agents.voice.events import MetricsCollectedEvent

    b = builder()
    b.on_metrics(MetricsCollectedEvent(metrics=llm_metrics(cancelled=True, reasoning_tokens=99)))
    b.on_metrics(
        MetricsCollectedEvent(metrics=llm_metrics(prompt_cached_tokens=256, reasoning_tokens=12))
    )
    r = b.on_agent_message(agent())
    assert (r.llm_prompt_tokens, r.llm_cached_tokens, r.llm_reasoning_tokens) == (300, 256, 12)
    assert b.on_agent_message(agent()).llm_prompt_tokens is None  # reset per reply


def test_barge_in_without_adaptive_detector_uses_vad_states():
    # LiveKit falls back to VAD-based interruption when the cloud detector times out;
    # no overlapping_speech event is emitted then.
    from livekit.agents.voice.events import UserStateChangedEvent

    b = builder()
    b.on_agent_state(
        AgentStateChangedEvent(old_state="thinking", new_state="speaking", created_at=30.0)
    )
    b.on_user_state(
        UserStateChangedEvent(old_state="listening", new_state="speaking", created_at=31.0)
    )
    b.on_agent_state(
        AgentStateChangedEvent(old_state="speaking", new_state="listening", created_at=31.12)
    )
    r = b.on_agent_message(agent(interrupted=True))
    assert r.interrupt_stop_ms == 120.0
    assert r.interrupt_detect_ms is None  # only the adaptive detector reports this


def test_caller_speaking_while_agent_silent_is_not_barge_in():
    from livekit.agents.voice.events import UserStateChangedEvent

    b = builder()
    b.on_user_state(
        UserStateChangedEvent(old_state="listening", new_state="speaking", created_at=5.0)
    )
    b.on_agent_state(
        AgentStateChangedEvent(old_state="speaking", new_state="listening", created_at=9.0)
    )
    assert b.on_agent_message(agent(interrupted=True)).interrupt_stop_ms is None


def test_cold_call_means_worker_was_idle(tmp_path, monkeypatch):
    import os
    import time

    from agent import telemetry

    marker = tmp_path / ".last_call_end"
    monkeypatch.setattr(telemetry, "LAST_CALL_MARKER", marker)
    assert telemetry._is_cold_call()  # no call has ever ended
    marker.touch()
    assert not telemetry._is_cold_call()  # a call just ended
    old = time.time() - telemetry.IDLE_COLD_S - 1
    os.utime(marker, (old, old))
    assert telemetry._is_cold_call()  # idle longer than the threshold


def test_each_turn_records_the_interruption_mode():
    """#26: the adaptive detector falls back to VAD mid-call when its quota runs out."""
    from types import SimpleNamespace

    from agent.telemetry import interruption_mode

    def session(enabled):
        return SimpleNamespace(_activity=SimpleNamespace(_interruption_detection_enabled=enabled))

    assert interruption_mode(session(True)) == "adaptive"
    assert interruption_mode(session(False)) == "vad"
    assert interruption_mode(SimpleNamespace(_activity=None)) == "unknown"
