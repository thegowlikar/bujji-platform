"""Domain View Adapter -- Shadow Trading Brain, Phase 5B.

Pure, mechanical translator: converts the five live MSI assessment
objects (psi/mssi/mdi/mppi/vsb, already produced live by Phases 3A-3D)
into the two generic, already-defined interim contracts
msi_consensus.compute_consensus() and msi_decision_synthesis.synthesize()
require -- DomainAssessmentView and DomainSignal. No new dataclass is
introduced; both target types are reused exactly as already defined,
unmodified.

Nothing here infers a directional opinion beyond what
msi_market_direction.engine.derive_price_structure_lens()/
derive_market_structure_lens() (already-existing, already-tested logic)
compute for psi/mssi, or beyond mdi/mppi's own already-fused directional
fields -- this module only relabels/collapses those existing conclusions
into msi_consensus's coarser 4-value lean vocabulary via one fixed,
deterministic table. It never ranks, scores, resolves conflicts, or
selects a strategy.

KNOWN, APPROVED LIMITATIONS (Phase 5B decisions):
  - mdi is included as a NON-REGISTRY domain ("MARKET_DIRECTION"),
    since msi_consensus/msi_decision_synthesis's shared 9-domain
    vocabulary (taxonomy.ALL_MSI_DOMAINS) has no MARKET_DIRECTION or
    PARTICIPANT_POSITIONING slot. It participates fully in
    agreement/conflict calculations but will NEVER appear in
    ConsensusAssessment.missing_domains or MarketOpportunityAssessment's
    missing-domain accounting, since that accounting is keyed to the
    registry. Disclosed, accepted tradeoff -- see
    test_mdi_non_registry_domain_name_and_limitation.
  - vsb is DELIBERATELY EXCLUDED from build_domain_assessment_views()
    (no DomainAssessmentView is ever built for volatility structure)
    because volatility structure has no directional lean -- a forced
    LEAN_NEUTRAL would dishonestly imply "no opinion" rather than "not
    a directional dimension." vsb DOES still appear in
    build_domain_signals(), since DomainSignal.state is free text with
    no lean-forcing required.
  - mppi has no `confidence` field (unlike psi/mssi/vsb) -- it has
    `positioning_strength` instead, on a parallel but distinct 4-level
    scale (UNKNOWN/WEAK/MODERATE/STRONG), mapped via its own separate
    table, never conflated with the NONE/LOW/MODERATE/HIGH scale.
"""
from __future__ import annotations

from typing import Optional, Tuple

from bujji.msi_consensus import taxonomy as consensus_taxonomy
from bujji.msi_consensus.engine import DomainAssessmentView
from bujji.msi_decision_synthesis import taxonomy as dse_taxonomy
from bujji.msi_decision_synthesis.models import DomainSignal
from bujji.msi_market_direction import taxonomy as mdi_taxonomy
from bujji.msi_market_direction.engine import derive_market_structure_lens, derive_price_structure_lens
from bujji.msi_market_direction.models import MarketDirectionAssessment
from bujji.msi_market_structure.models import MarketStructureAssessment
from bujji.msi_participant_positioning.models import MarketParticipantPositioningAssessment
from bujji.msi_price_structure.models import PriceStructureAssessment
from bujji.msi_volatility_structure.models import VolatilityStructureAssessment

MARKET_DIRECTION_DOMAIN_NAME = "MARKET_DIRECTION"  # Non-registry, approved Phase 5B Decision 1.

# Deterministic tables -- mirrors msi_market_direction.taxonomy's own
# confidence_rank precedent; never a guess, never re-derived per-call.
_CONFIDENCE_LEVEL_TO_FLOAT = {"NONE": 0.0, "LOW": 0.33, "MODERATE": 0.67, "HIGH": 1.0}
_POSITIONING_STRENGTH_TO_FLOAT = {"UNKNOWN": 0.0, "WEAK": 0.33, "MODERATE": 0.67, "STRONG": 1.0}


def _confidence_level_to_float(confidence_level: Optional[str]) -> float:
    return _CONFIDENCE_LEVEL_TO_FLOAT.get(confidence_level, 0.0)


def _positioning_strength_to_float(positioning_strength: Optional[str]) -> float:
    return _POSITIONING_STRENGTH_TO_FLOAT.get(positioning_strength, 0.0)


def _collapse_lean(directional_value: str) -> str:
    """Collapses msi_market_direction's 9-value lean vocabulary (and
    mdi.overall_direction/mppi.positioning_bias, both already
    compatible in shape) down to msi_consensus's 4-value ALL_LEANS --
    a fixed table, never an inference."""
    if "BULLISH" in directional_value:
        return consensus_taxonomy.LEAN_BULLISH
    if "BEARISH" in directional_value:
        return consensus_taxonomy.LEAN_BEARISH
    if directional_value == "NEUTRAL":
        return consensus_taxonomy.LEAN_NEUTRAL
    return consensus_taxonomy.LEAN_AMBIGUOUS  # UNKNOWN, MIXED, or anything unrecognized


