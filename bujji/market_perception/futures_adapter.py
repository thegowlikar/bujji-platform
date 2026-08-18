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
    return FutureSnapshot(
        symbol=quote.get("symbol") or f"{underlying}-FUT",
        ltp=ltp,
        volume=quote.get("volume"),
        open_interest=quote.get("oi"),
        basis=basis,
        premium_discount=basis,
    )
