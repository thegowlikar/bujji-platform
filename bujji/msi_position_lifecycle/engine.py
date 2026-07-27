"""Position Lifecycle Intelligence engine — Series 96.

Consumes ONLY: the ORIGINAL `TradeThesisAssessment` a position was
admitted under (Series 92), TODAY's freshly re-derived
`TradeThesisAssessment` (same engine, run again on today's real MSI),
today's real `PortfolioConstructionAssessment` (Series 91, for real
portfolio-level Greeks), and the position's own `construction_type`
(Series 95). Never re-derives MSI, thesis, expression, selection, or
portfolio-construction reasoning -- only compares their already-real
outputs day over day. Places no orders, performs no execution.
"""
from __future__ import annotations

import hashlib
from typing import Optional, Tuple

from bujji.msi_portfolio_construction.models import PortfolioConstructionAssessment
from bujji.msi_trade_thesis.models import TradeThesisAssessment

from . import config as _config
from . import taxonomy
from .models import (
    AdjustmentPolicy, EmergencyPolicy, Explanation, ExpiryPolicy, LossPolicy,
    PositionLifecycleAssessment, ProfitPolicy, ThesisInvalidation,
)


def _thesis_compatible(entry_thesis_type: str, current_thesis_type: str) -> bool:
    if entry_thesis_type == current_thesis_type:
        return True
    if entry_thesis_type == "EVENT_RISK":
        # Genuine, resolvable uncertainty -- resolving into ANY real thesis
        # (anything but falling back to NO_TRADE) is a healthy development,
        # never treated as an invalidation.
        return current_thesis_type != "NO_TRADE"
    compatible_set = taxonomy.COMPATIBLE_THESIS_TRANSITIONS.get(entry_thesis_type, (entry_thesis_type,))
    return current_thesis_type in compatible_set


def _dte_remaining(current_date: str, position_close_date: Optional[str]) -> Optional[int]:
    if position_close_date is None:
        return None
    from datetime import datetime
    return (datetime.fromisoformat(position_close_date).date() - datetime.fromisoformat(current_date).date()).days


def _adjustment_policy(construction_type: str, thesis_compatible: bool,
                        portfolio: Optional[PortfolioConstructionAssessment],
                        entry_thesis: TradeThesisAssessment, current_thesis: TradeThesisAssessment) -> Tuple[AdjustmentPolicy, list]:
    trigger_defs = taxonomy.CONSTRUCTION_TYPE_TRIGGERS.get(construction_type, ())
    triggers = tuple(label for label, _ in trigger_defs)
    actionable = tuple(label for label, ok in trigger_defs if ok)
    monitoring_only = tuple(label for label, ok in trigger_defs if not ok)

    fired = []
    if not thesis_compatible and taxonomy.TRIGGER_THESIS_INVALIDATION in actionable:
        fired.append(taxonomy.TRIGGER_THESIS_INVALIDATION)
    if portfolio is not None:
        if taxonomy.TRIGGER_DELTA_DRIFT in actionable and portfolio.portfolio_delta_after is not None \
                and abs(portfolio.portfolio_delta_after) > _config.WATCH_ABS_PORTFOLIO_DELTA:
            fired.append(taxonomy.TRIGGER_DELTA_DRIFT)
        if taxonomy.TRIGGER_VOLATILITY_EXPANSION in actionable and portfolio.portfolio_vega_after is not None \
                and abs(portfolio.portfolio_vega_after) > _config.WATCH_ABS_PORTFOLIO_VEGA:
            fired.append(taxonomy.TRIGGER_VOLATILITY_EXPANSION)
    if taxonomy.TRIGGER_IV_CONTRACTION in actionable:
        if entry_thesis.volatility_expectation == "EXPANSION" and current_thesis.volatility_expectation in ("STABLE", "COMPRESSION"):
            fired.append(taxonomy.TRIGGER_IV_CONTRACTION)

    reasoning = [f"{construction_type} declares triggers {triggers} "
                 f"({len(monitoring_only)} disclosed as monitoring-only, no real data source at this layer)"]
    if fired:
        reasoning.append(f"fired today: {tuple(fired)}")

    policy = AdjustmentPolicy(
        triggers=triggers, actionable_triggers_today=actionable, monitoring_only_triggers=monitoring_only,
        fired=tuple(fired), reasoning=tuple(reasoning),
    )
    return policy, fired


