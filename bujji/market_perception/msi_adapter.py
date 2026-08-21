"""MSI Intelligence Bridge -- Shadow Campaign v2 Phase 3A.

Translates real MarketSnapshot + spot-candle data into the exact input
shape bujji.msi_volatility_structure.engine.assess_volatility_structure()
expects, then returns its unmodified return value. That function itself
is never modified, only imported and called.

HONEST SCOPE, PHASE 3A: of the 7 MSI modules named in this phase's
brief (msi_market_direction, msi_volatility_structure,
msi_price_structure, msi_participant_positioning, msi_trade_thesis,
msi_trade_intent, msi_decision_synthesis), only msi_volatility_structure's
entrypoint takes plain scalar inputs (spot/strike/t_years/premiums/
candle closes) that a single point-in-time MarketSnapshot can honestly
supply. The other six all require either:
  - Episode/MarketEvent objects (bujji.market_episode /
    bujji.live_market_events) -- a multi-observation aggregated history
    market_perception does not build (msi_price_structure, and
    transitively msi_market_direction / msi_trade_thesis /
    msi_decision_synthesis via episode_ids)
  - MarketStructureAssessment from bujji.msi_market_structure -- a
    package never named in this phase's authorized reuse list
    (msi_market_direction, transitively msi_trade_thesis)
  - StrategyEligibilityAssessment from bujji.msi_strategy_eligibility --
    likewise never authorized this phase (msi_trade_intent)
  - a ChainSnapshot shape not yet confidently located in this codebase
    (msi_participant_positioning, transitively msi_trade_thesis)

Bridging those six is explicitly NOT attempted here -- doing so without
the real upstream objects would mean fabricating them, which this
phase's own "never invent missing data" rule forbids. See the Phase 3A
completion report for the full evidence trail; unblocking each is
separate, later, not-yet-authorized work.

No broker calls, no strategy logic, no order/position/margin method, no
import of trading_brain/execution/risk_governor anywhere in this file --
enforced by tests/test_msi_adapter_safety_phase3a.py.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import List, Optional, Sequence, Tuple

from bujji.core.models import Candle
from bujji.msi_volatility_structure.engine import assess_volatility_structure
from bujji.msi_volatility_structure.models import VolatilityStructureAssessment

from .models import MarketSnapshot


def closes_with_timestamps(candles: Sequence[Candle]) -> List[Tuple[str, float]]:
    """Real (timestamp_iso, close) pairs from real candles only -- no
    resampling, no synthetic points, no fabricated history."""
    return [(c.timestamp.isoformat(), c.close) for c in candles]


def _atm_mid_premiums(snapshot: MarketSnapshot) -> Tuple[Optional[float], Optional[float]]:
    """Mid = (bid+ask)/2, only when BOTH sides are real -- never a
    one-sided approximation, never a fabricated premium."""
    if snapshot.option_chain is None:
        return None, None
    atm = snapshot.option_chain.atm_strike
    ce = snapshot.option_chain.leg(atm, "CE")
    pe = snapshot.option_chain.leg(atm, "PE")
    ce_mid = (ce.bid + ce.ask) / 2.0 if ce and ce.bid is not None and ce.ask is not None else None
    pe_mid = (pe.bid + pe.ask) / 2.0 if pe and pe.bid is not None and pe.ask is not None else None
    return ce_mid, pe_mid


def _t_years_to_expiry(expiry_iso: str, now: datetime) -> Optional[float]:
    """Same expiry-close convention (15:30 local) already used by
    bujji.intelligence.runner._position_context -- reused for
    consistency, not reinvented. None if expiry is missing/invalid/past."""
    try:
        expiry_date = date.fromisoformat(expiry_iso)
    except (TypeError, ValueError):
        return None
    expiry_time = datetime.combine(expiry_date, datetime.min.time(), tzinfo=now.tzinfo).replace(hour=15, minute=30)
    t_years = (expiry_time - now).total_seconds() / (365 * 24 * 3600)
    return t_years if t_years > 0 else None


def build_volatility_structure_assessment(
    snapshot: MarketSnapshot, candles: Sequence[Candle], now: datetime,
) -> Optional[VolatilityStructureAssessment]:
    """Returns None (never a fabricated assessment) when spot, an ATM
    option chain, or a valid time-to-expiry are unavailable. Premiums
    and candle history are allowed to be partially/fully missing --
    assess_volatility_structure()'s own brain is null-safe on those and
    reports its own honest IV/regime UNKNOWN, exactly as it does when
    called from the existing intelligence runner with no open position."""
    spot = snapshot.spot.ltp
    if spot is None or snapshot.option_chain is None:
        return None
    strike = snapshot.option_chain.atm_strike
    t_years = _t_years_to_expiry(snapshot.option_chain.expiry, now)
    if t_years is None:
        return None

    ce_premium, pe_premium = _atm_mid_premiums(snapshot)
    closes_ts = closes_with_timestamps(candles)

    return assess_volatility_structure(
        spot=spot, strike=strike, t_years=t_years,
        ce_premium=ce_premium, pe_premium=pe_premium,
        closes_with_ts=closes_ts, timestamp=snapshot.timestamp,
    )
