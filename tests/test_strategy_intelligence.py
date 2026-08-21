"""Phase 20.5 -- Strategy Intelligence & Ranking Layer tests."""
from __future__ import annotations

import pytest

from bujji.epistemics import uncertainty as epi
from bujji.strategy_intelligence import (
    MarketContext, StrategyEvidence, rank_strategies, score_strategy,
)


def _evidence(name="S", n=1000, win_rate=0.60, pf=2.0, net=100.0, gross=150.0,
              train=100.0, validation=100.0, oos=100.0):
    return StrategyEvidence(
        strategy_name=name, sample_size=n, win_rate=win_rate, profit_factor=pf,
        net_expectancy=net, gross_expectancy=gross,
        train_expectancy=train, validation_expectancy=validation, out_of_sample_expectancy=oos,
    )


# --------------------------------------------------------------------- #
# Ranking correctness
# --------------------------------------------------------------------- #
def test_higher_quality_evidence_ranks_higher():
    strong = _evidence("STRONG", n=1000, win_rate=0.65, pf=2.3, net=500.0, gross=900.0,
                        train=500.0, validation=480.0, oos=520.0)
    weak = _evidence("WEAK", n=1000, win_rate=0.30, pf=0.5, net=-50.0, gross=-30.0,
                      train=-50.0, validation=-60.0, oos=-40.0)
    report = rank_strategies([score_strategy(weak), score_strategy(strong)])
    assert report.scores[0].strategy_name == "STRONG"
    assert report.scores[0].effective_score > report.scores[1].effective_score


def test_ranking_report_is_sorted_descending():
    a = score_strategy(_evidence("A", net=10.0, gross=20.0))
    b = score_strategy(_evidence("B", net=200.0, gross=250.0))
    c = score_strategy(_evidence("C", net=-5.0, gross=5.0))
    report = rank_strategies([a, b, c])
    scores = [s.effective_score for s in report.scores]
    assert scores == sorted(scores, reverse=True)


def test_render_produces_readable_report():
    report = rank_strategies([score_strategy(_evidence("TREND"))])
    text = report.render()
    assert "Strategy Ranking:" in text
    assert "TREND" in text
    assert "Score:" in text
    assert "Confidence:" in text


# --------------------------------------------------------------------- #
# No overfitting: small-sample huge-return must not outrank
# large-sample modest-stable-return.
# --------------------------------------------------------------------- #
def test_small_sample_huge_return_ranks_below_large_sample_modest_return():
    tiny_huge = StrategyEvidence(
        strategy_name="TINY_HUGE", sample_size=2, win_rate=1.0, profit_factor=50.0,
        net_expectancy=10000.0, gross_expectancy=10500.0,
        train_expectancy=10000.0, validation_expectancy=None, out_of_sample_expectancy=None,
    )
    large_modest = _evidence("LARGE_MODEST", n=1000, win_rate=0.58, pf=1.4, net=50.0, gross=90.0,
                              train=48.0, validation=45.0, oos=52.0)
    report = rank_strategies([score_strategy(tiny_huge), score_strategy(large_modest)])
    assert report.scores[0].strategy_name == "LARGE_MODEST"
    tiny_score = next(s for s in report.scores if s.strategy_name == "TINY_HUGE")
    assert tiny_score.confidence in (epi.NONE, epi.LOW)


def test_below_minimum_sample_size_gets_none_confidence():
    tiny = _evidence("TINY", n=5)
    score = score_strategy(tiny)
    assert score.confidence == epi.NONE
    assert score.effective_score <= 30.0


# --------------------------------------------------------------------- #
# MIC independence: context changes confidence, never evidence_score.
# --------------------------------------------------------------------- #
def test_mic_context_never_changes_evidence_score():
    evidence = _evidence("TREND", n=11278, win_rate=0.665, pf=2.30, net=517.0, gross=949.0,
                          train=512.0, validation=481.0, oos=573.0)
    no_context = score_strategy(evidence, context=None)
    favorable = score_strategy(evidence, context=MarketContext(
        mic_regime="TREND_UP", favorable_regimes=("TREND_UP", "TREND_DOWN"), unfavorable_regimes=("TRANSITION",),
    ))
    unfavorable = score_strategy(evidence, context=MarketContext(
        mic_regime="TRANSITION", favorable_regimes=("TREND_UP", "TREND_DOWN"), unfavorable_regimes=("TRANSITION",),
    ))
    for s in (no_context, favorable, unfavorable):
        assert s.evidence_score == no_context.evidence_score
        assert s.edge_component == no_context.edge_component
        assert s.execution_component == no_context.execution_component
        assert s.stability_component == no_context.stability_component


