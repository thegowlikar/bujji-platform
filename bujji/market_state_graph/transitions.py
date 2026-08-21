"""Transition detection -- Phase 19.8.

Reuses `bujji.decision_context.transition.detect_transition()` directly
for the 4 posture-based transition types Phase 19.4 already built
(`COMPRESSION_TO_EXPANSION`, `RANGE_TO_TREND`, `TREND_TO_RANGE`,
`NORMAL_TO_EVENT_RISK`) -- confirmed by this phase's own audit as an
exact vocabulary match, never reimplemented. Adds two new detectors
for the two transition types Phase 19.4 has no member for
(`LIQUIDITY_NORMAL_TO_STRESS`, `LIQUIDITY_STRESS_RECOVERY`), built
directly on Phase 19.7's own `PHENOMENON_LIQUIDITY_STRESS` detection --
no new liquidity classification logic, only a comparison of two
already-real `MarketPhenomenaAssessment` outputs.
"""
from __future__ import annotations

from typing import Optional, Tuple

from bujji.decision_context.models import TransitionType as _DecisionContextTransitionType
from bujji.decision_context.transition import detect_transition as _detect_posture_transition
from bujji.intelligence.market_intelligence_snapshot.models import MarketIntelligenceSnapshot
from bujji.market_phenomena.models import PHENOMENON_LIQUIDITY_STRESS, MarketPhenomenaAssessment

# The 4 values reused VERBATIM from decision_context.TransitionType
# (Phase 19.4) -- same string values, not re-declared independently.
COMPRESSION_TO_EXPANSION = _DecisionContextTransitionType.COMPRESSION_TO_EXPANSION.value
RANGE_TO_TREND = _DecisionContextTransitionType.RANGE_TO_TREND.value
TREND_TO_RANGE = _DecisionContextTransitionType.TREND_TO_RANGE.value
NORMAL_TO_EVENT_RISK = _DecisionContextTransitionType.NORMAL_TO_EVENT_RISK.value

# The 2 new values this phase adds -- no equivalent exists upstream.
LIQUIDITY_NORMAL_TO_STRESS = "LIQUIDITY_NORMAL_TO_STRESS"
LIQUIDITY_STRESS_RECOVERY = "LIQUIDITY_STRESS_RECOVERY"

UNKNOWN = _DecisionContextTransitionType.UNKNOWN.value

ALL_STATE_TRANSITION_TYPES = (
    COMPRESSION_TO_EXPANSION, RANGE_TO_TREND, TREND_TO_RANGE, NORMAL_TO_EVENT_RISK,
    LIQUIDITY_NORMAL_TO_STRESS, LIQUIDITY_STRESS_RECOVERY, UNKNOWN,
)


def detect_posture_transition_type(
    previous_snapshot: MarketIntelligenceSnapshot, current_snapshot: MarketIntelligenceSnapshot,
) -> Tuple[str, Tuple[str, ...]]:
    """Delegates entirely to Phase 19.4's own `detect_transition()`.
    Returns (transition_type, evidence) -- `UNKNOWN` when no named
    posture transition matched, exactly as Phase 19.4 already defines
    it (including "no posture change")."""
    result = _detect_posture_transition(previous_snapshot, current_snapshot)
    return result.transition_type.value, result.evidence


def detect_liquidity_transition_type(
    previous_phenomena: Optional[MarketPhenomenaAssessment], current_phenomena: MarketPhenomenaAssessment,
) -> Optional[Tuple[str, Tuple[str, ...]]]:
    """Compares whether Phase 19.7's own LIQUIDITY_STRESS phenomenon was
    present before versus now. Returns `None` when there is no real
    `previous_phenomena` to compare against (structurally cannot detect
    a transition from a single instant) or when liquidity state did not
    change."""
    if previous_phenomena is None:
        return None

    was_stressed = any(p.phenomenon_type == PHENOMENON_LIQUIDITY_STRESS for p in previous_phenomena.phenomena)
    is_stressed = any(p.phenomenon_type == PHENOMENON_LIQUIDITY_STRESS for p in current_phenomena.phenomena)

    if not was_stressed and is_stressed:
        stress_phenomenon = next(p for p in current_phenomena.phenomena if p.phenomenon_type == PHENOMENON_LIQUIDITY_STRESS)
        evidence = tuple(f"{e.source}.{e.metric}={e.value}" for e in stress_phenomenon.supporting_evidence)
        return LIQUIDITY_NORMAL_TO_STRESS, evidence
    if was_stressed and not is_stressed:
        prior_stress_phenomenon = next(p for p in previous_phenomena.phenomena if p.phenomenon_type == PHENOMENON_LIQUIDITY_STRESS)
        evidence = tuple(f"{e.source}.{e.metric}={e.value}" for e in prior_stress_phenomenon.supporting_evidence)
        return LIQUIDITY_STRESS_RECOVERY, evidence
    return None
