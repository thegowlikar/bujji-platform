"""Strategy Selector engine — pure functions, no state, no IO, no
wall-clock reads.

---------------------------------------------------------------------
Design note (Objective, Constraints): this selector chooses among
already-SUITABLE families (Series 87/88) by matching MARKET STATE fit
(Deliverable 2), never by directional prediction and never by
optimizing against historical returns. Market state derivation
(Deliverable 1) is a set of small, disclosed, evidence-based rules —
one function per state, each citing the real upstream field it reads.
Selection is a categorical match-count comparison (preferred > acceptable,
forbidden disqualifies) with a fixed, disclosed tie-break order — never
a numeric score fit to outcomes.

This module imports the real model types of every consumed upstream
package DIRECTLY (MDI, MPPI, VSB, PSI, MSSI, Consensus,
StrategySuitabilityAssessment from SSF) — a deliberate downstream-
consumption exception (this package sits strictly downstream of all
of them), mirroring Series 82/85/87/88's own established precedent.
It does NOT import Decision Synthesis (77) or Strategy Eligibility (82)
directly — this sprint's own Inputs list names them, but this engine
reaches its real per-family evidence through StrategySuitabilityAssessment
(87/88), which already synthesizes everything upstream of it; importing
77/82's raw types here would be redundant, not additive.
"""
from __future__ import annotations

import hashlib
from typing import Dict, List, Optional, Tuple

from bujji.msi_consensus.models import ConsensusAssessment
from bujji.msi_consensus import taxonomy as consensus_taxonomy
from bujji.msi_market_direction.models import MarketDirectionAssessment
from bujji.msi_market_direction import taxonomy as mdi_taxonomy
from bujji.msi_market_structure.models import MarketStructureAssessment
from bujji.msi_market_structure import taxonomy as mssi_taxonomy
from bujji.msi_participant_positioning.models import MarketParticipantPositioningAssessment
from bujji.msi_participant_positioning import taxonomy as mppi_taxonomy
from bujji.msi_price_structure.models import PriceStructureAssessment
from bujji.msi_price_structure import taxonomy as psi_taxonomy
from bujji.msi_volatility_structure.models import VolatilityStructureAssessment
from bujji.msi_volatility_structure import taxonomy as vsb_taxonomy
from bujji.msi_strategy_selection_foundation.models import StrategySuitabilityAssessment
from bujji.msi_strategy_selection_foundation import taxonomy as ssf_taxonomy

from . import config as _config
from . import taxonomy
from .models import CandidateScore, Explanation, StrategySelectionAssessment


# ---------------------------------------------------------------------------
# Deliverable 1 — Market State Ontology derivation. One small, disclosed
# rule per state, each citing its real evidence source.
# ---------------------------------------------------------------------------
def derive_market_states(
    psi: PriceStructureAssessment,
    mssi: MarketStructureAssessment,
    mdi: MarketDirectionAssessment,
    mppi: MarketParticipantPositioningAssessment,
    vsb: Optional[VolatilityStructureAssessment],
    consensus: ConsensusAssessment,
) -> Tuple[str, ...]:
    active: List[str] = []

    if psi.structure_state == psi_taxonomy.STRUCTURE_TRENDING and vsb is not None and (
        vsb.expansion_state == vsb_taxonomy.EXPANSION_CONFIRMED
        or vsb.volatility_regime in (vsb_taxonomy.REGIME_TRANSITIONING, vsb_taxonomy.REGIME_HIGH_VOLATILITY)
    ):
        active.append(taxonomy.STATE_TREND_EXPANSION)

    if psi.structure_state == psi_taxonomy.STRUCTURE_CORRECTING:
        active.append(taxonomy.STATE_TREND_EXHAUSTION)

    if psi.structure_state == psi_taxonomy.STRUCTURE_BALANCE or mssi.structural_balance == mssi_taxonomy.STRUCTURAL_BALANCE_RANGE_BOUND:
        active.append(taxonomy.STATE_BALANCE)

    if psi.compression_state == psi_taxonomy.COMPRESSION_CONFIRMED:
        active.append(taxonomy.STATE_COMPRESSION)

    if vsb is not None and vsb.expansion_state == vsb_taxonomy.EXPANSION_CONFIRMED:
        active.append(taxonomy.STATE_VOLATILITY_EXPANSION)

    if vsb is not None and vsb.compression_state == vsb_taxonomy.COMPRESSION_CONFIRMED:
        active.append(taxonomy.STATE_VOLATILITY_CONTRACTION)

    if mdi.overall_direction == mdi_taxonomy.MIXED or mppi.positioning_bias == mppi_taxonomy.MIXED_POSITIONING:
        active.append(taxonomy.STATE_ROTATIONAL_MARKET)

    if (
        mdi.overall_direction == mdi_taxonomy.UNKNOWN
        or consensus.consensus_level in (consensus_taxonomy.CONSENSUS_NO_CONSENSUS, consensus_taxonomy.CONSENSUS_WEAK)
        or consensus.evidence_sufficiency == consensus_taxonomy.SUFFICIENCY_INSUFFICIENT
    ):
        active.append(taxonomy.STATE_UNCERTAIN_MARKET)

    if mppi.positioning_strength == mppi_taxonomy.STRENGTH_STRONG:
        active.append(taxonomy.STATE_STRONG_PARTICIPATION)
    if mppi.positioning_strength == mppi_taxonomy.STRENGTH_WEAK:
        active.append(taxonomy.STATE_WEAK_PARTICIPATION)

    return tuple(sorted(set(active)))


