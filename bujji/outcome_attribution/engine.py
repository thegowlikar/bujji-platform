"""Outcome Attribution Engine -- Phase 15J. Pure function, no state, no
IO, no broker, no execution, no wall-clock read, no randomness.

`attribute_position_outcome(lifecycle)` consumes ONLY a real
`PositionLifecycle` (Phase 15G) -- the entire thesis_evaluations and
management_assessments histories it already accumulated. Nothing here
re-reads market_snapshots.jsonl/intelligence_cycle.jsonl directly; the
lifecycle itself is the single, already-replayable source of truth
(Phase 15H's own replay engine already proves PositionLifecycle
reconstructs deterministically -- this module inherits that guarantee
for free by consuming its output, not by re-deriving anything).

HARD BOUNDARY, mechanically enforced by what this module imports (see
the dedicated safety tests): no msi_strategy_selection_foundation, no
msi_trade_intent, no position_management engine call, no broker, no
EventStore write -- this module only ever READS a PositionLifecycle
and RETURNS a new PositionOutcomeAttribution; it cannot alter anything
upstream even by accident, because it has no import path to reach it.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .models import (
    ALL_DIMENSIONS, DIM_DIRECTION, DIM_ENTRY_TIMING, DIM_EXIT_TIMING, DIM_GREEKS_EXPOSURE,
    DIM_INSUFFICIENT_EVIDENCE, DIM_LIQUIDITY, DIM_MANAGEMENT, DIM_PREMIUM_BEHAVIOUR, DIM_REGIME,
    DIM_SELECTION, DIM_STRUCTURAL_GEOMETRY, DIM_VOLATILITY,
    IMPACT_NEGATIVE, IMPACT_NEUTRAL, IMPACT_POSITIVE, IMPACT_UNKNOWN,
    NOT_READY, OUTCOME_BREAKEVEN, OUTCOME_LOSS, OUTCOME_PROFIT, OUTCOME_UNKNOWN, READY,
    ROLE_CONTRIBUTING_FACTOR, ROLE_PRIMARY_CAUSE, ROLE_PROTECTIVE_FACTOR, ROLE_UNKNOWN,
    STRENGTH_MODERATE, STRENGTH_STRONG, STRENGTH_UNKNOWN, STRENGTH_WEAK,
    AttributionEvidence, PositionOutcomeAttribution,
)

_STRENGTH_RANK = {STRENGTH_STRONG: 3, STRENGTH_MODERATE: 2, STRENGTH_WEAK: 1, STRENGTH_UNKNOWN: 0}


def _thesis_check(evaluation: dict, dimension: str) -> Optional[dict]:
    for check in evaluation.get("checks", []) or []:
        if check.get("dimension") == dimension:
            return check
    return None


def _management_evidence(assessment: dict, dimension: str) -> Optional[dict]:
    for item in assessment.get("evidence", []) or []:
        if item.get("dimension") == dimension:
            return item
    return None


def _evidence_from_thesis_trajectory(
    dimension: str, checks_dimension: str, thesis_evaluations: List[dict], source_label: str,
) -> AttributionEvidence:
    """Walks the FULL thesis-evaluation history for one Position
    Intelligence check dimension (e.g. "direction", "volatility_trend",
    "regime") and summarizes how often it stayed CONSISTENT vs
    DEVIATED -- a real, evidence-grounded trajectory, never a single
    cherry-picked snapshot."""
    resolved = [
        _thesis_check(e, checks_dimension) for e in thesis_evaluations
    ]
    resolved = [c for c in resolved if c is not None]
    if not resolved:
        return AttributionEvidence(
            dimension, ROLE_UNKNOWN, source_label, STRENGTH_UNKNOWN, None, None, IMPACT_UNKNOWN,
            "NONE", f"no {checks_dimension} check was ever recorded for this position (family may be out of scope for it)",
        )
    consistent = sum(1 for c in resolved if c["status"] == "CONSISTENT")
    deviated = sum(1 for c in resolved if c["status"] == "DEVIATED")
    unknown = sum(1 for c in resolved if c["status"] == "UNKNOWN")
    observed = f"{deviated} deviated / {consistent} consistent / {unknown} unknown across {len(resolved)} evaluations"
    if deviated == 0 and consistent == 0:
        return AttributionEvidence(dimension, ROLE_UNKNOWN, source_label, STRENGTH_UNKNOWN, observed, None,
                                    IMPACT_UNKNOWN, "NONE", f"{checks_dimension} was never resolved across this position's lifetime")
    if deviated > consistent:
        strength = STRENGTH_STRONG if deviated >= 2 * max(consistent, 1) else STRENGTH_MODERATE
        return AttributionEvidence(dimension, ROLE_CONTRIBUTING_FACTOR, source_label, strength, observed, "CONSISTENT",
                                    IMPACT_NEGATIVE, "MODERATE" if strength == STRENGTH_STRONG else "LOW",
                                    f"{checks_dimension} deviated from the entry thesis more often than it held")
    if consistent > deviated:
        strength = STRENGTH_STRONG if consistent >= 2 * max(deviated, 1) else STRENGTH_MODERATE
        return AttributionEvidence(dimension, ROLE_PROTECTIVE_FACTOR, source_label, strength, observed, "CONSISTENT",
                                    IMPACT_POSITIVE, "MODERATE" if strength == STRENGTH_STRONG else "LOW",
                                    f"{checks_dimension} remained consistent with the entry thesis for most of this position's life")
    return AttributionEvidence(dimension, ROLE_CONTRIBUTING_FACTOR, source_label, STRENGTH_WEAK, observed, "CONSISTENT",
                                IMPACT_NEUTRAL, "LOW", f"{checks_dimension} was evenly split between consistent and deviated")


def _evidence_from_management_trajectory(
    dimension: str, evidence_dimension: str, management_assessments: List[dict], source_label: str,
) -> AttributionEvidence:
    resolved = [_management_evidence(a, evidence_dimension) for a in management_assessments]
    resolved = [e for e in resolved if e is not None and e.get("status") != "UNKNOWN"]
    if not resolved:
        return AttributionEvidence(
            dimension, ROLE_UNKNOWN, source_label, STRENGTH_UNKNOWN, None, None, IMPACT_UNKNOWN,
            "NONE", f"no resolved {evidence_dimension} evidence was ever recorded for this position",
        )
    adverse_statuses = {"SEVERE_DRIFT", "UNFAVORABLE", "RISING"}
    adverse = sum(1 for e in resolved if e["status"] in adverse_statuses)
    observed = f"{adverse}/{len(resolved)} assessments showed adverse {evidence_dimension}"
    if adverse > 0:
        strength = STRENGTH_STRONG if adverse >= len(resolved) / 2 else STRENGTH_MODERATE
        return AttributionEvidence(dimension, ROLE_CONTRIBUTING_FACTOR, source_label, strength, observed, "NORMAL",
                                    IMPACT_NEGATIVE, "MODERATE" if strength == STRENGTH_STRONG else "LOW",
                                    f"{evidence_dimension} showed adverse readings in {adverse} of {len(resolved)} management assessments")
    return AttributionEvidence(dimension, ROLE_PROTECTIVE_FACTOR, source_label, STRENGTH_MODERATE, observed, "NORMAL",
                                IMPACT_POSITIVE, "MODERATE", f"{evidence_dimension} never showed an adverse reading across this position's life")


def _evidence_for_management(management_assessments: List[dict], exit_reason: Optional[str]) -> AttributionEvidence:
    if not management_assessments:
        return AttributionEvidence(
            DIM_MANAGEMENT, ROLE_UNKNOWN, "management_assessments", STRENGTH_UNKNOWN, None, None,
            IMPACT_UNKNOWN, "NONE", "no management assessment was ever recorded for this position",
        )
    recommendations = [a.get("recommendation") for a in management_assessments]
    final_recommendation = recommendations[-1]
    exit_recommended = "EXIT" in recommendations
    exit_matches = exit_reason == "thesis_invalidated" or (exit_reason and "management" in exit_reason.lower())
    if exit_recommended and exit_matches:
        return AttributionEvidence(
            DIM_MANAGEMENT, ROLE_PROTECTIVE_FACTOR, "management_assessments[*].recommendation", STRENGTH_STRONG,
            f"final_recommendation={final_recommendation}", "EXIT", IMPACT_POSITIVE, "MODERATE",
            "management recommended EXIT and the position was subsequently closed -- protective, whether or not it prevented further loss (magnitude unknown, see realized_pnl)",
        )
    if exit_recommended and not exit_matches:
        return AttributionEvidence(
            DIM_MANAGEMENT, ROLE_CONTRIBUTING_FACTOR, "management_assessments[*].recommendation", STRENGTH_WEAK,
            f"final_recommendation={final_recommendation}", "EXIT", IMPACT_UNKNOWN, "LOW",
            "management recommended EXIT at some point but the position closed for an unrelated/unlabeled reason -- unclear whether the recommendation was acted on (this layer never mutates execution, so no action record exists)",
        )
    return AttributionEvidence(
        DIM_MANAGEMENT, ROLE_UNKNOWN, "management_assessments[*].recommendation", STRENGTH_WEAK,
        f"final_recommendation={final_recommendation}", None, IMPACT_UNKNOWN, "LOW",
        "management never recommended EXIT -- its influence on the eventual outcome cannot be determined from evidence alone",
    )


def _evidence_for_exit_timing(exit_reason: Optional[str], closed_at: Optional[str]) -> AttributionEvidence:
    if not exit_reason or not closed_at:
        return AttributionEvidence(
            DIM_EXIT_TIMING, ROLE_UNKNOWN, "exit_reason/closed_at", STRENGTH_UNKNOWN, None, None,
            IMPACT_UNKNOWN, "NONE", "no exit reason/timestamp recorded -- exit timing cannot be assessed",
        )
    if exit_reason in ("session_end",):
        return AttributionEvidence(
            DIM_EXIT_TIMING, ROLE_UNKNOWN, "exit_reason", STRENGTH_WEAK, exit_reason, None, IMPACT_UNKNOWN,
            "LOW", "position closed due to an external/arbitrary boundary (session end), not a deliberate timing decision -- exit timing quality cannot be assessed from this evidence alone",
        )
    return AttributionEvidence(
        DIM_EXIT_TIMING, ROLE_CONTRIBUTING_FACTOR, "exit_reason", STRENGTH_WEAK, exit_reason, None, IMPACT_UNKNOWN,
        "LOW", f"position closed for a real, labeled reason ({exit_reason!r}) -- no exit price/premium is persisted "
               "(a disclosed limitation, see Phase 15J report), so the QUALITY of the timing cannot be quantified",
    )


def _evidence_for_selection(entry: dict, final_thesis_status: Optional[str]) -> AttributionEvidence:
    confidence = entry.get("entry_confidence")
    if final_thesis_status is None:
        return AttributionEvidence(
            DIM_SELECTION, ROLE_UNKNOWN, "entry.entry_confidence + final_thesis_status", STRENGTH_UNKNOWN,
            confidence, None, IMPACT_UNKNOWN, "NONE", "no thesis history exists to judge whether the original selection held up",
        )
    if final_thesis_status == "THESIS_INVALIDATED":
        return AttributionEvidence(
            DIM_SELECTION, ROLE_CONTRIBUTING_FACTOR, "final_thesis_status", STRENGTH_MODERATE,
            final_thesis_status, "THESIS_INTACT", IMPACT_NEGATIVE, "MODERATE",
            "the thesis this selection was based on was ultimately invalidated by the time the position closed",
        )
    if final_thesis_status == "THESIS_INTACT":
        return AttributionEvidence(
            DIM_SELECTION, ROLE_PROTECTIVE_FACTOR, "final_thesis_status", STRENGTH_MODERATE,
            final_thesis_status, "THESIS_INTACT", IMPACT_POSITIVE, "MODERATE",
            "the thesis this selection was based on remained intact through to close -- selection itself is not implicated",
        )
    return AttributionEvidence(
        DIM_SELECTION, ROLE_UNKNOWN, "final_thesis_status", STRENGTH_WEAK, final_thesis_status, None,
        IMPACT_UNKNOWN, "LOW", f"final thesis status ({final_thesis_status}) does not clearly implicate or clear the original selection",
    )


def _evidence_for_entry_timing(thesis_evaluations: List[dict]) -> AttributionEvidence:
    if not thesis_evaluations:
        return AttributionEvidence(
            DIM_ENTRY_TIMING, ROLE_UNKNOWN, "thesis_evaluations[0]", STRENGTH_UNKNOWN, None, None,
            IMPACT_UNKNOWN, "NONE", "no post-entry thesis evaluation exists -- entry timing cannot be assessed",
        )
    first = thesis_evaluations[0]
    direction_check = _thesis_check(first, "direction")
    if direction_check is None:
        return AttributionEvidence(
            DIM_ENTRY_TIMING, ROLE_UNKNOWN, "thesis_evaluations[0].checks.direction", STRENGTH_UNKNOWN, None, None,
            IMPACT_UNKNOWN, "NONE", "the first post-entry evaluation had no resolvable direction check",
        )
    if direction_check["status"] == "DEVIATED":
        return AttributionEvidence(
            DIM_ENTRY_TIMING, ROLE_CONTRIBUTING_FACTOR, "thesis_evaluations[0].checks.direction", STRENGTH_MODERATE,
            direction_check["current_value"], direction_check["entry_value"], IMPACT_NEGATIVE, "LOW",
            "the market had already moved against the entry thesis by the very first post-entry evaluation",
        )
    return AttributionEvidence(
        DIM_ENTRY_TIMING, ROLE_PROTECTIVE_FACTOR, "thesis_evaluations[0].checks.direction", STRENGTH_WEAK,
        direction_check["current_value"], direction_check["entry_value"], IMPACT_POSITIVE, "LOW",
        "the market had not yet moved against the entry thesis at the first post-entry evaluation",
    )


def compute_mfe_mae(valuation_history=()) -> Tuple[Optional[float], Optional[float]]:
    """Maximum Favorable/Adverse Excursion over a REAL, caller-supplied
    per-cycle unrealized-P&L history -- pure `max()`/`min()`, no new
    math. Returns `(None, None)` honestly, never `(0.0, 0.0)`, when no
    history is supplied -- an empty/absent history means "we don't
    know," not "the position never moved." See models.py's own
    docstring for why every real caller supplies `()` today: no
    per-cycle valuation capture point exists anywhere in
    `PositionLifecycle.thesis_evaluations`/`management_assessments`
    yet (confirmed by direct trace, not assumed)."""
    values = [v for v in valuation_history if v is not None]
    if not values:
        return None, None
    return max(values), min(values)


def attribute_position_outcome(lifecycle, valuation_history=()) -> PositionOutcomeAttribution:
    """`lifecycle`: a real `PositionLifecycle` (Phase 15G). Never
    raises; every missing input degrades to an honest UNKNOWN evidence
    item, never a fabricated conclusion. `valuation_history`
    (Phase 5, optional, defaults to `()`): a real per-cycle unrealized-
    P&L sequence, when a caller has one -- see compute_mfe_mae()."""
    position_id = lifecycle.position_id
    mfe, mae = compute_mfe_mae(valuation_history)

    if lifecycle.status != "CLOSED":
        return PositionOutcomeAttribution(
            position_id=position_id, evaluation_timestamp=lifecycle.opened_at, readiness=NOT_READY,
            outcome_direction=OUTCOME_UNKNOWN, realized_pnl=None, primary_cause=None,
            contributing_factors=(), protective_factors=(), evidence=(),
            narrative="position is not CLOSED -- an outcome attribution cannot exist yet",
            mfe=mfe, mae=mae,
        )

    entry = lifecycle.entry.to_dict() if hasattr(lifecycle.entry, "to_dict") else lifecycle.entry
    thesis_evaluations = list(lifecycle.thesis_evaluations)
    management_assessments = list(lifecycle.management_assessments)

    evidence = [
        _evidence_for_selection(entry, lifecycle.final_thesis_status),
        _evidence_for_entry_timing(thesis_evaluations),
        _evidence_from_thesis_trajectory(DIM_REGIME, "regime", thesis_evaluations, "thesis_evaluations[*].checks.regime"),
        _evidence_from_thesis_trajectory(DIM_DIRECTION, "direction", thesis_evaluations, "thesis_evaluations[*].checks.direction"),
        _evidence_from_thesis_trajectory(DIM_VOLATILITY, "volatility_trend", thesis_evaluations, "thesis_evaluations[*].checks.volatility_trend"),
        _evidence_from_management_trajectory(DIM_GREEKS_EXPOSURE, "exposure", management_assessments, "management_assessments[*].evidence.exposure"),
        _evidence_from_management_trajectory(DIM_PREMIUM_BEHAVIOUR, "premium_behaviour", management_assessments, "management_assessments[*].evidence.premium_behaviour"),
        _evidence_from_management_trajectory(DIM_LIQUIDITY, "liquidity", management_assessments, "management_assessments[*].evidence.liquidity"),
        _evidence_from_management_trajectory(DIM_STRUCTURAL_GEOMETRY, "expiry_geometry", management_assessments, "management_assessments[*].evidence.expiry_geometry"),
        _evidence_for_management(management_assessments, lifecycle.exit_reason),
        _evidence_for_exit_timing(lifecycle.exit_reason, lifecycle.closed_at),
    ]

    resolved = [e for e in evidence if e.role != ROLE_UNKNOWN]
    if not resolved:
        primary_cause = None
        contributing = ()
        protective = ()
    else:
        ranked = sorted(resolved, key=lambda e: (_STRENGTH_RANK[e.strength], e.role == ROLE_PRIMARY_CAUSE), reverse=True)
        negative = [e for e in ranked if e.impact_direction == IMPACT_NEGATIVE]
        primary_cause = negative[0].dimension if negative else (ranked[0].dimension if ranked else None)
        contributing = tuple(e.dimension for e in resolved if e.role == ROLE_CONTRIBUTING_FACTOR and e.dimension != primary_cause)
        protective = tuple(e.dimension for e in resolved if e.role == ROLE_PROTECTIVE_FACTOR)

    if not resolved:
        evidence.append(AttributionEvidence(
            DIM_INSUFFICIENT_EVIDENCE, ROLE_UNKNOWN, "all dimensions", STRENGTH_UNKNOWN, None, None,
            IMPACT_UNKNOWN, "NONE", "no dimension resolved to a real conclusion -- insufficient evidence overall",
        ))

    realized_pnl = lifecycle.realized_pnl
    if realized_pnl is None:
        outcome_direction = OUTCOME_UNKNOWN
    elif realized_pnl > 0:
        outcome_direction = OUTCOME_PROFIT
    elif realized_pnl < 0:
        outcome_direction = OUTCOME_LOSS
    else:
        outcome_direction = OUTCOME_BREAKEVEN

    if outcome_direction == OUTCOME_UNKNOWN:
        pnl_clause = "the $ outcome (profit/loss) is UNKNOWN -- realized_pnl is not yet linked to any position (a disclosed Phase 15B/15G/15J limitation, not fabricated here)"
    else:
        pnl_clause = f"the position realized a {outcome_direction.lower()} of {realized_pnl}"
    cause_clause = f"the dominant causal factor identified was {primary_cause}" if primary_cause else "no single dimension could be identified as the dominant cause"
    narrative = f"{pnl_clause}; {cause_clause}."

    return PositionOutcomeAttribution(
        position_id=position_id, evaluation_timestamp=lifecycle.closed_at or "", readiness=READY,
        outcome_direction=outcome_direction, realized_pnl=realized_pnl, primary_cause=primary_cause,
        contributing_factors=contributing, protective_factors=protective, evidence=tuple(evidence),
        narrative=narrative, mfe=mfe, mae=mae,
    )
