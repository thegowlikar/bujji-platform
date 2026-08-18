"""Strategy Research Engine + Attribution Framework -- Phase 20.3.

Answers "given a market condition, can Bujji select an appropriate
strategy and determine whether the decision was correct?" -- NOT
"is this strategy profitable." Two Cycle-1 families only (trend
following, mean reversion), driven exclusively by MIC v0's already-
validated (Phase 20.1C) intraday classification. No new intelligence
layer: `bujji.mic_v0_validation.intraday_validation.classify_intraday_window`
is reused unmodified. No new execution layer: `bujji.execution_backtest.
driver.simulate_round_trip_trade` (Phase 20.2, itself built on the
unmodified Gate F.2 stack and the Phase 20.2.1-corrected net-P&L
accounting) is reused unmodified. This package adds only: a formal
`StrategyFamily` interface, a declarative eligibility matrix, and an
attribution engine -- no order placement, no broker dependency.

Phase 20.4 adds: two concrete, non-lookahead, indicator-based signal
hypotheses (moving-average alignment for Trend Following, Bollinger
overshoot fade for Mean Reversion, both reusing `bujji.market_timeseries.
indicators` unmodified), a `forecast_correct` attribution axis
separating "was the direction call right" from "was it profitable
after costs", and `stats`/`periods` modules for performance statistics
and train/validation/out-of-sample stability checking.

See docs/PHASE_20_3_STRATEGY_RESEARCH_REPORT.md and
docs/PHASE_20_4_STRATEGY_FORECAST_VALIDATION_REPORT.md.
"""
from .models import (
    ATTRIBUTION_A_MIC_CORRECT_STRATEGY_WORKED,
    ATTRIBUTION_B_MIC_CORRECT_STRATEGY_FAILED,
    ATTRIBUTION_C_MIC_WRONG_STRATEGY_WORKED,
    ATTRIBUTION_D_MIC_WRONG_STRATEGY_FAILED,
    AttributedTrade,
    StrategyFamily,
)
from .eligibility import ELIGIBILITY_MATRIX, eligible_family_for_regime
from .families import MEAN_REVERSION, TREND_FOLLOWING, ALL_FAMILIES
from .periods import OUT_OF_SAMPLE, TRAIN, VALIDATION, period_for_date
from .research_driver import run_research_day
from .signals import (
    generate_bollinger_reversion_signal,
    generate_ma_alignment_signal,
    generate_mean_reversion_signal,
    generate_trend_following_signal,
)
from .stats import PerformanceStats, compute_performance_stats

__all__ = [
    "StrategyFamily", "AttributedTrade",
    "ATTRIBUTION_A_MIC_CORRECT_STRATEGY_WORKED", "ATTRIBUTION_B_MIC_CORRECT_STRATEGY_FAILED",
    "ATTRIBUTION_C_MIC_WRONG_STRATEGY_WORKED", "ATTRIBUTION_D_MIC_WRONG_STRATEGY_FAILED",
    "ELIGIBILITY_MATRIX", "eligible_family_for_regime",
    "TREND_FOLLOWING", "MEAN_REVERSION", "ALL_FAMILIES",
    "run_research_day",
    "generate_trend_following_signal", "generate_mean_reversion_signal",
    "generate_ma_alignment_signal", "generate_bollinger_reversion_signal",
    "TRAIN", "VALIDATION", "OUT_OF_SAMPLE", "period_for_date",
    "PerformanceStats", "compute_performance_stats",
]
