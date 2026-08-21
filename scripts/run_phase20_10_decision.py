#!/usr/bin/env python
"""Phase 20.10 -- Decision Orchestration against Phase 20.5's own
published strategy evidence and Phase 20.6/20.7/20.8/20.9's own real
scenarios. Does NOT rerun strategy discovery or optimization.
Read-only, no broker, no order capability.
"""
from __future__ import annotations

import sys

sys.path.insert(0, "/opt/bujji/app")


def _main():
    from bujji.mic_v0 import models as mic_models
    from bujji.opportunity_intelligence import MarketEnvironment, evaluate_opportunity
    from bujji.opportunity_ranking import OpportunityCandidate
    from bujji.capital_intelligence import assess_risk_allocation
    from bujji.opportunity_portfolio import rank_portfolio_choices
    from bujji.decision_orchestration import compose_decision, explain_decision
    from bujji.strategy_intelligence import StrategyEvidence, score_strategy

    TREND_FAVORABLE, TREND_UNFAVORABLE = ("TREND_UP", "TREND_DOWN"), ("RANGE",)
    MR_FAVORABLE, MR_UNFAVORABLE = ("RANGE",), ("TREND_UP", "TREND_DOWN")

    def alloc(evidence, environment, favorable, unfavorable):
        score = score_strategy(evidence)
        assessment = evaluate_opportunity(score, environment, favorable, unfavorable)
        return assess_risk_allocation(OpportunityCandidate(assessment=assessment))

    trend_evidence = StrategyEvidence(
        strategy_name="FAMILY_A_TREND_FOLLOWING", sample_size=11278, win_rate=0.665, profit_factor=2.30,
        net_expectancy=517.0, gross_expectancy=949.0,
        train_expectancy=512.0, validation_expectancy=481.0, out_of_sample_expectancy=573.0,
    )
    mr_evidence = StrategyEvidence(
        strategy_name="FAMILY_B_MEAN_REVERSION", sample_size=150, win_rate=0.0, profit_factor=0.0,
        net_expectancy=-1664.0, gross_expectancy=-1218.0,
        train_expectancy=-1375.0, validation_expectancy=-1806.0, out_of_sample_expectancy=-2260.0,
    )

    scenarios = [
        ("Strong Case: TREND_UP, all NORMAL", "TREND_UP", mic_models.RISK_NORMAL, mic_models.VOLATILITY_NORMAL, "NORMAL"),
        ("WATCH Case: TRANSITION regime", "TRANSITION", mic_models.RISK_NORMAL, mic_models.VOLATILITY_NORMAL, "NORMAL"),
        ("Extreme Risk Case: EXTREME risk", "TREND_UP", mic_models.RISK_EXTREME, mic_models.VOLATILITY_NORMAL, "NORMAL"),
    ]

    for label, regime, risk, vol, execp in scenarios:
        env = MarketEnvironment(mic_regime=regime, risk_state=risk, volatility_state=vol, execution_profile_name=execp)
        tf_alloc = alloc(trend_evidence, env, TREND_FAVORABLE, TREND_UNFAVORABLE)
        mr_alloc = alloc(mr_evidence, env, MR_FAVORABLE, MR_UNFAVORABLE)
        portfolio = rank_portfolio_choices([tf_alloc, mr_alloc])
        print(f"=== {label} ===")
        print(explain_decision(compose_decision(tf_alloc, portfolio)))
        print()
        print(explain_decision(compose_decision(mr_alloc, portfolio)))
        print()

    print("--- Missing intelligence case ---")
    print(explain_decision(compose_decision(None, None)))
    print()

    print("--- Proof: evidence_score constant across every scenario above ---")
    tf_score = score_strategy(trend_evidence).evidence_score
    mr_score = score_strategy(mr_evidence).evidence_score
    print(f"Trend Following evidence_score: {tf_score}")
    print(f"Mean Reversion evidence_score: {mr_score}")


if __name__ == "__main__":
    _main()
