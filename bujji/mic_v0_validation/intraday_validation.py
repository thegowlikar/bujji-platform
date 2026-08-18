"""bujji.mic_v0_validation.intraday_validation — Phase 20.1C.

Answers, using ONLY real market data: does MIC's classification become
meaningful at the rolling-window horizons (30/60/90/120 min) a trading
decision actually uses, rather than only at full-session granularity
(Phase 20.1B's question, which returned INCONCLUSIVE for TREND)?

REUSE, NOT DUPLICATION: every window is classified via
`bujji.intelligence.regime_brain.RegimeBrain.analyze()` -- the EXACT
SAME call `bujji.mic_v0.engine.compose_market_state()` makes, just
against a shorter candle slice. This module never recomputes
efficiency ratio, realized volatility, or compression ratio itself --
those come back inside `RegimeReading.evidence`, read, not re-derived.
ADX, reversal frequency, and persistence are the SAME functions Phase
20.1B already built (`market_timeseries.indicators.adx`,
`validation._reversal_frequency`, `validation._persistence`) --
imported, not reimplemented.

`bujji.mic_v0.engine`/`bujji.mic_v0.models` are NOT modified by this
phase and NOT imported here for classification -- this is a
VALIDATION-only exploration of an expanded vocabulary, kept
deliberately separate from the frozen MIC v0 architecture per this
phase's own explicit boundary ("do not redesign MIC architecture").
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List, Optional

from bujji.core.models import Candle as CoreCandle
from bujji.intelligence.context import EXECUTION_MODE_HISTORICAL_REPLAY, IntelligenceContext
from bujji.intelligence.models import RegimeType
from bujji.intelligence.regime_brain import RegimeBrain
from bujji.market_timeseries.indicators import adx as compute_adx
from bujji.market_timeseries.models import Candle as TSCandle

from .models_intraday import (
    INTRADAY_RANGE,
    INTRADAY_TRANSITION,
    INTRADAY_TREND_DOWN,
    INTRADAY_TREND_UP,
    INTRADAY_UNKNOWN,
    INTRADAY_VOLATILITY_COMPRESSION,
    INTRADAY_VOLATILITY_EXPANSION,
    WINDOW_LENGTHS_MINUTES,
    IntradayRegimeGroupStats,
    IntradayValidationReport,
    IntradayWindowReading,
    WindowLengthReport,
)
from .validation import _persistence, _reversal_frequency

_regime_brain = RegimeBrain()

CANDLES_PER_MINUTE_5MIN = 1.0 / 5.0

# Disclosed mapping, RegimeType -> expanded intraday vocabulary.
# TRENDING splits into TREND_UP/TREND_DOWN by the sign of the window's
# own net_move (already present in RegimeReading.evidence -- not a new
# calculation). BREAKOUT_ATTEMPT and NO_TRADE are NOT reachable from
# RegimeBrain's current output -- see this phase's report for why they
# are listed in models_intraday but never emitted here: detecting a
# "breakout attempt" specifically (a range tightening then decisively
# breaking) is new classification logic this phase does not build,
# per its own "do not redesign MIC architecture" boundary. Emitting
# them without real logic behind them would be exactly the kind of
# forced classification the charter forbids.
_REGIME_MAP = {
    RegimeType.RANGING: INTRADAY_RANGE,
    RegimeType.VOLATILE: INTRADAY_VOLATILITY_EXPANSION,
    RegimeType.COMPRESSED: INTRADAY_VOLATILITY_COMPRESSION,
    RegimeType.TRANSITIONING: INTRADAY_TRANSITION,
    RegimeType.UNKNOWN: INTRADAY_UNKNOWN,
}


def _map_regime(regime: RegimeType, net_move: Optional[float]) -> str:
    if regime == RegimeType.TRENDING:
        if net_move is None:
            return INTRADAY_UNKNOWN
        return INTRADAY_TREND_UP if net_move > 0 else INTRADAY_TREND_DOWN
    return _REGIME_MAP.get(regime, INTRADAY_UNKNOWN)


def generate_rolling_windows(
    sorted_candles: List[CoreCandle], window_minutes: int, step_minutes: int = 5,
) -> List[List[CoreCandle]]:
    """Real, overlapping, session-anchored rolling windows -- e.g.
    09:15-09:45, 09:20-09:50, 09:25-09:55, ... for a 30-minute window
    stepped every 5 minutes (matching the store's own 5-minute
    resolution, per the charter's own worked example). Windows with
    fewer than `RegimeBrain.MIN_CANDLES` candles are silently excluded
    by the caller (never padded, never fabricated)."""
    if not sorted_candles:
        return []
    windows: List[List[CoreCandle]] = []
    start = sorted_candles[0].timestamp
    end = sorted_candles[-1].timestamp
    window_delta = timedelta(minutes=window_minutes)
    step_delta = timedelta(minutes=step_minutes)

    cursor = start
    while cursor + window_delta <= end + timedelta(seconds=1):
        window_end = cursor + window_delta
        window = [c for c in sorted_candles if cursor <= c.timestamp < window_end]
        if window:
            windows.append(window)
        cursor += step_delta
    return windows


def classify_intraday_window(date_str: str, window_candles: List[CoreCandle], window_minutes: int) -> Optional[IntradayWindowReading]:
    """Returns None if there are too few real candles for RegimeBrain
    to classify honestly -- never a forced or padded classification."""
    if len(window_candles) < 2:
        return None

    as_of = window_candles[-1].timestamp
    context = IntelligenceContext(as_of_time=as_of, execution_mode=EXECUTION_MODE_HISTORICAL_REPLAY)
    reading = _regime_brain.analyze(window_candles, context)
    if reading.data_quality.value == "INSUFFICIENT":
        return None

    net_move = reading.evidence.get("net_move")
    intraday_regime = _map_regime(reading.regime, net_move)

    ts_candles = [
        TSCandle(
            instrument="VALIDATION", kind="FUTURES", interval="FIVE_MINUTE",
            window_start=c.timestamp.isoformat(), window_end=c.timestamp.isoformat(),
            open=c.open, high=c.high, low=c.low, close=c.close, volume=c.volume, tick_count=1,
        )
        for c in window_candles
    ]
    closes = [c.close for c in window_candles]

    start_label = window_candles[0].timestamp.strftime("%H:%M")
    end_label = window_candles[-1].timestamp.strftime("%H:%M")

    return IntradayWindowReading(
        date=date_str, window_label=f"{start_label}-{end_label}", window_minutes=window_minutes,
        intraday_regime=intraday_regime,
        efficiency_ratio=reading.evidence.get("efficiency_ratio"),
        adx=compute_adx(ts_candles, period=min(14, max(1, len(ts_candles) // 2 - 1))) if len(ts_candles) >= 4 else None,
        realized_vol=reading.evidence.get("realized_vol"),
        compression_ratio=reading.evidence.get("compression_ratio"),
        reversal_frequency=_reversal_frequency(closes),
        persistence=_persistence(closes),
        candles_used=len(window_candles),
    )


def _group_stats(label: str, window_minutes: int, readings: List[IntradayWindowReading]) -> IntradayRegimeGroupStats:
    group = [r for r in readings if r.intraday_regime == label and r.window_minutes == window_minutes]

    def _avg(values):
        real = [v for v in values if v is not None]
        return round(sum(real) / len(real), 4) if real else None

    return IntradayRegimeGroupStats(
        label=label, window_minutes=window_minutes, n=len(group),
        avg_efficiency_ratio=_avg([r.efficiency_ratio for r in group]),
        avg_adx=_avg([r.adx for r in group]),
        avg_reversal_frequency=_avg([r.reversal_frequency for r in group]),
        avg_persistence=_avg([r.persistence for r in group]),
    )


MIN_SAMPLES_PER_GROUP = 30


def _build_window_length_report(window_minutes: int, readings: List[IntradayWindowReading]) -> WindowLengthReport:
    relevant = [r for r in readings if r.window_minutes == window_minutes]
    labels_present = sorted({r.intraday_regime for r in relevant})
    group_stats = tuple(_group_stats(label, window_minutes, relevant) for label in labels_present)

    trend_groups = [gs for gs in group_stats if gs.label in (INTRADAY_TREND_UP, INTRADAY_TREND_DOWN)]
    range_stats = next((gs for gs in group_stats if gs.label == INTRADAY_RANGE), None)

    trend_n = sum(gs.n for gs in trend_groups)
    range_n = range_stats.n if range_stats else 0
    sufficient = trend_n >= MIN_SAMPLES_PER_GROUP and range_n >= MIN_SAMPLES_PER_GROUP

    evidence: List[str] = []
    separated_dims = 0
    checked_dims = 0

    if trend_groups and range_stats:
        # Combine TREND_UP + TREND_DOWN (direction-agnostic) for the
        # trend-strength comparison against RANGE -- direction doesn't
        # bear on "is this window more trend-like than that one."
        trend_er = _weighted_avg([(gs.avg_efficiency_ratio, gs.n) for gs in trend_groups])
        trend_adx = _weighted_avg([(gs.avg_adx, gs.n) for gs in trend_groups])
        trend_pers = _weighted_avg([(gs.avg_persistence, gs.n) for gs in trend_groups])
        trend_rev = _weighted_avg([(gs.avg_reversal_frequency, gs.n) for gs in trend_groups])

        for dim_name, trend_val, range_val, higher_means_trend in (
            ("efficiency_ratio", trend_er, range_stats.avg_efficiency_ratio, True),
            ("ADX", trend_adx, range_stats.avg_adx, True),
            ("persistence", trend_pers, range_stats.avg_persistence, True),
            ("reversal_frequency", trend_rev, range_stats.avg_reversal_frequency, False),
        ):
            if trend_val is None or range_val is None:
                evidence.append(f"{dim_name}: insufficient data -- not evaluated")
                continue
            checked_dims += 1
            separated = (trend_val > range_val) if higher_means_trend else (trend_val < range_val)
            evidence.append(
                f"{dim_name}: TREND(n={trend_n})={round(trend_val, 4)} vs RANGE(n={range_n})={range_val} "
                f"({'separated' if separated else 'NOT separated'})"
            )
            if separated:
                separated_dims += 1
    else:
        evidence.append("one or both of TREND_UP/TREND_DOWN/RANGE groups absent at this window length")

    if not sufficient:
        conclusion = (
            f"INSUFFICIENT SAMPLES at {window_minutes}min (trend n={trend_n}, range n={range_n}, "
            f"need >= {MIN_SAMPLES_PER_GROUP} each)."
        )
    elif checked_dims == 0:
        conclusion = f"INCONCLUSIVE at {window_minutes}min -- no dimension could be evaluated."
    elif separated_dims == checked_dims:
        conclusion = f"Meaningful separation across all {checked_dims} dimensions at {window_minutes}min."
    elif separated_dims >= checked_dims / 2:
        conclusion = f"PARTIAL separation ({separated_dims}/{checked_dims}) at {window_minutes}min."
    else:
        conclusion = f"NO meaningful separation ({separated_dims}/{checked_dims}) at {window_minutes}min."

    return WindowLengthReport(
        window_minutes=window_minutes, total_windows=len(relevant), group_stats=group_stats,
        separation_conclusion=conclusion, separation_evidence=tuple(evidence), sufficient_sample_size=sufficient,
    )


def _weighted_avg(pairs) -> Optional[float]:
    total_n = sum(n for v, n in pairs if v is not None)
    if total_n == 0:
        return None
    return sum(v * n for v, n in pairs if v is not None) / total_n


def combine_session_and_intraday(session_market_state, window_reading: IntradayWindowReading) -> dict:
    """Step 4's exact output shape -- session-level classification
    (Phase 20.1B, `bujji.mic_v0.models.MarketState`, UNCHANGED) plus
    the new intraday reading, side by side. Purely a validation-report
    convenience -- never persisted, never wired into a decision path."""
    return {
        "session_regime": session_market_state.market_regime,
        "intraday_regime": window_reading.intraday_regime,
        "window": window_reading.window_label,
        "evidence": {
            "efficiency_ratio": window_reading.efficiency_ratio,
            "adx": window_reading.adx,
        },
        "confidence": {"level": "LOW", "sample_size": 0},
    }


def build_intraday_validation_report(
    all_readings: List[IntradayWindowReading], total_days: int,
) -> IntradayValidationReport:
    window_reports = tuple(
        _build_window_length_report(wm, all_readings) for wm in WINDOW_LENGTHS_MINUTES
    )

    passing = [wr for wr in window_reports if wr.sufficient_sample_size and "Meaningful separation" in wr.separation_conclusion]
    overall_pass = len(passing) > 0

    if overall_pass:
        overall_conclusion = (
            f"MIC intraday classification shows meaningful separation at: "
            f"{', '.join(f'{wr.window_minutes}min' for wr in passing)}."
        )
    else:
        overall_conclusion = "MIC intraday validation inconclusive."

    return IntradayValidationReport(
        window_reports=window_reports, total_days_analyzed=total_days,
        overall_pass=overall_pass, overall_conclusion=overall_conclusion,
    )
