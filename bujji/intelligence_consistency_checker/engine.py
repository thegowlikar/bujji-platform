"""Intelligence Consistency Checker -- Phase 12 Task 7. Pure functions,
no state, no IO. Detects and REPORTS contradictions between independent
readings this cycle -- never auto-corrects, never modifies any upstream
assessment, never touches strategy selection logic itself. A second,
independent verification layer over decisions already made -- if it
disagrees with what msi_strategy_selector concluded, that disagreement
is surfaced, not silently resolved either way.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

# Orientation groupings -- deliberately conservative (only the clearest,
# most defensible mismatches are flagged) to avoid false positives on
# families whose real gating logic (msi_strategy_selection_foundation)
# already legitimately allows more nuance than a simple regime label.
_DIRECTIONAL_FAMILIES = frozenset({"LONG_DIRECTIONAL", "SHORT_DIRECTIONAL", "RATIO", "SYNTHETIC"})
_RANGE_FAMILIES = frozenset({"NEUTRAL_PREMIUM_SELLING", "IRON_CONDOR", "IRON_FLY", "BUTTERFLY"})
_CONFLICTING_REGIMES_FOR_DIRECTIONAL = frozenset({"RANGING", "COMPRESSED"})
_CONFLICTING_REGIMES_FOR_RANGE = frozenset({"TRENDING"})


def _safe_get(d: Optional[dict], *path):
    cur = d
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def check_cycle(record: dict) -> Tuple[str, ...]:
    """Pure function: one intelligence_cycle record -> a tuple of
    plain-English warning strings (empty if nothing detected). Never
    raises on missing/None fields -- an absent regime or selection
    simply means nothing to check yet, not a warning."""
    warnings: List[str] = []

    regime = _safe_get(record, "market_state", "regime")
    family = _safe_get(record, "strategy_selection", "selected_strategy_family")

    if regime and family:
        if family in _DIRECTIONAL_FAMILIES and regime in _CONFLICTING_REGIMES_FOR_DIRECTIONAL:
            warnings.append(
                f"Regime/Strategy mismatch: {family} (directional) selected while regime={regime}"
            )
        if family in _RANGE_FAMILIES and regime in _CONFLICTING_REGIMES_FOR_RANGE:
            warnings.append(
                f"Regime/Strategy mismatch: {family} (range-bound) selected while regime={regime}"
            )

    overall_direction = _safe_get(record, "market_direction", "overall_direction")
    if family in _DIRECTIONAL_FAMILIES and overall_direction in (None, "UNKNOWN", "MIXED"):
        warnings.append(
            f"Direction/Strategy mismatch: {family} (directional) selected with overall_direction={overall_direction}"
        )

    consensus_level = _safe_get(record, "consensus", "consensus_level")
    selection_confidence = _safe_get(record, "strategy_selection", "confidence")
    if family is not None and consensus_level == "NO_CONSENSUS" and selection_confidence == "HIGH":
        warnings.append(
            "Consensus/Confidence mismatch: HIGH selection confidence despite NO_CONSENSUS across domains"
        )

    return tuple(warnings)


def check_session(records: List[dict]) -> List[Tuple[str, ...]]:
    """Maps check_cycle over a session's records. Pure map."""
    return [check_cycle(r) for r in records]