def test_mic_context_can_change_confidence_and_effective_score():
    evidence = _evidence("TREND", n=200, win_rate=0.60, pf=1.6, net=100.0, gross=150.0)
    favorable = score_strategy(evidence, context=MarketContext(
        mic_regime="TREND_UP", favorable_regimes=("TREND_UP",), unfavorable_regimes=("TRANSITION",),
    ))
    unfavorable = score_strategy(evidence, context=MarketContext(
        mic_regime="TRANSITION", favorable_regimes=("TREND_UP",), unfavorable_regimes=("TRANSITION",),
    ))
    assert epi.rank(unfavorable.confidence) < epi.rank(favorable.confidence)
    assert unfavorable.effective_score <= favorable.effective_score
    assert unfavorable.context_note is not None and "demoted" in unfavorable.context_note


def test_mic_context_never_promotes_confidence_above_evidence_based_level():
    evidence = _evidence("SMALL", n=30)  # LOW confidence from sample size alone.
    favorable = score_strategy(evidence, context=MarketContext(
        mic_regime="TREND_UP", favorable_regimes=("TREND_UP",),
    ))
    assert epi.rank(favorable.confidence) <= epi.rank(epi.LOW)


def test_unvalidated_regime_also_demotes_confidence():
    evidence = _evidence("TREND", n=200)
    unvalidated = score_strategy(evidence, context=MarketContext(
        mic_regime="VOLATILITY_EXPANSION", favorable_regimes=("TREND_UP",), unfavorable_regimes=("TRANSITION",),
    ))
    assert unvalidated.context_note is not None and "never validated" in unvalidated.context_note


# --------------------------------------------------------------------- #
# Execution awareness
# --------------------------------------------------------------------- #
def test_strategy_losing_all_edge_after_costs_scores_zero_edge_and_execution():
    destroyed = _evidence("DESTROYED", n=1000, win_rate=0.60, pf=1.8, net=-5.0, gross=200.0,
                           train=-5.0, validation=-5.0, oos=-5.0)
    score = score_strategy(destroyed)
    assert score.edge_component == 0.0
    assert score.execution_component == 0.0
    assert score.evidence_score < 30.0


def test_high_execution_drag_scores_lower_than_low_drag_at_same_net():
    low_drag = _evidence("LOW_DRAG", net=100.0, gross=110.0)
    high_drag = _evidence("HIGH_DRAG", net=100.0, gross=500.0)
    assert score_strategy(low_drag).execution_component > score_strategy(high_drag).execution_component


def test_execution_component_zero_when_gross_never_positive():
    evidence = StrategyEvidence(
        strategy_name="X", sample_size=100, win_rate=0.5, profit_factor=1.0,
        net_expectancy=10.0, gross_expectancy=-5.0,
        train_expectancy=10.0, validation_expectancy=10.0, out_of_sample_expectancy=10.0,
    )
    assert score_strategy(evidence).execution_component == 0.0


# --------------------------------------------------------------------- #
# Stability component
# --------------------------------------------------------------------- #
def test_stability_full_credit_when_all_periods_positive():
    evidence = _evidence(train=10.0, validation=10.0, oos=10.0)
    assert score_strategy(evidence).stability_component == 25.0


def test_stability_zero_when_all_periods_negative():
    evidence = _evidence(net=1.0, gross=1.0, train=-10.0, validation=-10.0, oos=-10.0)
    assert score_strategy(evidence).stability_component == 0.0


def test_stability_partial_credit_for_partial_consistency():
    evidence = _evidence(train=10.0, validation=10.0, oos=-5.0)
    assert score_strategy(evidence).stability_component == pytest.approx(25.0 * 2 / 3, abs=0.01)


def test_stability_ignores_missing_periods():
    evidence = _evidence(train=10.0, validation=None, oos=None)
    assert score_strategy(evidence).stability_component == 25.0


# --------------------------------------------------------------------- #
# Phase 20.4's real finding, reproduced directly: Mean Reversion (FAIL)
# must score near zero and rank below Trend Following (PASS).
# --------------------------------------------------------------------- #
def test_phase_20_4_findings_reproduce_correct_ranking():
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
    report = rank_strategies([score_strategy(mean_reversion), score_strategy(trend_following)])
    assert report.scores[0].strategy_name == "FAMILY_A_TREND_FOLLOWING"
    assert report.scores[1].strategy_name == "FAMILY_B_MEAN_REVERSION"
    assert report.scores[1].evidence_score == 0.0
    assert report.scores[0].confidence == epi.HIGH  # n=11278.
