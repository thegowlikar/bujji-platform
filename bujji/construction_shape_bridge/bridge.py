"""bujji.construction_shape_bridge.bridge — Phase 20.29.

ONE public function. Calls `bujji.msi_trade_construction.engine.
construct_trade` -- the real, unmodified Series 90 entrypoint, with its
own real, unmodified signature -- only when Series 95's real
`construction_type` decision is one this package's leg-construction
logic is already known (by direct code reading) to build for that
family. See package `__init__.py` for the full rationale, including why
this is a bridge and not a direct edit to `msi_trade_construction`.
"""
from __future__ import annotations

from typing import Optional, Sequence, Tuple

from bujji.msi_position_construction.models import PositionConstructionAssessment
from bujji.msi_trade_construction.engine import construct_trade
from bujji.msi_trade_construction.models import Explanation, ExpiryDecision, TradeConstructionAssessment
from bujji.msi_trade_construction import taxonomy as tc_taxonomy
from bujji.premium_structure_intelligence.models import StructureSelectionAssessment

# Real capability declaration: the ONE construction_type value (Series 95's
# own vocabulary, bujji.msi_position_construction.taxonomy.CONSTRUCTION_*)
# msi_trade_construction's real, UNMODIFIED `_build_legs()` is already
# known -- by direct reading of that function, Phase 20.29's own audit --
# to build for each family today. Plain strings, not an import of
# msi_position_construction.taxonomy -- keeps this bridge decoupled from
# that package's internals too, the same boundary
# msi_strategy_selector.select_strategy()'s own expression_compatible_families
# parameter already established elsewhere in this codebase.
#
# Deliberately equal, family-for-family, to Series 95's own
# FAMILY_DEFAULT_CONSTRUCTION_TYPE table -- not a coincidence, both
# describe the same real, current one-shape-per-family fact from two
# sides. The two DIRECTIONAL families are the one place they could
# diverge (Series 95's real refinement logic can ask for
# VERTICAL_DEBIT_SPREAD/VERTICAL_CREDIT_SPREAD; Series 90 only ever
# builds a naked SINGLE_LEG for them) -- that divergence is the real,
# disclosed gap this bridge surfaces rather than papers over.
SUPPORTED_CONSTRUCTION_TYPE_PER_FAMILY = {
    "LONG_DIRECTIONAL": "SINGLE_LEG",
    "SHORT_DIRECTIONAL": "SINGLE_LEG",
    "COVERED": "COVERED_SHAPE",
    "SYNTHETIC": "SYNTHETIC_SHAPE",
    "NEUTRAL_PREMIUM_SELLING": "SHORT_STRANGLE",
    "NEUTRAL_PREMIUM_BUYING": "LONG_STRANGLE",
    "VOLATILITY_EXPANSION": "LONG_STRADDLE",
    "VOLATILITY_COMPRESSION": "SHORT_STRADDLE",
    "IRON_CONDOR": "IRON_CONDOR_SHAPE",
    "IRON_FLY": "IRON_FLY_SHAPE",
    "BUTTERFLY": "BUTTERFLY_SHAPE",
    "RATIO": "RATIO_SHAPE",
    "CALENDAR": "CALENDAR_SHAPE",
}

# Namespaced (not added to msi_trade_construction.taxonomy.ALL_REJECTION_REASONS,
# which is protected) so these bridge-originated reasons are never
# confused with one of Series 90's own native rejection reasons.
REJECT_CONSTRUCTION_TYPE_NOT_SUPPORTED = "BRIDGE_CONSTRUCTION_TYPE_NOT_SUPPORTED"
REJECT_NO_TRADE_SELECTED = "BRIDGE_NO_TRADE_SELECTED"

# Phase 20.31: the one honest mapping from a Premium Structure
# Intelligence decision (bujji.premium_structure_intelligence,
# StructureType) to the real MSI family whose UNMODIFIED leg-building
# logic produces that exact shape (confirmed by direct reading,
# Phase 20.29/20.31 audits -- IRON_CONDOR/IRON_FLY family names happen
# to equal their own structure names; NEUTRAL_PREMIUM_SELLING is the
# only family whose real _build_legs() branch produces a strangle).
# NO_TRADE is deliberately absent -- it never reaches construction at
# all, handled explicitly in construct_trade_honoring_structure_selection.
STRUCTURE_TO_FAMILY = {
    "SHORT_STRANGLE": "NEUTRAL_PREMIUM_SELLING",
    "IRON_CONDOR": "IRON_CONDOR",
    "IRON_FLY": "IRON_FLY",
}


def _rejected_before_construction(
    label: str, reason: str, note: str, *, timestamp: str, supporting_assessment_ids: Tuple[str, ...],
    provenance: str,
) -> TradeConstructionAssessment:
    schema_version = tc_taxonomy.MSI_TRADE_CONSTRUCTION_VERSION
    expiry_decision = ExpiryDecision(
        chosen_expiry=None, dte=None, candidate_expiries=(), rejected_expiries=(), reasoning=(note,),
    )
    explanation = Explanation(
        assessment_id="BRIDGE-REJECTED", why_this_expiry=(), why_these_strikes=(),
        why_not_neighbouring_strikes=(), dominant_constraints=(reason,),
        schema_version=schema_version,
    )
    return TradeConstructionAssessment(
        assessment_id="BRIDGE-REJECTED", timestamp=timestamp, strategy_family=label,
        constructed=False, rejection_reason=reason,
        expiry=None, expiry_decision=expiry_decision, legs=(),
        entry_reference_prices={}, expected_credit_debit=None,
        risk_profile=tc_taxonomy.RISK_UNKNOWN, required_margin=None,
        margin_unavailable_reason="construction was rejected before margin would apply",
        supporting_assessment_ids=supporting_assessment_ids, explanation=explanation,
        provenance=provenance, schema_version=schema_version,
    )


