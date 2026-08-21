"""Phase 20.3/20.4 -- minimal research driver. Composes, unmodified:
`generate_rolling_windows`/`classify_intraday_window` (Phase 20.1C),
this package's `eligibility`/`families`/`attribution`, and
`bujji.execution_profiles` (Phase 20.2) for realistic execution
assumptions. No optimizer, no parameter search, no ML -- one pass over
consecutive window pairs per real trading day.

Phase 20.4's information boundary: `history_upto_t` is every real
candle in `day_candles` with a timestamp <= window N's own last
candle's timestamp (T) -- computed by a plain timestamp filter over
already-real, already-ordered data, never a slice that reaches past T.
This is what `family.generate_signal` actually receives; it is a
STRICT SUPERSET of `window_n` (whatever the family's signal function
needs from window N specifically, it can read from the tail of this),
never anything from window N+1 or later.
"""
from __future__ import annotations

import random
from typing import List, Sequence

from bujji.core.models import Candle
from bujji.execution_profiles import ExecutionProfile
from bujji.mic_v0_validation.intraday_validation import classify_intraday_window, generate_rolling_windows

from .attribution import attribute, compute_mic_correct
from .eligibility import NO_TRADE, eligible_family_for_regime
from .families import ALL_FAMILIES, _explain_reason as _reason
from .models import AttributedTrade

_FAMILIES_BY_NAME = {f.name: f for f in ALL_FAMILIES}


def run_research_day(
    date: str, day_candles: Sequence[Candle], window_minutes: int,
    execution_profile: ExecutionProfile, quantity: int, multiplier: int, seed: int = 42,
) -> List[AttributedTrade]:
    """One real trading day, one window length. For every consecutive
    rolling-window pair (N, N+1): classify N (MIC's call), look up the
    eligible family, and -- unless NO_TRADE -- simulate a real trade
    on N+1 using that family's own signal rule, then attribute the
    outcome against N+1's OWN independently-classified regime (see
    `attribution` module docstring for why N+1, not N)."""
    windows = generate_rolling_windows(day_candles, window_minutes)
    rng = random.Random(seed)
    results: List[AttributedTrade] = []

    for i in range(len(windows) - 1):
        window_n, window_n1 = windows[i], windows[i + 1]
        reading_n = classify_intraday_window(date, window_n, window_minutes)
        if reading_n is None:
            continue

        family_name = eligible_family_for_regime(reading_n.intraday_regime)
        if family_name == NO_TRADE:
            results.append(AttributedTrade(
                date=date, window_minutes=window_minutes, mic_state=reading_n.intraday_regime,
                selected_family=NO_TRADE, reason=_reason(reading_n),
                realized_next_window_state=None, mic_correct=None,
                theoretical_gross_pnl=None, net_pnl=None, execution_drag=None,
                forecast_correct=None, outcome_worked=None, attribution=None,
            ))
            continue

        family = _FAMILIES_BY_NAME[family_name]
        t = window_n[-1].timestamp
        history_upto_t = [c for c in day_candles if c.timestamp <= t]
        signal = family.generate_signal(history_upto_t, window_n1)
        reading_n1 = classify_intraday_window(date, window_n1, window_minutes)
        if signal is None or reading_n1 is None:
            continue  # no real trade evidence to attribute -- never fabricated.

        side, entry_price, exit_price = signal
        trade = family.simulate_position(
            side, quantity, multiplier, entry_price, exit_price, execution_profile, rng,
            family=family_name, window_minutes=window_minutes, window_date=date,
        )
        if trade.net_pnl is None:
            continue  # rejected/unfilled leg -- honestly excluded, never assumed zero.

        mic_correct = compute_mic_correct(reading_n1.intraday_regime, family)
        forecast_correct = trade.theoretical_gross_pnl > 0 if trade.theoretical_gross_pnl is not None else None
        outcome_worked = trade.net_pnl > 0
        drag = trade.theoretical_gross_pnl - trade.net_pnl if trade.theoretical_gross_pnl is not None else None

        results.append(AttributedTrade(
            date=date, window_minutes=window_minutes, mic_state=reading_n.intraday_regime,
            selected_family=family_name, reason=_reason(reading_n),
            realized_next_window_state=reading_n1.intraday_regime, mic_correct=mic_correct,
            theoretical_gross_pnl=trade.theoretical_gross_pnl, net_pnl=trade.net_pnl,
            execution_drag=drag, forecast_correct=forecast_correct, outcome_worked=outcome_worked,
            attribution=attribute(mic_correct, outcome_worked),
        ))

    return results
