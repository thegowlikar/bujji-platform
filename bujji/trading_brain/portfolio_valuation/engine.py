"""Portfolio Valuation engine — Live Shadow Real-Time Paper Execution
sprint (Part 3).

`revalue()` is a plain, pure function: positions in, latest observed
prices in, a PortfolioValuation out. It performs no I/O, holds no
subscription, and knows nothing about FYERS, WebSockets, or any
specific broker -- it is called once per incoming market-data event by
whatever caller owns that event loop (the live shadow operator today;
a replay driver or a future live broker tomorrow, unchanged). This is
what makes it "tick-driven, no polling" without inventing a new
event-bus abstraction: the caller decides when a real tick has
arrived and calls this function then, exactly once, with the prices it
already has -- no second network request, no second broker query, per
the sprint's own explicit instruction.

Never computes MTM using a symbol PaperBroker (or any other broker)
itself hasn't already confirmed a real position in -- an open position
this function was not told about is invisible to it, by design; it
never guesses.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Callable, Dict, List, Optional

from .models import LegValuation, PortfolioValuation

Clock = Callable[[], datetime]


def _real_clock() -> datetime:
    return datetime.now()


def revalue(
    positions: List[dict],
    latest_prices: Dict[str, float],
    realized_pnl_by_symbol: Dict[str, float],
    *,
    price_timestamps: Optional[Dict[str, str]] = None,
    triggering_symbol: Optional[str] = None,
    triggering_tick_timestamp: Optional[str] = None,
    clock: Clock = _real_clock,
) -> PortfolioValuation:
    """Revalue every open position against the latest observed prices.

    `positions`: exactly the list of dicts `Broker.get_open_positions()`
    already returns (symbol/side/qty/avg_price/entry_timestamp) --
    never re-derived, never fetched again here.

    `latest_prices`: the SAME market snapshot that produced the trading
    decision / the most recent tick per symbol -- supplied by the
    caller, never queried by this function. A symbol with no entry here
    yields `current_price=None` for that leg (never defaulted to entry
    price, never to zero) and the whole valuation's totals become
    `None` rather than silently omitting that leg's contribution.

    `price_timestamps`: optional, per-symbol timestamp of when
    `latest_prices[symbol]` was observed -- used only to mark a leg
    `price_is_stale=True` when its price came from a tick other than
    the one that triggered this revaluation (`triggering_symbol`).
    Never used to reject or discard a price -- staleness is reported,
    not enforced, here; a caller-side freshness gate (already proven
    elsewhere in this codebase) is where enforcement belongs.
    """
    timestamp = clock().isoformat()
    price_timestamps = price_timestamps or {}

    legs: List[LegValuation] = []
    any_missing_price = False
    total_unrealized = 0.0

    for pos in positions:
        symbol = pos["symbol"]
        side = pos["side"]
        qty = pos["qty"]
        entry_price = pos["avg_price"]
        entry_timestamp = pos.get("entry_timestamp")

        current_price = latest_prices.get(symbol)
        tick_ts = price_timestamps.get(symbol)
        is_stale = (
            triggering_symbol is not None
            and symbol != triggering_symbol
            and current_price is not None
        )

        if current_price is None:
            any_missing_price = True
            unrealized = None
        else:
            sign = 1 if side == "BUY" else -1
            unrealized = sign * (current_price - entry_price) * qty
            total_unrealized += unrealized

        legs.append(
            LegValuation(
                symbol=symbol,
                side=side,
                quantity=qty,
                entry_price=entry_price,
                entry_timestamp=entry_timestamp,
                current_price=current_price,
                tick_timestamp=tick_ts,
                unrealized_pnl=unrealized,
                price_is_stale=is_stale,
            )
        )

    total_realized = sum(realized_pnl_by_symbol.values())
    total_unrealized_final = None if any_missing_price else total_unrealized
    total_pnl = None if total_unrealized_final is None else (total_realized + total_unrealized_final)

    seed = "|".join([timestamp, triggering_symbol or "NONE", str(len(legs))])
    valuation_id = "PV-" + hashlib.md5(seed.encode()).hexdigest()[:16]

    return PortfolioValuation(
        valuation_id=valuation_id,
        as_of=timestamp,
        legs=tuple(legs),
        total_realized_pnl=total_realized,
        total_unrealized_pnl=total_unrealized_final,
        total_pnl=total_pnl,
        triggering_symbol=triggering_symbol,
        triggering_tick_timestamp=triggering_tick_timestamp,
    )
