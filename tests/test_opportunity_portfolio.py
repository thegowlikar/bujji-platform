"""Phase 20.9 -- Portfolio Intelligence & Multi-Opportunity Conflict
Resolution tests."""
from __future__ import annotations

import os

import pytest

from bujji.mic_v0 import models as mic_models
from bujji.opportunity_intelligence import MarketEnvironment, evaluate_opportunity
from bujji.opportunity_ranking import OpportunityCandidate
from bujji.capital_intelligence import assess_risk_allocation
from bujji.opportunity_portfolio import (
    ALLOW_MULTIPLE, COMPATIBLE, CONFLICTING, EXCLUSIVE, INSUFFICIENT_EVIDENCE, NEUTRAL,
    NO_SELECTION, REDUCE_CONFLICT, SELECT_PRIMARY,
    evaluate_conflicts, explain_portfolio_decision, rank_portfolio_choices,
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


def _trend_following(environment=None):
    ev = _evidence("FAMILY_A_TREND_FOLLOWING", n=11278, win_rate=0.665, pf=2.30, net=517.0, gross=949.0,
                    train=512.0, validation=481.0, oos=573.0)
    return _allocation(ev, environment or _env("TREND_UP"), TREND_FAVORABLE, TREND_UNFAVORABLE)


def _mean_reversion_strong(environment=None):
    """Synthetic: a hypothetically-STRONG Mean Reversion, used only to
    test conflict/coexistence logic in isolation from Phase 20.4's
    real (failed) Mean Reversion result."""
    ev = _evidence("FAMILY_B_MEAN_REVERSION", n=1500, win_rate=0.62, pf=2.0, net=300.0, gross=450.0,
                    train=300.0, validation=280.0, oos=310.0)
    return _allocation(ev, environment or _env("RANGE"), MR_FAVORABLE, MR_UNFAVORABLE)


def _mean_reversion_real_failed():
    ev = _evidence("FAMILY_B_MEAN_REVERSION", n=150, win_rate=0.0, pf=0.0, net=-1664.0, gross=-1218.0,
                    train=-1375.0, validation=-1806.0, oos=-2260.0)
    return _allocation(ev, _env("RANGE"), MR_FAVORABLE, MR_UNFAVORABLE)


def _weak_strategy(name="WEAK", environment=None):
    ev = _evidence(name, win_rate=0.20, pf=0.3, net=2.0, gross=10.0, train=2.0, validation=-5.0, oos=1.0)
    return _allocation(ev, environment or _env("TREND_UP"), TREND_FAVORABLE, ())


# --------------------------------------------------------------------- #
# 1. Evidence dominance
# --------------------------------------------------------------------- #
def test_strong_strategy_beats_weak_strategy_in_select_primary():
    strong = _trend_following()
    weak = _weak_strategy("WEAK", _env("TREND_UP"))
    decision = rank_portfolio_choices([strong, weak])
    # Both are TREND_UP-favorable and not opposing (WEAK isn't a registered
    # family, so correlation is empty -> NEUTRAL) -- allowed to coexist,
    # but if forced to pick strongest anywhere, strong must dominate.
    assert "FAMILY_A_TREND_FOLLOWING" in (decision.primary or "") or "FAMILY_A_TREND_FOLLOWING" in decision.kept


def test_evidence_dominance_in_exclusive_conflict():
    strong = _trend_following(_env("TREND_UP"))
    weak_mr = _weak_strategy("FAMILY_B_MEAN_REVERSION", _env("TREND_UP"))
    # Force both ELIGIBLE simultaneously by giving the weak one favorable=TREND_UP too --
    # but conflict detection uses the REAL registered family's declared sets for
    # FAMILY_B_MEAN_REVERSION, which are opposed to FAMILY_A_TREND_FOLLOWING.
    decision = rank_portfolio_choices([strong, weak_mr])
    assert decision.decision in (SELECT_PRIMARY, REDUCE_CONFLICT)
    assert decision.primary == "FAMILY_A_TREND_FOLLOWING"


# --------------------------------------------------------------------- #
# 2. Conflict detection
# --------------------------------------------------------------------- #
def test_trend_following_vs_mean_reversion_detected_as_conflicting_or_exclusive():
    tf = _trend_following(_env("TREND_UP"))
    mr = _mean_reversion_strong(_env("TREND_UP"))  # forced into TREND_UP env so both could be ELIGIBLE-checked.
    conflicts = evaluate_conflicts([tf, mr])
    assert len(conflicts) == 1
    assert conflicts[0].conflict_state in (CONFLICTING, EXCLUSIVE)
    assert conflicts[0].correlation.opposing_regimes  # real opposing regimes cited.


def test_unregistered_strategy_pair_is_neutral_never_fabricated():
    tf = _trend_following()
    weak = _weak_strategy("SOME_UNKNOWN_FAMILY")
    conflicts = evaluate_conflicts([tf, weak])
    assert conflicts[0].conflict_state == NEUTRAL
    assert conflicts[0].correlation.opposing_regimes == ()
    assert conflicts[0].correlation.shared_favorable_regimes == ()


# --------------------------------------------------------------------- #
# 3. MIC influence boundary
# --------------------------------------------------------------------- #
def test_mic_changes_qualification_not_evidence_score():
    tf_trend = _trend_following(_env("TREND_UP"))
    tf_transition = _trend_following(_env("TRANSITION"))
    assert tf_trend.candidate.assessment.strategy_score.evidence_score == tf_transition.candidate.assessment.strategy_score.evidence_score


def test_mic_cannot_flip_a_declared_conflict_to_compatible():
    """Changing environment can change qualification_state (and hence
    EXCLUSIVE-vs-CONFLICTING), but the DECLARED opposing regimes
    (Phase 20.3's own family definitions) never change."""
    tf = _trend_following(_env("TRANSITION"))
    mr = _mean_reversion_strong(_env("TRANSITION"))
    conflicts = evaluate_conflicts([tf, mr])
    assert conflicts[0].correlation.opposing_regimes  # still opposing regardless of current MIC reading.
    assert conflicts[0].conflict_state != COMPATIBLE


# --------------------------------------------------------------------- #
# 4. Multiple strategy support
# --------------------------------------------------------------------- #
def test_compatible_strategies_can_coexist():
    tf1 = _trend_following(_env("TREND_UP"))
    # A second, unregistered "family" sharing no declared opposition -> NEUTRAL, allowed.
    other = _allocation(
        _evidence("OTHER_COMPATIBLE", win_rate=0.6, pf=1.8, net=80.0, gross=120.0, train=80.0, validation=75.0, oos=85.0),
        _env("TREND_UP"), ("TREND_UP",), (),
    )
    decision = rank_portfolio_choices([tf1, other])
    assert decision.decision == ALLOW_MULTIPLE
    assert set(decision.kept) == {"FAMILY_A_TREND_FOLLOWING", "OTHER_COMPATIBLE"}


# --------------------------------------------------------------------- #
# 5. Weak strategy exclusion
# --------------------------------------------------------------------- #
def test_failed_strategy_cannot_survive_via_diversification():
    tf = _trend_following()
    mr_failed = _mean_reversion_real_failed()
    decision = rank_portfolio_choices([tf, mr_failed])
    assert "FAMILY_B_MEAN_REVERSION" not in decision.kept
    assert "FAMILY_B_MEAN_REVERSION" in decision.excluded


def test_all_candidates_insufficient_evidence_yields_insufficient_evidence_decision():
    mr_failed = _mean_reversion_real_failed()
    decision = rank_portfolio_choices([mr_failed])
    assert decision.decision == INSUFFICIENT_EVIDENCE


def test_empty_candidate_list_yields_no_selection():
    decision = rank_portfolio_choices([])
    assert decision.decision == NO_SELECTION


# --------------------------------------------------------------------- #
# 6. Explainability
# --------------------------------------------------------------------- #
def test_every_decision_has_reasons():
    for candidates in (
        [_trend_following()],
        [_trend_following(), _mean_reversion_real_failed()],
        [_mean_reversion_real_failed()],
        [],
    ):
        decision = rank_portfolio_choices(candidates)
        assert len(decision.reasons) >= 1


def test_explain_portfolio_decision_produces_readable_text():
    decision = rank_portfolio_choices([_trend_following(), _mean_reversion_real_failed()])
    text = explain_portfolio_decision(decision)
    assert "Portfolio Decision:" in text
    assert "FAMILY_A_TREND_FOLLOWING" in text


# --------------------------------------------------------------------- #
# 7. Safety boundary
# --------------------------------------------------------------------- #
def test_no_trading_capability_in_package():
    import bujji.opportunity_portfolio as pkg
    pkg_dir = os.path.dirname(pkg.__file__)
    forbidden = ("place_order", "modify_order", "cancel_order", "PaperBroker", "FyersBroker")
    for fname in os.listdir(pkg_dir):
        if not fname.endswith(".py"):
            continue
        with open(os.path.join(pkg_dir, fname)) as f:
            source = f.read()
        for term in forbidden:
            assert term not in source, f"{fname} contains forbidden term {term!r}"


def test_no_capital_or_broker_imports():
    import bujji.opportunity_portfolio as pkg
    pkg_dir = os.path.dirname(pkg.__file__)
    forbidden = ("import bujji.broker", "from bujji.broker", "import bujji.capital.", "from bujji.capital.")
    for fname in os.listdir(pkg_dir):
        if not fname.endswith(".py"):
            continue
        with open(os.path.join(pkg_dir, fname)) as f:
            source = f.read()
        for term in forbidden:
            assert term not in source, f"{fname} contains forbidden import {term!r}"
