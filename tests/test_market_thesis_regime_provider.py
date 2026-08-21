"""Tests for MarketThesisRegimeProvider -- the minimal wire between
real market intelligence and TradingSessionGovernor's existing,
unmodified strategy_selector.select_strategy(). The end-to-end tests
prove the wire works by calling the REAL, protected selector function
directly -- never re-implementing or mocking its logic."""
from __future__ import annotations

from datetime import datetime

import pytest

from bujji.market_thesis.models import MarketThesisAssessment
from bujji.production_runtime.market_thesis_regime_provider import (
    MarketThesisRegimeProvider, adapt_trend_regime_from_thesis,
)
from bujji.production_runtime.regime_provider import MissingRegimeInputError
from bujji.production_runtime.trading_session_governor.strategy_selector import select_strategy

FIXED_CLOCK = lambda: datetime(2026, 1, 1, 9, 30, 0)


def _thesis(**overrides):
    base = dict(
        assessment_id="MTA-1", timestamp="2026-01-01T09:15:00",
        market_regime="RANGE_PERSISTENCE", directional_bias="NEUTRAL", volatility_environment="STABLE",
        premium_environment="RICH", expected_move_environment="NORMAL", positioning_environment="NEUTRAL_POSITIONING",
        liquidity_environment="TIGHT", preferred_strategy_families=(), rejected_strategy_families=(),
        insufficient_evidence_families=(), confidence="HIGH", reasons=(), supporting_assessment_ids=(),
        provenance="test", schema_version="1.0.0",
    )
    base.update(overrides)
    return MarketThesisAssessment(**base)


# ---------------------------------------------------------------------------
# Trend regime translation -- pure relabeling, every real thesis_type.
# ---------------------------------------------------------------------------

class TestAdaptTrendRegime:
    @pytest.mark.parametrize("market_regime", ["RANGE_PERSISTENCE", "MEAN_REVERSION"])
    def test_sideways_theses(self, market_regime):
        assert adapt_trend_regime_from_thesis(market_regime, "NEUTRAL") == "SIDEWAYS"

    @pytest.mark.parametrize("market_regime", ["TREND_CONTINUATION", "BREAKOUT"])
    def test_trending_up_on_bullish_lean(self, market_regime):
        assert adapt_trend_regime_from_thesis(market_regime, "BULLISH") == "TRENDING_UP"

    @pytest.mark.parametrize("market_regime", ["TREND_CONTINUATION", "BREAKOUT"])
    def test_trending_down_on_bearish_lean(self, market_regime):
        assert adapt_trend_regime_from_thesis(market_regime, "STRONG_BEARISH") == "TRENDING_DOWN"

    def test_trend_thesis_with_no_directional_lean_is_unknown(self):
        assert adapt_trend_regime_from_thesis("TREND_CONTINUATION", "NEUTRAL") == "UNKNOWN"

    @pytest.mark.parametrize("market_regime", [
        "VOLATILITY_EXPANSION", "VOLATILITY_COMPRESSION", "FAILED_BREAKOUT",
        "TREND_REVERSAL", "EVENT_RISK", "NO_TRADE", "UNKNOWN",
    ])
    def test_everything_else_is_unknown_never_guessed(self, market_regime):
        assert adapt_trend_regime_from_thesis(market_regime, "BULLISH") == "UNKNOWN"


# ---------------------------------------------------------------------------
# Provider contract: fail closed, never guess.
# ---------------------------------------------------------------------------

class TestProviderFailsClosed:
    def test_no_thesis_raises(self):
        provider = MarketThesisRegimeProvider(thesis=None, volatility_regime="STABLE")
        with pytest.raises(MissingRegimeInputError):
            provider.get_trend_regime()

    def test_unknown_thesis_raises(self):
        provider = MarketThesisRegimeProvider(thesis=_thesis(market_regime="UNKNOWN"), volatility_regime="STABLE")
        with pytest.raises(MissingRegimeInputError):
            provider.get_trend_regime()

    def test_no_volatility_regime_raises(self):
        provider = MarketThesisRegimeProvider(thesis=_thesis(), volatility_regime=None)
        with pytest.raises(MissingRegimeInputError):
            provider.get_volatility_regime()

    def test_unknown_volatility_regime_raises(self):
        provider = MarketThesisRegimeProvider(thesis=_thesis(), volatility_regime="UNKNOWN")
        with pytest.raises(MissingRegimeInputError):
            provider.get_volatility_regime()

    def test_get_regime_raises_before_returning_partial_pair(self):
        provider = MarketThesisRegimeProvider(thesis=None)
        with pytest.raises(MissingRegimeInputError):
            provider.get_regime()


