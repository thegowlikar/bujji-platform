"""Strategy Eligibility Intelligence (SEI) engine — BUJJI Engineering
Series 82.

---------------------------------------------------------------------
Architecture boundary (Check 3's resolution) -- why this package
DIRECTLY IMPORTS real sibling-brain model types, unlike every prior
brain in this arc.
---------------------------------------------------------------------
Series 78/79/81 are PEERS: each independently reads the same
underlying market data and none may import another's real model type
(AST-enforced isolation), because doing so would let one peer's
internal representation leak into another's reasoning. SEI is not a
peer of 77 or 81 -- it is STRICTLY DOWNSTREAM of both. Series 77's
`MarketOpportunityAssessment` and Series 81's `ConsensusAssessment`
already encapsulate/summarize Price Structure (78)/Market Structure
(79)/Volatility (80, mocked -- see docs/MSI_STRATEGY_ELIGIBILITY.md
"Known limitations", Series 80 does not exist yet) information: 77
synthesizes an opportunity read FROM domain signals derived from
78/79/80; 81 measures cross-domain agreement across those same
domains. Consuming 77's and 81's PUBLIC output types directly is
therefore an explicit, deliberate, ONE-DIRECTION downstream dependency
-- not the "never import a sibling's code" rule that governs same-
level siblings. This satisfies Deliverable 10's literal "Strategy
Eligibility consumes existing outputs... without adapters" requirement
precisely: no test-only translation layer is needed to go from real
77/81 outputs into this engine -- the engine's real entrypoint accepts
real `MarketOpportunityAssessment`/`ConsensusAssessment` objects as
typed parameters.

SEI still does NOT import `bujji.msi_price_structure` or
`bujji.msi_market_structure` directly (see taxonomy.py's module
docstring) -- those are already summarized two levels up by 77/81's
own outputs, and reaching past them would duplicate reasoning 77/81
already did, with no principled benefit.

---------------------------------------------------------------------
Check 2's resolution -- relationship to Series 77's
`compatible_strategy_families`/`incompatible_strategy_families`.
---------------------------------------------------------------------
Series 77's family fields are a SINGLE synthesis-time judgment, a
byproduct of fusing domain signals into ONE opportunity conclusion --
computed from `synthesize()`'s own inputs alone (domain_signals,
previous_assessment, episode_ids). Confirmed by reading `engine.py`'s
`synthesize()` signature: it accepts no `ConsensusAssessment` and
never will by design (Series 81's own doc states 81 was built to sit
ALONGSIDE 77, not feed into it). SEI's eligibility read is therefore
STRICTLY RICHER than 77's family fields: it is a DEDICATED, separately
explained layer that ADDITIONALLY factors in Series 81's coherence-of-
understanding signal (`consensus_level`, `evidence_sufficiency`) --
information 77 structurally cannot see. The strongest proof of this is
implemented directly below: a HIGH-confidence DIRECTIONAL_OPPORTUNITY
read, if fed to 77 alone, produces confident compatible/incompatible
family lists purely from the opportunity's own internal domain-vote
math -- but if the SAME opportunity read arrives alongside a
WEAK_CONSENSUS/INSUFFICIENT-evidence ConsensusAssessment, SEI must NOT
license confident eligibility, because a clean-looking directional
read sitting on top of poor underlying multi-domain agreement is
exactly the situation 81 exists to detect and 77 alone cannot.
"""
from __future__ import annotations

import hashlib
from typing import Tuple

from bujji.msi_consensus.models import ConsensusAssessment
from bujji.msi_consensus import taxonomy as _consensus_taxonomy
from bujji.msi_decision_synthesis.models import MarketOpportunityAssessment
from bujji.msi_decision_synthesis import taxonomy as _dse_taxonomy

from . import config as _config
from . import taxonomy
from .models import Contradiction, Explanation, StrategyEligibilityAssessment

_ALL_FAMILIES = taxonomy.ALL_STRATEGY_FAMILIES

