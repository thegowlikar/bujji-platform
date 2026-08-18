"""bujji.premium_structure_intelligence.engine — Phase 20.31.

Pure functions, no state, no IO, no wall-clock reads, no broker
import. Answers exactly one question, narrower than
msi_strategy_selector's own 13-family/10-state world: GIVEN premium
selling is already known to be a reasonable stance (Phase 20.28's own
validated chain establishes that upstream), which of the three
structures `msi_trade_construction` can genuinely build today
(SHORT_STRANGLE, IRON_CONDOR, IRON_FLY) fits best -- or none
(NO_TRADE).

REUSE, NOT DUPLICATION: this module does not re-implement
msi_strategy_selector's market-state ontology or its 13-family
capability table. It reads the SAME real upstream assessments
(MarketDirectionAssessment, PriceStructureAssessment,
MarketStructureAssessment, VolatilityStructureAssessment, and
Cycle-1's own LiquidityReading -- the exact same type
msi_strategy_selection_foundation already imports directly, same
precedent) and applies a narrower, disclosed rule set derived from
Phase 20.30's real 45-day replay evidence, not invented.

DESIGN, priority-ordered (checked in this order -- documented so "why
this structure and not another" always has one answer, same
discipline as RegimeBrain._classify and MicrostructureClassifier):

    1. Evidence gate    -- mdi/psi/mssi genuinely absent -> NO_TRADE,
                            DataQuality.INSUFFICIENT, never guessed.
    2. Direction gate   -- overall_direction != NEUTRAL -> NO_TRADE.
                            Premium-selling structure choice is only a
                            meaningful question once direction is
                            genuinely neutral; this is not a
                            directional layer and never will be.
    3. Volatility gate  -- expansion_state == CONFIRMED -> NO_TRADE.
                            Selling premium into confirmed expanding
                            vol is the textbook way premium selling
                            blows up (same reasoning
                            msi_strategy_selection_foundation's own
                            NEUTRAL_PREMIUM_SELLING volatility rule
                            already uses).
    4. IRON_FLY         -- checked first among the three real
                            structures because it is the MOST SPECIFIC
                            condition set (requires compression, a
                            strict subset of plain "balance" days;
                            Phase 20.30's real replay found this exact
                            condition on 2026-07-20, where the family
                            selector's own generic scoring separately
                            arrived at the closely-related BUTTERFLY
                            shape for the same reason).
    5. IRON_CONDOR      -- requires a specific structural fact
                            IRON_FLY/SHORT_STRANGLE do not need: price
                            genuinely NEAR an identifiable support/
                            resistance boundary (not merely "somewhere
                            inside a range"), with real confidence --
                            the extra defined-risk legs are worth their
                            cost specifically when there's an elevated,
                            real signal of a range break, matching this
                            phase's own Scenario B framing.
    6. SHORT_STRANGLE   -- the general, default premium-selling shape
                            once direction is neutral, volatility isn't
                            expanding, and neither more specific
                            condition set (compression, or a
                            confidently-bounded range) is met.
    7. NO_TRADE         -- neutral and non-expanding, but no candidate's
                            own condition set was satisfied. Honest,
                            not forced.

LIQUIDITY, a disclosed departure from Suitability's own stricter
treatment: `msi_strategy_selection_foundation.assess_strategy_suitability`
treats missing/UNKNOWN liquidity as a hard INSUFFICIENT_EVIDENCE gate
for IRON_CONDOR/IRON_FLY (confirmed, Phase 20.30's own 45-day replay:
100% INSUFFICIENT_EVIDENCE on this exact gate, because the historical
Bhavcopy corpus this codebase actually has has no real bid/ask at
all). This layer treats liquidity as INFORMATIONAL for IRON_CONDOR
(matches this phase's own literal Step 3 spec: "acceptable liquidity"
is listed under IRON_CONDOR's *preferred* conditions, not as a
separate hard requirement) -- present in `supporting_metrics` and
`reasons` whenever available, and downgrades confidence one band when
it is WIDE or absent, but never blocks selection outright. This is a
deliberate, disclosed choice: it lets this layer produce a real,
useful, explainable answer even when live liquidity data isn't
available (as in the Phase 20.30 historical corpus), while still
surfacing the caveat honestly rather than hiding it.
"""
from __future__ import annotations

