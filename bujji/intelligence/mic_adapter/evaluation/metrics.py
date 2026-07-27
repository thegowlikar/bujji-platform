"""Evaluation Metrics — BUJJI Options OS, Integration Series 3, Sprint 1.

Tracks OPERATIONAL counts of evaluation outcomes only -- how many times
each of the five finite outcomes was reached. Never profitability, edge,
win rate, or expectancy: no such field exists anywhere in this module,
structurally, not merely by convention.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EvaluationProvenance:
    source: str              # "bujji_intelligence_evaluation" -- always this.
    ruleset_version: str


@dataclass(frozen=True)
class EvaluationMetrics:
    """A point-in-time, immutable snapshot of the engine's own running
    counters -- returned by `metrics()`, never mutated once constructed."""

    total_evaluations: int
    agreed_count: int
    disagreed_count: int
    abstained_count: int
    insufficient_count: int
    unknown_count: int
    provenance: EvaluationProvenance


class EvaluationMetricsTracker:
    """A plain, append-only recorder -- every `record()` call only
    updates this instance's own counters; it never calls back into the
    policy, the engine, the adapter, or any decision-making code. The
    five outcome literals are hardcoded here deliberately, matching the
    finite, versioned taxonomy in policy.py -- no outcome not already in
    that taxonomy can ever be recorded."""

    def __init__(self) -> None:
        self._total = 0
        self._agreed = 0
        self._disagreed = 0
        self._abstained = 0
        self._insufficient = 0
        self._unknown = 0

    def record(self, outcome: str) -> None:
        self._total += 1
        if outcome == "AGREED":
            self._agreed += 1
        elif outcome == "DISAGREED":
            self._disagreed += 1
        elif outcome == "ABSTAINED":
            self._abstained += 1
        elif outcome == "INSUFFICIENT_INTELLIGENCE":
            self._insufficient += 1
        elif outcome == "UNKNOWN":
            self._unknown += 1

    def snapshot(self, ruleset_version: str) -> EvaluationMetrics:
        return EvaluationMetrics(
            total_evaluations=self._total,
            agreed_count=self._agreed,
            disagreed_count=self._disagreed,
            abstained_count=self._abstained,
            insufficient_count=self._insufficient,
            unknown_count=self._unknown,
            provenance=EvaluationProvenance(source="bujji_intelligence_evaluation", ruleset_version=ruleset_version),
        )
