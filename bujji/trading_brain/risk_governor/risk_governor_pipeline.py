"""Risk Governor Orchestrator & Decision Pipeline -- BUJJI Options OS
v3, Numeric Risk Governor Gate D.6.

PURPOSE: run D.1-D.5 in the correct order and produce ONE final
decision. This module performs NO new risk calculations, duplicates
NO threshold, and never re-implements any governor's own admit/reject
logic -- every STOP condition below reads a field the corresponding
governor already computes (`.allowed`, a literal status constant, or
an action constant), never a value recomputed from raw inputs.

STEP 1 INSPECTION SUMMARY (each governor's actual public interface,
read directly from source before writing this file):

  D.1 capital_safety_governor.evaluate_trade_capital_safety(
        current_state: CapitalSafetySnapshot, proposed_trade_effect: ProposedTradeEffect,
        thresholds: Optional[CapitalSafetyThresholds], clock) -> CapitalSafetyDecision
      CapitalSafetyDecision has `.allowed: bool` -- the single
      authoritative admit/reject field, already correctly encoding
      the BLOCKED-before-state and RESTRICTED-risk-increasing
      invariants. Used directly; never re-derived from `.status`.

  D.2 portfolio_risk_aggregator.aggregate_portfolio_risk(...) ->
      PortfolioRiskSnapshot, then classify_portfolio_risk(snapshot, thresholds)
      -> (status, reasons), then explain_portfolio_risk(snapshot, status, reasons) -> str.
      D.2 is a pure classifier/aggregator, NOT a per-trade admit/reject
      governor (confirmed by its own module docstring's "Aggregator ->
      Governor -> Admission" one-way chain -- D.1/D.3 consume D.2's
      output, D.2 itself never decides). It has no `.allowed` field
      and no BLOCKED status. The task brief's "Portfolio BLOCKED ->
      STOP" rule is therefore mapped onto D.2's own RISK_INVALID
      status (missing/malformed data -- D.2's existing "cannot be
      trusted, don't proceed" signal) -- the only status D.2 exposes
      that means "this read cannot be used." RISK_HIGH_RISK/
      RISK_CONCENTRATED do NOT stop the pipeline on their own; they
      already flow into D.4's own portfolio-context escalation
      (REDUCE_SIZE vs ADD_HEDGE), exactly as D.4 was built to do.

  D.3 risk_budget_governor.calculate_available_risk_budget(
        capital_safety_snapshot, portfolio_risk_snapshot, risk_policy,
        current_risk_status, clock) -> RiskBudgetSnapshot
      then assess_trade_risk_budget(portfolio_risk_snapshot, proposed_trade_risk,
        risk_budget_snapshot, risk_policy, clock) -> RiskBudgetDecision
      (`.allowed: bool`, `.status` one of APPROVED/APPROVED_WITH_WARNING/
      REDUCED_SIZE_REQUIRED/REJECTED)
      then calculate_safe_position_size(desired_quantity, requested_risk,
        risk_budget_snapshot) -> PositionSizeRecommendation
      (`.recommended_quantity`, `.maximum_quantity` -- the hard ceiling
      D.5 itself already clamps against).

  D.4 position_lifecycle_intelligence.recommend_risk_action(
        position_snapshot, capital_safety_status, portfolio_risk_status,
        thresholds, clock) -> RiskActionRecommendation
      (`.action` in HOLD/MONITOR/REDUCE_SIZE/ADD_HEDGE/
      EXIT_CONSIDERATION/BLOCK_NEW_RISK -- BLOCK_NEW_RISK is the literal
      existing blocking constant, matching D.5's own established
      BLOCKING_ADMISSION_STATUSES precedent).

  D.5 adaptive_risk_memory.summarize_strategy_experience(entries, strategy_type,
        market_regime) -> StrategyExperience
      then adaptive_risk_recommendation.recommend_adaptive_risk_adjustment(experience)
        -> AdaptiveRiskRecommendation
      then adaptive_risk_governor.evaluate_adaptive_governor_decision(
        admission_status, base_size, maximum_size, adaptive_recommendation)
        -> AdaptiveGovernorDecision (`.final_suggested_size`, already
        clamped to maximum_size and forced to 0 on a blocking
        admission_status -- reused verbatim, never re-clamped here).

REUSABLE EXPLANATION FIELDS, carried into DecisionTrace/
explain_pipeline_result() VERBATIM, never rewritten: CapitalSafety
Decision.explanation, explain_portfolio_risk()'s return string,
RiskBudgetDecision.explanation + PositionSizeRecommendation.reason,
RiskActionRecommendation.explanation, AdaptiveGovernorDecision.reason.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .capital_safety_governor import (
    CapitalSafetyDecision, CapitalSafetySnapshot, CapitalSafetyThresholds, Clock,
    ProposedTradeEffect, evaluate_trade_capital_safety,
)
from .portfolio_risk_aggregator import (
    PortfolioRiskSnapshot, PortfolioRiskThresholds, RISK_INVALID,
    aggregate_portfolio_risk, classify_portfolio_risk, explain_portfolio_risk,
)
from .risk_budget_governor import (
    PositionSizeRecommendation, RiskBudgetDecision, RiskBudgetSnapshot, RiskPolicy,
    assess_trade_risk_budget, calculate_available_risk_budget, calculate_safe_position_size,
)
from .position_lifecycle_intelligence import (
    ACTION_BLOCK_NEW_RISK, PositionHealthThresholds, PositionRiskSnapshot,
    RiskActionRecommendation, recommend_risk_action,
)
from .adaptive_risk_memory import RiskMemoryEntry, StrategyExperience, summarize_strategy_experience
from .adaptive_risk_recommendation import AdaptiveRiskRecommendation, recommend_adaptive_risk_adjustment
from .adaptive_risk_governor import AdaptiveGovernorDecision, evaluate_adaptive_governor_decision

STAGE_CAPITAL = "CAPITAL"
STAGE_PORTFOLIO = "PORTFOLIO"
STAGE_BUDGET = "BUDGET"
STAGE_ADMISSION = "ADMISSION"
STAGE_ADAPTIVE = "ADAPTIVE"

PIPELINE_APPROVED = "APPROVED"
PIPELINE_BLOCKED = "BLOCKED"

# Fixed execution order -- the ONLY order this pipeline ever runs in.
STAGE_ORDER = (STAGE_CAPITAL, STAGE_PORTFOLIO, STAGE_BUDGET, STAGE_ADMISSION, STAGE_ADAPTIVE)


# --------------------------------------------------------------------- #
# Part 3 -- Pipeline Context. Only raw data each governor's own public
# function already requires -- no derived value, no cached decision.
# --------------------------------------------------------------------- #

@dataclass(frozen=True)
class GovernorPipelineContext:
    # Capital (D.1)
    capital_snapshot: CapitalSafetySnapshot
    proposed_trade_effect: ProposedTradeEffect
    capital_safety_thresholds: Optional[CapitalSafetyThresholds]

    # Portfolio (D.2)
    position_groups: List
    margin_snapshot: Optional[object]
    margin_explanation: Optional[object]
    risk_by_position_group_id: Optional[Dict[str, float]]
    portfolio_risk_thresholds: Optional[PortfolioRiskThresholds]

    # Budget (D.3)
    risk_policy: RiskPolicy
    desired_quantity: int
    requested_risk: float

    # Admission (D.4)
    position_snapshot: PositionRiskSnapshot
    position_health_thresholds: Optional[PositionHealthThresholds]

    # Adaptive (D.5)
    strategy_type: str
    market_regime: Optional[str]
    memory_entries: Tuple[RiskMemoryEntry, ...]

    clock: Clock


# --------------------------------------------------------------------- #
# Part 4 -- Pipeline Result / Part 6 -- Decision Trace
# --------------------------------------------------------------------- #

@dataclass(frozen=True)
class PortfolioGovernorOutcome:
    """Pure packaging of D.2's own three existing return values -- not
    a new classification, nothing recomputed."""
    snapshot: PortfolioRiskSnapshot
    status: str
    reasons: Tuple[str, ...]
    explanation: str


@dataclass(frozen=True)
class BudgetGovernorOutcome:
    """Pure packaging of D.3's own three existing return values."""
    snapshot: RiskBudgetSnapshot
    decision: RiskBudgetDecision
    sizing: PositionSizeRecommendation


