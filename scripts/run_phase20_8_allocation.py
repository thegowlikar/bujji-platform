#!/usr/bin/env python
"""Phase 20.8 -- Capital Allocation Intelligence against Phase 20.5's
own published strategy evidence and Phase 20.6/20.7's own qualification/
ranking scenarios. Does NOT rerun strategy discovery or optimization.
Read-only, no broker, no order/sizing capability.
"""
from __future__ import annotations

import sys

sys.path.insert(0, "/opt/bujji/app")


def _main():
    from bujji.mic_v0 import models as mic_models
    from bujji.opportunity_intelligence import MarketEnvironment, evaluate_opportunity
    from bujji.opportunity_ranking import OpportunityCandidate, rank_opportunities
    from bujji.capital_intelligence import assess_ranking_result, explain_allocation
    from bujji.strategy_intelligence import StrategyEvidence, score_strategy

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
    trend_score = score_strategy(trend_following)
    mr_score = score_strategy(mean_reversion)
    TREND_FAVORABLE, TREND_UNFAVORABLE = ("TREND_UP", "TREND_DOWN"), ("RANGE",)
    MR_FAVORABLE, MR_UNFAVORABLE = ("RANGE",), ("TREND_UP", "TREND_DOWN")

    scenarios = [
        ("TREND_UP, NORMAL everything", "TREND_UP", mic_models.RISK_NORMAL, mic_models.VOLATILITY_NORMAL, "NORMAL"),
        ("TRANSITION regime", "TRANSITION", mic_models.RISK_NORMAL, mic_models.VOLATILITY_NORMAL, "NORMAL"),
        ("EXTREME risk", "TREND_UP", mic_models.RISK_EXTREME, mic_models.VOLATILITY_NORMAL, "NORMAL"),
        ("STRESS execution", "TREND_UP", mic_models.RISK_NORMAL, mic_models.VOLATILITY_NORMAL, "STRESS"),
        ("EXTREME execution", "TREND_UP", mic_models.RISK_NORMAL, mic_models.VOLATILITY_NORMAL, "EXTREME"),
    ]

    for label, regime, risk, vol, execp in scenarios:
        env = MarketEnvironment(mic_regime=regime, risk_state=risk, volatility_state=vol, execution_profile_name=execp)
        candidates = [
            OpportunityCandidate(assessment=evaluate_opportunity(trend_score, env, TREND_FAVORABLE, TREND_UNFAVORABLE)),
            OpportunityCandidate(assessment=evaluate_opportunity(mr_score, env, MR_FAVORABLE, MR_UNFAVORABLE)),
        ]
        ranking = rank_opportunities(candidates)
        assessments = assess_ranking_result(ranking)
        print(f"=== {label} ===")
        for a in assessments:
            print(explain_allocation(a))
        print()

    print("--- Proof: evidence_score constant across every scenario above ---")
    print(f"Trend Following evidence_score: {trend_score.evidence_score}")
    print(f"Mean Reversion evidence_score: {mr_score.evidence_score}")


if __name__ == "__main__":
    _main()
