"""Phase 20.3 -- Cycle-1 strategy families. Exactly two, per the
phase's own scope decision (MIC v0 validation, Phase 20.1C, proved
intraday TREND/RANGE separation; full-day classification was the
wrong horizon).

`simulate_position` is still a direct, unmodified reference to
`execution_backtest.driver.simulate_round_trip_trade` (Phase 20.2) --
execution simulation is untouched.

`generate_signal` (Phase 20.4): each family now uses one concrete,
indicator-based, non-lookahead hypothesis from `strategy_research.
signals` -- Trend Following: moving-average alignment; Mean Reversion:
Bollinger-band overshoot fade. Both reuse `bujji.market_timeseries.
indicators` (Phase 15Q) unmodified; see that function's own docstring
for the full information-boundary argument. The Phase 20.3.1
net-move-sign functions (`generate_trend_following_signal`/
`generate_mean_reversion_signal`) remain defined and tested in
`signals.py` but are no longer wired here -- superseded, not deleted.
"""
from __future__ import annotations

from bujji.execution_backtest import driver as _driver
from bujji.mic_v0_validation.models_intraday import INTRADAY_RANGE, INTRADAY_TREND_DOWN, INTRADAY_TREND_UP

from .models import StrategyFamily
from .signals import generate_bollinger_reversion_signal, generate_ma_alignment_signal


def _explain_reason(reading) -> str:
    """One-line, human-readable evidence string -- every value cited
    is a real field already computed by `classify_intraday_window`
    (Phase 20.1C), never invented here. Realized volatility is
    reported as its real numeric value, NOT bucketed into a
    LOW/NORMAL/HIGH label: that bucketing exists for MIC v0's
    session-level `VolatilityState` (Phase 20.1), which nothing at
    intraday-window granularity computes -- fabricating one here
    would misrepresent evidence that doesn't exist at this horizon."""
    er = f"{reading.efficiency_ratio:.2f}" if reading.efficiency_ratio is not None else "NA"
    adx = f"{reading.adx:.1f}" if reading.adx is not None else "NA"
    vol = f"{reading.realized_vol:.4f}" if reading.realized_vol is not None else "NA"
    return f"ER={er} ADX={adx} REALIZED_VOL={vol}"


TREND_FOLLOWING = StrategyFamily(
    name=_driver.FAMILY_A_TREND_FOLLOWING,
    market_conditions_required=(INTRADAY_TREND_UP, INTRADAY_TREND_DOWN),
    incompatible_conditions=(INTRADAY_RANGE,),
    generate_signal=generate_ma_alignment_signal,
    simulate_position=_driver.simulate_round_trip_trade,
    explain_reason=_explain_reason,
)

MEAN_REVERSION = StrategyFamily(
    name=_driver.FAMILY_B_MEAN_REVERSION,
    market_conditions_required=(INTRADAY_RANGE,),
    incompatible_conditions=(INTRADAY_TREND_UP, INTRADAY_TREND_DOWN),
    generate_signal=generate_bollinger_reversion_signal,
    simulate_position=_driver.simulate_round_trip_trade,
    explain_reason=_explain_reason,
)

ALL_FAMILIES = (TREND_FOLLOWING, MEAN_REVERSION)