def _profit_policy(construction_type: str, near_expiry: bool, thesis_compatible: bool) -> ProfitPolicy:
    if construction_type in _config.EARLY_HARVEST_PREFERRED_CONSTRUCTION_TYPES:
        rule = "HARVEST_NEAR_EXPIRY_IF_THESIS_HOLDS"
        reasoning = (f"{construction_type} is a credit-collecting/defined-risk-decay shape -- "
                     "prefers closing before the final DTE to avoid pin/assignment risk once most "
                     "available theta has been captured, rather than holding to expiration",)
    else:
        rule = "HOLD_WHILE_THESIS_HOLDS"
        reasoning = (f"{construction_type} has no natural early-decay edge to harvest -- "
                     "profit is realized by the thesis playing out, not by time passing",)
    if near_expiry and thesis_compatible:
        reasoning = reasoning + ("today is within the near-expiry window and the thesis still holds -- "
                                  "this is exactly the harvest condition, not merely a policy description",)
    return ProfitPolicy(harvest_rule=rule, reasoning=reasoning)


def _loss_policy(construction_type: str, thesis_compatible: bool) -> LossPolicy:
    reasoning = [
        f"exit is warranted once the thesis this {construction_type} was built to express is genuinely invalidated "
        f"(Deliverable 5's real, computed thesis-compatibility check), not based on any P&L figure this layer "
        f"cannot compute without real chain/premium data",
    ]
    if not thesis_compatible:
        reasoning.append("thesis is invalidated today -- loss acceptance is warranted regardless of unrealized P&L")
    return LossPolicy(accept_loss_rule="EXIT_ON_THESIS_BROKEN", reasoning=tuple(reasoning))


def _expiry_policy(dte_remaining: Optional[int]) -> ExpiryPolicy:
    if dte_remaining is None:
        return ExpiryPolicy(rule="UNKNOWN", dte_remaining=None, reasoning=("no expiry information available",))
    if dte_remaining <= _config.NEAR_EXPIRY_DTE_THRESHOLD:
        return ExpiryPolicy(
            rule="CLOSE_BEFORE_FINAL_DTE", dte_remaining=dte_remaining,
            reasoning=(f"DTE={dte_remaining} <= configured near-expiry threshold "
                       f"({_config.NEAR_EXPIRY_DTE_THRESHOLD}) -- prefer closing rather than holding into expiry day",),
        )
    return ExpiryPolicy(
        rule="HOLD_UNTIL_NEAR_EXPIRY", dte_remaining=dte_remaining,
        reasoning=(f"DTE={dte_remaining}, still outside the near-expiry window",),
    )


def _emergency_policy(portfolio: Optional[PortfolioConstructionAssessment]) -> Tuple[EmergencyPolicy, bool]:
    if portfolio is None:
        return EmergencyPolicy(trigger="NONE", reasoning=("no portfolio assessment available today",)), False
    hard_breach = False
    reasons = []
    if portfolio.portfolio_delta_after is not None and abs(portfolio.portfolio_delta_after) > _config.HARD_MAX_ABS_PORTFOLIO_DELTA:
        hard_breach = True
        reasons.append(f"|portfolio_delta_after|={abs(portfolio.portfolio_delta_after):.2f} exceeds the HARD "
                       f"{_config.HARD_MAX_ABS_PORTFOLIO_DELTA} cap Series 91 itself would reject on")
    if portfolio.portfolio_vega_after is not None and abs(portfolio.portfolio_vega_after) > _config.HARD_MAX_ABS_PORTFOLIO_VEGA:
        hard_breach = True
        reasons.append(f"|portfolio_vega_after|={abs(portfolio.portfolio_vega_after):.2f} exceeds the HARD "
                       f"{_config.HARD_MAX_ABS_PORTFOLIO_VEGA} cap Series 91 itself would reject on")
    if hard_breach:
        return EmergencyPolicy(trigger="PORTFOLIO_EXPOSURE_HARD_BREACH", reasoning=tuple(reasons)), True
    return EmergencyPolicy(trigger="NONE", reasoning=("no hard exposure breach today",)), False


def _assessment_id(family: Optional[str], construction_type: str, state: str, schema_version: str) -> str:
    content = "|".join([family or "", construction_type, state, schema_version])
    return hashlib.md5(content.encode("utf-8")).hexdigest()


