"""Phase 20.10 -- Decision Orchestration Intelligence Layer tests."""
from __future__ import annotations

import os

import pytest

from bujji.mic_v0 import models as mic_models
from bujji.opportunity_intelligence import MarketEnvironment, evaluate_opportunity
from bujji.opportunity_ranking import OpportunityCandidate
from bujji.capital_intelligence import assess_risk_allocation
from bujji.opportunity_portfolio import rank_portfolio_choices
from bujji.decision_orchestration import (
    BLOCKED, EXECUTABLE_CANDIDATE, INSUFFICIENT_INTELLIGENCE, NO_OPPORTUNITY, WATCH,
    compose_decision, explain_decision,
)
from bujji.strategy_intelligence import StrategyEvidence, score_strategy

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
# 1. Strong candidate composition
# --------------------------------------------------------------------- #
def test_all_positive_layers_produce_executable_candidate():
    allocation = _trend_following_strong(_env("TREND_UP"))
    portfolio = rank_portfolio_choices([allocation])
    decision = compose_decision(allocation, portfolio)
    assert decision.decision_state == EXECUTABLE_CANDIDATE
    assert len(decision.positive) >= 3
    assert decision.negative == ()


# --------------------------------------------------------------------- #
# 2. Evidence failure
# --------------------------------------------------------------------- #
def test_failed_strategy_cannot_pass():
    allocation = _mean_reversion_failed()
    portfolio = rank_portfolio_choices([allocation])
    decision = compose_decision(allocation, portfolio)
    assert decision.decision_state == NO_OPPORTUNITY
    assert "insufficient historical evidence" in decision.negative[0].lower()


def test_failed_strategy_cannot_pass_even_with_perfect_regime():
    """Rule 5: MIC cannot manufacture opportunity out of failed evidence."""
    allocation = _mean_reversion_failed()  # RANGE is MR's own favorable regime -- as good as it gets.
    portfolio = rank_portfolio_choices([allocation])
    decision = compose_decision(allocation, portfolio)
    assert decision.decision_state == NO_OPPORTUNITY


# --------------------------------------------------------------------- #
# 3. Capital blocking
# --------------------------------------------------------------------- #
def test_extreme_risk_blocks_decision():
    allocation = _trend_following_strong(_env("TREND_UP", risk=mic_models.RISK_EXTREME))
    portfolio = rank_portfolio_choices([allocation])
    decision = compose_decision(allocation, portfolio)
    assert decision.decision_state == BLOCKED
    assert allocation.allocation_class == "NONE"


# --------------------------------------------------------------------- #
# 4. Portfolio conflict handling
# --------------------------------------------------------------------- #
def test_portfolio_excluded_candidate_yields_no_opportunity():
    """A genuine PORTFOLIO-LEVEL conflict (registered, opposing family,
    forced ELIGIBLE simultaneously with Trend Following -- the same
    EXCLUSIVE-conflict pattern Phase 20.9's own tests use) must
    exclude the weaker side, even though its evidence alone would have
    been sufficient to survive on its own."""
    strong = _trend_following_strong(_env("TREND_UP"))
    # Weaker than `strong` (evidence_score 72 < 78.62) but still ELIGIBLE
    # (>=70) -- needed so both candidates are simultaneously ELIGIBLE and
    # the EXCLUSIVE conflict path (not just CONFLICTING/REDUCE_CONFLICT)
    # actually fires.
    weak_mr = _allocation(
        _evidence("FAMILY_B_MEAN_REVERSION", win_rate=0.55, pf=2.0, net=28.0, gross=100.0, train=28.0, validation=28.0, oos=28.0),
        _env("TREND_UP"), TREND_FAVORABLE, (),  # forced ELIGIBLE under TREND_UP despite being registered as MEAN_REVERSION.
    )
    portfolio = rank_portfolio_choices([strong, weak_mr])
    decision_weak = compose_decision(weak_mr, portfolio)
    decision_strong = compose_decision(strong, portfolio)
    assert decision_weak.decision_state == NO_OPPORTUNITY
    assert decision_strong.decision_state in (EXECUTABLE_CANDIDATE, WATCH)


def test_only_survivor_from_portfolio_can_be_executable():
    strong = _trend_following_strong(_env("TREND_UP"))
    failed = _mean_reversion_failed()
    portfolio = rank_portfolio_choices([strong, failed])
    decision = compose_decision(strong, portfolio)
    assert decision.decision_state == EXECUTABLE_CANDIDATE