# ---------------------------------------------------------------------------
# Per-opportunity-state base family sets, at NORMAL (high-coherence)
# eligibility. These are the families that make sense GIVEN the KIND
# of opportunity, before any consensus-based gating is applied.
# ---------------------------------------------------------------------------
_BASE_ELIGIBLE_BY_STATE = {
    _dse_taxonomy.OPPORTUNITY_STATE_DIRECTIONAL: (
        taxonomy.FAMILY_DEFINED_RISK_DIRECTIONAL,
        taxonomy.FAMILY_HEDGED_DIRECTIONAL,
        taxonomy.FAMILY_DIAGONAL,
    ),
    _dse_taxonomy.OPPORTUNITY_STATE_BREAKOUT: (
        taxonomy.FAMILY_DEFINED_RISK_DIRECTIONAL,
        taxonomy.FAMILY_HEDGED_DIRECTIONAL,
        taxonomy.FAMILY_LONG_VOLATILITY,
    ),
    _dse_taxonomy.OPPORTUNITY_STATE_VOLATILITY: (
        taxonomy.FAMILY_LONG_VOLATILITY,
        taxonomy.FAMILY_SHORT_VOLATILITY,
        taxonomy.FAMILY_CALENDAR,
    ),
    _dse_taxonomy.OPPORTUNITY_STATE_NEUTRAL: (
        taxonomy.FAMILY_DEFINED_RISK_NEUTRAL,
        taxonomy.FAMILY_UNDEFINED_RISK_PREMIUM,
        taxonomy.FAMILY_CALENDAR,
    ),
    _dse_taxonomy.OPPORTUNITY_STATE_MEAN_REVERSION: (
        taxonomy.FAMILY_DEFINED_RISK_NEUTRAL,
        taxonomy.FAMILY_SHORT_VOLATILITY,
    ),
    # Non-opportunity states: nothing is eligible -- there is no edge
    # to select a family for.
    _dse_taxonomy.OPPORTUNITY_STATE_NO_ACTION: (),
    _dse_taxonomy.OPPORTUNITY_STATE_WAIT: (),
    _dse_taxonomy.OPPORTUNITY_STATE_MONITOR: (),
}


def _base_eligible_families(opportunity_state: str) -> Tuple[str, ...]:
    return _BASE_ELIGIBLE_BY_STATE.get(opportunity_state, ())


def _coherence_gate(consensus: ConsensusAssessment, opportunity: MarketOpportunityAssessment) -> str:
    """Returns one of "NORMAL", "REDUCED", "NONE" -- the coherence
    tier that gates how much of the base-eligible set survives, and
    how confident the overall read can be. A deterministic function of
    consensus_level rank, evidence_sufficiency rank, and the
    opportunity's own confidence_level rank -- never of opportunity
    family fields."""
    consensus_rank = _consensus_taxonomy.CONSENSUS_LEVEL_RANK[consensus.consensus_level]
    sufficiency_rank = _consensus_taxonomy.SUFFICIENCY_RANK[consensus.evidence_sufficiency]
    opportunity_rank = _dse_taxonomy.CONFIDENCE_RANK[opportunity.confidence_level]

    if opportunity_rank < _config.MIN_OPPORTUNITY_CONFIDENCE_RANK_FOR_ANY_ELIGIBILITY:
        return "NONE"
    if consensus_rank < _config.MIN_CONSENSUS_RANK_FOR_ANY_ELIGIBILITY:
        return "NONE"
    if sufficiency_rank < _config.MIN_SUFFICIENCY_RANK_FOR_ANY_ELIGIBILITY:
        return "NONE"

    if (
        consensus_rank >= _config.MIN_CONSENSUS_RANK_FOR_NORMAL_ELIGIBILITY
        and sufficiency_rank >= _config.MIN_SUFFICIENCY_RANK_FOR_NORMAL_ELIGIBILITY
    ):
        return "NORMAL"
    return "REDUCED"


def _families_for_gate(base_eligible: Tuple[str, ...], gate: str) -> Tuple[Tuple[str, ...], Tuple[str, ...]]:
    """Returns (eligible, ineligible), both sorted, disjoint,
    partitioning taxonomy.ALL_STRATEGY_FAMILIES."""
    if gate == "NONE":
        eligible: Tuple[str, ...] = ()
    elif gate == "REDUCED":
        # Only the conservative fallback families survive, and only if
        # they were part of the base-eligible set to begin with (a
        # REDUCED VOLATILITY-opportunity read does not suddenly become
        # eligible for DEFINED_RISK_NEUTRAL if NEUTRAL was never a
        # base-eligible family for that opportunity_state).
        eligible = tuple(f for f in _config.CONSERVATIVE_FALLBACK_FAMILIES if f in base_eligible)
    else:  # NORMAL
        eligible = base_eligible

    eligible_sorted = tuple(sorted(set(eligible)))
    ineligible_sorted = tuple(sorted(f for f in _ALL_FAMILIES if f not in eligible_sorted))
    return eligible_sorted, ineligible_sorted


