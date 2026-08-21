"""Environment classification rules -- Phase 19.9.

One documented rule per `EnvironmentType`, each a plain, deterministic
check over already-real fields on `MarketStateNode` (Phase 19.8) and
`DecisionIntelligenceSnapshot` (Phase 19.6) -- never a new indicator,
never free-form reasoning. `STAND_ASIDE` is checked FIRST (safety takes
priority, same discipline `decision_intelligence.reasoning.derive_posture()`
already established for `REDUCE_EXPOSURE`) and is also the honest
fallback when no other category's real conditions are met.
"""
from __future__ import annotations

from typing import List, Tuple

from bujji.decision_intelligence.models import DecisionIntelligenceSnapshot, DecisionPosture
from bujji.market_phenomena.models import (
    PHENOMENON_EVENT_RISK,
    PHENOMENON_LIQUIDITY_STRESS,
    PHENOMENON_VOLATILITY_COMPRESSION,
    PHENOMENON_VOLATILITY_EXPANSION,
)
from bujji.market_state_graph.models import MarketStateNode

from .models import CONFIDENCE_HIGH, CONFIDENCE_LOW, CONFIDENCE_MODERATE, EnvironmentType

_RANGING_REGIMES = {"RANGING", "COMPRESSED"}


def _has_phenomenon(node: MarketStateNode, phenomenon_type: str) -> bool:
    return phenomenon_type in node.phenomena


def _classify_premium_selling_favourable(
    node: MarketStateNode, decision_intelligence: DecisionIntelligenceSnapshot,
) -> Tuple[bool, List[str], List[str]]:
    supporting, blocking = [], []
    conditions_met = 0
    if node.volatility_state == "IV_RICH":
        supporting.append("volatility: IV_RICH")
        conditions_met += 1
    else:
        blocking.append(f"volatility: {node.volatility_state or 'UNKNOWN'} (need IV_RICH)")
    if node.liquidity_state in ("TIGHT", "NORMAL"):
        supporting.append(f"liquidity: {node.liquidity_state}")
        conditions_met += 1
    else:
        blocking.append(f"liquidity: {node.liquidity_state or 'UNKNOWN'} (need TIGHT/NORMAL)")
    if node.event_state == "NORMAL":
        supporting.append("event: NORMAL")
        conditions_met += 1
    else:
        blocking.append("event: EVENT_RISK")
    if node.regime in _RANGING_REGIMES:
        supporting.append(f"regime: {node.regime}")
        conditions_met += 1
    else:
        blocking.append(f"regime: {node.regime or 'UNKNOWN'} (need RANGING/COMPRESSED)")
    if decision_intelligence.contradictions:
        blocking.append("contradictions present")

    matched = conditions_met == 4 and not decision_intelligence.contradictions
    return matched, supporting, blocking


def _classify_premium_selling_unfavourable(node: MarketStateNode) -> Tuple[bool, List[str], List[str]]:
    supporting, blocking = [], []
    if node.liquidity_state == "WIDE":
        supporting.append("liquidity: WIDE")
    if node.event_state == "EVENT_RISK":
        supporting.append("event: EVENT_RISK")
    if _has_phenomenon(node, PHENOMENON_VOLATILITY_EXPANSION):
        supporting.append("phenomenon: VOLATILITY_EXPANSION")
    matched = bool(supporting)
    return matched, supporting, blocking


def _classify_trend_following_favourable(node: MarketStateNode) -> Tuple[bool, List[str], List[str]]:
    supporting, blocking = [], []
    conditions_met = 0
    if node.regime == "TRENDING":
        supporting.append("regime: TRENDING (confirmed)")
        conditions_met += 1
    else:
        blocking.append(f"regime: {node.regime or 'UNKNOWN'} (need TRENDING)")
    if _has_phenomenon(node, PHENOMENON_VOLATILITY_EXPANSION):
        supporting.append("phenomenon: VOLATILITY_EXPANSION")
        conditions_met += 1
    else:
        blocking.append("no VOLATILITY_EXPANSION phenomenon detected")
    if node.liquidity_state in ("TIGHT", "NORMAL"):
        supporting.append(f"liquidity: {node.liquidity_state}")
        conditions_met += 1
    else:
        blocking.append(f"liquidity: {node.liquidity_state or 'UNKNOWN'} (need TIGHT/NORMAL)")

    matched = conditions_met == 3
    return matched, supporting, blocking


