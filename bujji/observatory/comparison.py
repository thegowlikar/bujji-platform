"""Market Intelligence Observatory — Replay Comparison & Threshold
Observatory.

BUJJI Options OS v3, Engineering Series 66.

Read-only. Compares two already-recorded replay sessions (e.g. one
session from a 41-day corpus's saved qualification report vs. the same
calendar date from an 81-day corpus's saved report) and reports
exactly what differs -- never recomputes either side, never modifies
either input dict.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

# Canonical field order for "first divergence" -- upstream fields first,
# matching Series 62's own real composition chain (context -> opinion ->
# stability -> calibration -> governance -> lifecycle -> contract),
# followed by the Trading-Brain/runtime-level fields that consume them.
CANONICAL_FIELD_ORDER: Tuple[str, ...] = (
    "market_context",
    # Series 70 Phase 2 (Context Stability Observatory, recording-only):
    # the other five MarketContext dimensions for the same cycle
    # "market_context" (== MarketContext.trend_context) already comes
    # from -- inserted immediately after it since they are siblings on
    # the same already-computed MarketContext object, and are
    # documented causal INPUTS to context_stability below (context_
    # stability/engine.py's derive_stability() reads all six dimensions
    # off every MarketContext in its window). Only numeric/enum-like
    # scalar fields are added here; the free-text
    # "stability_reasoning_summary" and list-valued "transition_events"
    # are deliberately left OUT of this canonical equality-diff order
    # (see module docstring note below) and are instead meant to be
    # surfaced as supplementary narrative text by explanation.py/
    # timeline.py in a future series.
    "context_volatility",
    "context_liquidity",
    "context_regime",
    "context_conviction",
    "context_stability_dimension",
    "market_opinion",
    # Series 70 Phase 2: ContextStability's own numeric/enum detail
    # fields (context_stability/engine.py::derive_stability()'s own
    # scalar outputs -- transition_count, persistence_length,
    # dimension_agreement, confidence_variance, context_lifetime).
    # Placed immediately BEFORE "context_stability" rather than after:
    # although these are technically derive_stability()'s own outputs,
    # they are exactly the counted/measured detail its single
    # "classification" string summarizes (classification is literally
    # transition_count / (context_lifetime - 1), thresholded) -- for a
    # first-divergence read, seeing e.g. transition_count changed
    # explains *why* classification changed, so it belongs upstream of
    # it in this equality-diff order. "stability_reasoning_summary"
    # (free text) is intentionally excluded from this list -- see
    # module docstring note below.
    "stability_transition_count",
    "stability_persistence_length",
    "stability_dimension_agreement",
    "stability_confidence_variance",
    "stability_context_lifetime",
    "context_stability",
    "calibration",
    "governance",
    "lifecycle",
    "contract",
    # Series 69 Phase 2b (deep pass): Trading-Brain pipeline fields,
    # inserted in their actual call sequence (see
    # production_runtime/runtime.py's ShadowResult construction --
    # Evidence Interpreter -> Market State -> Strategy Selector ->
    # Risk Brain -> Capital Brain -> Execution Planner). Evidence
    # Interpreter's ontology fields come first since they are the
    # earliest Trading-Brain-stage output derived from the same
    # upstream classification fields above; market_character/
    # market_phase/confidence (Market State Builder) follow, then
    # strategy selection detail, then risk, then capital, then
    # execution -- each strictly downstream of the one before it.
    "evidence_opportunity_state",
    "evidence_risk_state",
    "market_character",
    "market_phase",
    "strategy",
    "selection_status",
    "selection_confidence",
    "selection_reason",
    "risk_status",
    "risk_level",
    "risk_approval",
    "risk_blocking_reason",
    "capital_intent",
    "capital_allocation_status",
    "capital_allocation_reason",
    "execution_status",
    "execution_intent",
    "outcome",
)

UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class ThresholdCrossing:
    """A detected value change on a field, reported as a threshold
    crossing only when the underlying numeric threshold is actually
    known -- this project's MIC v2 classification engines do not
    expose their internal thresholds as data, so `threshold_name`,
    `previous_value`, and `new_value` are `UNKNOWN` unless a future
    MIC v2 series publishes them. Never inferred, never guessed.
    """

    field_name: str
    previous_classification: Optional[str]
    new_classification: Optional[str]
    threshold_name: str = UNKNOWN
    previous_value: str = UNKNOWN
    new_value: str = UNKNOWN
    crossing_direction: str = UNKNOWN


@dataclass(frozen=True)
class SessionDiff:
    session_id: str
    unchanged_fields: Tuple[str, ...]
    changed_fields: Dict[str, Dict[str, Optional[str]]]
    first_divergence: Optional[str]
    threshold_crossings: Tuple[ThresholdCrossing, ...]
    downstream_impact: str


def compare_sessions(session_a: Dict[str, Any], session_b: Dict[str, Any]) -> SessionDiff:
    """Compare two already-recorded session dicts for the same
    `id`/session identity. Never mutates either input.
    """
    if session_a.get("id") != session_b.get("id"):
        raise ValueError(
            f"compare_sessions requires the same session id; got {session_a.get('id')!r} vs {session_b.get('id')!r}"
        )

    changed: Dict[str, Dict[str, Optional[str]]] = {}
    unchanged = []
    for field_name in CANONICAL_FIELD_ORDER:
        if field_name not in session_a and field_name not in session_b:
            continue
        value_a = session_a.get(field_name)
        value_b = session_b.get(field_name)
        if value_a == value_b:
            unchanged.append(field_name)
        else:
            changed[field_name] = {"before": value_a, "after": value_b}

    first_divergence = next((f for f in CANONICAL_FIELD_ORDER if f in changed), None)

    crossings = tuple(
        ThresholdCrossing(
            field_name=f,
            previous_classification=vals["before"],
            new_classification=vals["after"],
        )
        for f, vals in changed.items()
        if f in ("market_context", "market_opinion", "context_stability", "calibration", "governance", "lifecycle", "contract")
    )

    downstream_impact = _describe_downstream_impact(changed)

    return SessionDiff(
        session_id=session_a.get("id", UNKNOWN),
        unchanged_fields=tuple(unchanged),
        changed_fields=changed,
        first_divergence=first_divergence,
        threshold_crossings=crossings,
        downstream_impact=downstream_impact,
    )


def _describe_downstream_impact(changed: Dict[str, Dict[str, Optional[str]]]) -> str:
    if "strategy" not in changed and "outcome" not in changed:
        if changed:
            return "Classification changed but no recorded downstream strategy/outcome change was observed."
        return "No change."
    parts = []
    if "market_context" in changed:
        parts.append(f"market_context {changed['market_context']['before']} -> {changed['market_context']['after']}")
    if "strategy" in changed:
        parts.append(f"strategy {changed['strategy']['before']} -> {changed['strategy']['after']}")
    if "outcome" in changed:
        parts.append(f"outcome {changed['outcome']['before']} -> {changed['outcome']['after']}")
    return "; ".join(parts) if parts else "No change."


def compare_corpora(
    sessions_a: Tuple[Dict[str, Any], ...], sessions_b: Tuple[Dict[str, Any], ...]
) -> Tuple[SessionDiff, ...]:
    """Compare two corpora's saved sessions, matched by `id`. Sessions
    present in only one corpus are skipped -- never fabricated as a
    diff against a missing counterpart.
    """
    by_id_a = {s["id"]: s for s in sessions_a}
    by_id_b = {s["id"]: s for s in sessions_b}
    shared_ids = sorted(set(by_id_a) & set(by_id_b))
    return tuple(compare_sessions(by_id_a[sid], by_id_b[sid]) for sid in shared_ids)