import hashlib
from typing import List, Optional, Tuple

from bujji.intelligence.models import LiquidityReading, SpreadTightness
from bujji.msi_market_direction.models import MarketDirectionAssessment
from bujji.msi_market_direction import taxonomy as mdi_taxonomy
from bujji.msi_market_structure.models import MarketStructureAssessment
from bujji.msi_market_structure import taxonomy as mssi_taxonomy
from bujji.msi_price_structure.models import PriceStructureAssessment
from bujji.msi_price_structure import taxonomy as psi_taxonomy
from bujji.msi_volatility_structure.models import VolatilityStructureAssessment
from bujji.msi_volatility_structure import taxonomy as vsb_taxonomy

from . import taxonomy
from .models import CandidateEvaluation, Explanation, StructureSelectionAssessment

# Deliberately EXCLUDES LOCATION_INSIDE_RANGE: IRON_CONDOR's own extra
# defined-risk legs are worth their cost specifically when price sits
# NEAR an identifiable boundary (elevated real probability of a range
# break, matching this phase's own Scenario B framing) -- plain
# "comfortably inside the range, not near either boundary" has no
# elevated break signal and is SHORT_STRANGLE's case instead. Without
# this exclusion IRON_CONDOR's own preferred set is a strict subset of
# SHORT_STRANGLE's (both need NEUTRAL+BALANCE) and, checked first,
# would win essentially every cycle -- confirmed the hard way by this
# phase's own Scenario A test before this distinction was added.
_BOUNDED_LOCATIONS = (mssi_taxonomy.LOCATION_NEAR_SUPPORT, mssi_taxonomy.LOCATION_NEAR_RESISTANCE)
_CONFIDENT_BANDS = (taxonomy.CONFIDENCE_MODERATE, taxonomy.CONFIDENCE_HIGH)


def _assessment_id(structure: str, reasons: Tuple[str, ...], schema_version: str) -> str:
    content = "|".join([structure, "||".join(reasons), schema_version])
    return "PSA-" + hashlib.md5(content.encode("utf-8")).hexdigest()[:24]


def _insufficient(reason: str, *, timestamp: str, supporting_assessment_ids: Tuple[str, ...],
                   schema_version: str) -> StructureSelectionAssessment:
    aid = _assessment_id(taxonomy.STRUCTURE_NO_TRADE, (reason,), schema_version)
    explanation = Explanation(
        assessment_id=aid, why_this_structure=(reason,), why_not_alternatives=(),
        evidence_that_mattered_most=(), schema_version=schema_version,
    )
    return StructureSelectionAssessment(
        assessment_id=aid, timestamp=timestamp, selected_structure=taxonomy.STRUCTURE_NO_TRADE,
        confidence=taxonomy.CONFIDENCE_NONE, data_quality=taxonomy.DATA_QUALITY_INSUFFICIENT,
        reasons=(reason,), rejected_structures=(), supporting_metrics={},
        supporting_assessment_ids=supporting_assessment_ids, explanation=explanation,
        schema_version=schema_version,
    )


def _evaluate_iron_fly(psi: PriceStructureAssessment, vsb: Optional[VolatilityStructureAssessment]) -> CandidateEvaluation:
    satisfied: List[str] = []
    unsatisfied: List[str] = []
    (satisfied if psi.structure_state == psi_taxonomy.STRUCTURE_BALANCE else unsatisfied).append(
        f"psi.structure_state={psi.structure_state} (need {psi_taxonomy.STRUCTURE_BALANCE})")
    (satisfied if psi.compression_state == psi_taxonomy.COMPRESSION_CONFIRMED else unsatisfied).append(
        f"psi.compression_state={psi.compression_state} (need {psi_taxonomy.COMPRESSION_CONFIRMED})")
    if vsb is not None:
        (satisfied if vsb.iv_state == vsb_taxonomy.IV_RICH else unsatisfied).append(
            f"vsb.iv_state={vsb.iv_state} (need {vsb_taxonomy.IV_RICH})")
    else:
        unsatisfied.append("vsb is None (need iv_state=IV_RICH)")
    return CandidateEvaluation(taxonomy.STRUCTURE_IRON_FLY, not unsatisfied, tuple(satisfied), tuple(unsatisfied))


