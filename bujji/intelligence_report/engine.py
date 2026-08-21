"""Shadow Intelligence Report -- Phase 11 Upgrade 7 ("Bujji's brain
screen"). Pure functions, no state, no IO, no broker, no server.

Renders a plain read-only report from data this session's OTHER Phase
9/10/11 modules already produce (an intelligence_cycle record, plus the
optional regime_memory/narrative/context_window/completeness reports).
No trading buttons, no action anywhere in this module -- it only
formats what is already known, including what is honestly still
unknown. Deliberately NOT part of `bujji.dashboard` (that package is
the pre-existing, unrelated VWAP-straddle trading-loop status page,
built for a different subsystem) -- this is a standalone reporting
layer over the Shadow Observatory's intelligence records only.
"""
from __future__ import annotations

from typing import Optional


def _get(d: Optional[dict], *path, default=None):
    cur = d
    for key in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(key)
    return default if cur is None else cur


def build_report_dict(
    record: dict,
    regime_memory: Optional[dict] = None,
    narrative: Optional[dict] = None,
    context_window: Optional[dict] = None,
    completeness: Optional[dict] = None,
) -> dict:
    """Pure function: assembles a single, flat reporting dict from
    whatever inputs are available. Every field honestly reflects an
    UNKNOWN/None upstream value rather than fabricating a display
    string -- the renderer below is responsible for showing "UNKNOWN",
    never silently blanking a field."""
    unknowns = []
    if completeness and isinstance(completeness.get("domains"), dict):
        for name, info in completeness["domains"].items():
            if info.get("status") == "UNKNOWN":
                unknowns.append(name)

    return {
        "regime": _get(regime_memory, "current_regime", default="UNKNOWN"),
        "regime_duration_cycles": _get(regime_memory, "duration_cycles"),
        "structure": _get(record, "market_structure", "structure_location", default="UNKNOWN"),
        "volatility": _get(record, "volatility_structure", "volatility_regime", default="UNKNOWN"),
        "participants": _get(record, "participant_positioning", "positioning_bias", default="UNKNOWN"),
        "liquidity": _get(record, "liquidity", "tightness", default="UNKNOWN"),
        "narrative": _get(narrative, "market_story", default="Insufficient evidence to construct a market narrative."),
        "completeness_score": _get(completeness, "completeness_score"),
        "honesty_score": _get(completeness, "honesty_score"),
        "unknowns": sorted(unknowns),
        "short_term_direction": _get(context_window, "short_term", "direction", default="UNKNOWN"),
        "session_range_status": _get(context_window, "session_context", "range_status", default="UNKNOWN"),
    }


def render_report_text(report: dict) -> str:
    """Plain-text render, matching the mission brief's example layout.
    No trading buttons, no action controls, no HTML -- pure text."""
    lines = [
        "MARKET INTELLIGENCE REPORT",
        "",
        "Regime:",
        f"  {report['regime']}" + (f" (running {report['regime_duration_cycles']} cycles)" if report.get("regime_duration_cycles") else ""),
        "",
        "Structure:",
        f"  {report['structure']}",
        "",
        "Volatility:",
        f"  {report['volatility']}",
        "",
        "Participants:",
        f"  {report['participants']}",
        "",
        "Liquidity:",
        f"  {report['liquidity']}",
        "",
        "Narrative:",
        f"  {report['narrative']}",
        "",
        "Completeness:",
        f"  {report['completeness_score']}%" if report.get("completeness_score") is not None else "  UNKNOWN",
        "",
        "Unknowns:",
    ]
    if report["unknowns"]:
        lines.extend(f"  - {u}" for u in report["unknowns"])
    else:
        lines.append("  (none disclosed)")
    return "\n".join(lines)
