"""Intelligence Runner — Market Intelligence Core.

The ONLY place production code touches the `bujji.intelligence` package.
Computes all eight MIC brains' readings from whatever real data is
available on a given cycle and returns a dashboard-ready dict, one entry
per brain (a brain's key is simply absent when there is no real data to
give it at all -- never a fabricated placeholder reading).

This module only ever READS inputs the orchestrator already has and
RETURNS a dict for the dashboard to display. Nothing here feeds back
into any trading decision, order, or sizing -- the observation-only
principle the whole Market Intelligence Core was built on.

DATA COVERAGE, STATED HONESTLY: three brains have no live production
data feed yet -- Liquidity (needs real-time bid/ask), Structure (needs
option-chain OI), and Event's VIX half (needs a live VIX quote). None of
those are currently fetched anywhere in the trading loop -- wiring them
in is a separate, not-yet-done integration step (each brain's own
"Integration note" in docs/MARKET_INTELLIGENCE_CORE.md already says so).
Calling them with no real data is not a bug: each brain's own
data-quality gate is null-safe by design and correctly reports
UNKNOWN/INSUFFICIENT with an honest reason -- exactly the behavior their
own test suites already validate. Regime, Behaviour, and Event's
expiry-half always run because their inputs (spot candle history, the
real trade journal, and pure date arithmetic) are always available.
Volatility, Premium, and Greeks only run while a real straddle position
is open, because "entry premium", "entry IV", and "position Greeks" are
meaningless without one.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional

from ..core.enums import OptionType
from ..core.models import Candle, Position
from .behaviour_brain import BehaviourBrain
from .context import EXECUTION_MODE_LIVE, IntelligenceContext
from .event_brain import EventBrain
from .greeks_brain import GreeksBrain
from .liquidity_brain import LiquidityBrain
from .premium_brain import PremiumBrain
from .regime_brain import RegimeBrain
from .structure_brain import StructureBrain
from .volatility_brain import VolatilityBrain, solve_implied_volatility

_regime = RegimeBrain()
_volatility = VolatilityBrain()
_premium = PremiumBrain()
_greeks = GreeksBrain()
_liquidity = LiquidityBrain()
_structure = StructureBrain()
_event = EventBrain()
_behaviour = BehaviourBrain()

RISK_FREE_RATE = 0.065  # Same constant used throughout the MIC brains.


def run_intelligence(
    spot_candles: list[Candle],
    position: Optional[Position],
    now: datetime,
    ce_premium: Optional[float],
    pe_premium: Optional[float],
    trade_rows: list[dict],
    vix_level: Optional[float] = None,
    vix_prev_close: Optional[float] = None,
    ce_bid: Optional[float] = None,
    ce_ask: Optional[float] = None,
    pe_bid: Optional[float] = None,
    pe_ask: Optional[float] = None,
    oi_strikes: Optional[list[tuple[float, float, float]]] = None,
) -> dict[str, Any]:
    readings: dict[str, Any] = {}

    # ONE shared IntelligenceContext for every brain this cycle (Phase
    # 19.2.2) -- `now` is already the real live/replay time this function
    # was called with, so this introduces no new clock source, it just
    # threads the existing one through instead of each brain independently
    # calling now_ist().
    context = IntelligenceContext(as_of_time=now, execution_mode=EXECUTION_MODE_LIVE)

    readings["regime"] = _regime.analyze(spot_candles, context).to_dashboard()

    spot = spot_candles[-1].close if spot_candles else None

    # --- Position-dependent brains: only meaningful with a real open
    # straddle. Absent (not a placeholder) when there is none. ---
    ctx = _position_context(position, ce_premium, pe_premium, now, spot)
    if ctx is not None:
        readings["volatility"] = _volatility.analyze(
            spot_candles, ctx["spot_now"], ctx["strike"], ctx["t_years_now"],
            ce_premium, pe_premium, RISK_FREE_RATE, context=context,
        ).to_dashboard()
        readings["premium"] = _premium.analyze(
            entry_combined_premium=ctx["entry_combined_premium"],
            current_combined_premium=ctx["current_combined_premium"],
            spot_at_entry=ctx["spot_at_entry"], strike=ctx["strike"],
            entry_iv=ctx["entry_iv"], entry_time=ctx["entry_time"], now=now,
            expiry_time=ctx["expiry_time"], risk_free_rate=RISK_FREE_RATE,
        ).to_dashboard()
        readings["greeks"] = _greeks.analyze(
            spot=ctx["spot_now"], strike=ctx["strike"], t_years=ctx["t_years_now"],
            iv_ce=ctx["iv_ce_now"], iv_pe=ctx["iv_pe_now"], risk_free_rate=RISK_FREE_RATE,
            context=context,
        ).to_dashboard()

    # --- Not yet wired to a live production data feed (see module
    # docstring) -- null-safe brains report their own honest UNKNOWN. ---
    readings["liquidity"] = _liquidity.analyze(ce_bid, ce_ask, pe_bid, pe_ask, context=context).to_dashboard()
    readings["structure"] = _structure.analyze(spot, oi_strikes or [], context=context).to_dashboard()

    expiry_date = None
    if position is not None and position.ce_contract is not None:
        try:
            expiry_date = date.fromisoformat(position.ce_contract.expiry)
        except (TypeError, ValueError):
            expiry_date = None
    readings["event"] = _event.analyze(expiry_date, now.date(), vix_level, vix_prev_close, context=context).to_dashboard()

    trades = [(row["daily_result"], row.get("exit_reason") or "unknown")
              for row in reversed(trade_rows)]  # Oldest first -- streak must read forward in time.
    readings["behaviour"] = _behaviour.analyze(trades).to_dashboard()

    return readings


def _position_context(
    position: Optional[Position], ce_premium: Optional[float], pe_premium: Optional[float],
    now: datetime, spot_now: Optional[float],
) -> Optional[dict]:
    if position is None or position.ce_contract is None or position.pe_contract is None:
        return None
    if ce_premium is None or pe_premium is None or ce_premium <= 0 or pe_premium <= 0:
        return None
    if spot_now is None or spot_now <= 0:
        return None
    try:
        expiry_date = date.fromisoformat(position.ce_contract.expiry)
    except (TypeError, ValueError):
        return None
    expiry_time = datetime.combine(expiry_date, datetime.min.time(), tzinfo=now.tzinfo).replace(hour=15, minute=30)
    t_years_now = (expiry_time - now).total_seconds() / (365 * 24 * 3600)
    if t_years_now <= 0:
        return None

    strike = position.ce_contract.strike

    iv_ce_now = solve_implied_volatility(ce_premium, spot_now, strike, t_years_now,
                                         RISK_FREE_RATE, OptionType.CE)
    iv_pe_now = solve_implied_volatility(pe_premium, spot_now, strike, t_years_now,
                                         RISK_FREE_RATE, OptionType.PE)
    # entry_iv is honestly None, not approximated: Position only records the
    # COMBINED entry premium (`entry_price`), never separate CE/PE entry
    # legs -- there is no real per-leg entry price to solve an entry IV
    # from. Splitting the combined figure 50/50 to back one out would be a
    # guess (CE and PE premiums are rarely close to equal), exactly what
    # this codebase's discipline refuses to do. The Premium Brain's own
    # data-quality gate handles this correctly: no entry_iv -> UNKNOWN, with
    # an honest reason, not a fabricated theta-only baseline. Capturing
    # separate CE/PE entry premiums in the journal/Position is a real,
    # separate improvement for later, not attempted in this pass.
    entry_iv = None

    return {
        "strike": strike, "spot_now": spot_now, "spot_at_entry": position.entry_spot,
        "t_years_now": t_years_now, "entry_time": position.entry_time, "expiry_time": expiry_time,
        "entry_combined_premium": position.entry_price,
        "current_combined_premium": ce_premium + pe_premium,
        "entry_iv": entry_iv, "iv_ce_now": iv_ce_now, "iv_pe_now": iv_pe_now,
    }
