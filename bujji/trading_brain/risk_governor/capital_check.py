"""Capital/margin check — BUJJI Options OS v3, Numeric Risk Governor.

Implements the already-agreed margin policy: `margin_verified=False`
always VETOes, unconditionally, with no allow-with-warning branch.
This module has no way of knowing whether its caller is a "dispatch-
capable runtime" or a test harness -- so it applies the strict rule
always, rather than trusting a caller-supplied flag to relax it.
Synthetic/estimated margin figures must never reach this function
except from an explicitly test-only fixture, per the existing Gate B/
prior design's own certification discipline.

No certified whole-book margin PROVIDER exists yet anywhere in this
codebase (that is a separate, standalone, live-certification task --
"Gate C"). This module is the consumer-side contract that provider
will eventually satisfy; until it exists, every real call here that
supplies `margin_verified=True` is necessarily a test fixture, never a
live figure -- there is nothing in production that could supply one.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional

Clock = Callable[[], datetime]


@dataclass(frozen=True)
class CapitalCheckInput:
    margin_verified: bool
    required_margin: Optional[float]
    available_capital: Optional[float]
    configured_risk_capital: float
    margin_source: str   # audit-only label, e.g. "CERTIFIED_WHOLE_BOOK" | "TEST_FIXTURE" | "NONE"


@dataclass(frozen=True)
class CapitalCheckAssessment:
    decision: str                  # "ALLOW" | "VETO"
    blocking_reason: Optional[str]
    evaluated_at: datetime


def _veto(reason: str, clock: Clock) -> CapitalCheckAssessment:
    return CapitalCheckAssessment(decision="VETO", blocking_reason=reason, evaluated_at=clock())


def assess_capital(inputs: CapitalCheckInput, clock: Clock) -> CapitalCheckAssessment:
    if not inputs.margin_verified:
        return _veto("MARGIN_NOT_CERTIFIED", clock)

    if inputs.required_margin is None:
        return _veto("MARGIN_DATA_MISSING", clock)
    if inputs.required_margin < 0:
        return _veto("MARGIN_DATA_INVALID_NEGATIVE", clock)

    if inputs.available_capital is None:
        return _veto("CAPITAL_DATA_MISSING", clock)
    if inputs.available_capital < 0:
        return _veto("CAPITAL_DATA_INVALID_NEGATIVE", clock)

    if inputs.configured_risk_capital < 0:
        return _veto("CONFIGURED_RISK_CAPITAL_INVALID_NEGATIVE", clock)

    usable_capital = min(inputs.available_capital, inputs.configured_risk_capital)
    if inputs.required_margin > usable_capital:
        return _veto("CAPITAL_EXCEEDED", clock)

    return CapitalCheckAssessment(decision="ALLOW", blocking_reason=None, evaluated_at=clock())
