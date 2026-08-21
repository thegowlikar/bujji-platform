"""Phase 20.8 -- Capital Allocation Intelligence Layer tests."""
from __future__ import annotations

import os

import pytest

from bujji.epistemics import uncertainty as epi
from bujji.mic_v0 import models as mic_models
from bujji.opportunity_intelligence import MarketEnvironment, evaluate_opportunity
from bujji.opportunity_ranking import OpportunityCandidate, rank_opportunities
from bujji.capital_intelligence import (
    MAXIMUM, MINIMAL, NONE_ALLOCATION, NORMAL, REDUCED,
    allocation_rank, assess_ranking_result, assess_risk_allocation, demote_allocation, explain_allocation,
)
from bujji.strategy_intelligence import StrategyEvidence, score_strategy

FAVORABLE = ("TREND_UP", "TREND_DOWN")
UNFAVORABLE = ("RANGE",)


def _evidence(name, n=1000, win_rate=0.6, pf=2.0, net=100.0, gross=150.0, train=100.0, validation=100.0, oos=100.0):
    return StrategyEvidence(
        strategy_name=name, sample_size=n, win_rate=win_rate, profit_factor=pf,
        net_expectancy=net, gross_expectancy=gross,
        train_expectancy=train, validation_expectancy=validation, out_of_sample_expectancy=oos,
    )


def _strong_evidence():
    return _evidence("TREND_FOLLOWING", n=11278, win_rate=0.665, pf=2.30, net=517.0, gross=949.0,
                      train=512.0, validation=481.0, oos=573.0)


def _weak_evidence():
    return _evidence("MEAN_REVERSION", n=150, win_rate=0.0, pf=0.0, net=-1664.0, gross=-1218.0,
                      train=-1375.0, validation=-1806.0, oos=-2260.0)


def _env(regime="TREND_UP", risk=mic_models.RISK_NORMAL, vol=mic_models.VOLATILITY_NORMAL, execution="NORMAL"):
    return MarketEnvironment(mic_regime=regime, risk_state=risk, volatility_state=vol, execution_profile_name=execution)


def _candidate(evidence, environment, favorable=FAVORABLE, unfavorable=UNFAVORABLE):
    score = score_strategy(evidence)
    assessment = evaluate_opportunity(score, environment, favorable, unfavorable)
    return OpportunityCandidate(assessment=assessment)


# --------------------------------------------------------------------- #
# 1. Evidence dominance
# --------------------------------------------------------------------- #
def test_strong_evidence_moderate_conditions_beats_weak_evidence_perfect_conditions():
    strong = _candidate(_strong_evidence(), _env("TRANSITION"))  # WATCH.
    weak = _candidate(_weak_evidence(), _env("RANGE"), favorable=("RANGE",), unfavorable=())  # perfect fit, but weak evidence.
    strong_alloc = assess_risk_allocation(strong)
    weak_alloc = assess_risk_allocation(weak)
    assert allocation_rank(strong_alloc.allocation_class) > allocation_rank(weak_alloc.allocation_class)
    assert weak_alloc.allocation_class == NONE_ALLOCATION


def test_higher_priority_score_yields_equal_or_higher_allocation():
    a = assess_risk_allocation(_candidate(
        _evidence("A", win_rate=0.70, pf=2.5, net=500.0, gross=700.0, train=500.0, validation=480.0, oos=520.0),
        _env("TREND_UP"),
    ))
    b = assess_risk_allocation(_candidate(
        _evidence("B", win_rate=0.25, pf=0.4, net=10.0, gross=50.0, train=10.0, validation=10.0, oos=10.0),
        _env("TREND_UP"),
    ))
    assert allocation_rank(a.allocation_class) >= allocation_rank(b.allocation_class)


# --------------------------------------------------------------------- #
# 2. Qualification gate
# --------------------------------------------------------------------- #
def test_blocked_always_returns_none():
    blocked = _candidate(_strong_evidence(), _env("TREND_UP", risk=mic_models.RISK_EXTREME))
    result = assess_risk_allocation(blocked)
    assert result.allocation_class == NONE_ALLOCATION
    assert len(result.reasons) == 1


