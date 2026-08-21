#!/usr/bin/env python
"""Phase 20.6 -- Opportunity Qualification, evaluated against Phase
20.5's own strategy evidence (verbatim from
docs/PHASE_20_5_STRATEGY_INTELLIGENCE_REPORT.md, itself verbatim from
Phase 20.4). Does NOT rerun strategy discovery. Read-only, no broker,
no order capability.
"""
from __future__ import annotations

import sys

sys.path.insert(0, "/opt/bujji/app")


def _main():
    from bujji.mic_v0 import models as mic_models
    from bujji.opportunity_intelligence import MarketEnvironment, evaluate_opportunity, qualification_rules
    from bujji.strategy_intelligence import StrategyEvidence, score_strategy

    print("=== Qualification rules (fixed order) ===")
    for r in qualification_rules():
        print(f"  {r}")
    print()

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

    # Favorable/unfavorable regimes: exactly what Phase 20.3's own eligibility
    # matrix declares (bujji.strategy_research.eligibility.ELIGIBILITY_MATRIX),
    # reused as data here, not re-derived.
    TREND_FAVORABLE = ("TREND_UP", "TREND_DOWN")
    TREND_UNFAVORABLE = ("RANGE",)
    MR_FAVORABLE = ("RANGE",)
    MR_UNFAVORABLE = ("TREND_UP", "TREND_DOWN")

    scenarios = [
        ("TREND_UP, NORMAL risk, NORMAL vol, NORMAL execution", "TREND_UP", mic_models.RISK_NORMAL, mic_models.VOLATILITY_NORMAL, "NORMAL"),
        ("TREND_DOWN, NORMAL risk, NORMAL vol, NORMAL execution", "TREND_DOWN", mic_models.RISK_NORMAL, mic_models.VOLATILITY_NORMAL, "NORMAL"),
        ("RANGE, NORMAL risk, NORMAL vol, NORMAL execution", "RANGE", mic_models.RISK_NORMAL, mic_models.VOLATILITY_NORMAL, "NORMAL"),
        ("TRANSITION, NORMAL risk, NORMAL vol, NORMAL execution", "TRANSITION", mic_models.RISK_NORMAL, mic_models.VOLATILITY_NORMAL, "NORMAL"),
        ("TREND_UP, ELEVATED risk", "TREND_UP", mic_models.RISK_ELEVATED, mic_models.VOLATILITY_NORMAL, "NORMAL"),
        ("TREND_UP, EXTREME risk", "TREND_UP", mic_models.RISK_EXTREME, mic_models.VOLATILITY_NORMAL, "NORMAL"),
        ("TREND_UP, HIGH volatility", "TREND_UP", mic_models.RISK_NORMAL, mic_models.VOLATILITY_HIGH, "NORMAL"),
        ("TREND_UP, STRESS execution", "TREND_UP", mic_models.RISK_NORMAL, mic_models.VOLATILITY_NORMAL, "STRESS"),
        ("TREND_UP, EXTREME execution", "TREND_UP", mic_models.RISK_NORMAL, mic_models.VOLATILITY_NORMAL, "EXTREME"),
    ]

    print("=== Trend Following across scenarios ===\n")
    for label, regime, risk, vol, execp in scenarios:
        env = MarketEnvironment(mic_regime=regime, risk_state=risk, volatility_state=vol, execution_profile_name=execp)
        assessment = evaluate_opportunity(trend_score, env, TREND_FAVORABLE, TREND_UNFAVORABLE)
        print(f"[{label}] -> {assessment.decision.state}  ({assessment.decision.reasons[0].code}: {assessment.decision.reasons[0].detail})")

    print("\n=== Mean Reversion across the SAME scenarios (its own favorable/unfavorable sets) ===\n")
    for label, regime, risk, vol, execp in scenarios:
        env = MarketEnvironment(mic_regime=regime, risk_state=risk, volatility_state=vol, execution_profile_name=execp)
        assessment = evaluate_opportunity(mr_score, env, MR_FAVORABLE, MR_UNFAVORABLE)
        print(f"[{label}] -> {assessment.decision.state}  ({assessment.decision.reasons[0].code}: {assessment.decision.reasons[0].detail})")

    print("\n--- Proof: evidence unaffected across every scenario above ---")
    print(f"Trend Following evidence_score constant at: {trend_score.evidence_score}")
    print(f"Mean Reversion evidence_score constant at: {mr_score.evidence_score}")


if __name__ == "__main__":
    _main()
