"""Greeks Intelligence Bridge -- Phase 15E.

Translates a real MarketSnapshot into the plain-scalar input shape
`bujji.msi_greeks.engine.assess_atm_greeks()` expects, mirroring
`market_perception/msi_adapter.py`'s own bridge pattern exactly
(same-shaped MarketSnapshot -> plain scalars -> pure engine call).

`_atm_mid_premiums`/`_t_years_to_expiry` are DUPLICATED here rather
than imported from `msi_adapter.py`, following the established
precedent already set by `market_state/intelligence_cycle_recorder.py`
for `_atm_bid_ask` (Phase 9 Liquidity Bridge): both are private
(leading-underscore) helpers not meant for cross-module import, and
the logic is small enough that duplicating it is more honest than
reaching into another module's private surface.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional, Tuple

from bujji.msi_greeks.engine import DEFAULT_RISK_FREE_RATE, assess_atm_greeks
from bujji.msi_greeks.models import GreeksAssessment

from .models import MarketSnapshot


def _atm_mid_premiums(snapshot: MarketSnapshot) -> Tuple[Optional[float], Optional[float]]:
    """Mid = (bid+ask)/2, only when BOTH sides are real -- same
    convention as msi_adapter.py's own `_atm_mid_premiums`."""
    if snapshot.option_chain is None:
        return None, None
    atm = snapshot.option_chain.atm_strike
    ce = snapshot.option_chain.leg(atm, "CE")
    pe = snapshot.option_chain.leg(atm, "PE")
    ce_mid = (ce.bid + ce.ask) / 2.0 if ce and ce.bid is not None and ce.ask is not None else None
    pe_mid = (pe.bid + pe.ask) / 2.0 if pe and pe.bid is not None and pe.ask is not None else None
    return ce_mid, pe_mid


def _t_years_to_expiry(expiry_iso: str, now: datetime) -> Optional[float]:
    """Same expiry-close convention (15:30 local) as msi_adapter.py's
    own `_t_years_to_expiry` -- reused for consistency, not reinvented."""
    try:
        expiry_date = date.fromisoformat(expiry_iso)
    except (TypeError, ValueError):
        return None
    expiry_time = datetime.combine(expiry_date, datetime.min.time(), tzinfo=now.tzinfo).replace(hour=15, minute=30)
    t_years = (expiry_time - now).total_seconds() / (365 * 24 * 3600)
    return t_years if t_years > 0 else None


def build_greeks_assessment(
    snapshot: MarketSnapshot, now: datetime, risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
) -> Optional[GreeksAssessment]:
    """Returns None (never a fabricated assessment) when spot or an ATM
    option chain is unavailable at all -- mirrors
    build_volatility_structure_assessment's own None-on-missing-inputs
    discipline. A missing/invalid expiry or premium degrades to a
    per-leg UNKNOWN inside the returned assessment instead, since the
    OTHER leg may still be honestly computable."""
    spot = snapshot.spot.ltp if snapshot.spot else None
    if spot is None or snapshot.option_chain is None:
        return None
    strike = snapshot.option_chain.atm_strike
    t_years = _t_years_to_expiry(snapshot.option_chain.expiry, now)
    ce_premium, pe_premium = _atm_mid_premiums(snapshot)

    return assess_atm_greeks(
        spot=spot, strike=strike, t_years=t_years, ce_premium=ce_premium, pe_premium=pe_premium,
        timestamp=snapshot.timestamp, risk_free_rate=risk_free_rate,
    )