def _classify_mean_reversion_favourable(node: MarketStateNode) -> Tuple[bool, List[str], List[str]]:
    supporting, blocking = [], []
    conditions_met = 0
    if node.regime == "RANGING":
        supporting.append("regime: RANGING")
        conditions_met += 1
    else:
        blocking.append(f"regime: {node.regime or 'UNKNOWN'} (need RANGING)")
    if not _has_phenomenon(node, PHENOMENON_VOLATILITY_EXPANSION) and not _has_phenomenon(node, PHENOMENON_VOLATILITY_COMPRESSION):
        supporting.append("volatility: stable (no expansion/compression phenomenon)")
        conditions_met += 1
    else:
        blocking.append("volatility phenomenon present -- not stable")
    no_major_transition = node.transition is None or node.transition.transition_type == "UNKNOWN"
    if no_major_transition:
        supporting.append("no major transition")
        conditions_met += 1
    else:
        blocking.append(f"transition in progress: {node.transition.transition_type}")

    matched = conditions_met == 3
    return matched, supporting, blocking


def _classify_stand_aside(
    node: MarketStateNode, decision_intelligence: DecisionIntelligenceSnapshot,
) -> Tuple[bool, List[str], List[str]]:
    supporting = []
    if decision_intelligence.contradictions:
        supporting.append(f"{len(decision_intelligence.contradictions)} contradiction(s) present")
    if decision_intelligence.recommended_posture == DecisionPosture.INSUFFICIENT_INFORMATION:
        supporting.append("insufficient information posture")
    if (
        node.transition is not None and node.transition.transition_type != "UNKNOWN"
        and node.transition.interpretation_confidence == CONFIDENCE_LOW
    ):
        supporting.append(f"unstable transition: {node.transition.transition_type} (LOW interpretation confidence)")
    matched = bool(supporting)
    return matched, supporting, []


def classify_environment(
    node: MarketStateNode, decision_intelligence: DecisionIntelligenceSnapshot,
) -> Tuple[EnvironmentType, Tuple[str, ...], Tuple[str, ...], str]:
    """Returns (environment_type, supporting_conditions, blocking_conditions,
    confidence). `STAND_ASIDE` checked first; the honest fallback when
    no other category's real conditions are fully met is also
    `STAND_ASIDE` -- never a forced FAVOURABLE label without real
    supporting evidence."""
    stand_aside_matched, stand_aside_supporting, _ = _classify_stand_aside(node, decision_intelligence)
    if stand_aside_matched:
        confidence = CONFIDENCE_HIGH if len(stand_aside_supporting) >= 2 else CONFIDENCE_MODERATE
        return EnvironmentType.STAND_ASIDE, tuple(stand_aside_supporting), (), confidence

    unfav_matched, unfav_supporting, unfav_blocking = _classify_premium_selling_unfavourable(node)
    if unfav_matched:
        confidence = CONFIDENCE_HIGH if len(unfav_supporting) >= 2 else CONFIDENCE_MODERATE
        return EnvironmentType.PREMIUM_SELLING_UNFAVOURABLE, tuple(unfav_supporting), tuple(unfav_blocking), confidence

    fav_matched, fav_supporting, fav_blocking = _classify_premium_selling_favourable(node, decision_intelligence)
    if fav_matched:
        return EnvironmentType.PREMIUM_SELLING_FAVOURABLE, tuple(fav_supporting), tuple(fav_blocking), CONFIDENCE_HIGH

    trend_matched, trend_supporting, trend_blocking = _classify_trend_following_favourable(node)
    if trend_matched:
        return EnvironmentType.TREND_FOLLOWING_FAVOURABLE, tuple(trend_supporting), tuple(trend_blocking), CONFIDENCE_HIGH

    reversion_matched, reversion_supporting, reversion_blocking = _classify_mean_reversion_favourable(node)
    if reversion_matched:
        return EnvironmentType.MEAN_REVERSION_FAVOURABLE, tuple(reversion_supporting), tuple(reversion_blocking), CONFIDENCE_HIGH

    # Honest fallback: no category's real conditions were fully met --
    # never force a FAVOURABLE label without evidence.
    all_blocking = tuple(fav_blocking) + tuple(trend_blocking) + tuple(reversion_blocking)
    return EnvironmentType.STAND_ASIDE, (), all_blocking, CONFIDENCE_LOW
