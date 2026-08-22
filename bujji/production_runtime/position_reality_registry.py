"""Position Reality Registry -- BUJJI Options OS v3, Gate F.3 Part 1.

PURPOSE: PaperBroker's own `_positions` dict is keyed by SYMBOL -- a single
4-leg Iron Condor produces 4 separate flat entries, with no concept of "these
four legs are one strategy." This registry is a RUNTIME READ MODEL that groups
the broker's own, unmodified positions back into the logical position groups
the Strategy Engine actually constructed, using exactly the symbol set Gate
F.1's own entry cycle already knows about at fill time. It never stores its own
copy of quantity/price -- those numbers are always re-read from the broker on
demand.

STEP 1 FINDING THIS DESIGN RESTS ON: the broker remains the sole EXECUTION
truth -- `get_open_positions()`/`get_realized_pnl()` are the only source of
quantity/avg_price/realized-PnL, never re-derived or duplicated here. This
registry only adds the ONE piece the broker genuinely cannot know: which
symbols belong to which multi-leg strategy, and what that strategy's own
Gate B defined-risk figure was at entry (`initial_risk` -- captured once, at
registration time, from the same `requested_risk` value Gate D.1-D.6 already
approved this trade against; there remains no durable, live-recomputable Gate B
source, per the identical finding already established across E.1/E.2/E.3 --
this registry does not invent one).

M3 MIGRATION (2026-08-22) -- WHAT CHANGED AND WHY IT MATTERS
============================================================

Every position read here now goes through `bujji.broker_truth`, and that
changes three real behaviours rather than merely reshaping code.

1. AN UNREADABLE ACCOUNT NO LONGER READS AS A CLOSED GROUP.
   `_open_symbols()` was `{p["symbol"] for p in await broker.get_open_positions()}`.
   A read that returned an empty list for any reason produced an empty set,
   which produced `is_open = False`, which the lifecycle executor turned into
   `mark_closed(pg_id)`. "I could not establish the account" became "this
   position is finished". `PositionGroupReality` now carries the broker's
   answer as `truth_state`, and only a POSITIVE confirmation of flatness --
   `is_confirmed_flat` -- may close a group.

2. A ZERO-QUANTITY ROW IS NO LONGER A HOLDING.
   `_open_symbols()` had no quantity filter at all. PaperBroker pops closed
   symbols so this never surfaced, but FYERS returns netQty=0 rows for
   positions closed intraday, and one of those would have pinned a group open
   for the rest of the session -- forever unresolved, blocking EOD closure.
   That defect was latent behind the paper broker and would have arrived with
   the live read-only adapter.

3. A MALFORMED ROW FAILS CLOSED INSTEAD OF RAISING KeyError.
   `p["symbol"]` on a row without one raised from inside an async read model.
   The boundary answers UNKNOWN instead, which the callers above handle as a
   safety state rather than as a crash.

WHAT DID *NOT* CHANGE, DELIBERATELY: this registry still scopes its answers to
a group's registered symbols. That is not "local state as broker truth" -- it
is the only way to answer "is THIS strategy still on", which is a different
question from "what does the account hold". The unfiltered question has its own
callers (`_broker_reports_flat`, the EOD closure) and they must never be routed
through here.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from bujji.broker_truth import (
    STATE_UNKNOWN, BrokerTruth, BrokerTruthUnknownError, for_broker,
)

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
    """A read-model snapshot -- assembled fresh on every call from the broker's
    own live state, never cached/mutated in place.

    THE THREE QUESTIONS ARE NOT THE SAME QUESTION. `is_open` asks whether the
    broker NAMED one of this group's legs. `is_confirmed_flat` asks whether the
    broker ANSWERED and named none of them. When the account could not be read
    at all, both are False -- which is the honest answer, and the reason this
    type no longer exposes a single bool that would have to guess.
    """
    position_group_id: str
    strategy_family: str
    symbols: Tuple[str, ...]
    initial_risk: float
    entry_timestamp: str
    truth_state: str                    # the broker's answer this was built from
    open_symbols: Tuple[str, ...] = ()  # this group's legs the broker reports held

    @property
    def is_open(self) -> bool:
        """CONFIRMED open: the broker named at least one of this group's legs.

        Never True on an unreadable account -- an unread book is not evidence
        of a position any more than it is evidence of flatness.
        """
        return bool(self.open_symbols)

    @property
    def is_confirmed_flat(self) -> bool:
        """The broker answered, and none of this group's legs is held.

        THE ONLY READING THAT MAY CLOSE A GROUP. Note this is NOT `not
        is_open`: those differ exactly on UNKNOWN, which is the case that
        matters, and conflating them is what marked live groups closed.
        """
        return self.truth_state != STATE_UNKNOWN and not self.open_symbols

    @property
    def truth_is_unknown(self) -> bool:
        return self.truth_state == STATE_UNKNOWN

    @property
    def may_still_be_open(self) -> bool:
        """Open, or not established. What a caller deciding whether to keep
        MANAGING a position should ask -- managing something already flat
        costs a wasted evaluation; abandoning something live costs money.
        """
        return not self.is_confirmed_flat


class PositionRealityRegistry:
    """Runtime read model only. The broker remains execution truth -- this
    class never calls place_order/cancel_order/any mutating broker method, and
    never maintains its own quantity/price ledger."""

    def __init__(self, broker, truth=None) -> None:
        """`truth` is injectable so a caller that already built a correctly
        labelled reader can pass it; otherwise one is derived from the broker.
        It is never optional in effect -- there is no path here that reads the
        broker directly."""
        self._broker = broker
        self._truth = truth if truth is not None else for_broker(broker)
        self._groups: Dict[str, dict] = {}
        self._order: List[str] = []

    def register_entry(
        self, position_group_id: str, strategy_family: str, symbols: List[str],
        initial_risk: float, clock: Clock, contracts: Optional[Dict[str, object]] = None,
    ) -> None:
        """`contracts`: optional {symbol: OptionContract}, captured
        once at entry -- Gate F.4's own finding: the broker's own position
        ledger stores only a flat symbol string, never the structured contract
        (strike/option_type/expiry/lot_size) a reducing/hedging order needs to
        reference. This is the SAME category of entry-time-only metadata as
        strategy_family/initial_risk already stored here (a fact about the
        group the broker itself never tracks), not a new duplicate position
        store -- quantity/price/PnL are still always re-read from the broker on
        demand, never cached here."""
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
        self._require_registered(position_group_id)
        return self._groups[position_group_id]["contracts"].get(symbol)

    # ------------------------------------------------------------------ #
    # Broker reads -- ONE door, and it is the boundary.
    # ------------------------------------------------------------------ #

    async def broker_truth(self) -> BrokerTruth:
        """What the account holds, unfiltered and three-valued.

        Exposed so a caller needing the whole account does not reach past this
        registry to the broker and grow a fifth reading of the same question.
        """
        return await self._truth.read_async()

    async def get_group_reality(self, position_group_id: str) -> PositionGroupReality:
        self._require_registered(position_group_id)
        meta = self._groups[position_group_id]
        truth = await self._truth.read_async()
        held = set(truth.symbols) & set(meta["symbols"])
        return PositionGroupReality(
            position_group_id=position_group_id, strategy_family=meta["strategy_family"],
            symbols=meta["symbols"], initial_risk=meta["initial_risk"],
            entry_timestamp=meta["entry_timestamp"], truth_state=truth.state,
            open_symbols=tuple(sorted(held)),
        )

    async def open_group_ids(self) -> Tuple[str, ...]:
        """Groups that still have at least one leg the broker reports open.

        ON AN UNREADABLE ACCOUNT THIS RETURNS EVERY REGISTERED GROUP, which is
        deliberately the noisy answer. It feeds the EOD "unresolved positions"
        report and the heartbeat's active count; a group cannot be ruled out by
        a read that did not happen, and under-reporting here is precisely how a
        live position stops being anyone's problem. A caller that wants only
        confirmed-open groups asks each one for its `is_open`.
        """
        truth = await self._truth.read_async()
        if truth.is_unknown:
            return tuple(self._order)
        open_symbols = set(truth.symbols)
        return tuple(
            pg_id for pg_id in self._order
            if open_symbols & set(self._groups[pg_id]["symbols"])
        )

    async def positions_for_group(self, position_group_id: str) -> List[dict]:
        """The broker's own, unmodified position rows for exactly this group's
        registered symbols -- filters, never re-derives.

        RAISES on an unreadable account rather than returning []. Every caller
        of this reads an empty list as "nothing to reduce" and stops: the
        executor rejects the action as "no open position exists", the session
        governor builds an empty per-leg quantity map and submits no exit. On a
        hard-limit forced exit that is the worst possible silence. The raise is
        caught by the runner, which falls through to the EOD closure machine --
        itself fully guarded, and it records BROKER_TRUTH_UNKNOWN rather than
        claiming flatness.
        """
        self._require_registered(position_group_id)
        symbols = set(self._groups[position_group_id]["symbols"])
        truth = await self._truth.read_async()
        if truth.is_unknown:
            raise BrokerTruthUnknownError(
                f"positions for {position_group_id}: the broker's position book "
                f"could not be established ({truth.detail}). Returning no legs "
                f"here would read as 'nothing to reduce'.")
        return [dict(leg.raw or leg.as_dict()) for leg in truth.legs
                if leg.symbol in symbols]

    # ------------------------------------------------------------------ #
    # Pure-memory accessors -- no broker call, and none of them decides truth.
    # ------------------------------------------------------------------ #

    def realized_pnl_by_symbol(self, position_group_id: str) -> Dict[str, float]:
        self._require_registered(position_group_id)
        symbols = self._groups[position_group_id]["symbols"]
        return {symbol: self._broker.get_realized_pnl(symbol) for symbol in symbols}

    def all_group_ids(self) -> Tuple[str, ...]:
        return tuple(self._order)

    def symbols_for_group(self, position_group_id: str) -> Tuple[str, ...]:
        """This group's registered leg symbols. PURE -- no broker call.

        `get_group_reality()` also exposes `.symbols`, but it re-reads the
        broker on every call. A caller that wants only the symbols -- e.g.
        reconciliation, which already has its own single unfiltered read --
        would otherwise pay one broker call PER GROUP and, worse, derive its
        two sides of the comparison from DIFFERENT reads, so a position closing
        between them could manufacture a false divergence."""
        self._require_registered(position_group_id)
        return tuple(self._groups[position_group_id]["symbols"])

    def initial_risk(self, position_group_id: str) -> float:
        self._require_registered(position_group_id)
        return self._groups[position_group_id]["initial_risk"]

    def _require_registered(self, position_group_id: str) -> None:
        if position_group_id not in self._groups:
            raise UnknownPositionGroupError(
                f"position_group_id {position_group_id!r} was never registered")
