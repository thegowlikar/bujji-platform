"""Phase 20.1 -- mic_v0.models tests."""
from __future__ import annotations

import pytest

from bujji.mic_v0.models import (
    CONFIDENCE_LOW,
    CONFIDENCE_MEDIUM,
    CONFIDENCE_NONE,
    EVENT_CONTEXT_NOT_AVAILABLE,
    ENV_MEAN_REVERSION,
    REGIME_RANGE,
    RISK_NORMAL,
    VOLATILITY_NORMAL,
    ConfidenceInfo,
    EventContext,
    MarketState,
)


def test_confidence_info_rejects_medium_with_zero_samples():
    with pytest.raises(ValueError):
        ConfidenceInfo(level=CONFIDENCE_MEDIUM, sample_size=0, method="x")


def test_confidence_info_allows_low_with_zero_samples():
    info = ConfidenceInfo(level=CONFIDENCE_LOW, sample_size=0, method="pending validation")
    assert info.sample_size == 0


def test_confidence_info_allows_medium_with_real_samples():
    info = ConfidenceInfo(level=CONFIDENCE_MEDIUM, sample_size=150, method="historical frequency")
    assert info.sample_size == 150


def test_event_context_defaults_to_not_available():
    assert EventContext().status == EVENT_CONTEXT_NOT_AVAILABLE


def test_market_state_rejects_unknown_regime():
    with pytest.raises(ValueError):
        MarketState(
            as_of_time="2026-01-01T09:15:00+05:30", market_regime="BOGUS",
            volatility_state=VOLATILITY_NORMAL, risk_state=RISK_NORMAL,
            recommended_environment=ENV_MEAN_REVERSION, evidence=(),
            confidence=ConfidenceInfo(level=CONFIDENCE_NONE, sample_size=0, method="x"),
        )


def test_market_state_to_dict_round_trip_shape():
    state = MarketState(
        as_of_time="2026-01-01T09:15:00+05:30", market_regime=REGIME_RANGE,
        volatility_state=VOLATILITY_NORMAL, risk_state=RISK_NORMAL,
        recommended_environment=ENV_MEAN_REVERSION, evidence=("a", "b"),
        confidence=ConfidenceInfo(level=CONFIDENCE_LOW, sample_size=0, method="x"),
    )
    d = state.to_dict()
    assert d["market_regime"] == REGIME_RANGE
    assert d["event_context"] == {"status": EVENT_CONTEXT_NOT_AVAILABLE}
    assert d["evidence"] == ["a", "b"]
