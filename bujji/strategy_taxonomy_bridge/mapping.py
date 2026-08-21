"""Strategy Taxonomy Bridge — Phase 14B P0.2. Pure declarative data, no
IO, no execution. Neither `msi_strategy_eligibility.taxonomy` (Series
82, 8 families) nor `msi_strategy_selection_foundation.taxonomy` (Series
87, 13 families) is renamed or rewritten -- both stay exactly as they
are. This module is the missing explicit mapping between them, derived
from each family's own REAL, documented risk/exposure semantics (never
guessed):

- Series 87's own `msi_trade_construction.taxonomy.DEFINED_RISK_FAMILIES`/
  `UNDEFINED_RISK_FAMILIES` (a structural, already-declared risk-shape
  fact, not derived here) directly informed which Selection families
  map under `UNDEFINED_RISK_PREMIUM` vs `DEFINED_RISK_NEUTRAL`.
- Each Selection family's own `required_direction_leans`/`consumes`
  fields (`msi_strategy_selection_foundation.taxonomy.STRATEGY_DEFINITIONS`)
  informed the directional-vs-neutral-vs-volatility mappings.

A mapping is a COMPATIBILITY relationship, not equivalence: an
Eligibility family may legitimately map to more than one Selection
family (e.g. SHORT_VOLATILITY families overlap with UNDEFINED_RISK_PREMIUM
families -- volatility-directionality and risk-shape are different,
non-exclusive axes of the same real strategy). This mirrors how real
options strategies are simultaneously describable multiple ways, not a
modeling weakness.
"""
from __future__ import annotations

from typing import Dict, Tuple

from bujji.msi_strategy_eligibility import taxonomy as _sei_taxonomy
from bujji.msi_strategy_selection_foundation import taxonomy as _ssf_taxonomy

# ---------------------------------------------------------------------------
# Eligibility family (Series 82) -> compatible Selection families (Series 87).
# Every one of Series 82's 8 families appears as a key. Rationale
# documented per entry.
# ---------------------------------------------------------------------------
ELIGIBILITY_TO_SELECTION: Dict[str, Tuple[str, ...]] = {
    # Buying options for defined-risk directional exposure -- exact match:
    # LONG_DIRECTIONAL's own definition (msi_strategy_selection_foundation.
    # taxonomy.STRATEGY_DEFINITIONS) is precisely this.
    _sei_taxonomy.FAMILY_DEFINED_RISK_DIRECTIONAL: (_ssf_taxonomy.LONG_DIRECTIONAL,),

    # A directional view expressed through a combined (hedged) structure
    # rather than a single naked leg -- RATIO (1x long + 2x short, still
    # directional) and SYNTHETIC (long+short combo replicating direction)
    # both modify a plain directional bet's risk shape via a second leg,
    # matching "HEDGED" precisely; broad, not exact.
    _sei_taxonomy.FAMILY_HEDGED_DIRECTIONAL: (_ssf_taxonomy.RATIO, _ssf_taxonomy.SYNTHETIC),

    # No separate diagonal-spread family exists in Series 87 -- CALENDAR
    # (near/far expiry, same strike) is the closest existing structural
    # analog (a diagonal is a calendar with the strikes allowed to
    # differ). Declared broad/approximate, not exact.
    _sei_taxonomy.FAMILY_DIAGONAL: (_ssf_taxonomy.CALENDAR,),

    # Long-vega, premium-buying, profits from volatility expanding --
    # exact match to both families' own definitions.
    _sei_taxonomy.FAMILY_LONG_VOLATILITY: (_ssf_taxonomy.VOLATILITY_EXPANSION, _ssf_taxonomy.NEUTRAL_PREMIUM_BUYING),

    # Short-vega, profits from volatility compressing/decaying -- spans
    # both undefined-risk (VOLATILITY_COMPRESSION, NEUTRAL_PREMIUM_SELLING)
    # and defined-risk (IRON_CONDOR/IRON_FLY/BUTTERFLY) short-vol structures;
    # volatility-directionality and risk-shape are different axes of the
    # same real strategy, so overlap with DEFINED_RISK_NEUTRAL/
    # UNDEFINED_RISK_PREMIUM below is intentional, not an error.
    _sei_taxonomy.FAMILY_SHORT_VOLATILITY: (
        _ssf_taxonomy.VOLATILITY_COMPRESSION, _ssf_taxonomy.NEUTRAL_PREMIUM_SELLING,
        _ssf_taxonomy.IRON_CONDOR, _ssf_taxonomy.IRON_FLY, _ssf_taxonomy.BUTTERFLY,
    ),

    # Shared literal name -- the one family both taxonomies already agree
    # on (confirmed in Phase 14A). Exact match.
    _sei_taxonomy.FAMILY_CALENDAR: (_ssf_taxonomy.CALENDAR,),

    # Defined-risk, range/neutral structures -- exactly Series 90's own
    # declared DEFINED_RISK_FAMILIES that are also structurally neutral
    # (IRON_CONDOR, IRON_FLY, BUTTERFLY; CALENDAR/LONG_DIRECTIONAL/
    # NEUTRAL_PREMIUM_BUYING/VOLATILITY_EXPANSION are also defined-risk
    # per that same list but are already claimed by a more specific
    # Eligibility family above).
    _sei_taxonomy.FAMILY_DEFINED_RISK_NEUTRAL: (_ssf_taxonomy.IRON_CONDOR, _ssf_taxonomy.IRON_FLY, _ssf_taxonomy.BUTTERFLY),

    # Undefined-risk premium collection -- exactly Series 90's own
    # declared UNDEFINED_RISK_FAMILIES, minus RATIO/SYNTHETIC (already
    # claimed by HEDGED_DIRECTIONAL above, a more specific directional match).
    _sei_taxonomy.FAMILY_UNDEFINED_RISK_PREMIUM: (
        _ssf_taxonomy.SHORT_DIRECTIONAL, _ssf_taxonomy.NEUTRAL_PREMIUM_SELLING,
        _ssf_taxonomy.VOLATILITY_COMPRESSION, _ssf_taxonomy.COVERED,
    ),
}

