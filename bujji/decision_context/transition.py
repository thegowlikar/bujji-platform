"""MarketStateTransition detection -- Phase 19.4.

A single snapshot is useful; a sequence is powerful. This module compares
two already-built `MarketIntelligenceSnapshot`s' own `posture` field
(Phase 19.3) and classifies the transition via one documented mapping
table -- never a new measurement, never a new indicator.
"""
from __future__ import annotations

from bujji.intelligence.market_intelligence_snapshot.models import MarketIntelligenceSnapshot, MarketPosture

from .models import MarketStateTransition, TransitionType

# Documented, exhaustive mapping -- (previous posture, current posture) -> named transition.
# Any pairing not listed here (including "no change") is UNKNOWN, per the
# phase's own explicit supported-transitions list.
_NAMED_TRANSITIONS = {
    (MarketPosture.COMPRESSION, MarketPosture.EXPANSION): TransitionType.COMPRESSION_TO_EXPANSION,
    (MarketPosture.RANGING, MarketPosture.TRENDING): TransitionType.RANGE_TO_TREND,
    (MarketPosture.TRENDING, MarketPosture.RANGING): TransitionType.TREND_TO_RANGE,
}


def detect_transition(
    previous: MarketIntelligenceSnapshot, current: MarketIntelligenceSnapshot,
) -> MarketStateTransition:
    # EVENT_RISK checked first and independently of the named-pair table:
    # any non-EVENT_RISK -> EVENT_RISK move is NORMAL_TO_EVENT_RISK
    # regardless of what the prior posture specifically was.
    if previous.posture != MarketPosture.EVENT_RISK and current.posture == MarketPosture.EVENT_RISK:
        transition_type = TransitionType.NORMAL_TO_EVENT_RISK
    else:
        transition_type = _NAMED_TRANSITIONS.get((previous.posture, current.posture), TransitionType.UNKNOWN)

    if transition_type == TransitionType.UNKNOWN:
        transition_reason = f"{previous.posture.value} -> {current.posture.value}: no named transition matched"
    else:
        transition_reason = f"{previous.posture.value} -> {current.posture.value}"

    evidence = (
        f"previous: {previous.thesis.primary_thesis}",
        f"current: {current.thesis.primary_thesis}",
    )

    return MarketStateTransition(
        previous_intelligence_snapshot=previous,
        current_intelligence_snapshot=current,
        transition_type=transition_type,
        transition_reason=transition_reason,
        evidence=evidence,
    )
