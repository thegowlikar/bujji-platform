"""Phase 20.12 -- collection. Sits directly on top of Phase 20.11's
own `bujji.shadow_decision_runtime.ShadowDecisionLog` -- reused, never
reimplemented -- adding only session-level (market-open/close, health)
context. No new accumulation mechanism.
"""
from __future__ import annotations

from collections import Counter
from typing import Sequence

from bujji.decision_orchestration import INSUFFICIENT_INTELLIGENCE
from bujji.shadow_decision_runtime import DecisionObservation, ShadowDecisionLog

from .models import CampaignSession, HEALTH_DEGRADED, HEALTH_EMPTY, HEALTH_HEALTHY

_DEGRADED_INSUFFICIENT_THRESHOLD = 0.5   # disclosed, not tuned: >50% of a session's cycles
                                          # honestly uncertain is a reliability signal worth
                                          # surfacing, not a threshold this phase optimizes against.


def collect_observation(log: ShadowDecisionLog, observation: DecisionObservation) -> DecisionObservation:
    """The single required collection entry point. Delegates directly
    to Phase 20.11's own `ShadowDecisionLog.record()` -- this package
    never reimplements accumulation."""
    return log.record(observation)


def _health_status(observations: Sequence[DecisionObservation]) -> str:
    if not observations:
        return HEALTH_EMPTY  # No data is not success -- never HEALTHY/DEGRADED with zero observations.
    insufficient = sum(1 for o in observations if o.decision_state == INSUFFICIENT_INTELLIGENCE)
    insufficient_data = sum(1 for o in observations if o.data_quality != "SUFFICIENT")
    n = len(observations)
    if (insufficient / n) > _DEGRADED_INSUFFICIENT_THRESHOLD or (insufficient_data / n) > _DEGRADED_INSUFFICIENT_THRESHOLD:
        return HEALTH_DEGRADED
    return HEALTH_HEALTHY


def build_campaign_session(
    session_date: str, market_open_time: str, market_close_time: str, log: ShadowDecisionLog,
) -> CampaignSession:
    """Derives a `CampaignSession` DIRECTLY from `log.observations` --
    never a re-judgement of any individual observation."""
    observations = log.observations
    data_quality_summary = dict(Counter(o.data_quality for o in observations))
    uncertainty_summary = dict(Counter(u for o in observations for u in o.uncertainty))
    decision_distribution = dict(Counter(o.decision_state for o in observations))

    return CampaignSession(
        session_date=session_date,
        market_open_time=market_open_time,
        market_close_time=market_close_time,
        observation_count=len(observations),
        decision_count=len(observations),
        data_quality_summary=data_quality_summary,
        uncertainty_summary=uncertainty_summary,
        decision_distribution=decision_distribution,
        health_status=_health_status(observations),
    )