# ---------------------------------------------------------------------------
# Deliverable 2/3 — categorical match scoring. Never a numeric fit to
# outcomes; a fixed, disclosed weighting over categorical set overlap.
# ---------------------------------------------------------------------------
def score_candidate(family: str, active_states: Tuple[str, ...]) -> CandidateScore:
    fit = taxonomy.STRATEGY_STATE_FIT.get(family, {"preferred": (), "acceptable": (), "forbidden": ()})
    active_set = set(active_states)
    disqualifying = tuple(sorted(active_set & set(fit["forbidden"])))
    if disqualifying:
        return CandidateScore(
            strategy_family=family, disqualified=True, disqualifying_states=disqualifying,
            matched_preferred_states=(), matched_acceptable_states=(), match_score=None,
        )
    matched_preferred = tuple(sorted(active_set & set(fit["preferred"])))
    matched_acceptable = tuple(sorted(active_set & set(fit["acceptable"])))
    score = len(matched_preferred) * _score_preferred_weight() + len(matched_acceptable) * _score_acceptable_weight()
    return CandidateScore(
        strategy_family=family, disqualified=False, disqualifying_states=(),
        matched_preferred_states=matched_preferred, matched_acceptable_states=matched_acceptable, match_score=score,
    )


def _score_preferred_weight() -> int:
    return taxonomy.PREFERRED_MATCH_WEIGHT


def _score_acceptable_weight() -> int:
    return taxonomy.ACCEPTABLE_MATCH_WEIGHT