def assess_position_lifecycle(
    entry_thesis: TradeThesisAssessment, current_thesis: TradeThesisAssessment,
    strategy_family: Optional[str], construction_type: str,
    portfolio: Optional[PortfolioConstructionAssessment],
    entry_date: str, current_date: str, position_close_date: Optional[str],
    *, timestamp: str,
) -> PositionLifecycleAssessment:
    """Produces exactly one PositionLifecycleAssessment for one still-open
    (or just-closing) position, on one day. No optimisation, no
    execution logic, no order placement anywhere."""
    schema_version = taxonomy.MSI_POSITION_LIFECYCLE_VERSION
    supporting_ids = tuple(x for x in (entry_thesis.assessment_id, current_thesis.assessment_id,
                                       portfolio.assessment_id if portfolio else None) if x)

    dte_remaining = _dte_remaining(current_date, position_close_date)

    if dte_remaining is not None and dte_remaining < 0:
        state = taxonomy.STATE_CLOSED
    elif current_date == entry_date:
        state = taxonomy.STATE_NEWLY_OPENED
    else:
        state = None  # determined below.

    thesis_compatible = _thesis_compatible(entry_thesis.thesis_type, current_thesis.thesis_type)
    invalidation = ThesisInvalidation(
        entry_thesis_type=entry_thesis.thesis_type, current_thesis_type=current_thesis.thesis_type,
        compatible=thesis_compatible,
        reasoning=(f"entry thesis={entry_thesis.thesis_type}, current thesis={current_thesis.thesis_type}, "
                   f"declared-compatible={thesis_compatible} (per config.py's own COMPATIBLE_THESIS_TRANSITIONS table)",),
    )

    adjustment_policy, fired_triggers = _adjustment_policy(construction_type, thesis_compatible, portfolio, entry_thesis, current_thesis)
    emergency_policy, hard_breach = _emergency_policy(portfolio)
    expiry_policy = _expiry_policy(dte_remaining)
    near_expiry = dte_remaining is not None and dte_remaining <= _config.NEAR_EXPIRY_DTE_THRESHOLD
    profit_policy = _profit_policy(construction_type, near_expiry, thesis_compatible)
    loss_policy = _loss_policy(construction_type, thesis_compatible)

    if state is None:
        if not thesis_compatible:
            state = taxonomy.STATE_EXIT_CANDIDATE if hard_breach else taxonomy.STATE_THESIS_BROKEN
        elif hard_breach:
            state = taxonomy.STATE_EXIT_CANDIDATE
        elif near_expiry and construction_type in _config.EARLY_HARVEST_PREFERRED_CONSTRUCTION_TYPES:
            state = taxonomy.STATE_PROFIT_HARVEST
        elif fired_triggers:
            state = taxonomy.STATE_ADJUSTMENT_CANDIDATE
        elif taxonomy.conviction_rank(current_thesis.conviction) < taxonomy.conviction_rank(entry_thesis.conviction):
            state = taxonomy.STATE_AT_RISK
        elif taxonomy.conviction_rank(current_thesis.conviction) > taxonomy.conviction_rank(entry_thesis.conviction):
            state = taxonomy.STATE_IMPROVING
        else:
            state = taxonomy.STATE_HEALTHY

    if state in (taxonomy.STATE_EXIT_CANDIDATE, taxonomy.STATE_THESIS_BROKEN, taxonomy.STATE_CLOSED):
        expected_lifetime = taxonomy.LIFETIME_IMMEDIATE_EXIT if state != taxonomy.STATE_CLOSED else taxonomy.LIFETIME_NONE
    elif state == taxonomy.STATE_PROFIT_HARVEST:
        expected_lifetime = taxonomy.LIFETIME_IMMEDIATE_EXIT
    else:
        expected_lifetime = taxonomy.LIFETIME_UNTIL_THESIS_INVALIDATED if construction_type == "SINGLE_LEG" else taxonomy.LIFETIME_UNTIL_EXPIRY

    monitoring = tuple(sorted(set(adjustment_policy.triggers) | {"thesis_type", "conviction", "dte_remaining"}))

    aid = _assessment_id(strategy_family, construction_type, state, schema_version)
    explanation = Explanation(
        assessment_id=aid,
        why_this_adjustment_policy=adjustment_policy.reasoning,
        why_this_profit_policy=profit_policy.reasoning,
        why_this_invalidation_rule=invalidation.reasoning,
        why_this_emergency_policy=emergency_policy.reasoning,
        schema_version=schema_version,
    )

    return PositionLifecycleAssessment(
        lifecycle_id=aid, timestamp=timestamp, strategy_family=strategy_family, construction_type=construction_type,
        position_state=state, expected_lifetime=expected_lifetime, monitoring_requirements=monitoring,
        adjustment_policy=adjustment_policy, profit_policy=profit_policy, loss_policy=loss_policy,
        expiry_policy=expiry_policy, emergency_policy=emergency_policy, thesis_invalidation=invalidation,
        supporting_assessment_ids=supporting_ids, explanation=explanation,
        provenance="bujji.msi_position_lifecycle.engine.assess_position_lifecycle", schema_version=schema_version,
    )
