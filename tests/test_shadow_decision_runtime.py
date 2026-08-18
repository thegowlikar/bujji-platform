"""Phase 20.11 -- Shadow Decision Runtime & Intelligence Observation
Layer tests."""
from __future__ import annotations

import os

import pytest

from bujji.mic_v0 import models as mic_models
from bujji.mic_v0.models import ConfidenceInfo, EventContext, MarketState
from bujji.opportunity_intelligence import MarketEnvironment, evaluate_opportunity
from bujji.opportunity_ranking import OpportunityCandidate
from bujji.capital_intelligence import assess_risk_allocation
from bujji.opportunity_portfolio import rank_portfolio_choices
from bujji.decision_orchestration import (
    BLOCKED, EXECUTABLE_CANDIDATE, INSUFFICIENT_INTELLIGENCE, NO_OPPORTUNITY, WATCH,
    compose_decision,
)
from bujji.strategy_intelligence import StrategyEvidence, score_strategy
from bujji.shadow_decision_runtime import (
    DecisionObservation, ShadowDecisionLog, build_session_summary,
    explain_observation, record_decision, run_shadow_cycle,
)

TREND_FAVORABLE, TREND_UNFAVORABLE = ("TREND_UP", "TREND_DOWN"), ("RANGE",)
MR_FAVORABLE, MR_UNFAVORABLE = ("RANGE",), ("TREND_UP", "TREND_DOWN")


def _evidence(name, n=1000, win_rate=0.6, pf=2.0, net=100.0, gross=150.0, train=100.0, validation=100.0, oos=100.0):
    return StrategyEvidence(
        strategy_name=name, sample_size=n, win_rate=win_rate, profit_factor=pf,
        net_expectancy=net, gross_expectancy=gross,
        train_expectancy=train, validation_expectancy=validation, out_of_sample_expectancy=oos,
    )


def _env(regime="TREND_UP", risk=mic_models.RISK_NORMAL, vol=mic_models.VOLATILITY_NORMAL, execution="NORMAL"):
    return MarketEnvironment(mic_regime=regime, risk_state=risk, volatility_state=vol, execution_profile_name=execution)


def _market_state(regime=mic_models.REGIME_TREND, risk=mic_models.RISK_NORMAL,
                   vol=mic_models.VOLATILITY_NORMAL, data_quality="SUFFICIENT",
                   as_of_time="2026-08-17T09:30:00+05:30"):
    return MarketState(
        as_of_time=as_of_time, market_regime=regime, volatility_state=vol, risk_state=risk,
        recommended_environment=mic_models.ENV_TREND_FOLLOWING, evidence=("test_evidence",),
        confidence=ConfidenceInfo(level=mic_models.CONFIDENCE_LOW, sample_size=0, method="test"),
        event_context=EventContext(), data_quality=data_quality,
    )


def _allocation(evidence, environment, favorable, unfavorable):
    score = score_strategy(evidence)
    assessment = evaluate_opportunity(score, environment, favorable, unfavorable)
    candidate = OpportunityCandidate(assessment=assessment)
    return assess_risk_allocation(candidate)


def _trend_following_strong(environment=None):
    ev = _evidence("FAMILY_A_TREND_FOLLOWING", n=11278, win_rate=0.665, pf=2.30, net=517.0, gross=949.0,
                    train=512.0, validation=481.0, oos=573.0)
    return _allocation(ev, environment or _env("TREND_UP"), TREND_FAVORABLE, TREND_UNFAVORABLE)


def _mean_reversion_failed():
    ev = _evidence("FAMILY_B_MEAN_REVERSION", n=150, win_rate=0.0, pf=0.0, net=-1664.0, gross=-1218.0,
                    train=-1375.0, validation=-1806.0, oos=-2260.0)
    return _allocation(ev, _env("RANGE"), MR_FAVORABLE, MR_UNFAVORABLE)


# --------------------------------------------------------------------- #
# 1. Decision recording
# --------------------------------------------------------------------- #
def test_every_decision_produces_an_observation():
    allocation = _trend_following_strong(_env("TREND_UP"))
    portfolio = rank_portfolio_choices([allocation])
    final_decision = compose_decision(allocation, portfolio)
    observation = run_shadow_cycle(_market_state(), final_decision, "2026-08-17T09:30:00+05:30")
    assert isinstance(observation, DecisionObservation)
    assert observation.decision_state == EXECUTABLE_CANDIDATE
    assert observation.candidate_strategy == "FAMILY_A_TREND_FOLLOWING"


# --------------------------------------------------------------------- #
# 2. No execution leakage
# --------------------------------------------------------------------- #
def test_no_trading_capability_in_package():
    import bujji.shadow_decision_runtime as pkg
    pkg_dir = os.path.dirname(pkg.__file__)
    forbidden = ("place_order", "modify_order", "cancel_order", "PaperBroker", "FyersBroker",
                 "entry_price", "exit_price", "quantity", "order_id")
    for fname in os.listdir(pkg_dir):
        if not fname.endswith(".py"):
            continue
        with open(os.path.join(pkg_dir, fname)) as f:
            source = f.read()
        for term in forbidden:
            assert term not in source, f"{fname} contains forbidden term {term!r}"