def select_strategy(
    suitability_assessments: Tuple[StrategySuitabilityAssessment, ...],
    psi: PriceStructureAssessment,
    mssi: MarketStructureAssessment,
    mdi: MarketDirectionAssessment,
    mppi: MarketParticipantPositioningAssessment,
    consensus: ConsensusAssessment,
    vsb: Optional[VolatilityStructureAssessment] = None,
    *,
    timestamp: str,
    provenance: str = _config.DEFAULT_PROVENANCE,
    schema_version: str = _config.SCHEMA_VERSION,
    expression_compatible_families: Optional[Tuple[str, ...]] = None,
) -> StrategySelectionAssessment:
    # Series 93 (Strategy Expression Engine), ADDITIVE ONLY: an optional
    # filter of family names, applied strictly BETWEEN "Suitable" and
    # scoring -- "Suitable -> Matches Expression -> Not Forbidden ->
    # Selected". Deliberately a plain Tuple[str, ...], not an import of
    # bujji.msi_strategy_expression's own types -- this package stays
    # decoupled from that package's internals, exactly like every other
    # sibling-isolation boundary in this codebase (a caller-supplied
    # translation, not a direct sibling import). Omitted (None, the
    # default) -> zero behavior change from every prior series.
    active_states = derive_market_states(psi, mssi, mdi, mppi, vsb, consensus)

    suitable_families = tuple(a.strategy_family for a in suitability_assessments if a.suitability == ssf_taxonomy.SUITABLE)
    if expression_compatible_families is not None:
        suitable_families = tuple(f for f in suitable_families if f in expression_compatible_families)
    scores: Dict[str, CandidateScore] = {f: score_candidate(f, active_states) for f in suitable_families}

    eligible_scored = [s for s in scores.values() if not s.disqualified]

    selected_family: Optional[str] = None
    if eligible_scored:
        max_score = max(s.match_score for s in eligible_scored)
        winners = [s for s in eligible_scored if s.match_score == max_score]
        if len(winners) > 1:
            winners.sort(key=lambda s: taxonomy.TIE_BREAK_ORDER.index(s.strategy_family) if s.strategy_family in taxonomy.TIE_BREAK_ORDER else 999)
        selected_family = winners[0].strategy_family

    alternative_candidates = tuple(sorted(
        (s for s in scores.values() if s.strategy_family != selected_family),
        key=lambda s: s.strategy_family,
    ))

    rejection_reasons: List[str] = []
    why_not_alternatives: List[str] = []
    evidence_that_prevented_alternatives: List[str] = []
    for alt in alternative_candidates:
        if alt.disqualified:
            reason = f"{alt.strategy_family} disqualified: active market state(s) {alt.disqualifying_states} are forbidden for this family."
        elif selected_family is not None:
            winner_score = scores[selected_family].match_score
            reason = f"{alt.strategy_family} scored {alt.match_score} (matched preferred={alt.matched_preferred_states}, acceptable={alt.matched_acceptable_states}), below the selected family's score {winner_score}."
        else:
            reason = f"{alt.strategy_family} was not selected: no suitable, non-disqualified family exists this cycle."
        rejection_reasons.append(reason)
        why_not_alternatives.append(reason)
        if alt.disqualifying_states:
            evidence_that_prevented_alternatives.extend(alt.disqualifying_states)

    why_this_strategy: List[str] = []
    evidence_that_mattered_most: List[str] = []
    if selected_family is not None:
        winner = scores[selected_family]
        why_this_strategy.append(
            f"{selected_family} is SUITABLE (Series 87/88) and best-fits the active market state(s) "
            f"{active_states}: matched preferred={winner.matched_preferred_states}, acceptable={winner.matched_acceptable_states}, "
            f"score={winner.match_score}."
        )
        evidence_that_mattered_most.extend(winner.matched_preferred_states)
    elif not suitable_families:
        why_this_strategy.append("No strategy family is SUITABLE this cycle (Series 87/88) -- no selection possible.")
    else:
        why_this_strategy.append("Every SUITABLE family was disqualified by a forbidden active market state -- no selection possible.")

    ids = {psi.assessment_id, mssi.assessment_id, mdi.assessment_id, mppi.assessment_id, consensus.assessment_id}
    ids.update(a.assessment_id for a in suitability_assessments)
    if vsb is not None:
        ids.add(vsb.assessment_id)
    supporting_evidence = tuple(sorted(ids))

    confidence = _compute_confidence(selected_family, scores, active_states)

    assessment_id = _assessment_id(active_states, selected_family, scores, schema_version)

    explanation = Explanation(
        assessment_id=assessment_id,
        why_this_strategy=tuple(why_this_strategy),
        why_not_alternatives=tuple(why_not_alternatives),
        evidence_that_mattered_most=tuple(sorted(set(evidence_that_mattered_most))),
        evidence_that_prevented_alternatives=tuple(sorted(set(evidence_that_prevented_alternatives))),
        active_market_states=active_states,
        schema_version=schema_version,
    )

    return StrategySelectionAssessment(
        assessment_id=assessment_id,
        timestamp=timestamp,
        selected_strategy_family=selected_family,
        alternative_candidates=alternative_candidates,
        rejection_reasons=tuple(rejection_reasons),
        supporting_evidence=supporting_evidence,
        confidence=confidence,
        explanation=explanation,
        provenance=provenance,
        schema_version=schema_version,
    )


def _compute_confidence(selected_family: Optional[str], scores: Dict[str, CandidateScore], active_states: Tuple[str, ...]) -> str:
    if selected_family is None:
        return taxonomy.CONFIDENCE_NONE
    winner_score = scores[selected_family].match_score
    others = [s.match_score for f, s in scores.items() if f != selected_family and not s.disqualified]
    runner_up = max(others) if others else 0
    margin = winner_score - runner_up
    if not active_states:
        return taxonomy.CONFIDENCE_LOW
    if margin >= _config.CONFIDENCE_HIGH_MIN_SCORE_MARGIN:
        return taxonomy.CONFIDENCE_HIGH
    if margin > 0:
        return taxonomy.CONFIDENCE_MODERATE
    return taxonomy.CONFIDENCE_LOW


def _assessment_id(
    active_states: Tuple[str, ...],
    selected_family: Optional[str],
    scores: Dict[str, CandidateScore],
    schema_version: str,
) -> str:
    score_parts = "||".join(
        f"{f}:{s.disqualified}:{s.match_score}" for f, s in sorted(scores.items())
    )
    seed = "###".join([
        "|".join(active_states), selected_family or "NONE", score_parts, schema_version,
    ])
    return "MSA-" + hashlib.md5(seed.encode()).hexdigest()[:24]