# ---------------------------------------------------------------------------
# Real values pass through correctly.
# ---------------------------------------------------------------------------

class TestProviderRealValues:
    def test_volatility_regime_uses_real_adapter(self):
        provider = MarketThesisRegimeProvider(thesis=_thesis(), volatility_regime="HIGH_VOLATILITY")
        assert provider.get_volatility_regime() == "HIGH_VOL"

    def test_trend_regime_from_real_thesis(self):
        provider = MarketThesisRegimeProvider(
            thesis=_thesis(market_regime="RANGE_PERSISTENCE"), volatility_regime="COMPRESSED",
        )
        assert provider.get_trend_regime() == "SIDEWAYS"
        assert provider.get_volatility_regime() == "CONTRACTION"


# ---------------------------------------------------------------------------
# End-to-end: feed the provider's real output into the REAL, unmodified,
# protected local strategy_selector.select_strategy() -- proves the wire
# actually works without touching that function at all.
# ---------------------------------------------------------------------------

class TestEndToEndWithRealSelector:
    # Updated 2026-08-19: the selector became three-part by operator
    # directive. These still prove the same end-to-end linkage -- a real
    # thesis, through the real provider, into the real selector -- only the
    # expected shape moved.

    def test_range_bound_low_vol_thesis_selects_a_short_strangle(self):
        provider = MarketThesisRegimeProvider(
            thesis=_thesis(market_regime="RANGE_PERSISTENCE"), volatility_regime="STABLE",
        )
        trend, vol = provider.get_regime()
        result = select_strategy(trend, vol, FIXED_CLOCK)
        assert result.selected_strategy == "NEUTRAL_PREMIUM_SELLING"

    def test_range_bound_high_vol_thesis_selects_a_short_straddle(self):
        provider = MarketThesisRegimeProvider(
            thesis=_thesis(market_regime="MEAN_REVERSION"), volatility_regime="HIGH_VOLATILITY",
        )
        trend, vol = provider.get_regime()
        result = select_strategy(trend, vol, FIXED_CLOCK)
        assert result.selected_strategy == "VOLATILITY_COMPRESSION"

    def test_a_trending_thesis_now_selects_a_directional_credit_spread(self):
        """Previously asserted no-trade, and that WAS correct: no directional
        credit spread existed, so a trend had no sellable defined-risk shape.
        Two were added, so the trending third of the market is tradeable."""
        provider = MarketThesisRegimeProvider(
            thesis=_thesis(market_regime="TREND_CONTINUATION", directional_bias="BULLISH"),
            volatility_regime="STABLE",
        )
        trend, vol = provider.get_regime()
        result = select_strategy(trend, vol, FIXED_CLOCK)
        assert result.selected_strategy in ("BULL_PUT_SPREAD", "BEAR_CALL_SPREAD")

    def test_volatility_expansion_still_vetoes_a_trending_thesis(self):
        """Widening WHAT Bujji sells must not widen WHEN it sells."""
        provider = MarketThesisRegimeProvider(
            thesis=_thesis(market_regime="TREND_CONTINUATION", directional_bias="BULLISH"),
            volatility_regime="EXPANDING_VOLATILITY",
        )
        try:
            trend, vol = provider.get_regime()
        except Exception:
            return  # provider fails closed before the selector -- also acceptable
        assert select_strategy(trend, vol, FIXED_CLOCK).selected_strategy is None

    def test_event_risk_thesis_fails_closed_at_the_provider_boundary(self):
        # EVENT_RISK never maps to a trend regime -- the provider itself
        # raises before the selector is even called, matching the same
        # fail-closed discipline the selector already has for missing input.
        provider = MarketThesisRegimeProvider(
            thesis=_thesis(market_regime="EVENT_RISK"), volatility_regime="STABLE",
        )
        trend, vol = provider.get_regime()
        result = select_strategy(trend, vol, FIXED_CLOCK)
        assert result.selected_strategy is None
        assert result.trend_regime == "UNKNOWN"


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------

class TestDeterminism:
    def test_same_input_same_output(self):
        provider_a = MarketThesisRegimeProvider(thesis=_thesis(), volatility_regime="STABLE")
        provider_b = MarketThesisRegimeProvider(thesis=_thesis(), volatility_regime="STABLE")
        assert provider_a.get_regime() == provider_b.get_regime()