def _eligibility_confidence(gate: str, opportunity: MarketOpportunityAssessment, consensus: ConsensusAssessment) -> str:
    if gate == "NONE":
        return taxonomy.ELIGIBILITY_CONFIDENCE_NONE
    if gate == "REDUCED":
        return taxonomy.ELIGIBILITY_CONFIDENCE_LOW
    # NORMAL gate: confidence rises with BOTH the opportunity's own
    # confidence_level and the consensus's consensus_level -- the
    # minimum of the two ranks, never the maximum (a single weak input
    # must not be masked by a strong one).
    opportunity_rank = _dse_taxonomy.CONFIDENCE_RANK[opportunity.confidence_level]
    consensus_rank = _consensus_taxonomy.CONSENSUS_LEVEL_RANK[consensus.consensus_level]
    combined_rank = min(opportunity_rank, consensus_rank)
    if opportunity.confidence_level == _dse_taxonomy.CONFIDENCE_HIGH and consensus.consensus_level in (
        _consensus_taxonomy.CONSENSUS_STRONG, _consensus_taxonomy.CONSENSUS_UNANIMOUS,
    ):
        return taxonomy.ELIGIBILITY_CONFIDENCE_HIGH
    if combined_rank >= 2:
        return taxonomy.ELIGIBILITY_CONFIDENCE_MODERATE
    return taxonomy.ELIGIBILITY_CONFIDENCE_LOW


def detect_contradictions(
    opportunity: MarketOpportunityAssessment,
    consensus: ConsensusAssessment,
) -> Tuple[Contradiction, ...]:
    """Surfaces genuine tensions between the opportunity read's own
    confidence and the underlying multi-domain consensus. Never
    resolved here -- only surfaced, exactly like Series 81's own
    `detect_cross_domain_contradictions` precedent."""
    contradictions = []

    opportunity_is_confident = opportunity.confidence_level in (
        _dse_taxonomy.CONFIDENCE_HIGH, _dse_taxonomy.CONFIDENCE_MODERATE,
    )
    consensus_is_weak = consensus.consensus_level in (
        _consensus_taxonomy.CONSENSUS_NO_CONSENSUS, _consensus_taxonomy.CONSENSUS_WEAK,
    )
    if opportunity_is_confident and consensus_is_weak:
        contradictions.append(Contradiction(
            dimension_a="OPPORTUNITY_CONFIDENCE",
            value_a=opportunity.confidence_level,
            dimension_b="CONSENSUS_LEVEL",
            value_b=consensus.consensus_level,
            reason=(
                f"Decision Synthesis reports {opportunity.confidence_level} confidence in "
                f"{opportunity.opportunity_state}, but the underlying multi-domain consensus is "
                f"only {consensus.consensus_level} -- the opportunity layer's own agreement-based "
                f"confidence and the independently-measured cross-domain coherence disagree."
            ),
        ))

    if opportunity.confidence_level == _dse_taxonomy.CONFIDENCE_HIGH and consensus.conflicting_domains:
        contradictions.append(Contradiction(
            dimension_a="OPPORTUNITY_CONFIDENCE",
            value_a=opportunity.confidence_level,
            dimension_b="CONSENSUS_CONFLICTING_DOMAINS",
            value_b=",".join(sorted(consensus.conflicting_domains)),
            reason=(
                f"Decision Synthesis reports HIGH confidence, yet {len(consensus.conflicting_domains)} "
                f"domain(s) ({', '.join(sorted(consensus.conflicting_domains))}) are recorded as "
                f"conflicting by Consensus -- a confident read should not coexist with unresolved "
                f"cross-domain disagreement."
            ),
        ))

    if consensus.evidence_sufficiency == _consensus_taxonomy.SUFFICIENCY_INSUFFICIENT and opportunity_is_confident:
        contradictions.append(Contradiction(
            dimension_a="OPPORTUNITY_CONFIDENCE",
            value_a=opportunity.confidence_level,
            dimension_b="EVIDENCE_SUFFICIENCY",
            value_b=consensus.evidence_sufficiency,
            reason=(
                f"Decision Synthesis reports {opportunity.confidence_level} confidence while the "
                f"evidentiary base backing it is INSUFFICIENT -- a confident read requires an "
                f"adequately-evidenced foundation."
            ),
        ))

    return tuple(contradictions)


