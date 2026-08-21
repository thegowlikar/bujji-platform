"""Phase 20.1 -- mic_v0.volatility_classifier tests."""
from __future__ import annotations

from bujji.mic_v0.models import VOLATILITY_HIGH, VOLATILITY_LOW, VOLATILITY_NORMAL
from bujji.mic_v0.volatility_classifier import MIN_WINDOW, classify_volatility_state


def test_insufficient_history_returns_none_not_a_guess():
    state, evidence = classify_volatility_state(15.0, [12.0] * (MIN_WINDOW - 1))
    assert state is None
    assert any("insufficient_history" in e for e in evidence)


def test_low_percentile_classified_low():
    history = list(range(10, 10 + MIN_WINDOW))  # 10..69
    state, _ = classify_volatility_state(current_vix=11.0, trailing_vix_history=[float(v) for v in history])
    assert state == VOLATILITY_LOW


def test_high_percentile_classified_high():
    history = list(range(10, 10 + MIN_WINDOW))
    state, _ = classify_volatility_state(current_vix=68.0, trailing_vix_history=[float(v) for v in history])
    assert state == VOLATILITY_HIGH


def test_middle_percentile_classified_normal():
    history = list(range(10, 10 + MIN_WINDOW))
    mid = 10 + MIN_WINDOW / 2
    state, _ = classify_volatility_state(current_vix=mid, trailing_vix_history=[float(v) for v in history])
    assert state == VOLATILITY_NORMAL


def test_evidence_discloses_method_and_sample_size():
    history = [15.0] * MIN_WINDOW
    _, evidence = classify_volatility_state(15.0, history)
    joined = " ".join(evidence)
    assert f"trailing_window_n={MIN_WINDOW}" in joined
    assert "percentile=" in joined
