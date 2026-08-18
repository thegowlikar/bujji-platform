"""Shadow Validation Recorder -- Phase 12 Task 2. Pure functions, no
state, no IO, no broker. Read-only over already-persisted
intelligence_cycle records -- never a second broker call, never fed
back into any decision.
"""
from __future__ import annotations

from typing import Any, List, Optional


def _safe_get(d: Optional[dict], *path):
    cur = d
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def build_validation_record(
    record: dict,
    completeness: Optional[dict] = None,
    consistency_warnings: Optional[List[str]] = None,
) -> dict:
    """Pure function: one intelligence_cycle record (+ optional
    completeness report from bujji.intelligence_completeness, + optional
    consistency warnings from bujji.intelligence_consistency_checker) ->
    one shadow_validation.jsonl row, matching the exact schema requested
    in the Phase 12 mission brief. Every field is a plain lookup or
    honest None -- nothing here is computed, scored, or inferred."""
    unknown_domains: List[str] = []
    if completeness and isinstance(completeness.get("domains"), dict):
        unknown_domains = sorted(
            name for name, info in completeness["domains"].items()
            if isinstance(info, dict) and info.get("status") == "UNKNOWN"
        )

    contradictions = list(consistency_warnings or [])
    narrative_contradictions = _safe_get(record, "narrative", "contradictions")
    if narrative_contradictions:
        contradictions.extend(narrative_contradictions)

    return {
        "time": record.get("timestamp"),
        "market_regime": _safe_get(record, "market_state", "regime"),
        "market_direction": _safe_get(record, "market_direction", "overall_direction"),
        "consensus": _safe_get(record, "consensus", "consensus_level"),
        "opportunity": _safe_get(record, "opportunity", "opportunity_state"),
        "trade_thesis": _safe_get(record, "trade_thesis", "thesis_type"),
        "selected_strategy": _safe_get(record, "strategy_selection", "selected_strategy_family"),
        "selection_confidence": _safe_get(record, "strategy_selection", "confidence"),
        "trade_intent": _safe_get(record, "trade_intent", "intent_state"),
        "completeness_score": completeness.get("completeness_score") if completeness else None,
        "unknown_domains": unknown_domains,
        "contradictions": contradictions,
    }


def build_validation_session(
    records: List[dict],
    completeness_reports: Optional[List[Optional[dict]]] = None,
    consistency_warnings: Optional[List[List[str]]] = None,
) -> List[dict]:
    """Maps build_validation_record over a full session's records. Pure
    map -- if completeness_reports/consistency_warnings are shorter than
    records (or omitted), the missing entries are treated as None/[]
    rather than raising."""
    out = []
    for i, r in enumerate(records):
        completeness = completeness_reports[i] if completeness_reports and i < len(completeness_reports) else None
        warnings = consistency_warnings[i] if consistency_warnings and i < len(consistency_warnings) else None
        out.append(build_validation_record(r, completeness, warnings))
    return out
