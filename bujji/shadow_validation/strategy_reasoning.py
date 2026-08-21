"""Strategy Reasoning Validation -- Phase 12 Task 4. Pure functions, no
state, no IO, no execution. Renders WHY each strategy family was
assessed the way it was this cycle -- read-only, never places, never
suggests placing, an order.
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


def _reason_for(assessment: dict) -> str:
    """One-line reason, preferring the real disclosed reasons over a
    generic fallback -- never a fabricated explanation."""
    supporting = assessment.get("supporting_reasons") or []
    rejecting = assessment.get("rejecting_reasons") or []
    if assessment.get("suitability") == "SUITABLE" and supporting:
        return supporting[0]
    if rejecting:
        return rejecting[0]
    if supporting:
        return supporting[0]
    return "no reasoning disclosed"


def build_strategy_reasoning_report(record: dict) -> dict:
    """Pure function: one intelligence_cycle record -> a market context
    summary + per-family suitability/reason, matching the Phase 12
    mission brief's example layout. Read-only -- never selects,
    executes, or recommends acting."""
    market_context = {
        "regime": _safe_get(record, "market_state", "regime"),
        "volatility": _safe_get(record, "volatility_structure", "volatility_regime"),
        "liquidity": _safe_get(record, "liquidity", "tightness"),
        "consensus": _safe_get(record, "consensus", "consensus_level"),
    }

    families = []
    for assessment in record.get("strategy_suitability") or []:
        families.append({
            "family": assessment.get("strategy_family"),
            "suitability": assessment.get("suitability"),
            "confidence": assessment.get("confidence"),
            "reason": _reason_for(assessment),
        })

    return {"market_context": market_context, "families": families}


def render_strategy_reasoning_text(report: dict) -> str:
    lines = ["Market:", ""]
    for label, value in report["market_context"].items():
        lines.append(f"{label.capitalize()}:")
        lines.append(f"  {value}")
        lines.append("")

    lines.append("Strategy Evaluation:")
    lines.append("")
    for f in report["families"]:
        lines.append(f["family"] or "UNKNOWN_FAMILY")
        lines.append("")
        lines.append("Suitability:")
        lines.append(f"  {f['suitability']}")
        lines.append("")
        lines.append("Reason:")
        lines.append(f'  "{f["reason"]}"')
        lines.append("")

    return "\n".join(lines)
