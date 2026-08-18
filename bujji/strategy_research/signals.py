"""Phase 20.3 (redesign) -- non-lookahead signal generation.

Prior design (flagged as a limitation in
docs/PHASE_20_3_STRATEGY_RESEARCH_REPORT.md Section 3): `generate_signal`
delegated to `execution_backtest.driver._family_signal`, which decides
BUY/SELL from the SAME window it trades -- close to lookahead-
guaranteed to be directionally right before costs, regardless of
whether MIC's call (from window N) was any good. That made the
attribution framework's strategy-outcome axis untrustworthy even
though its MIC-correctness axis was genuinely non-circular.

Fixed here: direction is decided ENTIRELY from window N's own realized
candles (`window_n`) -- fully known before window N+1 begins, so
deciding from it is not lookahead. `window_n1` supplies ONLY the
entry/exit REFERENCE prices actually transacted (a real backtest must
transact at real prices in the window it trades; that is not the same
thing as using those prices to DECIDE direction). This is a genuine,
disclosed strategy-design change, not a threshold tune -- it does not
touch `execution_backtest.driver` (Phase 20.2's own single-window
design stays exactly as it was, with its own tests unchanged) or any
MIC classification logic.

Both families use the SAME window-N net-move-sign evidence, with
opposite interpretations -- Trend Following follows it, Mean Reversion
fades it. Neither rule is tuned, optimized, or claimed profitable;
both exist only to make the direction decision now depend on
information available before window N+1, per this phase's explicit
lookahead fix.
"""
from __future__ import annotations

from typing import Optional, Sequence, Tuple

from bujji.core.models import Candle
from bujji.market_timeseries.indicators import bollinger, ema, sma


def _net_move_sign(window: Sequence[Candle]) -> int:
    """+1 if the window closed higher than it opened, -1 if lower, 0
    if unchanged or too short to have a real net move."""
    if len(window) < 2:
        return 0
    delta = window[-1].close - window[0].close
    if delta > 0:
        return 1
    if delta < 0:
        return -1
    return 0


def generate_trend_following_signal(
    window_n: Sequence[Candle], window_n1: Sequence[Candle],
) -> Optional[Tuple[str, float, float]]:
    """Direction FOLLOWS window N's own net move. Entry/exit reference
    prices are window N+1's real open/close -- never used to decide
    direction, only to know what was actually transacted."""
    if not window_n or not window_n1:
        return None
    sign = _net_move_sign(window_n)
    if sign == 0:
        return None
    side = "BUY" if sign > 0 else "SELL"
    return side, window_n1[0].close, window_n1[-1].close


def generate_mean_reversion_signal(
    window_n: Sequence[Candle], window_n1: Sequence[Candle],
) -> Optional[Tuple[str, float, float]]:
    """Direction FADES window N's own net move (mean-reversion
    hypothesis: a range-bound drift in window N reverts in N+1).
    Entry/exit reference prices are window N+1's real open/close."""
    if not window_n or not window_n1:
        return None
    sign = _net_move_sign(window_n)
    if sign == 0:
        return None
    side = "SELL" if sign > 0 else "BUY"
    return side, window_n1[0].close, window_n1[-1].close


# --------------------------------------------------------------------- #
# Phase 20.4 -- indicator-based hypotheses. `generate_trend_following_
# signal`/`generate_mean_reversion_signal` above (Phase 20.3.1) remain
# defined and tested, but are no longer the ones wired into
# `families.py` -- these two supersede them as the Cycle-1 families'
# live signal, per the phase's own "one simple hypothesis first"
# instruction. Both reuse `sma`/`ema`/`bollinger` from
# `bujji.market_timeseries.indicators` (Phase 15Q) UNMODIFIED --
# duck-type compatible with `bujji.core.models.Candle` (both share
# plain `.close`/`.high`/`.low` attributes; confirmed by direct
# inspection, no adapter needed). Periods are the indicators' own
# textbook defaults (EMA 9 / SMA 20 / Bollinger 20,2-sigma) -- never
# tuned to this data, per this phase's "no parameter search" boundary.
#
# CRITICAL INFORMATION BOUNDARY: both functions take `history_upto_t`
# -- every real candle from the trading day's open through window N's
# OWN LAST CANDLE (T), and NOTHING after T. `window_n1` supplies ONLY
# the entry/exit reference prices actually transacted; it is never
# read to decide direction. See
# `test_ma_alignment_signal_direction_is_decided_from_history_upto_t_not_n1`
# and the Bollinger analogue for the direct, mechanical proof (flip
# window N+1's own prices; the chosen side must not change).
# --------------------------------------------------------------------- #

def generate_ma_alignment_signal(
    history_upto_t: Sequence[Candle], window_n1: Sequence[Candle],
    short_period: int = 9, long_period: int = 20,
) -> Optional[Tuple[str, float, float]]:
    """Family A (Phase 20.4): moving-average alignment. Short EMA above
    long SMA (both computed ONLY from `history_upto_t`) -> BUY;
    below -> SELL; equal, or insufficient history for either average
    (Phase 15Q's own "insufficient history is not zero" rule -- never
    a partial-window average) -> no signal."""
    if not window_n1 or not history_upto_t:
        return None
    short = ema(history_upto_t, short_period)
    long_ = sma(history_upto_t, long_period)
    if short is None or long_ is None or short == long_:
        return None
    side = "BUY" if short > long_ else "SELL"
    return side, window_n1[0].close, window_n1[-1].close


def generate_bollinger_reversion_signal(
    history_upto_t: Sequence[Candle], window_n1: Sequence[Candle],
    period: int = 20, num_std: float = 2.0,
) -> Optional[Tuple[str, float, float]]:
    """Family B (Phase 20.4): overshoot-from-mean fade. The LAST real
    close in `history_upto_t` (i.e. at T) above the upper Bollinger
    band -> SELL (fade the up-overshoot); below the lower band ->
    BUY (fade the down-overshoot); inside the bands -> no signal
    (nothing extreme to fade). Bands computed ONLY from
    `history_upto_t`."""
    if not window_n1 or not history_upto_t:
        return None
    bands = bollinger(history_upto_t, period, num_std)
    if bands is None:
        return None
    lower, _middle, upper = bands
    last_close = history_upto_t[-1].close
    if last_close > upper:
        side = "SELL"
    elif last_close < lower:
        side = "BUY"
    else:
        return None
    return side, window_n1[0].close, window_n1[-1].close
