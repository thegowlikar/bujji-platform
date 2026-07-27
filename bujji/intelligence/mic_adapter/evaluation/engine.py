"""Evaluation Engine — BUJJI Options OS, Integration Series 3, Sprint 1.

Wraps the (unmodified) `IntelligencePolicy` to compare BUJJI's own
production decision against MIC v2's published intelligence, once per
decision cycle. `evaluate()` is a pure function -- no mutation, no I/O,
no clock of its own; `EvaluationEngine.run_evaluation()` is the single
stateful method the Decision Pipeline calls, and its return value is
recorded to the Evaluation Journal and MUST NEVER be read by any
decision-making code.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Callable, Optional

from . import comparator
from .config import EvaluationConfig
from .metrics import EvaluationMetrics, EvaluationMetricsTracker, EvaluationProvenance
from .policy import IntelligenceEvaluation, IntelligencePolicy


def _evaluation_id(decision_id: str, timestamp: datetime, outcome: str) -> str:
    basis = f"{decision_id}|{timestamp.isoformat()}|{outcome}"
    return "EVAL-" + hashlib.md5(basis.encode()).hexdigest()[:16]


def evaluate(
    decision_id: str,
    production_direction: Optional[str],
    snapshot: object,
    policy: IntelligencePolicy,
    clock: Callable[[], datetime],
    feature_flag_enabled: bool,
    ruleset_version: str,
) -> IntelligenceEvaluation:
    """Pure function. `snapshot` is whatever the (unmodified) Adapter
    already loaded -- an `IntelligenceSnapshot` or None -- this function
    never loads one itself, never calls the adapter, never calls MIC v2.
    Duck-typed against `.consumer_status` / `.snapshot_id` only, so this
    module never imports the Adapter's own model type."""
    snapshot_available = snapshot is not None
    consumer_status = getattr(snapshot, "consumer_status", None) if snapshot_available else None
    snapshot_id = getattr(snapshot, "snapshot_id", None) if snapshot_available else None
    # Integration Series 4, Sprint 1 -- additive: also extracted via
    # getattr (never a hard attribute access), so a snapshot predating
    # this field (or any duck-typed stand-in without it) still works.
    market_opinion_id = getattr(snapshot, "market_opinion_id", None) if snapshot_available else None

    outcome, reason = policy.classify(
        feature_flag_enabled, snapshot_available, consumer_status, production_direction, snapshot_id,
        market_opinion_id,
    )
    timestamp = clock()
    return IntelligenceEvaluation(
        evaluation_id=_evaluation_id(decision_id, timestamp, outcome),
        decision_id=decision_id,
        timestamp=timestamp,
        outcome=outcome,
        reason=reason,
        production_direction=production_direction,
        snapshot_available=snapshot_available,
        snapshot_id=snapshot_id,
        provenance=EvaluationProvenance(source="bujji_intelligence_evaluation", ruleset_version=ruleset_version),
    )


class EvaluationEngine:
    def __init__(
        self,
        config: Optional[EvaluationConfig] = None,
        policy: Optional[IntelligencePolicy] = None,
        clock: Callable[[], datetime] = datetime.now,
    ) -> None:
        self._config = config or EvaluationConfig()
        self._policy = policy or IntelligencePolicy()
        self._clock = clock
        self._metrics = EvaluationMetricsTracker()
        self._history: list[IntelligenceEvaluation] = []

    def run_evaluation(
        self, decision_id: str, production_direction: Optional[str], snapshot: object, feature_flag_enabled: bool,
    ) -> IntelligenceEvaluation:
        result = evaluate(
            decision_id, production_direction, snapshot, self._policy, self._clock,
            feature_flag_enabled, self._config.ruleset_version,
        )
        self._metrics.record(result.outcome)
        self._history.append(result)
        return result

    # ------------------------------------------------------------------ #
    # Query API -- deterministic, read-only, no mutation.
    # ------------------------------------------------------------------ #
    def latest(self) -> Optional[IntelligenceEvaluation]:
        return self._history[-1] if self._history else None

    def for_date(self, date) -> tuple[IntelligenceEvaluation, ...]:
        return tuple(e for e in self._history if e.timestamp.date() == date)

    def summary(self) -> dict:
        return comparator.comparator_summary(self._history)

    def agreement_statistics(self) -> dict:
        return {
            "agreement_rate": comparator.agreement_rate(self._history),
            "disagreement_rate": comparator.disagreement_rate(self._history),
        }

    def coverage_statistics(self) -> dict:
        return {
            "coverage_percentage": comparator.coverage_percentage(self._history),
            "abstention_rate": comparator.abstention_rate(self._history),
            "unknown_rate": comparator.unknown_rate(self._history),
        }

    def metrics(self) -> EvaluationMetrics:
        return self._metrics.snapshot(self._config.ruleset_version)

    def history(self) -> tuple[IntelligenceEvaluation, ...]:
        return tuple(self._history)
