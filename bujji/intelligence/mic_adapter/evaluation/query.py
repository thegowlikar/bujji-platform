"""Evaluation Engine Query API — BUJJI Options OS, Integration Series 3,
Sprint 1.

Deterministic, READ-ONLY functions over an already-populated
`EvaluationEngine`. No function in this module accepts a mutation --
there is no delete, no update, no overwrite anywhere on this surface.
Every function only ever reads the engine's own accessors, never writes
to them.
"""
from __future__ import annotations

from datetime import date as date_type
from typing import Optional

from .engine import EvaluationEngine
from .policy import IntelligenceEvaluation


def latest(engine: EvaluationEngine) -> Optional[IntelligenceEvaluation]:
    return engine.latest()


def for_date(engine: EvaluationEngine, date: date_type) -> tuple[IntelligenceEvaluation, ...]:
    return engine.for_date(date)


def summary(engine: EvaluationEngine) -> dict:
    return engine.summary()


def agreement_statistics(engine: EvaluationEngine) -> dict:
    return engine.agreement_statistics()


def coverage_statistics(engine: EvaluationEngine) -> dict:
    return engine.coverage_statistics()
