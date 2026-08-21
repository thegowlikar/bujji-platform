"""bujji.mic_v0_validation.validation — Phase 20.1B.

Per real trading day: classify via `mic_v0` (which itself reuses
`regime_brain` unmodified), then independently compute ADX and two
NEW oscillation/persistence metrics from the SAME candles — never
from the classification, never from any strategy outcome. Aggregates
by regime label and reports whether TREND and RANGE days show real,
measured separation.
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional, Sequence

from bujji.core.models import Candle as CoreCandle
from bujji.intelligence.context import EXECUTION_MODE_HISTORICAL_REPLAY, IntelligenceContext
from bujji.market_timeseries.indicators import adx as compute_adx
from bujji.market_timeseries.models import Candle as TSCandle
from bujji.mic_v0.engine import compose_market_state
from bujji.mic_v0.models import REGIME_RANGE, REGIME_TREND, REGIME_UNCLEAR

from .models import DayClassification, RegimeGroupStats, ValidationReport

MIN_CANDLES_PER_DAY = 6


def _reversal_frequency(closes: Sequence[float]) -> Optional[float]:
    """Fraction of consecutive candle-to-candle moves that reverse
    direction (a sign change in close-to-close delta). Higher = more
    oscillating/mean-reverting. Independent of, and NOT derived from,
    efficiency ratio or ADX."""
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    deltas = [d for d in deltas if d != 0]
    if len(deltas) < 2:
        return None
    reversals = sum(1 for i in range(1, len(deltas)) if (deltas[i] > 0) != (deltas[i - 1] > 0))
    return reversals / (len(deltas) - 1)


def _persistence(closes: Sequence[float]) -> Optional[float]:
    """Average length of consecutive same-direction runs. Higher =
    more trending/persistent. The natural complement of reversal
    frequency, computed independently from the same raw deltas."""
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    deltas = [d for d in deltas if d != 0]
    if len(deltas) < 2:
        return None
    run_lengths: List[int] = []
    current_run = 1
    for i in range(1, len(deltas)):
        if (deltas[i] > 0) == (deltas[i - 1] > 0):
            current_run += 1
        else:
            run_lengths.append(current_run)
            current_run = 1
    run_lengths.append(current_run)
    return sum(run_lengths) / len(run_lengths)


def classify_day(
    date_str: str, day_rows: List, current_vix: float, trailing_vix: List[float],
) -> Optional[DayClassification]:
    """`day_rows`: real `HistoricalObservation` rows for ONE trading
    day, any order, RESOLUTION_FIVE_MINUTE, one instrument. Returns
    None if there isn't enough real data for this day -- never a
    fabricated classification."""
    if len(day_rows) < MIN_CANDLES_PER_DAY:
        return None

    sorted_rows = sorted(day_rows, key=lambda r: r.observation.identity.timestamp)
    core_candles = [
        CoreCandle(
            timestamp=datetime.fromisoformat(r.observation.identity.timestamp),
            open=r.payload["open"], high=r.payload["high"], low=r.payload["low"], close=r.payload["close"],
            volume=r.payload.get("volume") or 0.0,
        )
        for r in sorted_rows
    ]
    ts_candles = [
        TSCandle(
            instrument="VALIDATION", kind="FUTURES", interval="FIVE_MINUTE",
            window_start=r.observation.identity.timestamp, window_end=r.observation.identity.timestamp,
            open=r.payload["open"], high=r.payload["high"], low=r.payload["low"], close=r.payload["close"],
            volume=r.payload.get("volume"), tick_count=1,
        )
        for r in sorted_rows
    ]
    closes = [c.close for c in core_candles]

    as_of = core_candles[-1].timestamp
    context = IntelligenceContext(as_of_time=as_of, execution_mode=EXECUTION_MODE_HISTORICAL_REPLAY)
    state = compose_market_state(core_candles, current_vix, trailing_vix, context)

    return DayClassification(
        date=date_str,
        market_regime=state.market_regime,
        adx=compute_adx(ts_candles, period=min(14, len(ts_candles) // 2 - 1)) if len(ts_candles) >= 4 else None,
        efficiency_ratio=next(
            (float(e.split("=")[1]) for e in state.evidence if e.startswith("regime_brain.efficiency_ratio=")),
            None,
        ),
        reversal_frequency=_reversal_frequency(closes),
        persistence=_persistence(closes),
        candles_used=len(core_candles),
    )


def _group_stats(label: str, classifications: List[DayClassification]) -> RegimeGroupStats:
    group = [c for c in classifications if c.market_regime == label]

    def _avg(values: List[Optional[float]]) -> Optional[float]:
        real = [v for v in values if v is not None]
        return round(sum(real) / len(real), 4) if real else None

    return RegimeGroupStats(
        label=label, n=len(group),
        avg_adx=_avg([c.adx for c in group]),
        avg_efficiency_ratio=_avg([c.efficiency_ratio for c in group]),
        avg_reversal_frequency=_avg([c.reversal_frequency for c in group]),
        avg_persistence=_avg([c.persistence for c in group]),
    )


def build_validation_report(classifications: List[DayClassification]) -> ValidationReport:
    trend_stats = _group_stats(REGIME_TREND, classifications)
    range_stats = _group_stats(REGIME_RANGE, classifications)
    unclear_stats = _group_stats(REGIME_UNCLEAR, classifications)

    evidence: List[str] = []
    separated_dims = 0
    checked_dims = 0

    for dim_name, trend_val, range_val, higher_means_trend in (
        ("ADX", trend_stats.avg_adx, range_stats.avg_adx, True),
        ("efficiency_ratio", trend_stats.avg_efficiency_ratio, range_stats.avg_efficiency_ratio, True),
        ("persistence", trend_stats.avg_persistence, range_stats.avg_persistence, True),
        ("reversal_frequency", trend_stats.avg_reversal_frequency, range_stats.avg_reversal_frequency, False),
    ):
        if trend_val is None or range_val is None:
            evidence.append(f"{dim_name}: insufficient data in one or both groups -- not evaluated")
            continue
        checked_dims += 1
        separated = (trend_val > range_val) if higher_means_trend else (trend_val < range_val)
        evidence.append(
            f"{dim_name}: TREND={trend_val} vs RANGE={range_val} "
            f"({'separated as expected' if separated else 'NOT separated as expected'})"
        )
        if separated:
            separated_dims += 1

    if checked_dims == 0:
        conclusion = "INCONCLUSIVE -- insufficient real samples in one or both groups to evaluate any dimension."
    elif trend_stats.n < 20 or range_stats.n < 20:
        conclusion = (
            f"PRELIMINARY -- {separated_dims}/{checked_dims} dimensions show the expected separation, "
            f"but sample sizes (TREND n={trend_stats.n}, RANGE n={range_stats.n}) are small; "
            f"treat as directional, not statistically decisive."
        )
    elif separated_dims == checked_dims:
        conclusion = (
            f"MIC classifications show meaningful, consistent separation across all {checked_dims} "
            f"independent dimensions checked (TREND n={trend_stats.n}, RANGE n={range_stats.n})."
        )
    elif separated_dims >= checked_dims / 2:
        conclusion = (
            f"MIC classifications show PARTIAL separation ({separated_dims}/{checked_dims} dimensions) -- "
            f"some real signal, but not unambiguous across every independent measure."
        )
    else:
        conclusion = (
            f"MIC classifications do NOT show meaningful separation ({separated_dims}/{checked_dims} "
            f"dimensions) -- the regime labels may not correspond to real, measurably different market "
            f"behavior. Do not proceed to strategy selection on this classifier without investigation."
        )

    return ValidationReport(
        trend_stats=trend_stats, range_stats=range_stats, unclear_stats=unclear_stats,
        total_days=len(classifications), separation_conclusion=conclusion,
        separation_evidence=tuple(evidence),
    )
