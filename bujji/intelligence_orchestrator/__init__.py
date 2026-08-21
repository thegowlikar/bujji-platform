"""Intelligence Decision Orchestrator — BUJJI Options OS v3, Trading
Brain Intelligence Upgrade, Phase 4.

A thin conductor over four already-real engines: `market_thesis.
assess`, `trading_brain.strategy_selector.select`, `trading_brain.
strategy_evaluator.rank`, and this package's own small TRADE/NO_TRADE
synthesis. See engine.py for the full design rationale, including the
one open seam (strategy_id <-> MSI family cross-referencing) this
package discloses rather than papers over.
"""
from .engine import assess_market, evaluate_strategies, generate_thesis, orchestrate, select_strategy
from .models import DecisionContext, DecisionOutcome, DecisionTrace
from . import taxonomy

__all__ = [
    "assess_market", "generate_thesis", "evaluate_strategies", "select_strategy", "orchestrate",
    "DecisionContext", "DecisionOutcome", "DecisionTrace", "taxonomy",
]
