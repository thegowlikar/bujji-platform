"""Phase 20.12 -- Shadow Market Campaign & Runtime Validation
Framework tests."""
from __future__ import annotations

import os

import pytest

from bujji.mic_v0 import models as mic_models
from bujji.mic_v0.models import ConfidenceInfo, EventContext, MarketState
from bujji.opportunity_intelligence import MarketEnvironment, evaluate_opportunity
from bujji.opportunity_ranking import OpportunityCandidate
from bujji.capital_intelligence import assess_risk_allocation
from bujji.opportunity_portfolio import rank_portfolio_choices
from bujji.decision_orchestration import compose_decision
from bujji.strategy_intelligence import StrategyEvidence, score_strategy
from bujji.shadow_decision_runtime import ShadowDecisionLog, run_shadow_cycle
from bujji.shadow_market_campaign import (
    CampaignSession, build_campaign_report, build_campaign_session,
    collect_observation, validate_session_behavior,
)
from bujji.shadow_market_campaign.models import HEALTH_DEGRADED, HEALTH_EMPTY, HEALTH_HEALTHY

TREND_FAVORABLE, TREND_UNFAVORABLE = ("TREND_UP", "TREND_DOWN"), ("RANGE",)
MR_FAVORABLE, MR_UNFAVORABLE = ("RANGE",), ("TREND_UP", "TREND_DOWN")

MARKET_OPEN = "2026-08-17T09:15:00+05:30"
MARKET_CLOSE = "2026-08-17T15:30:00+05:30"


def _evidence(name, n=1000, win_rate=0.6, pf=2.0, net=100.0, gross=150.0, train=100.0, validation=100.0, oos=100.0):
    return StrategyEvidence(
        strategy_name=name, sample_size=n, win_rate=win_rate, profit_factor=pf,
        net_expectancy=net, gross_expectancy=gross,
        train_expectancy=train, validation_expectancy=validation, out_of_sample_expectancy=oos,
    )


def _env(regime="TREND_UP", risk=mic_models.RISK_NORMAL, vol=mic_models.VOLATILITY_NORMAL, execution="NORMAL"):
    return MarketEnvironment(mic_regime=regime, risk_state=risk, volatility_state=vol, execution_profile_name=execution)


