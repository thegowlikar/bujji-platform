"""Phase 20.13 -- runtime health. Three independent dimensions --
feed, intelligence, overall runtime -- every field computed directly
from the session's own real state and recorded observations, never
estimated.
"""
from __future__ import annotations

from datetime import datetime
from typing import Sequence

from bujji.decision_orchestration import INSUFFICIENT_INTELLIGENCE
from bujji.shadow_decision_runtime import DecisionObservation

from .models import (
    FeedHealth, HealthReport, IntelligenceHealth, ShadowRunState,
    RUNTIME_DEGRADED, RUNTIME_FAILED, RUNTIME_HEALTHY, SHADOW_RUN_FAILED,
)

# Disclosed, not tuned -- a feed older than this relative to `as_of` is
# stale. Mirrors the same "documented threshold, not optimized"
# discipline every prior phase's own constants use.
_STALE_MINUTES = 15
_DEGRADED_MISSING_INTELLIGENCE_RATIO = 0.5


def evaluate_runtime_health(
    state: ShadowRunState, observations: Sequence[DecisionObservation], as_of: str,
) -> HealthReport:
    reasons = []

    last_ts = observations[-1].timestamp if observations else None
    data_fresh = False
    if last_ts is not None:
        try:
            age_minutes = (datetime.fromisoformat(as_of) - datetime.fromisoformat(last_ts)).total_seconds() / 60.0
            data_fresh = 0 <= age_minutes <= _STALE_MINUTES
        except ValueError:
            data_fresh = False
    feed_health = FeedHealth(last_observation_timestamp=last_ts, missing_intervals=(), data_fresh=data_fresh)
    if not observations:
        reasons.append("feed: no observations recorded yet")
    elif not data_fresh:
        reasons.append(f"feed: last observation ({last_ts}) is stale relative to {as_of}")

    missing_intel = sum(1 for o in observations if o.decision_state == INSUFFICIENT_INTELLIGENCE)
    uncertain = sum(1 for o in observations if o.uncertainty)
    uncertainty_frequency = (uncertain / len(observations)) if observations else 0.0
    intelligence_health = IntelligenceHealth(
        decision_cycles_completed=state.cycles_completed,
        missing_intelligence_count=missing_intel, uncertainty_frequency=uncertainty_frequency,
    )
    missing_ratio = (missing_intel / len(observations)) if observations else 1.0
    if missing_ratio > _DEGRADED_MISSING_INTELLIGENCE_RATIO:
        reasons.append(f"intelligence: {missing_intel}/{len(observations) or 0} cycles INSUFFICIENT_INTELLIGENCE")

    if state.state == SHADOW_RUN_FAILED:
        runtime_status = RUNTIME_FAILED
        reasons.append("runtime: session state is FAILED")
    elif not observations or not data_fresh or missing_ratio > _DEGRADED_MISSING_INTELLIGENCE_RATIO:
        runtime_status = RUNTIME_DEGRADED
    else:
        runtime_status = RUNTIME_HEALTHY

    return HealthReport(
        session_date=state.session_date, runtime_status=runtime_status,
        feed_health=feed_health, intelligence_health=intelligence_health, reasons=tuple(reasons),
    )
