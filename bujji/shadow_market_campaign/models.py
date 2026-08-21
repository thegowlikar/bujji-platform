"""Phase 20.12 -- pure data contracts. No IO, no broker, no execution,
no holdings- or realized-outcome-tracking vocabulary anywhere in this
module.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Tuple

HEALTH_EMPTY = "EMPTY"
HEALTH_DEGRADED = "DEGRADED"
HEALTH_HEALTHY = "HEALTHY"
ALL_HEALTH_STATUSES = (HEALTH_EMPTY, HEALTH_DEGRADED, HEALTH_HEALTHY)

STABILITY_LOW = "LOW"
STABILITY_MODERATE = "MODERATE"
STABILITY_HIGH = "HIGH"
ALL_STABILITY_LABELS = (STABILITY_LOW, STABILITY_MODERATE, STABILITY_HIGH)


@dataclass(frozen=True)
class CampaignSession:
    """One trading day's aggregate, built directly from Phase 20.11's
    own `ShadowDecisionLog.observations` -- never a re-judgement of any
    individual `DecisionObservation`. `decision_count` equals
    `observation_count` by construction: every recorded observation IS
    one decision cycle for one candidate strategy (Phase 20.11's own
    1:1 mapping) -- kept as a separate, explicit field because the
    charter names both, not because the two can diverge."""

    session_date: str
    market_open_time: str
    market_close_time: str
    observation_count: int
    decision_count: int
    data_quality_summary: Dict[str, int]      # data_quality label -> occurrence count.
    uncertainty_summary: Dict[str, int]        # distinct uncertainty reason -> occurrence count.
    decision_distribution: Dict[str, int]
    health_status: str                         # ALL_HEALTH_STATUSES

    def __post_init__(self) -> None:
        if self.health_status not in ALL_HEALTH_STATUSES:
            raise ValueError(f"health_status={self.health_status!r} not in {ALL_HEALTH_STATUSES}")
        if self.observation_count == 0 and self.health_status != HEALTH_EMPTY:
            raise ValueError("a session with zero observations must be EMPTY, never HEALTHY/DEGRADED -- "
                              "no data is not success.")

    def render(self) -> str:
        lines = [
            f"Campaign Session {self.session_date}  [{self.market_open_time} - {self.market_close_time}]  "
            f"health={self.health_status}",
            f"  Observations: {self.observation_count}  Decisions: {self.decision_count}",
            f"  Decision distribution: {self.decision_distribution}",
            f"  Data quality: {self.data_quality_summary}",
        ]
        if self.uncertainty_summary:
            lines.append(f"  Uncertainty: {self.uncertainty_summary}")
        return "\n".join(lines)


@dataclass(frozen=True)
class CampaignMetrics:
    """Reliability/stability/completeness metrics ONLY -- never
    profit, win rate, or strategy performance (explicitly out of scope
    for this phase). Every field is computed directly from the
    session's own recorded observations."""

    session_date: str
    decision_change_count: int                # total decision-state flips, across all candidates, in timestamp order.
    decision_stability: str                    # ALL_STABILITY_LABELS -- derived from change_count / cycles.
    confidence_oscillation_count: int          # total confidence-label flips, across all candidates.
    pct_complete_intelligence: float           # fraction of observations NOT INSUFFICIENT_INTELLIGENCE.
    pct_insufficient_intelligence: float
    explanation_completeness_pct: float        # fraction carrying >=1 reason_code or >=1 uncertainty entry (never bare).
    market_coverage_pct: float                 # fraction of the expected 5-min grid (open..close) with an observation nearby.
    missing_intervals: Tuple[str, ...]          # human-readable gap descriptions, never silently dropped.

    def __post_init__(self) -> None:
        if self.decision_stability not in ALL_STABILITY_LABELS:
            raise ValueError(f"decision_stability={self.decision_stability!r} not in {ALL_STABILITY_LABELS}")
        for name, value in (
            ("pct_complete_intelligence", self.pct_complete_intelligence),
            ("pct_insufficient_intelligence", self.pct_insufficient_intelligence),
            ("explanation_completeness_pct", self.explanation_completeness_pct),
            ("market_coverage_pct", self.market_coverage_pct),
        ):
            if not (0.0 <= value <= 1.0):
                raise ValueError(f"{name}={value!r} must be within [0.0, 1.0]")

    def render(self) -> str:
        lines = [
            f"Campaign Metrics {self.session_date}",
            f"  Decision stability: {self.decision_stability}  ({self.decision_change_count} changes)",
            f"  Confidence oscillation: {self.confidence_oscillation_count}",
            f"  Intelligence availability: {self.pct_complete_intelligence:.0%} complete, "
            f"{self.pct_insufficient_intelligence:.0%} insufficient",
            f"  Explanation completeness: {self.explanation_completeness_pct:.0%}",
            f"  Market coverage: {self.market_coverage_pct:.0%}",
        ]
        if self.missing_intervals:
            lines.append("  Missing intervals:")
            for gap in self.missing_intervals:
                lines.append(f"    - {gap}")
        return "\n".join(lines)