def _evaluate_iron_condor(psi: PriceStructureAssessment, mssi: MarketStructureAssessment) -> CandidateEvaluation:
    satisfied: List[str] = []
    unsatisfied: List[str] = []
    (satisfied if psi.structure_state == psi_taxonomy.STRUCTURE_BALANCE else unsatisfied).append(
        f"psi.structure_state={psi.structure_state} (need {psi_taxonomy.STRUCTURE_BALANCE})")
    (satisfied if mssi.structure_location in _BOUNDED_LOCATIONS else unsatisfied).append(
        f"mssi.structure_location={mssi.structure_location} (need one of {_BOUNDED_LOCATIONS})")
    (satisfied if mssi.confidence in _CONFIDENT_BANDS else unsatisfied).append(
        f"mssi.confidence={mssi.confidence} (need one of {_CONFIDENT_BANDS})")
    return CandidateEvaluation(taxonomy.STRUCTURE_IRON_CONDOR, not unsatisfied, tuple(satisfied), tuple(unsatisfied))


def _evaluate_short_strangle(psi: PriceStructureAssessment, vsb: Optional[VolatilityStructureAssessment]) -> CandidateEvaluation:
    satisfied: List[str] = []
    unsatisfied: List[str] = []
    (satisfied if psi.structure_state == psi_taxonomy.STRUCTURE_BALANCE else unsatisfied).append(
        f"psi.structure_state={psi.structure_state} (need {psi_taxonomy.STRUCTURE_BALANCE})")
    (satisfied if psi.compression_state != psi_taxonomy.COMPRESSION_CONFIRMED else unsatisfied).append(
        f"psi.compression_state={psi.compression_state} (need != {psi_taxonomy.COMPRESSION_CONFIRMED})")
    if vsb is not None:
        (satisfied if vsb.iv_state in (vsb_taxonomy.IV_RICH, vsb_taxonomy.IV_FAIR) else unsatisfied).append(
            f"vsb.iv_state={vsb.iv_state} (need IV_RICH or IV_FAIR)")
    else:
        unsatisfied.append("vsb is None (need iv_state in (IV_RICH, IV_FAIR))")
    return CandidateEvaluation(taxonomy.STRUCTURE_SHORT_STRANGLE, not unsatisfied, tuple(satisfied), tuple(unsatisfied))


