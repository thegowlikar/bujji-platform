#!/usr/bin/env python
"""Phase 20.9 -- Portfolio Intelligence against Phase 20.5's own
published strategy evidence (real Trend Following / Mean Reversion)
plus a synthetic hypothetically-strong Mean Reversion, to exercise
conflict resolution logic beyond what the real (failed) evidence alone
can show. Does NOT rerun strategy discovery or optimization. Read-only,
no broker, no order/sizing capability.
"""
from __future__ import annotations

import sys

sys.path.insert(0, "/opt/bujji/app")


def _main():
    from bujji.mic_v0 import models as mic_models
    from bujji.opportunity_intelligence import MarketEnvironment, evaluate_opportunity
    from bujji.opportunity_ranking import OpportunityCandidate
    from bujji.capital_intelligence import assess_risk_allocation
    from bujji.opportunity_portfolio import explain_portfolio_decision, rank_portfolio_choices
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
    mr_real_failed_evidence = StrategyEvidence(
        strategy_name="FAMILY_B_MEAN_REVERSION", sample_size=150, win_rate=0.0, profit_factor=0.0,
        net_expectancy=-1664.0, gross_expectancy=-1218.0,
        train_expectancy=-1375.0, validation_expectancy=-1806.0, out_of_sample_expectancy=-2260.0,
    )
    # Synthetic hypothetically-strong MR, used ONLY to demonstrate conflict
    # resolution when both sides have real evidence -- clearly labeled, never
    # presented as Phase 20.4's actual (failed) Mean Reversion finding.
    mr_hypothetical_strong_evidence = StrategyEvidence(
        strategy_name="FAMILY_B_MEAN_REVERSION", sample_size=1500, win_rate=0.62, profit_factor=2.0,
        net_expectancy=300.0, gross_expectancy=450.0,
        train_expectancy=300.0, validation_expectancy=280.0, out_of_sample_expectancy=310.0,
    )

    print("=== Scenario 1: Real evidence -- TREND_UP regime ===")
    env1 = MarketEnvironment(mic_regime="TREND_UP", risk_state=mic_models.RISK_NORMAL,
                              volatility_state=mic_models.VOLATILITY_NORMAL, execution_profile_name="NORMAL")
    tf1 = alloc(trend_evidence, env1, TREND_FAVORABLE, TREND_UNFAVORABLE)
    mr1 = alloc(mr_real_failed_evidence, env1, MR_FAVORABLE, MR_UNFAVORABLE)
    decision1 = rank_portfolio_choices([tf1, mr1])
    print(explain_portfolio_decision(decision1))
    print()

    print("=== Scenario 2 (SYNTHETIC, for conflict-logic demonstration only): ===")
    print("=== Both Trend Following (real) and a HYPOTHETICAL strong Mean Reversion, TREND_UP regime ===")
    env2 = MarketEnvironment(mic_regime="TREND_UP", risk_state=mic_models.RISK_NORMAL,
                              volatility_state=mic_models.VOLATILITY_NORMAL, execution_profile_name="NORMAL")
    tf2 = alloc(trend_evidence, env2, TREND_FAVORABLE, TREND_UNFAVORABLE)
    mr2 = alloc(mr_hypothetical_strong_evidence, env2, MR_FAVORABLE, MR_UNFAVORABLE)
    decision2 = rank_portfolio_choices([tf2, mr2])
    print(explain_portfolio_decision(decision2))
    print()

    print("--- Proof: evidence_score constant regardless of portfolio outcome ---")
    print(f"Trend Following evidence_score: {tf1.candidate.assessment.strategy_score.evidence_score} "
          f"(scenario 1) / {tf2.candidate.assessment.strategy_score.evidence_score} (scenario 2)")


if __name__ == "__main__":
    _main()
