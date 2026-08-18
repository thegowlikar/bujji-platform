"""Decision Intelligence reasoning rules -- Phase 19.6.

Every function here is a plain, deterministic, documented mapping from
already-real evidence to a label -- never free-form generation, never a
new indicator, never a new number computed from raw market data (that
remains the six brains' job, already done upstream). No wall-clock call
anywhere in this file.
"""
from __future__ import annotations

from typing import List, Tuple

from bujji.decision_context.compatibility_engine import assess_strategy_compatibility
from bujji.decision_context.models import DecisionContext, StrategyCompatibilityAssessment
from bujji.intelligence.market_intelligence_snapshot.models import MarketIntelligenceSnapshot, MarketPosture
from bujji.intelligence.models import Richness, SpreadTightness
from bujji.market_understanding.memory_models import STATUS_KNOWN, MarketMemoryEntry
from bujji.market_understanding.memory_similarity import SimilarityExplanation
from bujji.msi_strategy_selection_foundation import taxonomy as ssf_taxonomy

from .evidence import MIN_SAMPLES_FOR_STATISTIC
from .models import (
    ContradictionObservation,
    DecisionPosture,
    EnvironmentAssessment,
    MemoryContext,
    OpportunityObservation,
    RiskObservation,
    StrategyFamilyAssessment,
    UncertaintyObservation,
)

_PREMIUM_SHAPED_FAMILIES = frozenset({
    ssf_taxonomy.NEUTRAL_PREMIUM_SELLING, ssf_taxonomy.IRON_CONDOR, ssf_taxonomy.IRON_FLY,
    ssf_taxonomy.NEUTRAL_PREMIUM_BUYING, ssf_taxonomy.VOLATILITY_EXPANSION, ssf_taxonomy.VOLATILITY_COMPRESSION,
})
_DIRECTIONAL_SHAPED_FAMILIES = frozenset({ssf_taxonomy.LONG_DIRECTIONAL, ssf_taxonomy.SHORT_DIRECTIONAL})


def derive_environment_state(snapshot: MarketIntelligenceSnapshot) -> EnvironmentAssessment:
    """A plain, mechanical composition of RegimeBrain's own regime label
    and VolatilityBrain's own richness label -- e.g.
    "range_with_iv_rich_volatility". Never a new classification; a
    rename/join of two already-real labels."""
    regime_label = snapshot.regime.regime.value.lower()
    richness = snapshot.volatility.richness
    if richness is not None and richness != Richness.UNKNOWN:
        state = f"{regime_label}_with_{richness.value.lower()}_volatility"
    else:
        state = regime_label

    supporting_evidence = tuple(
        item.metric_name for item in snapshot.evidence_bundle.items
        if item.metric_name.startswith("regime.") or item.metric_name.startswith("volatility.")
    )
    return EnvironmentAssessment(state=state, supporting_evidence=supporting_evidence)


def build_strategy_family_assessment(decision_context: DecisionContext) -> StrategyFamilyAssessment:
    """Carries `DecisionContext`'s own already-computed compatibility
    forward verbatim -- this layer never recomputes it."""
    return StrategyFamilyAssessment(
        compatible_strategy_families=decision_context.compatible_strategy_families,
        blocked_strategy_families=decision_context.blocked_strategy_families,
        reasoning=decision_context.reasoning,
    )


def build_observations(
    assessment: StrategyCompatibilityAssessment,
) -> Tuple[Tuple[RiskObservation, ...], Tuple[OpportunityObservation, ...], Tuple[UncertaintyObservation, ...]]:
    """Wraps `StrategyCompatibilityAssessment`'s own already-real
    supporting/blocking evidence and unassessed-family list -- never
    invents a new reason beyond what that assessment already stated."""
    opportunities = tuple(
        OpportunityObservation(description=reason, supporting_evidence=(reason,))
        for reason in assessment.supporting_evidence
    )
    risks = tuple(
        RiskObservation(description=reason, supporting_evidence=(reason,))
        for reason in assessment.blocking_evidence
    )
    uncertainties = tuple(
        UncertaintyObservation(
            description=f"no evidence to assess {family}",
            reason="MarketIntelligenceSnapshot carries no direction/term-structure/futures-positioning signal for this family",
        )
        for family in assessment.unassessed_strategy_families
    )
    return risks, opportunities, uncertainties