def select_structure(
    mdi: Optional[MarketDirectionAssessment],
    psi: Optional[PriceStructureAssessment],
    mssi: Optional[MarketStructureAssessment],
    vsb: Optional[VolatilityStructureAssessment] = None,
    liquidity: Optional[LiquidityReading] = None,
    *,
    timestamp: str,
    supporting_assessment_ids: Tuple[str, ...] = (),
    schema_version: str = taxonomy.PREMIUM_STRUCTURE_INTELLIGENCE_VERSION,
) -> StructureSelectionAssessment:
    if mdi is None or psi is None or mssi is None:
        missing = [n for n, v in (("mdi", mdi), ("psi", psi), ("mssi", mssi)) if v is None]
        return _insufficient(
            f"{taxonomy.REJECT_INSUFFICIENT_EVIDENCE}: required upstream assessment(s) {missing} genuinely absent this cycle",
            timestamp=timestamp, supporting_assessment_ids=supporting_assessment_ids, schema_version=schema_version,
        )

    ids = set(supporting_assessment_ids) | {mdi.assessment_id, psi.assessment_id, mssi.assessment_id}
    if vsb is not None:
        ids.add(vsb.assessment_id)
    all_ids = tuple(sorted(ids))

    liquidity_note = None
    if liquidity is not None and liquidity.tightness != SpreadTightness.UNKNOWN:
        liquidity_note = f"liquidity_tightness={liquidity.tightness.value}"

    metrics = {
        "overall_direction": mdi.overall_direction, "overall_confidence": mdi.overall_confidence,
        "psi_structure_state": psi.structure_state, "psi_compression_state": psi.compression_state,
        "mssi_structure_location": mssi.structure_location, "mssi_confidence": mssi.confidence,
        "vsb_iv_state": vsb.iv_state if vsb is not None else None,
        "vsb_expansion_state": vsb.expansion_state if vsb is not None else None,
        "liquidity_tightness": liquidity.tightness.value if liquidity is not None else None,
    }

    def _finish(structure: str, reasons: Tuple[str, ...], confidence: str,
                evaluations: Tuple[CandidateEvaluation, ...]) -> StructureSelectionAssessment:
        aid = _assessment_id(structure, reasons, schema_version)
        explanation = Explanation(
            assessment_id=aid, why_this_structure=reasons,
            why_not_alternatives=tuple(
                f"{e.structure}: unsatisfied {e.unsatisfied_conditions}" for e in evaluations if not e.matched
            ),
            evidence_that_mattered_most=tuple(sorted(all_ids)), schema_version=schema_version,
        )
        return StructureSelectionAssessment(
            assessment_id=aid, timestamp=timestamp, selected_structure=structure, confidence=confidence,
            data_quality=taxonomy.DATA_QUALITY_SUFFICIENT, reasons=reasons, rejected_structures=evaluations,
            supporting_metrics=metrics, supporting_assessment_ids=all_ids, explanation=explanation,
            schema_version=schema_version,
        )

    if mdi.overall_direction != mdi_taxonomy.NEUTRAL:
        reason = (f"{taxonomy.REJECT_NOT_NEUTRAL}: overall_direction={mdi.overall_direction}, "
                  f"premium-selling structure choice only applies once direction is genuinely NEUTRAL")
        return _finish(taxonomy.STRUCTURE_NO_TRADE, (reason,), taxonomy.CONFIDENCE_MODERATE, ())

    if vsb is not None and vsb.expansion_state == vsb_taxonomy.EXPANSION_CONFIRMED:
        reason = (f"{taxonomy.REJECT_VOLATILITY_EXPANDING}: expansion_state={vsb.expansion_state}, "
                  f"selling premium into confirmed expanding volatility is refused")
        return _finish(taxonomy.STRUCTURE_NO_TRADE, (reason,), taxonomy.CONFIDENCE_MODERATE, ())

    fly = _evaluate_iron_fly(psi, vsb)
    condor = _evaluate_iron_condor(psi, mssi)
    strangle = _evaluate_short_strangle(psi, vsb)
    evaluations = (fly, condor, strangle)

    def _confidence(base: str) -> str:
        if liquidity is None or liquidity.tightness == SpreadTightness.WIDE or liquidity.tightness == SpreadTightness.UNKNOWN:
            order = list(taxonomy.ALL_CONFIDENCE_LEVELS)
            return order[max(0, order.index(base) - 1)]
        return base

    if fly.matched:
        base = taxonomy.CONFIDENCE_HIGH if mssi.confidence == taxonomy.CONFIDENCE_HIGH else taxonomy.CONFIDENCE_MODERATE
        reasons = ("Compressed balanced market favors defined-risk ATM premium capture.",) + fly.satisfied_conditions
        if liquidity_note:
            reasons = reasons + (liquidity_note,)
        return _finish(taxonomy.STRUCTURE_IRON_FLY, reasons, _confidence(base), (condor, strangle))

    if condor.matched:
        base = taxonomy.CONFIDENCE_HIGH if mssi.confidence == taxonomy.CONFIDENCE_HIGH else taxonomy.CONFIDENCE_MODERATE
        reasons = ("Defined range supports defined-risk premium selling.",) + condor.satisfied_conditions
        if liquidity_note:
            reasons = reasons + (liquidity_note,)
        return _finish(taxonomy.STRUCTURE_IRON_CONDOR, reasons, _confidence(base), (fly, strangle))

    if strangle.matched:
        base = taxonomy.CONFIDENCE_HIGH if mdi.overall_confidence == taxonomy.CONFIDENCE_HIGH else taxonomy.CONFIDENCE_MODERATE
        reasons = ("Neutral balanced market without extreme compression.",) + strangle.satisfied_conditions
        return _finish(taxonomy.STRUCTURE_SHORT_STRANGLE, reasons, base, (fly, condor))

    reason = (f"{taxonomy.REJECT_NO_STRUCTURE_MATCHED}: direction is neutral and volatility is not confirmed-expanding, "
              f"but no candidate structure's own condition set was satisfied -- never forcing a structure onto ambiguous evidence")
    return _finish(taxonomy.STRUCTURE_NO_TRADE, (reason,), taxonomy.CONFIDENCE_LOW, evaluations)
