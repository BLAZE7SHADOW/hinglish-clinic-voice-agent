import pytest

from scripts.latency_report import percentile, summarize


def test_percentile_matches_linear_interpolation():
    vals = [100, 200, 300, 400, 500]
    assert percentile(vals, 50) == 300
    assert percentile(vals, 95) == pytest.approx(480)
    assert percentile([], 50) is None
    assert percentile([42], 95) == 42


def test_summarize_ignores_missing_values():
    rows = [{"voice_to_voice_ms": 1000}, {"voice_to_voice_ms": None}, {}]
    v2v = next(s for s in summarize(rows) if s[0] == "Voice-to-voice")
    assert v2v[1] == 1 and v2v[2] == 1000


def test_turn_filter_labels_and_defaults():
    import argparse

    from scripts.latency_report import keep

    args = argparse.Namespace(
        include_cold=False,
        include_greeting=False,
        turn="v1",
        channel=None,
        language=None,
        since=None,
    )
    v1 = {"turn_index": 1, "user_speech_end_at": "x", "providers": '{"turn": "v1"}'}
    old = {"turn_index": 1, "user_speech_end_at": "x", "providers": {}}  # before E7 = v1-mini
    assert keep(v1, args) and not keep(old, args)
    args.turn = "v1-mini"
    assert keep(old, args) and not keep(v1, args)