def test_insufficient_evidence_always_returns_none():
    weak = _candidate(_weak_evidence(), _env("RANGE"), favorable=("RANGE",), unfavorable=())
    result = assess_risk_allocation(weak)
    assert result.allocation_class == NONE_ALLOCATION
    assert "insufficient" in result.reasons[0].lower()


# --------------------------------------------------------------------- #
# 3. Confidence cap
# --------------------------------------------------------------------- #
def test_low_confidence_cannot_receive_maximum():
    # High win_rate/profit_factor (would otherwise score very high) but
    # a small-ish sample -> LOW confidence tier.
    evidence = _evidence("HIGH_SCORE_LOW_CONF", n=25, win_rate=0.9, pf=5.0, net=200.0, gross=220.0,
                          train=200.0, validation=200.0, oos=200.0)
    candidate = _candidate(evidence, _env("TREND_UP"))
    score = candidate.assessment.strategy_score
    assert score.confidence == epi.LOW
    result = assess_risk_allocation(candidate)
    assert result.allocation_class != MAXIMUM
    assert allocation_rank(result.allocation_class) <= allocation_rank(REDUCED)


def test_high_confidence_allows_higher_tiers_than_low_confidence_for_same_raw_score():
    evidence_high_n = _evidence("HC", n=11278, win_rate=0.9, pf=5.0, net=200.0, gross=220.0,
                                 train=200.0, validation=200.0, oos=200.0)
    evidence_low_n = _evidence("LC", n=25, win_rate=0.9, pf=5.0, net=200.0, gross=220.0,
                                train=200.0, validation=200.0, oos=200.0)
    high_conf = assess_risk_allocation(_candidate(evidence_high_n, _env("TREND_UP")))
    low_conf = assess_risk_allocation(_candidate(evidence_low_n, _env("TREND_UP")))
    assert allocation_rank(high_conf.allocation_class) > allocation_rank(low_conf.allocation_class)


# --------------------------------------------------------------------- #
# 4. MIC independence
# --------------------------------------------------------------------- #
def test_mic_context_never_changes_evidence_score():
    evidence = _strong_evidence()
    for regime in ("TREND_UP", "TREND_DOWN", "TRANSITION", "VOLATILITY_EXPANSION"):
        candidate = _candidate(evidence, _env(regime))
        assert candidate.assessment.strategy_score.evidence_score == pytest.approx(78.62, abs=0.01)


def test_mic_context_can_change_allocation_class_not_evidence():
    evidence = _strong_evidence()
    ideal = assess_risk_allocation(_candidate(evidence, _env("TREND_UP")))
    transition = assess_risk_allocation(_candidate(evidence, _env("TRANSITION")))
    assert ideal.candidate.assessment.strategy_score.evidence_score == transition.candidate.assessment.strategy_score.evidence_score
    # Allocation class itself may differ (qualification changes), evidence never does.


# --------------------------------------------------------------------- #
# 5. Execution impact
# --------------------------------------------------------------------- #
def test_extreme_execution_reduces_allocation_versus_normal():
    evidence = _strong_evidence()
    normal = assess_risk_allocation(_candidate(evidence, _env("TREND_UP", execution="NORMAL")))
    extreme = assess_risk_allocation(_candidate(evidence, _env("TREND_UP", execution="EXTREME")))
    assert allocation_rank(extreme.allocation_class) <= allocation_rank(normal.allocation_class)
    assert any("EXTREME" in p for p in extreme.penalties)


