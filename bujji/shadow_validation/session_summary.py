"""Decision Timeline Memory / session_intelligence_summary -- Phase 12
Task 5. Pure functions, no state, no IO. Builds an end-of-session
summary from records already collected during the session -- observation
only, never a forecast of what comes next.
"""
from __future__ import annotations

from typing import List, Optional


def _safe_get(d: Optional[dict], *path):
    cur = d
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def _market_evolution(records: List[dict]) -> List[dict]:
    """One entry per REAL regime change (never every cycle) -- a
    faithful timeline of what actually changed, not noise."""
    evolution = []
    last_regime = object()  # sentinel, never equal to any real value.
    for r in records:
        regime = _safe_get(r, "market_state", "regime")
        if regime is not None and regime != last_regime:
            evolution.append({"time": r.get("timestamp"), "regime": regime})
            last_regime = regime
    return evolution


def _strategy_evolution(records: List[dict]) -> List[dict]:
    """One entry per REAL change in selected_strategy_family (including
    transitions to/from "no strategy suitable")."""
    evolution = []
    last_family = object()
    for r in records:
        family = _safe_get(r, "strategy_selection", "selected_strategy_family")
        if family != last_family:
            if family is None:
                event = "No strategy suitable"
            else:
                event = f"{family} became suitable" if last_family not in (object, None) else f"{family} emerged"
            evolution.append({"time": r.get("timestamp"), "event": event})
            last_family = family
    return evolution


def _intelligence_maturity(records: List[dict], completeness_scores: Optional[List[Optional[float]]]) -> dict:
    """Opening/mid-session/closing completeness_score samples -- real
    values only, never interpolated or forecast. None where the input
    itself was never supplied for that index."""
    if not completeness_scores or not records:
        return {"opening": None, "mid_session": None, "closing": None}
    n = len(completeness_scores)
    return {
        "opening": completeness_scores[0],
        "mid_session": completeness_scores[n // 2],
        "closing": completeness_scores[-1],
    }


def build_session_intelligence_summary(
    records: List[dict],
    completeness_scores: Optional[List[Optional[float]]] = None,
) -> dict:
    """Pure function: a session's full record list (+ optional parallel
    completeness_score list, one per record, from bujji.
    intelligence_completeness) -> session_intelligence_summary.json's
    contents. Never raises on an empty session."""
    return {
        "cycles_observed": len(records),
        "market_evolution": _market_evolution(records),
        "strategy_evolution": _strategy_evolution(records),
        "intelligence_maturity": _intelligence_maturity(records, completeness_scores),
    }
