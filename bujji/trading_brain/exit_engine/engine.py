"""Exit Engine — Exit Engine v1 sprint.

`evaluate()` is a plain, pure function: a `PortfolioValuation`, the
current open positions, and a config in; one `ExitDecision` out. It
performs no I/O, fetches no market data, computes no MTM itself, and
never talks to a broker -- it is a CONSUMER of exactly the inputs the
sprint's own Design Principles name (`PortfolioValuation`, the position
ledger, and -- for a future strategy-aware rule -- strategy context),
nothing more. This is what makes it replay-safe by construction: same
inputs, same clock, same decision, every time (verified by a dedicated
determinism test).

Rule evaluation order is fixed and disclosed
(`taxonomy.RULE_PRIORITY`): Maximum Loss > Profit Target > Hard Time
Exit > Strategy Exit. Capital protection is checked first on purpose --
if a max-loss and a profit-target threshold were ever misconfigured to
both be satisfiable at once (a config error, not a normal state), this
engine reports the loss-protection reason, never the profit one.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Callable, List, Optional

from . import taxonomy
from .config import ExitRuleConfig
from .models import ExitDecision
from ..portfolio_valuation.models import PortfolioValuation

Clock = Callable[[], datetime]


def _real_clock() -> datetime:
    return datetime.now()


def _rule_maximum_loss(valuation: PortfolioValuation, config: ExitRuleConfig) -> bool:
    if config.max_loss is None or valuation.total_pnl is None:
        return False
    return valuation.total_pnl <= config.max_loss


def _rule_profit_target(valuation: PortfolioValuation, config: ExitRuleConfig) -> bool:
    if config.profit_target is None or valuation.total_pnl is None:
        return False
    return valuation.total_pnl >= config.profit_target


def _rule_hard_time_exit(config: ExitRuleConfig, now_ist_time: Optional[str]) -> bool:
    if config.hard_time_exit is None or now_ist_time is None:
        return False
    return now_ist_time >= config.hard_time_exit


def _rule_strategy_exit(config: ExitRuleConfig) -> bool:
    """Placeholder rule, explicitly documented as such (Part 3's own
    instruction: "If none exists: implement a simple placeholder rule.
    Document clearly. Do NOT invent advanced logic."). No
    strategy-specific exit logic exists anywhere in Trading Brain today
    (confirmed during the prior EQ1 qualification sprint's audit, its
    own Gap G5) -- this function structurally never triggers. It exists
    so the RULE_PRIORITY ordering and the ExitDecision.reason vocabulary
    already have a real, named slot for a future strategy-aware rule to
    fill in, without this sprint fabricating logic that doesn't exist."""
    return False  # Always False. Not a bug -- see docstring.


_RULE_FUNCS = {
    taxonomy.EXIT_REASON_MAXIMUM_LOSS: lambda v, c, t: _rule_maximum_loss(v, c),
    taxonomy.EXIT_REASON_PROFIT_TARGET: lambda v, c, t: _rule_profit_target(v, c),
    taxonomy.EXIT_REASON_HARD_TIME_EXIT: lambda v, c, t: _rule_hard_time_exit(c, t),
    taxonomy.EXIT_REASON_STRATEGY_EXIT: lambda v, c, t: _rule_strategy_exit(c),
}


def evaluate(
    valuation: PortfolioValuation,
    positions: List[dict],
    config: ExitRuleConfig,
    *,
    now_ist_time: Optional[str] = None,
    clock: Clock = _real_clock,
) -> ExitDecision:
    """Evaluate all v1 exit rules, in the fixed priority order, against
    one already-computed PortfolioValuation and the current open
    positions. Never fetches anything itself.

    `now_ist_time`: "HH:MM" real IST wall-clock string, supplied by the
    caller -- this module never reads the clock for rule evaluation
    (only for the decision's own `timestamp`/id), matching the
    determinism discipline used throughout this codebase.
    """
    timestamp = clock().isoformat()
    symbols = tuple(p["symbol"] for p in positions)

    if not positions:
        # Nothing to exit -- a real, honest, high-confidence conclusion,
        # not a skipped evaluation.
        return _decision(False, taxonomy.EXIT_REASON_NO_EXIT, taxonomy.CONFIDENCE_HIGH,
                         (), None, valuation, "No open positions to evaluate.", timestamp)

    for reason in taxonomy.RULE_PRIORITY:
        triggered = _RULE_FUNCS[reason](valuation, config, now_ist_time)
        if triggered:
            trace = f"Rule {reason} triggered against PortfolioValuation {valuation.valuation_id} (total_pnl={valuation.total_pnl})."
            return _decision(True, reason, taxonomy.CONFIDENCE_HIGH, symbols, reason,
                             valuation, trace, timestamp)

    # No rule triggered. Confidence reflects whether we actually KNEW
    # enough to be sure -- missing price data means the MTM-based rules
    # were structurally unable to evaluate, so "no exit" here is an
    # honest but LOWER-confidence conclusion, never silently upgraded.
    if valuation.total_pnl is None:
        confidence = taxonomy.CONFIDENCE_LOW
        trace = "No rule triggered, but total_pnl is unknown (missing observed price on at least one leg) -- MTM-based rules could not evaluate."
    else:
        confidence = taxonomy.CONFIDENCE_HIGH
        trace = f"No rule triggered against PortfolioValuation {valuation.valuation_id} (total_pnl={valuation.total_pnl})."

    return _decision(False, taxonomy.EXIT_REASON_NO_EXIT, confidence, (), None,
                     valuation, trace, timestamp)


def _decision(should_exit, reason, confidence, symbols, triggering_rule, valuation, trace, timestamp) -> ExitDecision:
    seed = "|".join([str(should_exit), reason, str(symbols), valuation.valuation_id, timestamp])
    decision_id = "EXIT-" + hashlib.md5(seed.encode()).hexdigest()[:16]
    return ExitDecision(
        decision_id=decision_id,
        should_exit=should_exit,
        reason=reason,
        confidence=confidence,
        affected_positions=symbols,
        triggering_rule=triggering_rule,
        portfolio_valuation_id=valuation.valuation_id,
        decision_trace=trace,
        timestamp=timestamp,
        version=taxonomy.EXIT_ENGINE_VERSION,
    )