# Every mapping's exactness -- distinguishes "these are genuinely the
# same thing" from "these are compatible but not equivalent."
EXACT_MAPPINGS = frozenset({
    _sei_taxonomy.FAMILY_DEFINED_RISK_DIRECTIONAL,
    _sei_taxonomy.FAMILY_CALENDAR,
})
BROAD_MAPPINGS = frozenset(set(_sei_taxonomy.ALL_STRATEGY_FAMILIES) - EXACT_MAPPINGS)


def selection_families_for_eligibility_family(eligibility_family: str) -> Tuple[str, ...]:
    return ELIGIBILITY_TO_SELECTION.get(eligibility_family, ())


def eligibility_families_for_selection_family(selection_family: str) -> Tuple[str, ...]:
    """Reverse index -- a Selection family may legitimately map back to
    more than one Eligibility family (declared overlap, see module
    docstring). Computed once at import time, never re-derived per call."""
    return _REVERSE_INDEX.get(selection_family, ())


def _build_reverse_index() -> Dict[str, Tuple[str, ...]]:
    reverse: Dict[str, list] = {f: [] for f in _ssf_taxonomy.ALL_STRATEGY_FAMILIES}
    for eligibility_family, selection_families in ELIGIBILITY_TO_SELECTION.items():
        for sf in selection_families:
            reverse.setdefault(sf, []).append(eligibility_family)
    return {k: tuple(v) for k, v in reverse.items()}


_REVERSE_INDEX = _build_reverse_index()

# ---------------------------------------------------------------------------
# Exhaustiveness/compatibility-status vocabulary (Phase 14B P0.2 requirement:
# every Eligibility family AND every Selection family must have a declared
# relationship).
# ---------------------------------------------------------------------------
STATUS_SUPPORTED = "SUPPORTED"       # Eligibility family: has >=1 real Selection-family mapping.
STATUS_MAPPED = "MAPPED"             # Selection family: reachable from >=1 real Eligibility family.
STATUS_NOT_APPLICABLE = "NOT_APPLICABLE"
STATUS_UNSUPPORTED = "UNSUPPORTED"   # Would mean a family with zero mapping either direction -- none exist today, kept for completeness.


def eligibility_family_status(eligibility_family: str) -> str:
    if eligibility_family not in _sei_taxonomy.ALL_STRATEGY_FAMILIES:
        return STATUS_NOT_APPLICABLE
    return STATUS_SUPPORTED if ELIGIBILITY_TO_SELECTION.get(eligibility_family) else STATUS_UNSUPPORTED


def selection_family_status(selection_family: str) -> str:
    if selection_family not in _ssf_taxonomy.ALL_STRATEGY_FAMILIES:
        return STATUS_NOT_APPLICABLE
    return STATUS_MAPPED if _REVERSE_INDEX.get(selection_family) else STATUS_UNSUPPORTED