@dataclass(frozen=True)
class TraceStep:
    stage: str
    status: str
    detail: str


@dataclass(frozen=True)
class DecisionTrace:
    steps: Tuple[TraceStep, ...]


@dataclass(frozen=True)
class GovernorPipelineResult:
    capital_decision: Optional[CapitalSafetyDecision]
    portfolio_decision: Optional[PortfolioGovernorOutcome]
    budget_decision: Optional[BudgetGovernorOutcome]
    admission_decision: Optional[RiskActionRecommendation]
    adaptive_decision: Optional[AdaptiveGovernorDecision]
    final_status: str
    final_quantity: int
    blocking_stage: Optional[str]
    decision_trace: DecisionTrace


# --------------------------------------------------------------------- #
# Part 2 -- Pipeline
# --------------------------------------------------------------------- #

def run_risk_governor_pipeline(context: GovernorPipelineContext) -> GovernorPipelineResult:
    """Executes D.1 -> D.2 -> D.3 -> D.4 -> D.5 in exactly this order,
    exactly once each, with no stage skipped. A blocking result from
    any of D.1-D.4 halts execution immediately -- D.5 never runs on a
    trade that was already going to be blocked, and it can never
    reverse a block that already happened (Part 5's "adaptive may
    never unblock anything")."""
    steps: List[TraceStep] = []

    # -- Stage 1: Capital Safety (D.1) -----------------------------------
    capital_decision = evaluate_trade_capital_safety(
        context.capital_snapshot, context.proposed_trade_effect, context.capital_safety_thresholds, context.clock,
    )
    steps.append(TraceStep(STAGE_CAPITAL, capital_decision.status, capital_decision.explanation))

    if not capital_decision.allowed:
        return GovernorPipelineResult(
            capital_decision=capital_decision, portfolio_decision=None, budget_decision=None,
            admission_decision=None, adaptive_decision=None, final_status=PIPELINE_BLOCKED,
            final_quantity=0, blocking_stage=STAGE_CAPITAL, decision_trace=DecisionTrace(tuple(steps)),
        )

    # -- Stage 2: Portfolio Risk (D.2) -----------------------------------
    portfolio_snapshot = aggregate_portfolio_risk(
        context.position_groups, context.margin_snapshot, context.margin_explanation,
        context.risk_by_position_group_id, context.clock,
    )
    portfolio_status, portfolio_reasons = classify_portfolio_risk(portfolio_snapshot, context.portfolio_risk_thresholds)
    portfolio_explanation = explain_portfolio_risk(portfolio_snapshot, portfolio_status, portfolio_reasons)
    portfolio_decision = PortfolioGovernorOutcome(
        snapshot=portfolio_snapshot, status=portfolio_status, reasons=portfolio_reasons, explanation=portfolio_explanation,
    )
    steps.append(TraceStep(STAGE_PORTFOLIO, portfolio_status, portfolio_explanation))

    if portfolio_status == RISK_INVALID:
        return GovernorPipelineResult(
            capital_decision=capital_decision, portfolio_decision=portfolio_decision, budget_decision=None,
            admission_decision=None, adaptive_decision=None, final_status=PIPELINE_BLOCKED,
            final_quantity=0, blocking_stage=STAGE_PORTFOLIO, decision_trace=DecisionTrace(tuple(steps)),
        )

    # -- Stage 3: Risk Budget (D.3) --------------------------------------
    budget_snapshot = calculate_available_risk_budget(
        context.capital_snapshot, portfolio_snapshot, context.risk_policy, capital_decision.status, context.clock,
    )
    budget_trade_decision = assess_trade_risk_budget(
        portfolio_snapshot, context.requested_risk, budget_snapshot, context.risk_policy, context.clock,
    )
    if context.requested_risk < 0:
        # Hedge/de-risking trade -- assess_trade_risk_budget() above already
        # approves this unconditionally (D.3's own established precedent:
        # a risk-reducing trade can only ever improve the budget, mirroring
        # D.1's identical "risk-reducing trades are never blocked" rule).
        # calculate_safe_position_size() explicitly rejects negative
        # requested_risk by design (its own docstring: hedges belong to
        # assess_trade_risk_budget, not sizing) -- calling it here would
        # raise IllegalRiskBudgetInputError and crash the whole pipeline on
        # every hedge trade. A hedge is never reduced, so the full
        # desired_quantity is the correct sizing outcome by construction.
        sizing = PositionSizeRecommendation(
            recommended_quantity=context.desired_quantity, maximum_quantity=context.desired_quantity,
            reduction_required=False,
            reason="Hedge/de-risking trade (requested_risk < 0) -- sizing reduction not applicable; "
                   "full desired_quantity approved.",
        )
    else:
        sizing = calculate_safe_position_size(context.desired_quantity, context.requested_risk, budget_snapshot)
    budget_decision = BudgetGovernorOutcome(snapshot=budget_snapshot, decision=budget_trade_decision, sizing=sizing)
    steps.append(TraceStep(
        STAGE_BUDGET, budget_trade_decision.status,
        f"{budget_trade_decision.explanation} | sizing: {sizing.reason}",
    ))

    if not budget_trade_decision.allowed:
        return GovernorPipelineResult(
            capital_decision=capital_decision, portfolio_decision=portfolio_decision, budget_decision=budget_decision,
            admission_decision=None, adaptive_decision=None, final_status=PIPELINE_BLOCKED,
            final_quantity=0, blocking_stage=STAGE_BUDGET, decision_trace=DecisionTrace(tuple(steps)),
        )

    # -- Stage 4: Trade Admission (D.4) ----------------------------------
    admission_decision = recommend_risk_action(
        context.position_snapshot, capital_decision.status, portfolio_status,
        context.position_health_thresholds, context.clock,
    )
    steps.append(TraceStep(STAGE_ADMISSION, admission_decision.action, admission_decision.explanation))

    if admission_decision.action == ACTION_BLOCK_NEW_RISK:
        return GovernorPipelineResult(
            capital_decision=capital_decision, portfolio_decision=portfolio_decision, budget_decision=budget_decision,
            admission_decision=admission_decision, adaptive_decision=None, final_status=PIPELINE_BLOCKED,
            final_quantity=0, blocking_stage=STAGE_ADMISSION, decision_trace=DecisionTrace(tuple(steps)),
        )

    # -- Stage 5: Adaptive Memory (D.5) -- sizing adjustment only --------
    experience = summarize_strategy_experience(context.memory_entries, context.strategy_type, context.market_regime)
    adaptive_recommendation = recommend_adaptive_risk_adjustment(experience)
    adaptive_decision = evaluate_adaptive_governor_decision(
        admission_decision.action, sizing.recommended_quantity, sizing.maximum_quantity, adaptive_recommendation,
    )
    steps.append(TraceStep(STAGE_ADAPTIVE, adaptive_decision.adaptive_recommendation, adaptive_decision.reason))

    return GovernorPipelineResult(
        capital_decision=capital_decision, portfolio_decision=portfolio_decision, budget_decision=budget_decision,
        admission_decision=admission_decision, adaptive_decision=adaptive_decision, final_status=PIPELINE_APPROVED,
        final_quantity=adaptive_decision.final_suggested_size, blocking_stage=None,
        decision_trace=DecisionTrace(tuple(steps)),
    )


# --------------------------------------------------------------------- #
# Part 7 -- Explainability
# --------------------------------------------------------------------- #

def explain_pipeline_result(result: GovernorPipelineResult) -> str:
    """Explains which governor stopped execution, or why the final
    quantity differs from D.3's own recommended_quantity -- using only
    text already produced by the governors themselves (decision_trace
    entries), never new reasoning authored here."""
    if result.blocking_stage is not None:
        blocking_step = next(s for s in result.decision_trace.steps if s.stage == result.blocking_stage)
        return f"BLOCKED at {result.blocking_stage}: {blocking_step.detail}"

    admission_step = next(s for s in result.decision_trace.steps if s.stage == STAGE_ADMISSION)
    adaptive_step = next(s for s in result.decision_trace.steps if s.stage == STAGE_ADAPTIVE)
    base_quantity = result.budget_decision.sizing.recommended_quantity

    if result.final_quantity == base_quantity:
        return (
            f"APPROVED at {result.final_quantity} lots (D.3 recommended size, unchanged by "
            f"Adaptive Memory). {admission_step.detail}"
        )

    return (
        f"APPROVED at {result.final_quantity} lots, adjusted from D.3's recommended "
        f"{base_quantity} lots. {adaptive_step.detail}"
    )