def build_explanation(
    assessment_id: str,
    opportunity: MarketOpportunityAssessment,
    consensus: ConsensusAssessment,
    eligible: Tuple[str, ...],
    ineligible: Tuple[str, ...],
    gate: str,
) -> Explanation:
    why_eligible = tuple(
        f"{family} eligible: opportunity_state={opportunity.opportunity_state}, "
        f"coherence_gate={gate} admits this family for this opportunity type."
        for family in eligible
    )
    why_ineligible = tuple(
        f"{family} ineligible: {'not part of the base-eligible set for ' + opportunity.opportunity_state if family not in _base_eligible_families(opportunity.opportunity_state) else 'excluded by the ' + gate + ' coherence gate'}."
        for family in ineligible
    )
    supporting_evidence = (
        f"opportunity_state={opportunity.opportunity_state}",
        f"opportunity.confidence_level={opportunity.confidence_level}",
        f"consensus.consensus_level={consensus.consensus_level}",
        f"consensus.evidence_sufficiency={consensus.evidence_sufficiency}",
    )
    weakening_evidence = tuple(
        item for item in (
            f"consensus.conflicting_domains={sorted(consensus.conflicting_domains)}" if consensus.conflicting_domains else None,
            f"consensus.missing_domains={sorted(consensus.missing_domains)}" if consensus.missing_domains else None,
            f"consensus.evidence_sufficiency={consensus.evidence_sufficiency}" if consensus.evidence_sufficiency in (
                _consensus_taxonomy.SUFFICIENCY_INSUFFICIENT, _consensus_taxonomy.SUFFICIENCY_LIMITED,
            ) else None,
            f"consensus.consensus_level={consensus.consensus_level}" if consensus.consensus_level in (
                _consensus_taxonomy.CONSENSUS_NO_CONSENSUS, _consensus_taxonomy.CONSENSUS_WEAK,
            ) else None,
        ) if item is not None
    )
    what_would_change_it = (
        "consensus.consensus_level rising to STRONG_CONSENSUS or above",
        "consensus.evidence_sufficiency rising to ADEQUATE or above",
        "opportunity.confidence_level rising, if currently below HIGH",
    )
    return Explanation(
        assessment_id=assessment_id,
        why_eligible=why_eligible,
        why_ineligible=why_ineligible,
        supporting_evidence=supporting_evidence,
        weakening_evidence=weakening_evidence,
        what_would_change_it=what_would_change_it,
        schema_version=_config.SCHEMA_VERSION,
    )


def _compute_assessment_id(
    supporting_assessment_ids: Tuple[str, ...],
    eligible: Tuple[str, ...],
    ineligible: Tuple[str, ...],
    schema_version: str,
) -> str:
    payload = "|".join((
        ",".join(sorted(supporting_assessment_ids)),
        ",".join(sorted(eligible)),
        ",".join(sorted(ineligible)),
        schema_version,
    ))
    return hashlib.md5(payload.encode("utf-8")).hexdigest()


def determine_eligibility(
    opportunity: MarketOpportunityAssessment,
    consensus: ConsensusAssessment,
    *,
    timestamp: str,
    schema_version: str = _config.SCHEMA_VERSION,
    provenance: str = _config.DEFAULT_PROVENANCE,
) -> StrategyEligibilityAssessment:
    """The one real entrypoint. Takes REAL `MarketOpportunityAssessment`
    (Series 77) and `ConsensusAssessment` (Series 81) objects directly
    -- no adapter -- per this module's Architecture boundary docstring
    and Deliverable 10's "without adapters" requirement."""
    base_eligible = _base_eligible_families(opportunity.opportunity_state)
    gate = _coherence_gate(consensus, opportunity)
    eligible, ineligible = _families_for_gate(base_eligible, gate)
    eligibility_confidence = _eligibility_confidence(gate, opportunity, consensus)
    contradictions = detect_contradictions(opportunity, consensus)

    supporting_assessment_ids = tuple(sorted({opportunity.assessment_id, consensus.assessment_id}))
    assessment_id = _compute_assessment_id(supporting_assessment_ids, eligible, ineligible, schema_version)

    explanation = build_explanation(assessment_id, opportunity, consensus, eligible, ineligible, gate)

    return StrategyEligibilityAssessment(
        assessment_id=assessment_id,
        timestamp=timestamp,
        eligible_strategy_families=eligible,
        ineligible_strategy_families=ineligible,
        eligibility_confidence=eligibility_confidence,
        supporting_assessment_ids=supporting_assessment_ids,
        contradictions=contradictions,
        explanation=explanation,
        provenance=provenance,
        schema_version=schema_version,
    )