def build_domain_assessment_views(
    psi: Optional[PriceStructureAssessment] = None,
    mssi: Optional[MarketStructureAssessment] = None,
    mdi: Optional[MarketDirectionAssessment] = None,
    mppi: Optional[MarketParticipantPositioningAssessment] = None,
) -> Tuple[DomainAssessmentView, ...]:
    """vsb is intentionally NOT a parameter here -- see module docstring
    (Phase 5B Decision 2). Any of psi/mssi/mdi/mppi may be None
    (honestly absent this cycle); that domain is simply omitted from
    the returned tuple, never fabricated."""
    views = []

    if psi is not None:
        lens = derive_price_structure_lens(psi)
        views.append(DomainAssessmentView(
            domain_name=dse_taxonomy.DOMAIN_PRICE_STRUCTURE,
            lean=_collapse_lean(lens.directional_lean),
            confidence=_confidence_level_to_float(psi.confidence),
            evidence_ids=psi.supporting_observation_ids,
            source_assessment_id=psi.assessment_id,
        ))

    if mssi is not None:
        lens = derive_market_structure_lens(mssi)
        views.append(DomainAssessmentView(
            domain_name=dse_taxonomy.DOMAIN_SUPPORT_RESISTANCE,
            lean=_collapse_lean(lens.directional_lean),
            confidence=_confidence_level_to_float(mssi.confidence),
            evidence_ids=mssi.supporting_observation_ids,
            source_assessment_id=mssi.assessment_id,
        ))

    if mdi is not None:
        views.append(DomainAssessmentView(
            domain_name=MARKET_DIRECTION_DOMAIN_NAME,
            lean=_collapse_lean(mdi.overall_direction),
            confidence=_confidence_level_to_float(mdi.overall_confidence),
            evidence_ids=mdi.supporting_assessment_ids,
            source_assessment_id=mdi.assessment_id,
        ))

    if mppi is not None:
        views.append(DomainAssessmentView(
            domain_name=dse_taxonomy.DOMAIN_OPTIONS_MARKET_STRUCTURE,
            lean=_collapse_lean(mppi.positioning_bias),
            confidence=_positioning_strength_to_float(mppi.positioning_strength),
            evidence_ids=mppi.supporting_observation_ids,
            source_assessment_id=mppi.assessment_id,
        ))

    return tuple(views)


def build_domain_signals(
    psi: Optional[PriceStructureAssessment] = None,
    mssi: Optional[MarketStructureAssessment] = None,
    mdi: Optional[MarketDirectionAssessment] = None,
    mppi: Optional[MarketParticipantPositioningAssessment] = None,
    vsb: Optional[VolatilityStructureAssessment] = None,
) -> Tuple[DomainSignal, ...]:
    """Unlike build_domain_assessment_views(), vsb IS included here
    (Phase 5B Decision 2) -- DomainSignal.state is free text, no lean
    required. Any input may be None; that domain is simply omitted."""
    signals = []

    if psi is not None:
        signals.append(DomainSignal(
            domain_name=dse_taxonomy.DOMAIN_PRICE_STRUCTURE, state=psi.structure_state,
            confidence=_confidence_level_to_float(psi.confidence), evidence_ids=psi.supporting_observation_ids,
        ))

    if mssi is not None:
        signals.append(DomainSignal(
            domain_name=dse_taxonomy.DOMAIN_SUPPORT_RESISTANCE, state=mssi.structure_location,
            confidence=_confidence_level_to_float(mssi.confidence), evidence_ids=mssi.supporting_observation_ids,
        ))

    if mdi is not None:
        signals.append(DomainSignal(
            domain_name=MARKET_DIRECTION_DOMAIN_NAME, state=mdi.overall_direction,
            confidence=_confidence_level_to_float(mdi.overall_confidence), evidence_ids=mdi.supporting_assessment_ids,
        ))

    if mppi is not None:
        signals.append(DomainSignal(
            domain_name=dse_taxonomy.DOMAIN_OPTIONS_MARKET_STRUCTURE, state=mppi.positioning_bias,
            confidence=_positioning_strength_to_float(mppi.positioning_strength),
            evidence_ids=mppi.supporting_observation_ids,
        ))

    if vsb is not None:
        signals.append(DomainSignal(
            domain_name=dse_taxonomy.DOMAIN_VOLATILITY_STRUCTURE, state=vsb.volatility_regime,
            confidence=_confidence_level_to_float(vsb.confidence), evidence_ids=(),
        ))

    return tuple(signals)