# --------------------------------------------------------------------- #
# 5. MIC boundary
# --------------------------------------------------------------------- #
def test_mic_changes_context_not_evidence():
    trend_env = _trend_following_strong(_env("TREND_UP"))
    transition_env = _trend_following_strong(_env("TRANSITION"))
    assert (trend_env.candidate.assessment.strategy_score.evidence_score
            == transition_env.candidate.assessment.strategy_score.evidence_score)


def test_transition_regime_produces_watch_not_executable():
    allocation = _trend_following_strong(_env("TRANSITION"))
    portfolio = rank_portfolio_choices([allocation])
    decision = compose_decision(allocation, portfolio)
    assert decision.decision_state == WATCH
    assert any("transition" in n.lower() or "watch" in n.lower() for n in decision.negative)


def test_event_context_always_surfaced_as_unknown_by_default():
    allocation = _trend_following_strong(_env("TREND_UP"))
    portfolio = rank_portfolio_choices([allocation])
    decision = compose_decision(allocation, portfolio)
    assert any("event calendar" in u.lower() for u in decision.unknown)


# --------------------------------------------------------------------- #
# 6. Missing data honesty
# --------------------------------------------------------------------- #
def test_missing_allocation_yields_insufficient_intelligence():
    portfolio = rank_portfolio_choices([_trend_following_strong()])
    decision = compose_decision(None, portfolio)
    assert decision.decision_state == INSUFFICIENT_INTELLIGENCE


def test_missing_portfolio_decision_yields_insufficient_intelligence():
    allocation = _trend_following_strong()
    decision = compose_decision(allocation, None)
    assert decision.decision_state == INSUFFICIENT_INTELLIGENCE


def test_mismatched_strategy_not_in_portfolio_yields_insufficient_intelligence():
    allocation = _trend_following_strong()
    other_portfolio = rank_portfolio_choices([_mean_reversion_failed()])
    decision = compose_decision(allocation, other_portfolio)
    assert decision.decision_state == INSUFFICIENT_INTELLIGENCE


# --------------------------------------------------------------------- #
# 7. Explainability
# --------------------------------------------------------------------- #
def test_every_decision_contains_reasons():
    scenarios = [
        (_trend_following_strong(_env("TREND_UP")), None),
        (_mean_reversion_failed(), None),
        (_trend_following_strong(_env("TREND_UP", risk=mic_models.RISK_EXTREME)), None),
    ]
    for allocation, _ in scenarios:
        portfolio = rank_portfolio_choices([allocation])
        decision = compose_decision(allocation, portfolio)
        assert len(decision.positive) + len(decision.negative) + len(decision.unknown) >= 1


def test_explain_decision_produces_readable_text():
    allocation = _trend_following_strong(_env("TRANSITION"))
    portfolio = rank_portfolio_choices([allocation])
    decision = compose_decision(allocation, portfolio)
    text = explain_decision(decision)
    assert "Decision:" in text
    assert "FAMILY_A_TREND_FOLLOWING" in text
    assert "Positive:" in text or "Negative:" in text


# --------------------------------------------------------------------- #
# 8. Safety boundary
# --------------------------------------------------------------------- #
def test_no_trading_capability_in_package():
    import bujji.decision_orchestration as pkg
    pkg_dir = os.path.dirname(pkg.__file__)
    forbidden = ("place_order", "modify_order", "cancel_order", "PaperBroker", "FyersBroker")
    for fname in os.listdir(pkg_dir):
        if not fname.endswith(".py"):
            continue
        with open(os.path.join(pkg_dir, fname)) as f:
            source = f.read()
        for term in forbidden:
            assert term not in source, f"{fname} contains forbidden term {term!r}"


def test_no_broker_or_capital_module_imports():
    import bujji.decision_orchestration as pkg
    pkg_dir = os.path.dirname(pkg.__file__)
    forbidden = ("import bujji.broker", "from bujji.broker", "import bujji.capital.", "from bujji.capital.")
    for fname in os.listdir(pkg_dir):
        if not fname.endswith(".py"):
            continue
        with open(os.path.join(pkg_dir, fname)) as f:
            source = f.read()
        for term in forbidden:
            assert term not in source, f"{fname} contains forbidden import {term!r}"
