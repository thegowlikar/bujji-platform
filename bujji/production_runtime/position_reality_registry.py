"""Position Reality Registry -- BUJJI Options OS v3, Gate F.3 Part 1.

PURPOSE: PaperBroker's own `_positions` dict (read via
`get_open_positions()`) is keyed by SYMBOL -- a single 4-leg Iron
Condor produces 4 separate flat entries, with no concept of "these
four legs are one strategy." This registry is a RUNTIME READ MODEL
that groups PaperBroker's own, unmodified positions back into the
logical position groups the Strategy Engine actually constructed,
using exactly the symbol set Gate F.1's own entry cycle already knows
about at fill time. It never stores its own copy of quantity/price --
those numbers are always re-read from PaperBroker on demand.

STEP 1 FINDING THIS DESIGN RESTS ON: PaperBroker (`bujji/broker/
paper.py`) remains the sole EXECUTION truth -- `get_open_positions()`/
`get_realized_pnl()` are the only source of quantity/avg_price/
realized-PnL, never re-derived or duplicated here. This registry only
adds the ONE piece PaperBroker genuinely cannot know: which symbols
belong to which multi-leg strategy, and what that strategy's own
Gate B defined-risk figure was at entry (`initial_risk` -- captured
once, at registration time, from the same `requested_risk` value
Gate D.1-D.6 already approved this trade against in Gate F.1's entry
cycle; there remains no durable, live-recomputable Gate B source, per
the identical finding already established across E.1/E.2/E.3 -- this
registry does not invent one).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Dict, List, Optional, Tuple

Clock = Callable[[], datetime]


class DuplicatePositionGroupError(Exception):
    """Raised when register_entry() is called twice for the same
    position_group_id -- registration is a one-time event per entry,
    never silently overwritten."""


class UnknownPositionGroupError(Exception):
    """Raised when a caller references a position_group_id this
    registry never registered."""


@dataclass(frozen=True)
class PositionGroupReality:
    """A read-model snapshot -- assembled fresh on every call from
    PaperBroker's own live state, never cached/mutated in place."""
    position_group_id: str
    strategy_family: str
    symbols: Tuple[str, ...]
    initial_risk: float
    entry_timestamp: str
    is_open: bool          # True while at least one leg symbol still has an open PaperBroker position.


class PositionRealityRegistry:
    """Runtime read model only. PaperBroker remains execution truth --
    this class never calls place_order/cancel_order/any mutating
    broker method, and never maintains its own quantity/price ledger."""

    def __init__(self, broker) -> None:
        self._broker = broker
        self._groups: Dict[str, dict] = {}
        self._order = []

    def register_entry(
        self, position_group_id: str, strategy_family: str, symbols: List[str],
        initial_risk: float, clock: Clock, contracts: Optional[Dict[str, object]] = None,
    ) -> None:
        """`contracts`: optional {symbol: OptionContract}, captured
        once at entry -- Gate F.4's own finding: PaperBroker's own
        position ledger stores only a flat symbol string, never the
        structured contract (strike/option_type/expiry/lot_size) a
        reducing/hedging order needs to reference. This is the SAME
        category of entry-time-only metadata as strategy_family/
        initial_risk already stored here (a fact about the group
        PaperBroker itself never tracks), not a new duplicate
        position store -- quantity/price/PnL are still always re-read
        from PaperBroker on demand, never cached here."""
        if position_group_id in self._groups:
            raise DuplicatePositionGroupError(
                f"position_group_id {position_group_id!r} already registered -- registration is one-time"
            )
        self._groups[position_group_id] = {
            "strategy_family": strategy_family, "symbols": tuple(symbols),
            "initial_risk": initial_risk, "entry_timestamp": clock().isoformat(),
            "contracts": dict(contracts) if contracts else {},
        }
        self._order.append(position_group_id)

    def contract_for_symbol(self, position_group_id: str, symbol: str) -> Optional[object]:
        if position_group_id not in self._groups:
            raise UnknownPositionGroupError(f"position_group_id {position_group_id!r} was never registered")
        return self._groups[position_group_id]["contracts"].get(symbol)

    async def _open_symbols(self) -> set:
        positions = await self._broker.get_open_positions()
        return {p["symbol"] for p in positions}

    async def get_group_reality(self, position_group_id: str) -> PositionGroupReality:
        if position_group_id not in self._groups:
            raise UnknownPositionGroupError(f"position_group_id {position_group_id!r} was never registered")
        meta = self._groups[position_group_id]
        open_symbols = await self._open_symbols()
        is_open = bool(open_symbols & set(meta["symbols"]))
        return PositionGroupReality(
            position_group_id=position_group_id, strategy_family=meta["strategy_family"],
            symbols=meta["symbols"], initial_risk=meta["initial_risk"],
            entry_timestamp=meta["entry_timestamp"], is_open=is_open,
        )

    async def open_group_ids(self) -> Tuple[str, ...]:
        """Groups registered here that still have at least one open
        PaperBroker leg -- a group whose every leg has fully closed is
        excluded, matching D.2's own established 'only active groups
        count' convention."""
        open_symbols = await self._open_symbols()
        return tuple(
            pg_id for pg_id in self._order
            if open_symbols & set(self._groups[pg_id]["symbols"])
        )

    async def positions_for_group(self, position_group_id: str) -> List[dict]:
        """PaperBroker's own, unmodified position dicts for exactly
        this group's registered symbols -- filters, never re-derives."""
        if position_group_id not in self._groups:
            raise UnknownPositionGroupError(f"position_group_id {position_group_id!r} was never registered")
        symbols = set(self._groups[position_group_id]["symbols"])
        positions = await self._broker.get_open_positions()
        return [p for p in positions if p["symbol"] in symbols]

    def realized_pnl_by_symbol(self, position_group_id: str) -> Dict[str, float]:
        if position_group_id not in self._groups:
            raise UnknownPositionGroupError(f"position_group_id {position_group_id!r} was never registered")
        symbols = self._groups[position_group_id]["symbols"]
        return {symbol: self._broker.get_realized_pnl(symbol) for symbol in symbols}

    def all_group_ids(self) -> Tuple[str, ...]:
        return tuple(self._order)

    def initial_risk(self, position_group_id: str) -> float:
        if position_group_id not in self._groups:
            raise UnknownPositionGroupError(f"position_group_id {position_group_id!r} was never registered")
        return self._groups[position_group_id]["initial_risk"]