def _market_state(regime=mic_models.REGIME_TREND, data_quality="SUFFICIENT", as_of_time=MARKET_OPEN):
    return MarketState(
        as_of_time=as_of_time, market_regime=regime, volatility_state=mic_models.VOLATILITY_NORMAL,
        risk_state=mic_models.RISK_NORMAL, recommended_environment=mic_models.ENV_TREND_FOLLOWING,
        evidence=("test_evidence",),
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


def _mean_reversion_failed(environment=None):
    ev = _evidence("FAMILY_B_MEAN_REVERSION", n=150, win_rate=0.0, pf=0.0, net=-1664.0, gross=-1218.0,
                    train=-1375.0, validation=-1806.0, oos=-2260.0)
    return _allocation(ev, environment or _env("RANGE"), MR_FAVORABLE, MR_UNFAVORABLE)


def _observation_for(allocation, timestamp, market_state=None):
    portfolio = rank_portfolio_choices([allocation])
    final_decision = compose_decision(allocation, portfolio)
    return run_shadow_cycle(market_state or _market_state(as_of_time=timestamp), final_decision, timestamp)


# --------------------------------------------------------------------- #
# 1. Session lifecycle
# --------------------------------------------------------------------- #
def test_campaign_session_opens_and_closes_correctly():
    log = ShadowDecisionLog()
    log.record(_observation_for(_trend_following_strong(_env("TREND_UP")), "2026-08-17T09:30:00+05:30"))
    session = build_campaign_session("2026-08-17", MARKET_OPEN, MARKET_CLOSE, log)
    assert session.session_date == "2026-08-17"
    assert session.market_open_time == MARKET_OPEN
    assert session.market_close_time == MARKET_CLOSE
    assert session.observation_count == 1
    assert session.decision_count == session.observation_count


# --------------------------------------------------------------------- #
# 2. Observation collection
# --------------------------------------------------------------------- #
def test_every_shadow_decision_becomes_campaign_artifact():
    log = ShadowDecisionLog()
    obs1 = _observation_for(_trend_following_strong(_env("TREND_UP")), "2026-08-17T09:30:00+05:30")
    obs2 = _observation_for(_mean_reversion_failed(), "2026-08-17T09:30:00+05:30")
    collect_observation(log, obs1)
    collect_observation(log, obs2)
    assert len(log.observations) == 2
    session = build_campaign_session("2026-08-17", MARKET_OPEN, MARKET_CLOSE, log)
    assert session.observation_count == 2


# --------------------------------------------------------------------- #
# 3. No execution leakage
# --------------------------------------------------------------------- #
def test_no_trading_capability_in_package():
    import bujji.shadow_market_campaign as pkg
    pkg_dir = os.path.dirname(pkg.__file__)
    forbidden = ("place_order", "modify_order", "cancel_order", "PaperBroker", "FyersBroker",
                 "entry_price", "exit_price", "order_id", "quantity", "position_size")
    for fname in os.listdir(pkg_dir):
        if not fname.endswith(".py"):
            continue
        with open(os.path.join(pkg_dir, fname)) as f:
            source = f.read()
        for term in forbidden:
            assert term not in source, f"{fname} contains forbidden term {term!r}"


def test_no_broker_module_imports():
    import bujji.shadow_market_campaign as pkg
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
# 4. Decision stability calculation
# --------------------------------------------------------------------- #
def test_decision_stability_calculation_counts_flips_correctly():
    log = ShadowDecisionLog()
    # TREND_UP -> EXECUTABLE_CANDIDATE, TRANSITION -> WATCH, TREND_UP -> EXECUTABLE_CANDIDATE: 2 flips.
    for regime, ts in (("TREND_UP", "2026-08-17T09:30:00+05:30"),
                        ("TRANSITION", "2026-08-17T10:00:00+05:30"),
                        ("TREND_UP", "2026-08-17T10:30:00+05:30")):
        log.record(_observation_for(_trend_following_strong(_env(regime)), ts))
    session = build_campaign_session("2026-08-17", MARKET_OPEN, MARKET_CLOSE, log)
    metrics = validate_session_behavior(session, log.observations)
    assert metrics.decision_change_count == 2

    # Same decision every cycle -> zero flips.
    stable_log = ShadowDecisionLog()
    for ts in ("2026-08-17T09:30:00+05:30", "2026-08-17T10:00:00+05:30", "2026-08-17T10:30:00+05:30"):
        stable_log.record(_observation_for(_trend_following_strong(_env("TREND_UP")), ts))
    stable_session = build_campaign_session("2026-08-17", MARKET_OPEN, MARKET_CLOSE, stable_log)
    stable_metrics = validate_session_behavior(stable_session, stable_log.observations)
    assert stable_metrics.decision_change_count == 0
    assert stable_metrics.decision_stability == "LOW"


# --------------------------------------------------------------------- #
# 5. Data quality tracking
# --------------------------------------------------------------------- #
def test_missing_observations_reduce_health():
    good_log = ShadowDecisionLog()
    for ts in ("2026-08-17T09:30:00+05:30", "2026-08-17T10:00:00+05:30"):
        good_log.record(_observation_for(_trend_following_strong(_env("TREND_UP")), ts,
                                          market_state=_market_state(data_quality="SUFFICIENT", as_of_time=ts)))
    good_session = build_campaign_session("2026-08-17", MARKET_OPEN, MARKET_CLOSE, good_log)
    assert good_session.health_status == HEALTH_HEALTHY

    bad_log = ShadowDecisionLog()
    for ts in ("2026-08-17T09:30:00+05:30", "2026-08-17T10:00:00+05:30"):
        bad_log.record(_observation_for(_trend_following_strong(_env("TREND_UP")), ts,
                                         market_state=_market_state(data_quality="INSUFFICIENT", as_of_time=ts)))
    bad_session = build_campaign_session("2026-08-17", MARKET_OPEN, MARKET_CLOSE, bad_log)
    assert bad_session.health_status == HEALTH_DEGRADED
    assert bad_session.data_quality_summary.get("INSUFFICIENT") == 2


# --------------------------------------------------------------------- #
# 6. Explanation preservation
# --------------------------------------------------------------------- #
def test_reasons_survive_campaign_storage():
    log = ShadowDecisionLog()
    log.record(_observation_for(_trend_following_strong(_env("TRANSITION")), "2026-08-17T09:30:00+05:30"))
    session = build_campaign_session("2026-08-17", MARKET_OPEN, MARKET_CLOSE, log)
    metrics = validate_session_behavior(session, log.observations)
    assert metrics.explanation_completeness_pct == 1.0
    report_text = build_campaign_report(session, metrics)
    assert "Decision distribution" in report_text
    assert "Explanation completeness" in report_text


# --------------------------------------------------------------------- #
# 7. Empty session honesty
# --------------------------------------------------------------------- #
def test_empty_session_is_unhealthy_not_success():
    empty_log = ShadowDecisionLog()
    session = build_campaign_session("2026-08-17", MARKET_OPEN, MARKET_CLOSE, empty_log)
    assert session.observation_count == 0
    assert session.health_status == HEALTH_EMPTY

    with pytest.raises(ValueError):
        CampaignSession(
            session_date="2026-08-17", market_open_time=MARKET_OPEN, market_close_time=MARKET_CLOSE,
            observation_count=0, decision_count=0, data_quality_summary={}, uncertainty_summary={},
            decision_distribution={}, health_status=HEALTH_HEALTHY,
        )
