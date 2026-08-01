"""Adaptive Risk Memory -- BUJJI Options OS v3, Numeric Risk Governor
Gate D.5, Part 2.

PURPOSE: an append-only record of what actually happened after each
prior admitted trade -- strategy, regime, sizing, and realized
outcome -- so later phases can ask "what has Bujji actually observed
about this situation before?" This is memory, not prediction: nothing
in this file forecasts, scores, or optimizes anything.

NOT A DUPLICATE -- VERIFIED BEFORE WRITING ANYTHING: no existing
"memory"/"knowledge graph"/"trade memory"/"strategy journal" module
exists in this codebase (searched exhaustively). The closest relative
is bujji/msi_performance_analytics/ (TradeAnalytics/EdgeValidationReport,
MetricEstimate pairing every statistic with its own sample_size and a
ReliabilityNote) -- a genuinely different domain (Shadow Trade replay
analytics, not live risk-governor memory) and a different data shape,
never imported here, but its "never present a number without its
uncertainty" discipline is deliberately carried forward into Part 4's
StrategyExperience.confidence field. The append-only store shape
itself mirrors the established convention already used by
TradeConstructionJournal (bujji/msi_trade_construction/journal.py) and
Gate C.5's MarginCalibrationStore -- immutable entries, a plain
in-memory list, no update/delete method anywhere.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Optional, Tuple

OUTCOME_WIN = "WIN"
OUTCOME_LOSS = "LOSS"
OUTCOME_BREAKEVEN = "BREAKEVEN"
OUTCOME_UNKNOWN = "UNKNOWN"
_VALID_OUTCOMES = (OUTCOME_WIN, OUTCOME_LOSS, OUTCOME_BREAKEVEN, OUTCOME_UNKNOWN)

CONFIDENCE_NONE = "NONE"
CONFIDENCE_LOW = "LOW"
CONFIDENCE_MODERATE = "MODERATE"
CONFIDENCE_HIGH = "HIGH"

# Sample-size bands for StrategyExperience.confidence -- deterministic,
# documented, no hidden scoring. Deliberately conservative: this is a
# NEW subsystem with no accumulated real history yet, so the bar for
# "HIGH" confidence is set high rather than flattering a small sample.
CONFIDENCE_LOW_MIN_SAMPLES = 5
CONFIDENCE_MODERATE_MIN_SAMPLES = 15
CONFIDENCE_HIGH_MIN_SAMPLES = 30


class IllegalRiskMemoryEntryError(Exception):
    """Raised on a structurally impossible entry -- never silently
    normalized into a plausible-looking record."""


class DuplicateRiskMemoryEntryError(Exception):
    """Raised when an entry_id already exists -- risk memory is
    immutable and append-only, exactly like every other journal-style
    store in this codebase."""


@dataclass(frozen=True)
class RiskMemoryEntry:
    entry_id: str
    timestamp: datetime

    strategy_type: str
    market_regime: str          # normalized trend regime, see market_regime_adapter.py
    volatility_regime: str      # normalized volatility regime, see market_regime_adapter.py

    capital_state: Optional[str]     # e.g. D.1's SAFETY_* classification at the time
    portfolio_state: Optional[str]    # e.g. D.2's RISK_* classification at the time

    recommended_size: Optional[int]    # what D.3/D.5 suggested
    actual_size: Optional[int]          # what was actually taken

    realized_outcome: str                # WIN | LOSS | BREAKEVEN | UNKNOWN
    max_drawdown: Optional[float]         # worst adverse excursion, as a fraction (e.g. 0.03 = 3%)
    max_profit: Optional[float]            # best favourable excursion, as a fraction
    holding_period_days: Optional[int]

    exit_reason: Optional[str]
    notes: Tuple[str, ...] = ()


def build_risk_memory_entry(
    entry_id: str, strategy_type: str, market_regime: str, volatility_regime: str,
    capital_state: Optional[str], portfolio_state: Optional[str],
    recommended_size: Optional[int], actual_size: Optional[int],
    realized_outcome: str, max_drawdown: Optional[float], max_profit: Optional[float],
    holding_period_days: Optional[int], exit_reason: Optional[str],
    notes: Tuple[str, ...] = (), *, clock,
) -> RiskMemoryEntry:
    if realized_outcome not in _VALID_OUTCOMES:
        raise IllegalRiskMemoryEntryError(f"unrecognized realized_outcome {realized_outcome!r}")
    for name, value in (
        ("recommended_size", recommended_size), ("actual_size", actual_size),
        ("holding_period_days", holding_period_days),
    ):
        if value is not None and value < 0:
            raise IllegalRiskMemoryEntryError(f"{name} must be non-negative, got {value!r}")
    for name, value in (("max_drawdown", max_drawdown),):
        if value is not None and value < 0:
            raise IllegalRiskMemoryEntryError(f"{name} must be non-negative, got {value!r}")

    return RiskMemoryEntry(
        entry_id=entry_id, timestamp=clock(),
        strategy_type=strategy_type, market_regime=market_regime, volatility_regime=volatility_regime,
        capital_state=capital_state, portfolio_state=portfolio_state,
        recommended_size=recommended_size, actual_size=actual_size,
        realized_outcome=realized_outcome, max_drawdown=max_drawdown, max_profit=max_profit,
        holding_period_days=holding_period_days, exit_reason=exit_reason, notes=tuple(notes),
    )


class AdaptiveRiskMemory:
    """Append-only. No update/delete/mutate method exists anywhere on
    this class -- history, once recorded, cannot be changed."""

    def __init__(self) -> None:
        self._entries_by_id: Dict[str, RiskMemoryEntry] = {}
        self._order: list = []

    def append_observation(self, entry: RiskMemoryEntry) -> None:
        if entry.entry_id in self._entries_by_id:
            raise DuplicateRiskMemoryEntryError(
                f"entry_id {entry.entry_id!r} already recorded -- risk memory is immutable"
            )
        self._entries_by_id[entry.entry_id] = entry
        self._order.append(entry.entry_id)

    def all_entries(self) -> Tuple[RiskMemoryEntry, ...]:
        """Returned in insertion order -- history ordering is
        preserved, never re-sorted or shuffled."""
        return tuple(self._entries_by_id[entry_id] for entry_id in self._order)

    def lookup_by_strategy(self, strategy_type: str) -> Tuple[RiskMemoryEntry, ...]:
        return tuple(e for e in self.all_entries() if e.strategy_type == strategy_type)

    def lookup_by_regime(self, market_regime: str) -> Tuple[RiskMemoryEntry, ...]:
        return tuple(e for e in self.all_entries() if e.market_regime == market_regime)

    def lookup_by_volatility(self, volatility_regime: str) -> Tuple[RiskMemoryEntry, ...]:
        return tuple(e for e in self.all_entries() if e.volatility_regime == volatility_regime)

    def lookup(
        self, *, strategy_type: Optional[str] = None, market_regime: Optional[str] = None,
        volatility_regime: Optional[str] = None,
    ) -> Tuple[RiskMemoryEntry, ...]:
        results = self.all_entries()
        if strategy_type is not None:
            results = tuple(e for e in results if e.strategy_type == strategy_type)
        if market_regime is not None:
            results = tuple(e for e in results if e.market_regime == market_regime)
        if volatility_regime is not None:
            results = tuple(e for e in results if e.volatility_regime == volatility_regime)
        return results

    def __len__(self) -> int:
        return len(self._order)


# --------------------------------------------------------------------- #
# Part 4 -- Strategy Experience Summary. Pure aggregation over
# AdaptiveRiskMemory's own entries -- no forecasting, no new data
# source, every field traceable back to a stored RiskMemoryEntry.
# --------------------------------------------------------------------- #

@dataclass(frozen=True)
class StrategyExperience:
    strategy_type: str
    market_regime: Optional[str]     # None if this summary spans all regimes for the strategy
    observations: int
    wins: int
    losses: int
    win_rate: Optional[float]              # None if observations == 0
    average_drawdown: Optional[float]
    average_profit: Optional[float]
    worst_drawdown: Optional[float]
    best_profit: Optional[float]
    average_holding_period_days: Optional[float]
    confidence: str                          # NONE | LOW | MODERATE | HIGH
    sample_size: int


def summarize_strategy_experience(
    entries: Tuple[RiskMemoryEntry, ...], strategy_type: str, market_regime: Optional[str] = None,
) -> StrategyExperience:
    """entries must already be pre-filtered to the strategy_type (and,
    if market_regime is given, to that regime too) by the caller via
    AdaptiveRiskMemory.lookup() -- this function only aggregates, it
    never queries the store itself, keeping the "where the data came
    from" question answerable by reading the caller's own lookup call."""
    observations = len(entries)
    wins = sum(1 for e in entries if e.realized_outcome == OUTCOME_WIN)
    losses = sum(1 for e in entries if e.realized_outcome == OUTCOME_LOSS)

    drawdowns = [e.max_drawdown for e in entries if e.max_drawdown is not None]
    profits = [e.max_profit for e in entries if e.max_profit is not None]
    holding_periods = [e.holding_period_days for e in entries if e.holding_period_days is not None]

    win_rate = wins / observations if observations > 0 else None
    average_drawdown = sum(drawdowns) / len(drawdowns) if drawdowns else None
    average_profit = sum(profits) / len(profits) if profits else None
    worst_drawdown = max(drawdowns) if drawdowns else None
    best_profit = max(profits) if profits else None
    average_holding_period_days = (
        sum(holding_periods) / len(holding_periods) if holding_periods else None
    )

    if observations >= CONFIDENCE_HIGH_MIN_SAMPLES:
        confidence = CONFIDENCE_HIGH
    elif observations >= CONFIDENCE_MODERATE_MIN_SAMPLES:
        confidence = CONFIDENCE_MODERATE
    elif observations >= CONFIDENCE_LOW_MIN_SAMPLES:
        confidence = CONFIDENCE_LOW
    else:
        confidence = CONFIDENCE_NONE

    return StrategyExperience(
        strategy_type=strategy_type, market_regime=market_regime, observations=observations,
        wins=wins, losses=losses, win_rate=win_rate, average_drawdown=average_drawdown,
        average_profit=average_profit, worst_drawdown=worst_drawdown, best_profit=best_profit,
        average_holding_period_days=average_holding_period_days, confidence=confidence,
        sample_size=observations,
    )