def construct_trade_honoring_position_plan(
    position_construction: PositionConstructionAssessment,
    strategy_family: str, chain: Sequence, spot: Optional[float], as_of_date: str, *,
    direction: Optional[str] = None, expected_move_pct: Optional[float] = None,
    supporting_assessment_ids: Tuple[str, ...] = (), timestamp: str,
    min_dte: Optional[int] = None, max_dte: Optional[int] = None,
) -> TradeConstructionAssessment:
    """The missing wire between Series 95 and Series 90.

    Real object shapes only: `position_construction` must be a real
    `PositionConstructionAssessment` (from `msi_position_construction.
    construct_position()`, unmodified), `strategy_family` should match
    `position_construction.selected_strategy_family`. This function
    never recomputes or reinterprets Series 95's decision -- it only
    reads `position_construction.construction_type` and either honors
    it (by calling the real Series 90 entrypoint) or refuses honestly.
    """
    requested = position_construction.construction_type
    supported = SUPPORTED_CONSTRUCTION_TYPE_PER_FAMILY.get(strategy_family)

    if requested != supported:
        note = (
            f"Position Construction (Series 95) selected construction_type={requested!r} for "
            f"{strategy_family}, but msi_trade_construction's real leg-building logic only knows "
            f"how to build {supported!r} for this family today -- refusing rather than silently "
            f"building a different shape than the real Position Construction plan asked for, or "
            f"silently ignoring that plan."
        )
        return _rejected_before_construction(
            strategy_family, REJECT_CONSTRUCTION_TYPE_NOT_SUPPORTED, note, timestamp=timestamp,
            supporting_assessment_ids=supporting_assessment_ids,
            provenance="bujji.construction_shape_bridge.bridge.construct_trade_honoring_position_plan",
        )

    kwargs = dict(
        direction=direction, expected_move_pct=expected_move_pct,
        supporting_assessment_ids=supporting_assessment_ids, timestamp=timestamp,
    )
    if min_dte is not None:
        kwargs["min_dte"] = min_dte
    if max_dte is not None:
        kwargs["max_dte"] = max_dte
    return construct_trade(strategy_family, chain, spot, as_of_date, **kwargs)


def construct_trade_honoring_structure_selection(
    structure_selection: StructureSelectionAssessment,
    chain: Sequence, spot: Optional[float], as_of_date: str, *,
    direction: Optional[str] = None, expected_move_pct: Optional[float] = None,
    supporting_assessment_ids: Tuple[str, ...] = (), timestamp: str,
    min_dte: Optional[int] = None, max_dte: Optional[int] = None,
) -> TradeConstructionAssessment:
    """Phase 20.31 -- the second, sibling wire this bridge now carries:
    from `bujji.premium_structure_intelligence.select_structure()`'s
    real `StructureSelectionAssessment` into the same real, unmodified
    Series 90 `construct_trade()` entrypoint
    `construct_trade_honoring_position_plan` already uses.
    `construct_trade_honoring_position_plan` itself is untouched --
    this is an additive sibling function, not a replacement.

    NO_TRADE never reaches construction at all -- returns an honest,
    disclosed "not constructed, none attempted" result immediately.
    Any other selected_structure is mapped to the one real MSI family
    whose unmodified leg-building logic produces that exact shape
    (`STRUCTURE_TO_FAMILY`, confirmed by direct reading) and then
    calls the real `construct_trade()` exactly as
    `construct_trade_honoring_position_plan` does -- no new
    construction logic, no divergence in behavior for the shared path.
    """
    combined_ids = tuple(sorted(set(supporting_assessment_ids) | set(structure_selection.supporting_assessment_ids)))
    structure = structure_selection.selected_structure

    if structure == "NO_TRADE" or structure not in STRUCTURE_TO_FAMILY:
        note = (
            f"Premium Structure Intelligence selected {structure!r} -- "
            f"{'; '.join(structure_selection.reasons) if structure_selection.reasons else 'no reason recorded'} "
            f"-- no construction attempted."
        )
        return _rejected_before_construction(
            "NO_TRADE", REJECT_NO_TRADE_SELECTED, note, timestamp=timestamp,
            supporting_assessment_ids=combined_ids,
            provenance="bujji.construction_shape_bridge.bridge.construct_trade_honoring_structure_selection",
        )

    family = STRUCTURE_TO_FAMILY[structure]
    kwargs = dict(
        direction=direction, expected_move_pct=expected_move_pct,
        supporting_assessment_ids=combined_ids, timestamp=timestamp,
    )
    if min_dte is not None:
        kwargs["min_dte"] = min_dte
    if max_dte is not None:
        kwargs["max_dte"] = max_dte
    return construct_trade(family, chain, spot, as_of_date, **kwargs)
