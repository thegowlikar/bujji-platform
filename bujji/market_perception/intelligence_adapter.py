"""Intelligence Snapshot Adapter -- Shadow Campaign v2 Phase 2.

Converts a MarketSnapshot (+ freshly-fetched spot candles) into the
exact input shape bujji.intelligence.runner.run_intelligence() expects,
then returns its unmodified return value. Neither run_intelligence()
nor any of the eight brain modules is imported-for-modification or
touched by this file -- run_intelligence is called exactly as-is.

HONEST STRUCTURAL LIMITATION (see bujji.intelligence.runner's own module
docstring, which states this explicitly): Volatility, Premium, and
Greeks Brains only run inside run_intelligence() when a real Position
object is supplied ("meaningless without one"). Phase 2 has no Virtual
Portfolio -- that's a separate, later, explicitly-out-of-scope phase --
so this adapter always passes position=None, ce_premium=None,
pe_premium=None. Those three brains' keys are simply absent from the
returned dict, exactly as run_intelligence()'s own docstring describes
happening with no real data ("a brain's key is simply absent... never a
fabricated placeholder"). Regime, Liquidity, Structure, Event, and
Behaviour run every cycle from real live data.

Only broker call made in this file: get_recent_candles() (read-only,
already an existing FyersBroker method used elsewhere in this project).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from bujji.core.models import Candle
from bujji.intelligence.runner import run_intelligence

from .models import MarketSnapshot

DEFAULT_CANDLE_MINUTES = 5
DEFAULT_CANDLE_COUNT = 30


async def fetch_spot_candles(
    broker, underlying: str, minutes: int = DEFAULT_CANDLE_MINUTES, count: int = DEFAULT_CANDLE_COUNT,
) -> List[Candle]:
    """Best-effort: RegimeBrain (and spot-derivation inside
    run_intelligence) is null-safe on an empty list per its own
    docstring, so a broker failure here degrades gracefully rather than
    raising -- consistent with how every other adapter in this project
    treats a single failed data source."""
    try:
        return await broker.get_recent_candles(underlying, minutes=minutes, count=count)
    except Exception:
        return []


def _oi_strikes(snapshot: MarketSnapshot) -> Optional[List[Tuple[float, float, float]]]:
    if snapshot.option_chain is None:
        return None
    by_strike: Dict[float, Dict[str, float]] = {}
    for leg in snapshot.option_chain.legs:
        if leg.open_interest is None:
            continue
        entry = by_strike.setdefault(leg.strike, {})
        entry["ce" if leg.option_type == "CE" else "pe"] = leg.open_interest
    if not by_strike:
        return None
    return [
        (strike, values.get("ce", 0.0), values.get("pe", 0.0))
        for strike, values in sorted(by_strike.items())
    ]


def _atm_bid_ask(
    snapshot: MarketSnapshot,
) -> Tuple[Optional[float], Optional[float], Optional[float], Optional[float]]:
    if snapshot.option_chain is None:
        return None, None, None, None
    atm = snapshot.option_chain.atm_strike
    ce = snapshot.option_chain.leg(atm, "CE")
    pe = snapshot.option_chain.leg(atm, "PE")
    return (
        ce.bid if ce else None, ce.ask if ce else None,
        pe.bid if pe else None, pe.ask if pe else None,
    )


def build_intelligence_snapshot(
    snapshot: MarketSnapshot, spot_candles: List[Candle], now: datetime,
    trade_rows: Optional[List[dict]] = None,
) -> Dict[str, Any]:
    """Returns run_intelligence()'s own dict, unmodified -- this
    function only maps inputs, it never reshapes or reinterprets the
    output. trade_rows defaults to empty (Phase 2 does not wire the
    real trade journal in) -- Behaviour Brain's own gate then correctly
    reports UNKNOWN, exactly its documented no-data behavior."""
    ce_bid, ce_ask, pe_bid, pe_ask = _atm_bid_ask(snapshot)
    return run_intelligence(
        spot_candles=spot_candles,
        position=None,
        now=now,
        ce_premium=None,
        pe_premium=None,
        trade_rows=trade_rows or [],
        vix_level=snapshot.vix.value,
        vix_prev_close=snapshot.vix.prev_close,
        ce_bid=ce_bid, ce_ask=ce_ask, pe_bid=pe_bid, pe_ask=pe_ask,
        oi_strikes=_oi_strikes(snapshot),
    )
