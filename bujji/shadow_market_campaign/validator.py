"""Phase 20.12 -- validation. Measures RELIABILITY of the intelligence
chain during a session -- decision stability, intelligence
availability, explanation quality, market coverage. Never profit, win
rate, or strategy performance; never tunes a threshold or optimizes a
decision. Every metric is computed directly from the session's own
recorded observations.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List, Sequence

from bujji.decision_orchestration import INSUFFICIENT_INTELLIGENCE
from bujji.shadow_decision_runtime import DecisionObservation

from .models import CampaignMetrics, CampaignSession, STABILITY_HIGH, STABILITY_LOW, STABILITY_MODERATE

_COVERAGE_GRID_MINUTES = 5          # matches Cycle 1's own 5-minute candle cadence (Phase 20.1).
_COVERAGE_TOLERANCE_MINUTES = 10    # an observation "covers" a grid slot within this window.

# Disclosed, not tuned -- change_ratio = flips / (cycles - 1) per candidate,
# averaged across candidates with >1 cycle. Mirrors the same "documented
# threshold, not optimized" discipline every prior phase's own constants use.
_STABILITY_LOW_MAX_RATIO = 0.10
_STABILITY_MODERATE_MAX_RATIO = 0.30


def _by_candidate(observations: Sequence[DecisionObservation]) -> Dict[str, List[DecisionObservation]]:
    grouped: Dict[str, List[DecisionObservation]] = {}
    for o in observations:
        if o.candidate_strategy is None:
            continue
        grouped.setdefault(o.candidate_strategy, []).append(o)
    for name in grouped:
        grouped[name].sort(key=lambda o: o.timestamp)
    return grouped


def _count_flips(values: List[str]) -> int:
    return sum(1 for i in range(1, len(values)) if values[i] != values[i - 1])


def _decision_stability(observations: Sequence[DecisionObservation]) -> tuple:
    grouped = _by_candidate(observations)
    total_changes = 0
    ratios = []
    for name, obs_list in grouped.items():
        states = [o.decision_state for o in obs_list]
        flips = _count_flips(states)
        total_changes += flips
        if len(obs_list) > 1:
            ratios.append(flips / (len(obs_list) - 1))
    avg_ratio = sum(ratios) / len(ratios) if ratios else 0.0
    if avg_ratio <= _STABILITY_LOW_MAX_RATIO:
        label = STABILITY_LOW
    elif avg_ratio <= _STABILITY_MODERATE_MAX_RATIO:
        label = STABILITY_MODERATE
    else:
        label = STABILITY_HIGH
    return total_changes, label


def _confidence_oscillation(observations: Sequence[DecisionObservation]) -> int:
    grouped = _by_candidate(observations)
    total = 0
    for obs_list in grouped.values():
        confidences = [o.confidence for o in obs_list if o.confidence is not None]
        total += _count_flips(confidences)
    return total


def _market_coverage(session: CampaignSession, observations: Sequence[DecisionObservation]) -> tuple:
    try:
        open_dt = datetime.fromisoformat(session.market_open_time)
        close_dt = datetime.fromisoformat(session.market_close_time)
    except ValueError:
        return 0.0, ()  # honest zero -- never fabricate coverage from an unparseable window.
    if close_dt <= open_dt:
        return 0.0, ()

    timestamps = sorted(datetime.fromisoformat(o.timestamp) for o in observations)

    slots = []
    t = open_dt
    while t <= close_dt:
        slots.append(t)
        t += timedelta(minutes=_COVERAGE_GRID_MINUTES)
    if not slots:
        return 0.0, ()

    tolerance = timedelta(minutes=_COVERAGE_TOLERANCE_MINUTES)
    covered = 0
    gap_start = None
    missing_intervals = []
    for slot in slots:
        has_coverage = any(abs((slot - ts).total_seconds()) <= tolerance.total_seconds() for ts in timestamps)
        if has_coverage:
            covered += 1
            if gap_start is not None:
                missing_intervals.append(f"{gap_start.isoformat()} to {slot.isoformat()}")
                gap_start = None
        else:
            if gap_start is None:
                gap_start = slot
    if gap_start is not None:
        missing_intervals.append(f"{gap_start.isoformat()} to {slots[-1].isoformat()}")

    return covered / len(slots), tuple(missing_intervals)


def validate_session_behavior(
    session: CampaignSession, observations: Sequence[DecisionObservation],
) -> CampaignMetrics:
    decision_change_count, decision_stability = _decision_stability(observations)
    confidence_oscillation_count = _confidence_oscillation(observations)

    n = len(observations)
    if n == 0:
        pct_complete = 0.0
        pct_insufficient = 0.0
        explanation_completeness = 0.0
    else:
        insufficient = sum(1 for o in observations if o.decision_state == INSUFFICIENT_INTELLIGENCE)
        pct_insufficient = insufficient / n
        pct_complete = 1.0 - pct_insufficient
        explained = sum(1 for o in observations if o.reason_codes or o.uncertainty)
        explanation_completeness = explained / n

    market_coverage_pct, missing_intervals = _market_coverage(session, observations)

    return CampaignMetrics(
        session_date=session.session_date,
        decision_change_count=decision_change_count,
        decision_stability=decision_stability,
        confidence_oscillation_count=confidence_oscillation_count,
        pct_complete_intelligence=pct_complete,
        pct_insufficient_intelligence=pct_insufficient,
        explanation_completeness_pct=explanation_completeness,
        market_coverage_pct=market_coverage_pct,
        missing_intervals=missing_intervals,
    )
