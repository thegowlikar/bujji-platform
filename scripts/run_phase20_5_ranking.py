#!/usr/bin/env python
"""Phase 20.5 -- Strategy Intelligence ranking against Phase 20.4's
own real-data research findings. Does NOT rerun strategy discovery --
`StrategyEvidence` below is taken verbatim from
docs/PHASE_20_4_STRATEGY_FORECAST_VALIDATION_REPORT.md Section 6/8.
Read-only, no broker, no order capability.
"""
from __future__ import annotations

import sys

sys.path.insert(0, "/opt/bujji/app")


def _main():
    from bujji.strategy_intelligence import MarketContext, StrategyEvidence, rank_strategies, score_strategy

    # Verbatim from PHASE_20_4_STRATEGY_FORECAST_VALIDATION_REPORT.md Section 6 & 8.
    trend_following = StrategyEvidence(
        strategy_name="FAMILY_A_TREND_FOLLOWING", sample_size=11278, win_rate=0.665, profit_factor=2.30,
        net_expectancy=517.0, gross_expectancy=949.0,
        train_expectancy=512.0, validation_expectancy=481.0, out_of_sample_expectancy=573.0,
    )
    mean_reversion = StrategyEvidence(
        strategy_name="FAMILY_B_MEAN_REVERSION", sample_size=150, win_rate=0.0, profit_factor=0.0,
        net_expectancy=-1664.0, gross_expectancy=-1218.0,
        train_expectancy=-1375.0, validation_expectancy=-1806.0, out_of_sample_expectancy=-2260.0,
    )

    print("=== No MIC context (evidence-only baseline) ===\n")
    report = rank_strategies([score_strategy(trend_following), score_strategy(mean_reversion)])
    print(report.render())

    print("=== With MIC context: current regime = TREND_UP (favorable for Trend Following) ===\n")
    ctx_favorable_trend = MarketContext(
        mic_regime="TREND_UP",
        favorable_regimes=("TREND_UP", "TREND_DOWN"), unfavorable_regimes=("RANGE", "TRANSITION"),
    )
    ctx_unfavorable_mr = MarketContext(
        mic_regime="TREND_UP", favorable_regimes=("RANGE",), unfavorable_regimes=("TREND_UP", "TREND_DOWN"),
    )
    report2 = rank_strategies([
        score_strategy(trend_following, ctx_favorable_trend),
        score_strategy(mean_reversion, ctx_unfavorable_mr),
    ])
    print(report2.render())

    print("=== With MIC context: current regime = TRANSITION (unfavorable for both) ===\n")
    ctx_transition = MarketContext(
        mic_regime="TRANSITION", favorable_regimes=("TREND_UP", "TREND_DOWN"), unfavorable_regimes=("TRANSITION",),
    )
    report3 = rank_strategies([
        score_strategy(trend_following, ctx_transition),
        score_strategy(mean_reversion, ctx_transition),
    ])
    print(report3.render())

    print("--- Proof: evidence_score is identical across all three MIC contexts for each strategy ---")
    s1 = score_strategy(trend_following, None)
    s2 = score_strategy(trend_following, ctx_favorable_trend)
    s3 = score_strategy(trend_following, ctx_transition)
    print(f"Trend Following evidence_score: no_context={s1.evidence_score} favorable={s2.evidence_score} "
          f"transition={s3.evidence_score}  (identical: {s1.evidence_score == s2.evidence_score == s3.evidence_score})")
    print(f"Trend Following confidence:     no_context={s1.confidence} favorable={s2.confidence} "
          f"transition={s3.confidence}")


if __name__ == "__main__":
    _main()
