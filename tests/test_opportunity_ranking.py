"""Phase 20.7 -- Opportunity Ranking Engine tests."""
from __future__ import annotations

import os

import pytest

from bujji.mic_v0 import models as mic_models
from bujji.opportunity_intelligence import MarketEnvironment, evaluate_opportunity
from bujji.opportunity_ranking import (
    PRIORITY_HIGH, OpportunityCandidate, explain_ranking, rank_opportunities,
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


def _env(regime="TREND_UP", risk=mic_models.RISK_NORMAL, vol=mic_models.VOLATILITY_NORMAL, execution="NORMAL", data_quality_ok=True):
    return MarketEnvironment(mic_regime=regime, risk_state=risk, volatility_state=vol,
                              execution_profile_name=execution, data_quality_ok=data_quality_ok)


def _candidate(evidence, environment, favorable=FAVORABLE, unfavorable=UNFAVORABLE):
    score = score_strategy(evidence)
    assessment = evaluate_opportunity(score, environment, favorable, unfavorable)
    return OpportunityCandidate(assessment=assessment)


def _strong_evidence():
    return _evidence("TREND_FOLLOWING", n=11278, win_rate=0.665, pf=2.30, net=517.0, gross=949.0,
                      train=512.0, validation=481.0, oos=573.0)


def _weak_evidence():
    return _evidence("MEAN_REVERSION", n=150, win_rate=0.0, pf=0.0, net=-1664.0, gross=-1218.0,
                      train=-1375.0, validation=-1806.0, oos=-2260.0)


# --------------------------------------------------------------------- #
# 1. Evidence dominance
# --------------------------------------------------------------------- #
def test_strong_evidence_weaker_conditions_beats_weak_evidence_perfect_conditions():
    strong_watch = _candidate(_strong_evidence(), _env("TRANSITION"))  # WATCH, discounted.
    weak_ideal = _candidate(
        _evidence("WEAK_BUT_PERFECT_FIT", n=1000, win_rate=0.35, pf=0.6, net=5.0, gross=8.0, train=5.0, validation=5.0, oos=5.0),
        _env("TREND_UP"), favorable=("TREND_UP",), unfavorable=(),
    )
    result = rank_opportunities([strong_watch, weak_ideal])
    assert result.ranked[0].strategy_name == "TREND_FOLLOWING"


def test_higher_effective_score_ranks_higher_at_equal_qualification():
    # A: strong RATES (win_rate/profit_factor/drag%) -- Phase 20.5's scoring
    # is rate-based, not magnitude-based, so the gap must be in rates.
    a = _candidate(
        _evidence("A", win_rate=0.70, pf=2.5, net=500.0, gross=700.0, train=500.0, validation=480.0, oos=520.0),
        _env("TREND_UP"),
    )
    b = _candidate(
        _evidence("B", win_rate=0.25, pf=0.4, net=10.0, gross=50.0, train=10.0, validation=10.0, oos=10.0),
        _env("TREND_UP"),
    )
    result = rank_opportunities([b, a])
    assert result.ranked[0].strategy_name == "A"


# --------------------------------------------------------------------- #
# 2. Qualification enforcement -- BLOCKED cannot rank.
# --------------------------------------------------------------------- #
def test_blocked_opportunity_is_excluded_not_ranked():
    blocked = _candidate(_strong_evidence(), _env("TREND_UP", risk=mic_models.RISK_EXTREME))
    result = rank_opportunities([blocked])
    assert result.ranked == ()
    assert len(result.excluded) == 1
    assert "BLOCKED" in result.excluded[0].reason


def test_blocked_never_appears_among_ranked_even_alongside_others():
    blocked = _candidate(_strong_evidence(), _env("TREND_UP", risk=mic_models.RISK_EXTREME))
    eligible = _candidate(_evidence("OTHER", net=50.0, gross=80.0), _env("TREND_UP"))
    result = rank_opportunities([blocked, eligible])
    ranked_names = {r.strategy_name for r in result.ranked}
    assert "TREND_FOLLOWING" not in ranked_names
    assert "OTHER" in ranked_names


# --------------------------------------------------------------------- #
# 3. Insufficient evidence enforcement
# --------------------------------------------------------------------- #
def test_insufficient_evidence_cannot_outrank_trusted_strategy_even_with_perfect_regime_match():
    weak_perfect_regime = _candidate(_weak_evidence(), _env("TREND_UP"), favorable=("TREND_UP",), unfavorable=())
    strong_moderate = _candidate(_strong_evidence(), _env("TRANSITION"))
    result = rank_opportunities([weak_perfect_regime, strong_moderate])
    assert all(r.strategy_name != "MEAN_REVERSION" for r in result.ranked)
    assert result.ranked[0].strategy_name == "TREND_FOLLOWING"
    assert any(e.strategy_name == "MEAN_REVERSION" and "INSUFFICIENT_EVIDENCE" in e.reason for e in result.excluded)


def test_tiny_sample_cannot_rank_regardless_of_environment():
    tiny = _candidate(
        _evidence("TINY", n=3, win_rate=1.0, pf=10.0, net=1000.0, gross=1100.0, train=1000.0, validation=None, oos=None),
        _env("TREND_UP"), favorable=("TREND_UP",), unfavorable=(),
    )
    result = rank_opportunities([tiny])
    assert result.ranked == ()
    assert result.excluded[0].strategy_name == "TINY"


# --------------------------------------------------------------------- #
# 4. Ranking stability
# --------------------------------------------------------------------- #
def test_small_mic_change_does_not_reorder_when_evidence_gap_is_large():
    strong_evidence = _strong_evidence()
    # OTHER: genuinely weak rates (low win_rate/profit_factor, heavy drag),
    # not just a small dollar figure -- Phase 20.5's scoring is rate-based.
    other_evidence = _evidence("OTHER", win_rate=0.20, pf=0.3, net=2.0, gross=10.0, train=2.0, validation=-5.0, oos=1.0)

    result_normal_risk = rank_opportunities([
        _candidate(strong_evidence, _env("TREND_UP", risk=mic_models.RISK_NORMAL)),
        _candidate(other_evidence, _env("TREND_UP", risk=mic_models.RISK_NORMAL)),
    ])
    result_elevated_risk = rank_opportunities([
        _candidate(strong_evidence, _env("TREND_UP", risk=mic_models.RISK_ELEVATED)),  # WATCH now, not ELIGIBLE.
        _candidate(other_evidence, _env("TREND_UP", risk=mic_models.RISK_NORMAL)),
    ])
    # Top strategy stays the same across a small MIC perturbation, because
    # the evidence gap (517 vs 20 net expectancy) is far larger than the
    # bounded WATCH discount (0.6x) could ever close.
    assert result_normal_risk.ranked[0].strategy_name == result_elevated_risk.ranked[0].strategy_name == "TREND_FOLLOWING"


def test_qualification_weight_never_exceeds_one():
    from bujji.opportunity_ranking.scorer import _QUALIFICATION_WEIGHT
    assert all(w <= 1.0 for w in _QUALIFICATION_WEIGHT.values())


def test_watch_priority_score_never_exceeds_eligible_for_same_evidence():
    evidence = _strong_evidence()
    eligible = _candidate(evidence, _env("TREND_UP"))
    watch = _candidate(evidence, _env("TRANSITION"))
    result = rank_opportunities([eligible])
    result_watch = rank_opportunities([watch])
    assert result_watch.ranked[0].priority_score <= result.ranked[0].priority_score


# --------------------------------------------------------------------- #
# 5. Explainability
# --------------------------------------------------------------------- #
def test_every_ranked_result_has_at_least_one_reason():
    a = _candidate(_strong_evidence(), _env("TREND_UP"))
    b = _candidate(_evidence("B", net=20.0, gross=30.0), _env("TRANSITION"))
    result = rank_opportunities([a, b])
    for r in result.ranked:
        assert len(r.reasons) >= 1


def test_watch_candidate_carries_a_penalty_eligible_does_not():
    eligible = _candidate(_strong_evidence(), _env("TREND_UP"))
    watch = _candidate(_evidence("OTHER", net=20.0, gross=30.0), _env("TRANSITION"))
    result = rank_opportunities([eligible, watch])
    eligible_result = next(r for r in result.ranked if r.strategy_name == "TREND_FOLLOWING")
    watch_result = next(r for r in result.ranked if r.strategy_name == "OTHER")
    assert eligible_result.penalties == ()
    assert len(watch_result.penalties) >= 1


def test_explain_ranking_produces_readable_text_with_comparison():
    a = _candidate(_strong_evidence(), _env("TREND_UP"))
    b = _candidate(_evidence("OTHER", net=20.0, gross=30.0), _env("TREND_UP"))
    result = rank_opportunities([a, b])
    text = explain_ranking(result)
    assert "TREND_FOLLOWING" in text
    assert "Ahead of" in text


def test_explain_ranking_lists_excluded_candidates_with_reasons():
    blocked = _candidate(_strong_evidence(), _env("TREND_UP", risk=mic_models.RISK_EXTREME))
    result = rank_opportunities([blocked])
    text = explain_ranking(result)
    assert "Excluded from ranking entirely" in text
    assert "TREND_FOLLOWING" in text


# --------------------------------------------------------------------- #
# 6. Safety boundary
# --------------------------------------------------------------------- #
def test_no_trading_capability_in_package():
    import bujji.opportunity_ranking as pkg
    pkg_dir = os.path.dirname(pkg.__file__)
    forbidden = ("place_order", "modify_order", "cancel_order", "PaperBroker", "FyersBroker")
    for fname in os.listdir(pkg_dir):
        if not fname.endswith(".py"):
            continue
        with open(os.path.join(pkg_dir, fname)) as f:
            source = f.read()
        for term in forbidden:
            assert term not in source, f"{fname} contains forbidden term {term!r}"


def test_no_quantity_margin_or_portfolio_weight_vocabulary():
    import bujji.opportunity_ranking as pkg
    pkg_dir = os.path.dirname(pkg.__file__)
    forbidden = ("quantity", "margin_", "position_size", "portfolio_weight", "lot_size")
    for fname in os.listdir(pkg_dir):
        if not fname.endswith(".py"):
            continue
        with open(os.path.join(pkg_dir, fname)) as f:
            source = f.read().lower()
        for term in forbidden:
            assert term not in source, f"{fname} contains forbidden term {term!r}"