def test_stress_execution_strictly_reduces_allocation():
    """STRESS execution compounds two real effects: Phase 20.6's own
    qualification rule already demotes ELIGIBLE->WATCH for any non-
    NORMAL execution profile (discounting priority_score), and this
    phase's own Rule 4 demotes the resulting tier by one more step --
    both are legitimate, so the net drop can exceed one tier. This
    test checks the direction (strictly lower), not an exact tier
    count."""
    evidence = _strong_evidence()
    normal = assess_risk_allocation(_candidate(evidence, _env("TREND_UP", execution="NORMAL")))
    stress = assess_risk_allocation(_candidate(evidence, _env("TREND_UP", execution="STRESS")))
    assert allocation_rank(stress.allocation_class) < allocation_rank(normal.allocation_class)
    assert any("STRESS" in p for p in stress.penalties)


def test_demote_allocation_floors_at_none():
    assert demote_allocation(MINIMAL, 5) == NONE_ALLOCATION
    assert demote_allocation(MAXIMUM, 1) == NORMAL


# --------------------------------------------------------------------- #
# 6. Explainability
# --------------------------------------------------------------------- #
def test_every_result_has_at_least_one_reason_or_penalty():
    for evidence, env in [
        (_strong_evidence(), _env("TREND_UP")),
        (_strong_evidence(), _env("TRANSITION")),
        (_strong_evidence(), _env("TREND_UP", risk=mic_models.RISK_EXTREME)),
        (_weak_evidence(), _env("RANGE")),
    ]:
        result = assess_risk_allocation(_candidate(evidence, env))
        assert len(result.reasons) + len(result.penalties) >= 1


def test_explain_allocation_produces_readable_text():
    result = assess_risk_allocation(_candidate(_strong_evidence(), _env("TREND_UP")))
    text = explain_allocation(result)
    assert "TREND_FOLLOWING" in text
    assert result.allocation_class in text


def test_render_includes_priority_and_rank_when_present():
    result = assess_risk_allocation(_candidate(_strong_evidence(), _env("TREND_UP")), priority_score=79.0, rank=1)
    text = result.render()
    assert "priority_score=79" in text
    assert "rank=#1" in text


# --------------------------------------------------------------------- #
# 7. Safety boundary
# --------------------------------------------------------------------- #
def test_no_trading_capability_in_package():
    import bujji.capital_intelligence as pkg
    pkg_dir = os.path.dirname(pkg.__file__)
    forbidden = ("place_order", "modify_order", "cancel_order", "PaperBroker", "FyersBroker")
    for fname in os.listdir(pkg_dir):
        if not fname.endswith(".py"):
            continue
        with open(os.path.join(pkg_dir, fname)) as f:
            source = f.read()
        for term in forbidden:
            assert term not in source, f"{fname} contains forbidden term {term!r}"


def test_no_sizing_or_margin_vocabulary():
    import bujji.capital_intelligence as pkg
    pkg_dir = os.path.dirname(pkg.__file__)
    forbidden = ("lot_calculation", "contract_selection", "margin_allocation", "stop_loss", "approved_lots")
    for fname in os.listdir(pkg_dir):
        if not fname.endswith(".py"):
            continue
        with open(os.path.join(pkg_dir, fname)) as f:
            source = f.read().lower()
        for term in forbidden:
            assert term not in source, f"{fname} contains forbidden term {term!r}"


# --------------------------------------------------------------------- #
# assess_ranking_result -- integration with Phase 20.7's own output
# --------------------------------------------------------------------- #
def test_assess_ranking_result_covers_ranked_and_excluded():
    strong = _candidate(_strong_evidence(), _env("TREND_UP"))
    weak = _candidate(_weak_evidence(), _env("TREND_UP"))
    ranking = rank_opportunities([strong, weak])
    assessments = assess_ranking_result(ranking)
    names = {a.strategy_name for a in assessments}
    assert names == {"TREND_FOLLOWING", "MEAN_REVERSION"}
    mr = next(a for a in assessments if a.strategy_name == "MEAN_REVERSION")
    assert mr.allocation_class == NONE_ALLOCATION
    tf = next(a for a in assessments if a.strategy_name == "TREND_FOLLOWING")
    assert tf.rank == 1
    assert tf.priority_score is not None
