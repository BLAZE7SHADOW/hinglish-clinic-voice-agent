from livekit.agents import llm, stt, tts

from agent import providers


def fake_keys(monkeypatch, **env):
    for k in ("DEEPGRAM_API_KEY", "SARVAM_API_KEY", "CARTESIA_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.setenv(k, "test")
    for k, v in env.items():
        monkeypatch.setenv(k, v)


def test_each_stage_has_a_backup_of_another_provider(monkeypatch):
    fake_keys(monkeypatch, AGENT_LANGUAGE="en", STT_PROVIDER="deepgram", TTS_PROVIDER="cartesia")
    monkeypatch.setenv("AI_MODEL", "gemini-3.6-flash")

    s = providers.build_stt_with_fallback()
    assert isinstance(s, stt.FallbackAdapter)
    assert [i.provider for i in s._stt_instances] == ["Deepgram", "Sarvam"]

    t = providers.build_tts_with_fallback()
    assert isinstance(t, tts.FallbackAdapter)
    assert [i.provider for i in t._tts_instances] == ["Cartesia", "Sarvam"]

    m = providers.build_llm_with_fallback()
    assert isinstance(m, llm.FallbackAdapter)
    assert [i.model for i in m._llm_instances] == ["gemini-3.6-flash", "gemini-2.5-flash"]


def test_primary_follows_env(monkeypatch):
    fake_keys(monkeypatch, AGENT_LANGUAGE="en", STT_PROVIDER="sarvam", TTS_PROVIDER="sarvam")
    monkeypatch.setenv("AI_MODEL", "gemini-2.5-flash")
    assert [i.provider for i in providers.build_stt_with_fallback()._stt_instances] == [
        "Sarvam",
        "Deepgram",
    ]
    assert [i.provider for i in providers.build_tts_with_fallback()._tts_instances] == [
        "Sarvam",
        "Cartesia",
    ]
    assert len(providers.build_llm_with_fallback()._llm_instances) == 1  # no duplicate model