def detect_contradictions(
    snapshot: MarketIntelligenceSnapshot, assessment: StrategyCompatibilityAssessment,
) -> Tuple[ContradictionObservation, ...]:
    """The `mil_next`-derived contradiction concept (Phase 19.1.2's
    evaluation), reused as a concept, never as code. Two documented
    rules, each citing the exact two real readings that conflict --
    never a synthesized "contradiction score" without a named cause."""
    contradictions = []
    volatility, liquidity = snapshot.volatility, snapshot.liquidity

    premium_selling_assessed = bool(set(assessment.compatible_strategy_families) & _PREMIUM_SHAPED_FAMILIES)
    liquidity_poor = liquidity.tightness == SpreadTightness.WIDE

    if volatility.richness == Richness.IV_RICH and liquidity_poor:
        contradictions.append(ContradictionObservation(
            description="Premium opportunity exists but liquidity conditions reduce confidence",
            dimensions_involved=("volatility", "liquidity"),
            supporting_evidence=(f"volatility.richness={volatility.richness.value}", f"liquidity.tightness={liquidity.tightness.value}"),
        ))
    if volatility.richness == Richness.IV_CHEAP and liquidity_poor:
        contradictions.append(ContradictionObservation(
            description="Premium buying opportunity exists but liquidity conditions reduce confidence",
            dimensions_involved=("volatility", "liquidity"),
            supporting_evidence=(f"volatility.richness={volatility.richness.value}", f"liquidity.tightness={liquidity.tightness.value}"),
        ))
    return tuple(contradictions)


def derive_posture(
    snapshot: MarketIntelligenceSnapshot, assessment: StrategyCompatibilityAssessment,
    contradictions: Tuple[ContradictionObservation, ...],
) -> DecisionPosture:
    """One documented priority order over already-real signals -- never a
    new indicator."""
    if snapshot.posture == MarketPosture.EVENT_RISK:
        return DecisionPosture.REDUCE_EXPOSURE
    if snapshot.liquidity.tightness == SpreadTightness.WIDE:
        return DecisionPosture.REDUCE_EXPOSURE
    if contradictions:
        return DecisionPosture.WAIT_FOR_CONFIRMATION
    if not assessment.compatible_strategy_families and not assessment.incompatible_strategy_families:
        return DecisionPosture.INSUFFICIENT_INFORMATION
    if any(f in _DIRECTIONAL_SHAPED_FAMILIES for f in assessment.compatible_strategy_families):
        return DecisionPosture.FAVOR_DIRECTIONAL_ENVIRONMENT
    if any(f in _PREMIUM_SHAPED_FAMILIES for f in assessment.compatible_strategy_families):
        return DecisionPosture.FAVOR_PREMIUM_ENVIRONMENT
    return DecisionPosture.OBSERVE


def build_memory_context(
    matches: List[Tuple[MarketMemoryEntry, SimilarityExplanation]],
) -> MemoryContext:
    """Historical OBSERVATION, never a prediction. `statistic` stays
    `None` unless at least `MIN_SAMPLES_FOR_STATISTIC` matched entries
    carry a real, KNOWN outcome observation -- and even then it is a
    plain fraction, never a synthesized probability."""
    matched_count = len(matches)
    matched_ids = tuple(entry.market_memory_id for entry, _ in matches)

    if matched_count == 0:
        confidence_note = "no historical precedent found"
    elif matched_count < MIN_SAMPLES_FOR_STATISTIC:
        confidence_note = f"limited -- only {matched_count} matching event(s)"
    else:
        confidence_note = f"{matched_count} matching events found"

    known_outcomes = [
        entry for entry, _ in matches
        if entry.outcome_observation is not None and entry.outcome_observation.status == STATUS_KNOWN
    ]
    statistic = None
    if len(known_outcomes) >= MIN_SAMPLES_FOR_STATISTIC:
        expanded = sum(1 for e in known_outcomes if e.outcome_observation.volatility_richness_after == "IV_RICH")
        statistic = f"{expanded} of {len(known_outcomes)} matched states showed IV_RICH volatility afterward"

    return MemoryContext(
        matched_count=matched_count, matched_market_memory_ids=matched_ids,
        confidence_note=confidence_note, statistic=statistic,
    )
