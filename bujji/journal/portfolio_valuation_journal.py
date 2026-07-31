"""Portfolio Valuation Journal — Live Shadow Real-Time Paper Execution
sprint (Part 6). Append-only JSONL, own schema, own storage path --
matches the existing bujji/journal/*_journal.py convention exactly
(e.g. StrategySelectorJournal).

Two responsibilities, kept separate:
  - record_valuation(): append every PortfolioValuation snapshot,
    verbatim, as it happens -- the raw tick-by-tick record.
  - TradeLifecycleTracker: a small, explicit, per-symbol running
    tracker for "maximum profit" / "maximum drawdown" over a position's
    life (the sprint's own Part 6 requirement) -- this is real,
    necessary state (a peak/trough cannot be computed from a single
    snapshot), kept as a separate, narrow object rather than folded
    into the journal itself, so the journal stays a plain, stateless
    append-only writer like every other journal in this codebase.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Union

from ..trading_brain.portfolio_valuation.models import PortfolioValuation

SCHEMA_VERSION = "1.0.0"


class PortfolioValuationJournal:
    def __init__(self, path: Union[str, Path]) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def record_valuation(self, valuation: PortfolioValuation) -> None:
        payload = {"schema_version": SCHEMA_VERSION, "type": "VALUATION", "valuation": asdict(valuation)}
        with open(self._path, "a") as fh:
            fh.write(json.dumps(payload) + "\n")

    def record_exit(
        self, *, symbol: str, entry_ltp: float, exit_ltp: float, running_mtm: float,
        max_profit: float, max_drawdown: float, exit_reason: str,
        entry_timestamp: Optional[str], exit_timestamp: str,
        final_mtm: Optional[float] = None, exit_decision_timestamp: Optional[str] = None,
    ) -> None:
        # Exit Engine v1 sprint (Part 7): final_mtm / exit_decision_timestamp
        # added additively (both optional, backward compatible with every
        # existing caller) -- final_mtm is the realized P&L for this
        # closed trade specifically (distinct from running_mtm, which is
        # the last live/unrealized reading before the close);
        # exit_decision_timestamp is when ExitEngine.evaluate() produced
        # the ExitDecision, distinct from exit_timestamp (when the
        # closing fill actually confirmed) -- kept separate since a real
        # fill can lag its own triggering decision.
        payload = {
            "schema_version": SCHEMA_VERSION, "type": "EXIT",
            "symbol": symbol, "entry_ltp": entry_ltp, "exit_ltp": exit_ltp,
            "running_mtm": running_mtm, "max_profit": max_profit, "max_drawdown": max_drawdown,
            "exit_reason": exit_reason, "entry_timestamp": entry_timestamp, "exit_timestamp": exit_timestamp,
            "final_mtm": final_mtm, "exit_decision_timestamp": exit_decision_timestamp,
        }
        with open(self._path, "a") as fh:
            fh.write(json.dumps(payload) + "\n")

    def read_all(self) -> List[dict]:
        if not self._path.exists():
            return []
        out: List[dict] = []
        with open(self._path, "r") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        return out


@dataclass
class _SymbolPeak:
    entry_ltp: float
    entry_timestamp: Optional[str]
    max_profit: float = float("-inf")
    max_drawdown: float = float("inf")   # most negative unrealized_pnl observed.
    last_ltp: Optional[float] = None


class TradeLifecycleTracker:
    """Per-symbol running peak/trough tracker across a sequence of real
    PortfolioValuation snapshots -- explicit, small, in-memory state,
    exactly as much as "maximum profit so far" / "maximum drawdown so
    far" actually requires and no more. Never persists anything itself;
    callers decide when to hand a completed trade's summary to
    PortfolioValuationJournal.record_exit()."""

    def __init__(self) -> None:
        self._by_symbol: Dict[str, _SymbolPeak] = {}

    def observe(self, valuation: PortfolioValuation) -> None:
        for leg in valuation.legs:
            if leg.unrealized_pnl is None:
                continue
            tracker = self._by_symbol.get(leg.symbol)
            if tracker is None:
                tracker = _SymbolPeak(entry_ltp=leg.entry_price, entry_timestamp=leg.entry_timestamp)
                self._by_symbol[leg.symbol] = tracker
            tracker.max_profit = max(tracker.max_profit, leg.unrealized_pnl)
            tracker.max_drawdown = min(tracker.max_drawdown, leg.unrealized_pnl)
            tracker.last_ltp = leg.current_price

    def summary(self, symbol: str) -> Optional[dict]:
        tracker = self._by_symbol.get(symbol)
        if tracker is None:
            return None
        return {
            "symbol": symbol,
            "entry_ltp": tracker.entry_ltp,
            "entry_timestamp": tracker.entry_timestamp,
            "last_ltp": tracker.last_ltp,
            "max_profit": tracker.max_profit,
            "max_drawdown": tracker.max_drawdown,
        }
