"""Margin Stress & Scenario Engine — BUJJI Options OS v3, Numeric Risk
Governor Gate C.2.5.

PURPOSE: moves from "what is my current margin" (Gate C.1) and "why is
it this number" (Gate C.2) to "what happens to my margin, risk, and
capital availability if the market or portfolio changes" -- WITHOUT
placing any trade, and without any broker/network call anywhere in this
module. Every computation here is pure composition of Gate C.1/C.2's
already-existing, already-tested functions
(SimulatedMarginProvider.get_portfolio_margin_with_explanation,
capital_utilization.assess_capital_utilization,
capital_check.assess_capital via
whole_book_margin_provider.margin_snapshot_to_capital_check_input) --
this module introduces NO new margin math and NO new ALLOW/VETO rule.
capital_check.assess_capital() remains the single authority for
ALLOW/VETO; MarginScenarioEngine only ever reports what that function
already decided, exactly like explain_trade_decision() does in Gate C.2.

SAFEST EXTENSION POINT, VERIFIED BEFORE CODING: MarginLegRequest is a
frozen (immutable) dataclass -- a scenario is therefore built by
constructing a NEW list of (possibly new) MarginLegRequest objects,
never by mutating an existing one in place. The original
`current_legs` a caller passes in is never touched; Python's
frozen-dataclass immutability makes accidental in-place mutation
structurally impossible, not just a coding discipline.

LIMITATION, DOCUMENTED RATHER THAN FAKED: a "volatility multiplier" is
NOT implemented. MarginLegRequest carries no vega, implied volatility,
or any other volatility-sensitive field -- only qty, side, and
limit_price. A price move and a volatility move affect option premiums
through fundamentally different mechanisms (delta/gamma exposure vs
vega exposure), and approximating one with the other -- e.g. treating
a "volatility multiplier" as just another price scale factor -- would
silently misrepresent what is actually being modeled. Rather than
inventing a fake Greek this codebase has no data to back, this engine
supports only `price_shift_percent` (a real, honest, deterministic
transformation of the one price field MarginLegRequest actually
carries) and documents the volatility gap explicitly here, per this
task's own instruction: "If unsupported, document limitation."
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Callable, List, Optional, Tuple

from bujji.trading_brain.risk_governor.capital_check import CapitalCheckAssessment, assess_capital
from bujji.trading_brain.risk_governor.capital_utilization import (
    CapitalUtilizationReport,
    TradeDecisionExplanation,
    assess_capital_utilization,
    explain_trade_decision,
)
from bujji.trading_brain.risk_governor.simulated_margin_provider import (
    MarginExplanation,
    SimulatedMarginProvider,
)
from bujji.trading_brain.risk_governor.whole_book_margin_provider import (
    MarginLegRequest,
    MarginSnapshot,
    margin_snapshot_to_capital_check_input,
)

Clock = Callable[[], datetime]


@dataclass(frozen=True)
class MarginScenarioRequest:
    """Pure, deterministic description of a hypothetical book change.
    No randomness anywhere -- every field is a fixed, caller-supplied
    transformation.

    Application order (fixed, deterministic, documented so results are
    reproducible): start from `current_legs`, DROP any leg whose
    symbol appears in `removed_symbols` (exact string match -- if
    `current_legs` contains duplicate symbols, ALL matching legs are
    removed, a documented limitation of using symbol as the only
    identity key this model has), APPEND `added_legs`, then apply
    `quantity_multiplier` and `price_shift_percent` uniformly to every
    leg in the resulting combined book (baseline survivors AND newly
    added legs alike) -- never to a subset, so a scenario's stress
    parameters are unambiguous.

    `available_capital`/`configured_risk_capital` are optional: if
    `available_capital` is omitted, MarginScenarioEngine still computes
    baseline/stressed margin and explanation, just no utilization
    report or decision. `configured_risk_capital` is additionally
    required before a real capital_check.assess_capital() decision can
    be computed (matching CapitalCheckInput's own non-Optional
    requirement for that field)."""

    current_legs: Tuple[MarginLegRequest, ...]
    added_legs: Tuple[MarginLegRequest, ...] = ()
    removed_symbols: Tuple[str, ...] = ()
    quantity_multiplier: float = 1.0
    price_shift_percent: float = 0.0
    available_capital: Optional[float] = None
    configured_risk_capital: Optional[float] = None


@dataclass(frozen=True)
class MarginScenarioResult:
    baseline_snapshot: MarginSnapshot
    baseline_explanation: MarginExplanation
    stressed_snapshot: MarginSnapshot
    stressed_explanation: MarginExplanation
    margin_change: Optional[float]                          # stressed - baseline; None if either side unverified
    utilization_before: Optional[CapitalUtilizationReport]    # None unless available_capital was supplied
    utilization_after: Optional[CapitalUtilizationReport]
    risk_classification_before: str
    risk_classification_after: str
    capital_assessment: Optional[CapitalCheckAssessment]       # the REAL, authoritative decision (post-scenario)
    decision: Optional[TradeDecisionExplanation]               # explanatory layer on top of capital_assessment


class MarginScenarioEngine:
    """Pure computation only -- no broker calls, no order calls,
    verified via a dedicated adversarial audit (see this module's test
    suite) that scenario evaluation never touches any
    bujji.broker.*/fyers-adjacent import."""

    def __init__(self, provider: Optional[SimulatedMarginProvider] = None) -> None:
        self._provider = provider or SimulatedMarginProvider()

    def run(self, request: MarginScenarioRequest, clock: Clock) -> MarginScenarioResult:
        baseline_snapshot, baseline_explanation = self._provider.get_portfolio_margin_with_explanation(
            list(request.current_legs), clock=clock,
        )

        stressed_legs = self._build_stressed_legs(request)
        stressed_snapshot, stressed_explanation = self._provider.get_portfolio_margin_with_explanation(
            stressed_legs, clock=clock,
        )

        margin_change = None
        if baseline_snapshot.required_margin is not None and stressed_snapshot.required_margin is not None:
            margin_change = stressed_snapshot.required_margin - baseline_snapshot.required_margin

        utilization_before: Optional[CapitalUtilizationReport] = None
        utilization_after: Optional[CapitalUtilizationReport] = None
        capital_assessment: Optional[CapitalCheckAssessment] = None
        decision: Optional[TradeDecisionExplanation] = None

        if request.available_capital is not None:
            if baseline_snapshot.required_margin is not None:
                utilization_before = assess_capital_utilization(
                    available_capital=request.available_capital, required_margin=baseline_snapshot.required_margin,
                )
            if stressed_snapshot.required_margin is not None:
                utilization_after = assess_capital_utilization(
                    available_capital=request.available_capital, required_margin=stressed_snapshot.required_margin,
                )

            if request.configured_risk_capital is not None:
                capital_input = margin_snapshot_to_capital_check_input(
                    stressed_snapshot, available_capital=request.available_capital,
                    configured_risk_capital=request.configured_risk_capital,
                )
                # capital_check.assess_capital() is the ONLY ALLOW/VETO authority consulted anywhere
                # in this module -- this engine builds no independent decision rule of its own.
                capital_assessment = assess_capital(capital_input, clock=clock)
                if utilization_after is not None:
                    decision = explain_trade_decision(capital_assessment, stressed_explanation, utilization_after)

        return MarginScenarioResult(
            baseline_snapshot=baseline_snapshot, baseline_explanation=baseline_explanation,
            stressed_snapshot=stressed_snapshot, stressed_explanation=stressed_explanation,
            margin_change=margin_change,
            utilization_before=utilization_before, utilization_after=utilization_after,
            risk_classification_before=baseline_explanation.risk_classification,
            risk_classification_after=stressed_explanation.risk_classification,
            capital_assessment=capital_assessment, decision=decision,
        )

    @staticmethod
    def _build_stressed_legs(request: MarginScenarioRequest) -> List[MarginLegRequest]:
        """Pure. Never mutates any leg in request.current_legs/
        added_legs -- MarginLegRequest is frozen, so every
        transformation here builds a brand-new object via
        dataclasses.replace(), leaving the caller's originals intact."""
        survivors = [leg for leg in request.current_legs if leg.symbol not in request.removed_symbols]
        combined = list(survivors) + list(request.added_legs)

        stressed: List[MarginLegRequest] = []
        for leg in combined:
            new_qty = round(leg.qty * request.quantity_multiplier)
            new_price = (
                leg.limit_price * (1.0 + request.price_shift_percent / 100.0)
                if leg.limit_price is not None else leg.limit_price
            )
            stressed.append(replace(leg, qty=new_qty, limit_price=new_price))
        return stressed
