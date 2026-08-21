"""Futures Adapter -- Shadow Campaign v2 Phase 1.

Translates a live FYERS futures quote into MarketSnapshot's
FutureSnapshot, reusing the EXISTING
bujji.futures_observation.engine.compute_basis() for the basis
calculation -- never reimplemented here. Only broker call made:
broker.get_futures_quote() (read-only).
"""
from __future__ import annotations

from typing import Optional

from bujji.futures_observation.engine import compute_basis

from .models import FutureSnapshot


async def build_future_snapshot(broker, underlying: str, spot: Optional[float]) -> Optional[FutureSnapshot]:
    try:
        quote = await broker.get_futures_quote(underlying)
    except Exception:
        return None
    if not quote:
        return None

    ltp = quote.get("ltp")
    basis = compute_basis(ltp, spot)

    # PER-CYCLE DEPTH (operator directive, 2026-08-20). One extra call per
    # cycle against the host-wide FYERS budget -- negligible at ~8.3/s, and
    # this is the only genuine ORDER-BOOK PRESSURE signal Bujji has: not spot
    # price, not option open interest, not basis.
    #
    # NEVER FATAL and never fabricated. A failed or malformed depth call
    # leaves both aggregates None, which downstream must read as "no
    # observation" rather than "balanced book" -- a zero imbalance is a
    # measurement, absence is not.
    total_buy_qty = total_sell_qty = None
    symbol = quote.get("symbol")
    if symbol:
        try:
            depth = await broker.get_depth(symbol)
        except Exception:  # noqa: BLE001 -- depth is additive; it must not cost the futures read
            depth = None
        if depth:
            raw_buy = depth.get("totalbuyqty")
            raw_sell = depth.get("totalsellqty")
            try:
                total_buy_qty = int(raw_buy) if raw_buy is not None else None
                total_sell_qty = int(raw_sell) if raw_sell is not None else None
            except (TypeError, ValueError):
                total_buy_qty = total_sell_qty = None

    return FutureSnapshot(
        symbol=quote.get("symbol") or f"{underlying}-FUT",
        ltp=ltp,
        total_buy_qty=total_buy_qty,
        total_sell_qty=total_sell_qty,
        volume=quote.get("volume"),
        open_interest=quote.get("oi"),
        basis=basis,
        premium_discount=basis,
    )
