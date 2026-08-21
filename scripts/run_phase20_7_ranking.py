#!/usr/bin/env python
"""Phase 20.7 -- Opportunity Ranking against Phase 20.5's own published
strategy evidence and Phase 20.6's own qualification scenarios. Does
NOT rerun strategy discovery, optimization, or backtesting. Read-only,
no broker, no order capability.
"""
from __future__ import annotations

import sys

sys.path.insert(0, "/opt/bujji/app")


def _main():
    from bujji.mic_v0 import models as mic_models
    from bujji.opportunity_intelligence import MarketEnvironment, evaluate_opportunity
    from bujji.opportunity_ranking import OpportunityCandidate, explain_ranking, rank_opportunities
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

    TREND_FAVORABLE, TREND_UNFAVORABLE = ("TREND_UP", "TREND_DOWN"), ("RANGE",)
    MR_FAVORABLE, MR_UNFAVORABLE = ("RANGE",), ("TREND_UP", "TREND_DOWN")

    trend_score = score_strategy(trend_following)
    mr_score = score_strategy(mean_reversion)

    print("=== Scenario 1: TREND_UP, NORMAL everything (Trend Following ideal; Mean Reversion off-regime) ===\n")
    env1 = MarketEnvironment(mic_regime="TREND_UP", risk_state=mic_models.RISK_NORMAL,
                              volatility_state=mic_models.VOLATILITY_NORMAL, execution_profile_name="NORMAL")
    candidates1 = [
        OpportunityCandidate(assessment=evaluate_opportunity(trend_score, env1, TREND_FAVORABLE, TREND_UNFAVORABLE)),
        OpportunityCandidate(assessment=evaluate_opportunity(mr_score, env1, MR_FAVORABLE, MR_UNFAVORABLE)),
    ]
    result1 = rank_opportunities(candidates1)
    print(result1.render())
    print(explain_ranking(result1))

    print("\n=== Scenario 2: TRANSITION regime (Trend Following WATCH; Mean Reversion still insufficient) ===\n")
    env2 = MarketEnvironment(mic_regime="TRANSITION", risk_state=mic_models.RISK_NORMAL,
                              volatility_state=mic_models.VOLATILITY_NORMAL, execution_profile_name="NORMAL")
    candidates2 = [
        OpportunityCandidate(assessment=evaluate_opportunity(trend_score, env2, TREND_FAVORABLE, TREND_UNFAVORABLE)),
        OpportunityCandidate(assessment=evaluate_opportunity(mr_score, env2, MR_FAVORABLE, MR_UNFAVORABLE)),
    ]
    result2 = rank_opportunities(candidates2)
    print(result2.render())

    print("\n=== Scenario 3: EXTREME risk (Trend Following BLOCKED; Mean Reversion still insufficient -- NO_QUALIFIED_OPPORTUNITY) ===\n")
    env3 = MarketEnvironment(mic_regime="TREND_UP", risk_state=mic_models.RISK_EXTREME,
                              volatility_state=mic_models.VOLATILITY_NORMAL, execution_profile_name="NORMAL")
    candidates3 = [
        OpportunityCandidate(assessment=evaluate_opportunity(trend_score, env3, TREND_FAVORABLE, TREND_UNFAVORABLE)),
        OpportunityCandidate(assessment=evaluate_opportunity(mr_score, env3, MR_FAVORABLE, MR_UNFAVORABLE)),
    ]
    result3 = rank_opportunities(candidates3)
    print(result3.render())
    print(f"Ranked opportunities: {len(result3.ranked)} -- "
          f"{'NO QUALIFIED OPPORTUNITY (correct)' if not result3.ranked else 'unexpected'}")

    print("\n--- Proof: evidence_score constant across all three scenarios ---")
    print(f"Trend Following evidence_score: {trend_score.evidence_score} (identical in every scenario above)")
    print(f"Mean Reversion evidence_score: {mr_score.evidence_score} (identical in every scenario above)")


if __name__ == "__main__":
    _main()