def test_no_broker_or_capital_module_imports():
    import bujji.shadow_decision_runtime as pkg
    pkg_dir = os.path.dirname(pkg.__file__)
    forbidden = ("import bujji.broker", "from bujji.broker", "import bujji.capital.", "from bujji.capital.")
    for fname in os.listdir(pkg_dir):
        if not fname.endswith(".py"):
            continue
        with open(os.path.join(pkg_dir, fname)) as f:
            source = f.read()
        for term in forbidden:
            assert term not in source, f"{fname} contains forbidden import {term!r}"


# --------------------------------------------------------------------- #
# 3. Data quality propagation
# --------------------------------------------------------------------- #
def test_insufficient_data_quality_propagates_honestly():
    allocation = _trend_following_strong(_env("TREND_UP"))
    portfolio = rank_portfolio_choices([allocation])
    final_decision = compose_decision(allocation, portfolio)
    bad_state = _market_state(data_quality="INSUFFICIENT")
    observation = run_shadow_cycle(bad_state, final_decision, "2026-08-17T09:30:00+05:30")
    assert observation.data_quality == "INSUFFICIENT"
    assert "data_quality" in explain_observation(observation).lower() or "INSUFFICIENT" in explain_observation(observation)


# --------------------------------------------------------------------- #
# 4. Explainability preservation
# --------------------------------------------------------------------- #
def test_reasons_survive_storage_and_retrieval():
    allocation = _trend_following_strong(_env("TRANSITION"))
    portfolio = rank_portfolio_choices([allocation])
    final_decision = compose_decision(allocation, portfolio)
    observation = run_shadow_cycle(_market_state(), final_decision, "2026-08-17T09:30:00+05:30")

    # Round-trip through to_dict() -- proves it's the STORED observation
    # being explained, not the live FinalDecision object.
    stored = observation.to_dict()
    assert stored["reason_codes"] == list(final_decision.positive) + list(final_decision.negative)
    assert stored["uncertainty"] == list(final_decision.unknown)

    text = explain_observation(observation)
    for reason in final_decision.positive + final_decision.negative:
        assert reason in text
    assert "Decision:" in text and "WATCH" in text


# --------------------------------------------------------------------- #
# 5. Multiple cycles
# --------------------------------------------------------------------- #
def test_same_session_can_produce_multiple_observations():
    log = ShadowDecisionLog()
    for regime in ("TREND_UP", "TRANSITION", "TREND_UP"):
        allocation = _trend_following_strong(_env(regime))
        portfolio = rank_portfolio_choices([allocation])
        final_decision = compose_decision(allocation, portfolio)
        obs = run_shadow_cycle(_market_state(), final_decision, "2026-08-17T09:30:00+05:30")
        log.record(obs)
    assert len(log.observations) == 3


# --------------------------------------------------------------------- #
# 6. Missing intelligence
# --------------------------------------------------------------------- #
def test_missing_intelligence_creates_honest_uncertainty():
    final_decision = compose_decision(None, None)
    observation = run_shadow_cycle(_market_state(), final_decision, "2026-08-17T09:30:00+05:30")
    assert observation.decision_state == INSUFFICIENT_INTELLIGENCE
    assert observation.confidence is None
    assert observation.priority_score is None
    assert observation.allocation_class is None
    assert any("Missing allocation" in u for u in observation.uncertainty)
    assert any("Missing portfolio" in u for u in observation.uncertainty)


def test_record_decision_rejects_wrong_type():
    with pytest.raises(TypeError):
        record_decision("not an observation")


# --------------------------------------------------------------------- #
# 7. Session summary correctness
# --------------------------------------------------------------------- #
def test_session_summary_counts_match_observations():
    log = ShadowDecisionLog()

    strong = _trend_following_strong(_env("TREND_UP"))
    portfolio1 = rank_portfolio_choices([strong])
    log.record(run_shadow_cycle(_market_state(), compose_decision(strong, portfolio1), "2026-08-17T09:15:00+05:30"))

    watch = _trend_following_strong(_env("TRANSITION"))
    portfolio2 = rank_portfolio_choices([watch])
    log.record(run_shadow_cycle(_market_state(), compose_decision(watch, portfolio2), "2026-08-17T09:20:00+05:30"))

    failed = _mean_reversion_failed()
    portfolio3 = rank_portfolio_choices([failed])
    log.record(run_shadow_cycle(_market_state(), compose_decision(failed, portfolio3), "2026-08-17T09:25:00+05:30"))

    summary = log.summarize("2026-08-17")
    assert summary.number_of_cycles == 3
    assert summary.candidate_count == 2  # FAMILY_A + FAMILY_B, across 3 cycles
    assert sum(summary.decision_distribution.values()) == 3
    assert summary.watch_count == 1
    assert summary.no_opportunity_count == 1
    assert summary.blocked_count == 0
    assert summary.highest_confidence_decision is not None

    # Cross-check against build_session_summary() called directly, same inputs.
    direct = build_session_summary("2026-08-17", log.observations)
    assert direct == summary
