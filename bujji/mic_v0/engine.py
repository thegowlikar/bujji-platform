"""bujji.mic_v0.engine — Phase 20.1.

Composes THREE already-existing or newly-added-but-non-duplicative
inputs into one `MarketState`:

  1. `bujji.intelligence.regime_brain.RegimeBrain.analyze()` — REUSED
     VERBATIM, unmodified, for market_regime. This module never
     recomputes efficiency ratio or realized volatility itself.
  2. `volatility_classifier.classify_volatility_state()` — new (Phase
     20.1), VIX-percentile based, because the existing options-IV-based
     `volatility_brain` cannot run without historical options data
     Cycle 1 does not have.
  3. `risk_classifier.classify_risk_state()` — new (Phase 20.1), reuses
     `event_brain`'s own threshold constants, adds one new EXTREME tier.

`recommended_environment` is a small, disclosed, evidence-based rule
directly analogous in spirit to `market_environment.classify_
environment()` (Phase 19.9) but deliberately NOT calling that function:
`classify_environment()` requires a full `MarketStateNode`/
`DecisionIntelligenceSnapshot` and several options-derived fields
(IV richness, option-spread liquidity) that do not exist for Cycle 1's
futures/spot/VIX-only scope. Reusing it here would either silently
fail on missing fields or require fabricating options-shaped inputs —
both violate this phase's own anti-fabrication rule. This is a
narrower, Cycle-1-scoped rule instead.

`confidence` is ALWAYS `{level: NONE|LOW, sample_size: 0, method:
"pending Phase 20.1B validation"}` for every classification produced by
this module today — this package makes no claim about whether its
regime labels correspond to real market behavior. That claim can only
be made by `mic_v0_validation` (Phase 20.1B), which has not run yet at
the time this file is written. `RegimeBrain`'s own internal
`RegimeReading.confidence` (a continuous 0.5-1.0 heuristic scaled by
distance from a threshold) is NEVER surfaced as MIC v0's own confidence
— that heuristic is not sample-size-backed evidence, and re-exporting
it under a different name would be exactly the kind of fake-precision
this phase's charter explicitly forbids. It is still recorded in
`evidence` for transparency, clearly labeled as MODELED.
"""
from __future__ import annotations

from typing import List, Optional

from bujji.core.models import Candle
from bujji.intelligence.context import IntelligenceContext
from bujji.intelligence.models import RegimeType
from bujji.intelligence.regime_brain import RegimeBrain

from .models import (
    CONFIDENCE_LOW,
    CONFIDENCE_NONE,
    ENV_MEAN_REVERSION,
    ENV_NO_TRADE,
    ENV_TREND_FOLLOWING,
    REGIME_RANGE,
    REGIME_TREND,
    REGIME_UNCLEAR,
    RISK_EXTREME,
    ConfidenceInfo,
    EventContext,
    MarketState,
)
from .risk_classifier import classify_risk_state
from .volatility_classifier import classify_volatility_state

# regime_brain.RegimeType -> mic_v0 market_regime. Disclosed, not
# implicit: VOLATILE/COMPRESSED/TRANSITIONING/UNKNOWN do not cleanly
# indicate "price is trending" or "price is ranging" -- they indicate
# something ELSE is going on (volatility shift, regime change in
# progress, insufficient data), so they map to UNCLEAR rather than
# being forced into TREND or RANGE.
_REGIME_MAP = {
    RegimeType.TRENDING: REGIME_TREND,
    RegimeType.RANGING: REGIME_RANGE,
    RegimeType.VOLATILE: REGIME_UNCLEAR,
    RegimeType.COMPRESSED: REGIME_UNCLEAR,
    RegimeType.TRANSITIONING: REGIME_UNCLEAR,
    RegimeType.UNKNOWN: REGIME_UNCLEAR,
}

_regime_brain = RegimeBrain()


def _recommended_environment(market_regime: str, risk_state: str) -> List[str]:
    """Returns (environment is implied by the FIRST matching rule
    below; the evidence list explains which). EXTREME risk always
    forces NO_TRADE regardless of regime -- the charter's own Strategy
    C conditions ("extreme risk" is listed independently of "unclear
    regime"). No trade is a real, first-class output, never an
    afterthought fallback dressed up as a decision."""
    if risk_state == RISK_EXTREME:
        return [ENV_NO_TRADE, "risk_state=EXTREME forces NO_TRADE regardless of regime"]

    if market_regime == REGIME_TREND:
        return [ENV_TREND_FOLLOWING, "market_regime=TREND -> TREND_FOLLOWING"]

    if market_regime == REGIME_RANGE:
        return [ENV_MEAN_REVERSION, "market_regime=RANGE -> MEAN_REVERSION"]

    return [ENV_NO_TRADE, f"market_regime={market_regime} (not TREND or RANGE) -> NO_TRADE"]


def compose_market_state(
    candles: List[Candle], current_vix: float, trailing_vix_history: List[float],
    context: IntelligenceContext,
) -> MarketState:
    """`candles`: real NIFTY spot or futures 5-minute candles, sorted or
    unsorted (RegimeBrain sorts internally), ending at or before
    `context.as_of_time`. `current_vix`: the real VIX close/level as of
    `context.as_of_time`. `trailing_vix_history`: real VIX closes
    STRICTLY BEFORE `context.as_of_time`, oldest first — never including
    the current observation (no look-ahead)."""
    regime_reading = _regime_brain.analyze(candles, context)
    market_regime = _REGIME_MAP[regime_reading.regime]

    volatility_state, vol_evidence = classify_volatility_state(current_vix, trailing_vix_history)
    risk_state, risk_evidence = classify_risk_state(current_vix)

    data_quality = "SUFFICIENT" if (regime_reading.data_quality.value == "SUFFICIENT" and volatility_state is not None) else "INSUFFICIENT"

    if volatility_state is None:
        # Insufficient VIX history -- never guess. Report the
        # classification as UNCLEAR-adjacent by forcing NO_TRADE via
        # INSUFFICIENT data quality rather than fabricating a
        # volatility label.
        volatility_state_out = "NORMAL"  # placeholder value only used when data_quality=INSUFFICIENT
        recommended = [ENV_NO_TRADE, "volatility_state indeterminate (insufficient VIX history) -> NO_TRADE"]
    else:
        volatility_state_out = volatility_state
        recommended = _recommended_environment(market_regime, risk_state)

    evidence = (
        [f"regime_brain.regime={regime_reading.regime.value}", f"regime_brain.reason={regime_reading.reason}"]
        + [f"regime_brain.MODELED_confidence={regime_reading.confidence}"]
        + [f"regime_brain.{k}={v}" for k, v in regime_reading.evidence.items()]
        + [f"volatility: {line}" for line in vol_evidence]
        + [f"risk: {line}" for line in risk_evidence]
        + [recommended[1]]
    )

    confidence = ConfidenceInfo(
        level=CONFIDENCE_NONE if data_quality == "INSUFFICIENT" else CONFIDENCE_LOW,
        sample_size=0,
        method="pending Phase 20.1B validation",
        evaluation_window=None,
    )

    return MarketState(
        as_of_time=context.as_of_time.isoformat(),
        market_regime=market_regime,
        volatility_state=volatility_state_out,
        risk_state=risk_state,
        recommended_environment=recommended[0],
        evidence=tuple(evidence),
        confidence=confidence,
        event_context=EventContext(),
        data_quality=data_quality,
    )
