"""Exit Policy Engine -- BUJJI Options OS v3, Gate V1.1 Component 5.

PURPOSE: execution policy only -- NOT a second intelligence layer. D.4
(`position_lifecycle_intelligence.recommend_risk_action`) remains the
sole lifecycle-intelligence authority; this module never recomputes a
health assessment or invents a new exit condition of its own. It only
compares already-known numbers (current P&L, initial risk, time)
against CONFIGURED thresholds -- every number is a named field on
`ExitPolicyConfig`, nothing hardcoded, nothing inferred.

D.4's own recommendation is respected: if none of this policy's hard
limits are triggered, the policy defers entirely to D.4's action. A
hard limit (loss exceeded, mandatory EOD time) overrides D.4 the same
way a real desk's hard stop overrides an analyst's opinion -- risk
limits are non-negotiable; D.4's judgment governs everything short of
that line.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from typing import Optional

DECISION_NONE = "NONE"                          # defer to D.4
DECISION_PROFIT_TARGET_REACHED = "PROFIT_TARGET_REACHED"
DECISION_MAX_LOSS_EXCEEDED = "MAX_LOSS_EXCEEDED"
DECISION_MANDATORY_EOD_EXIT = "MANDATORY_EOD_EXIT"

_HARD_DECISIONS = (DECISION_PROFIT_TARGET_REACHED, DECISION_MAX_LOSS_EXCEEDED, DECISION_MANDATORY_EOD_EXIT)


@dataclass(frozen=True)
class ExitPolicyConfig:
    """Every field Optional and defaulting to None (disabled) -- a
    policy dimension that isn't configured is simply never evaluated,
    never defaulted to an invented number."""
    profit_target_fraction: Optional[float] = None    # fraction of initial_risk captured as profit, e.g. 0.5
    max_loss_fraction: Optional[float] = None            # fraction of initial_risk lost, e.g. 1.0
    mandatory_exit_time: Optional[time] = None              # e.g. time(15, 15) IST


class IllegalExitPolicyInputError(Exception):
    """Raised on a structurally impossible input -- never silently
    coerced."""


@dataclass(frozen=True)
class ExitPolicyDecision:
    decision: str            # one of DECISION_* above
    is_hard_limit: bool        # True for PROFIT_TARGET/MAX_LOSS/EOD -- overrides D.4; False (NONE) defers to D.4
    reasoning: str


def evaluate_exit_policy(
    current_pnl: Optional[float], initial_risk: Optional[float], now: datetime, config: ExitPolicyConfig,
) -> ExitPolicyDecision:
    if config.mandatory_exit_time is not None and now.time() >= config.mandatory_exit_time:
        return ExitPolicyDecision(
            DECISION_MANDATORY_EOD_EXIT, True,
            f"current time {now.time().isoformat()} >= configured mandatory_exit_time "
            f"{config.mandatory_exit_time.isoformat()}.",
        )

    if current_pnl is None or initial_risk is None:
        return ExitPolicyDecision(DECISION_NONE, False, "current_pnl/initial_risk unavailable -- deferring to D.4.")
    if initial_risk <= 0:
        raise IllegalExitPolicyInputError(f"initial_risk must be positive, got {initial_risk!r}")

    if config.max_loss_fraction is not None:
        loss_fraction = -current_pnl / initial_risk if current_pnl < 0 else 0.0
        if loss_fraction >= config.max_loss_fraction:
            return ExitPolicyDecision(
                DECISION_MAX_LOSS_EXCEEDED, True,
                f"loss {loss_fraction:.2%} of initial_risk >= configured max_loss_fraction "
                f"{config.max_loss_fraction:.2%}.",
            )

    if config.profit_target_fraction is not None:
        profit_fraction = current_pnl / initial_risk if current_pnl > 0 else 0.0
        if profit_fraction >= config.profit_target_fraction:
            return ExitPolicyDecision(
                DECISION_PROFIT_TARGET_REACHED, True,
                f"profit {profit_fraction:.2%} of initial_risk >= configured profit_target_fraction "
                f"{config.profit_target_fraction:.2%}.",
            )

    return ExitPolicyDecision(DECISION_NONE, False, "no configured hard limit triggered -- deferring to D.4.")
