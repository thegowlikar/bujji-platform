"""Phase 20.6 -- Opportunity Qualification Engine tests."""
from __future__ import annotations

import os

import pytest

from bujji.epistemics import uncertainty as epi
from bujji.mic_v0 import models as mic_models
from bujji.opportunity_intelligence import (
    BLOCKED, ELIGIBLE, INSUFFICIENT_EVIDENCE, WATCH,
    MarketEnvironment, evaluate_opportunity,
)
from bujji.strategy_intelligence import StrategyEvidence, score_strategy

FAVORABLE = ("TREND_UP", "TREND_DOWN")
UNFAVORABLE = ("RANGE",)


def _strong_evidence():
    return StrategyEvidence(
        strategy_name="TREND_FOLLOWING", sample_size=11278, win_rate=0.665, profit_factor=2.30,
        net_expectancy=517.0, gross_expectancy=949.0,
        train_expectancy=512.0, validation_expectancy=481.0, out_of_sample_expectancy=573.0,
    )


def _weak_evidence():
    return StrategyEvidence(
        strategy_name="MEAN_REVERSION", sample_size=150, win_rate=0.0, profit_factor=0.0,
        net_expectancy=-1664.0, gross_expectancy=-1218.0,
        train_expectancy=-1375.0, validation_expectancy=-1806.0, out_of_sample_expectancy=-2260.0,
    )


def _tiny_evidence():
    return StrategyEvidence(
        strategy_name="TINY", sample_size=3, win_rate=1.0, profit_factor=10.0,
        net_expectancy=1000.0, gross_expectancy=1100.0,
        train_expectancy=1000.0, validation_expectancy=None, out_of_sample_expectancy=None,
    )


def _env(regime="TREND_UP", risk=mic_models.RISK_NORMAL, vol=mic_models.VOLATILITY_NORMAL,
          execution="NORMAL", data_quality_ok=True):
    return MarketEnvironment(
        mic_regime=regime, risk_state=risk, volatility_state=vol,
        execution_profile_name=execution, data_quality_ok=data_quality_ok,
    )


# --------------------------------------------------------------------- #
# 1. Evidence preservation
# --------------------------------------------------------------------- #
def test_mic_context_does_not_change_strategy_score_object():
    score = score_strategy(_strong_evidence())
    ideal = evaluate_opportunity(score, _env("TREND_UP"), FAVORABLE, UNFAVORABLE)
    transition = evaluate_opportunity(score, _env("TRANSITION", risk=mic_models.RISK_ELEVATED), FAVORABLE, UNFAVORABLE)
    blocked = evaluate_opportunity(score, _env("TREND_UP", risk=mic_models.RISK_EXTREME), FAVORABLE, UNFAVORABLE)
    for assessment in (ideal, transition, blocked):
        assert assessment.strategy_score is score  # literally the same object -- never rebuilt.
        assert assessment.strategy_score.evidence_score == score.evidence_score
        assert assessment.strategy_score.confidence == score.confidence
        assert assessment.strategy_score.effective_score == score.effective_score


def test_evaluating_across_many_environments_never_mutates_evidence():
    score = score_strategy(_strong_evidence())
    original_evidence = score.evidence
    for regime in ("TREND_UP", "TREND_DOWN", "RANGE", "TRANSITION", "VOLATILITY_EXPANSION"):
        for risk in mic_models.ALL_RISK_STATES:
            evaluate_opportunity(score, _env(regime, risk=risk), FAVORABLE, UNFAVORABLE)
    assert score.evidence is original_evidence
    assert score.evidence.net_expectancy == 517.0
    assert score.evidence.sample_size == 11278


# --------------------------------------------------------------------- #
# 2. Qualification correctness (the phase's own worked examples)
# --------------------------------------------------------------------- #
def test_strong_strategy_suitable_environment_is_eligible():
    score = score_strategy(_strong_evidence())
    assessment = evaluate_opportunity(score, _env("TREND_UP"), FAVORABLE, UNFAVORABLE)
    assert assessment.decision.state == ELIGIBLE


def test_strong_strategy_uncertain_environment_is_watch():
    score = score_strategy(_strong_evidence())
    assessment = evaluate_opportunity(score, _env("TRANSITION", risk=mic_models.RISK_NORMAL), FAVORABLE, UNFAVORABLE)
    assert assessment.decision.state == WATCH


def test_strong_strategy_extreme_risk_is_blocked():
    score = score_strategy(_strong_evidence())
    assessment = evaluate_opportunity(score, _env("TREND_UP", risk=mic_models.RISK_EXTREME), FAVORABLE, UNFAVORABLE)
    assert assessment.decision.state == BLOCKED
    assert assessment.decision.reasons[0].code == "EXTREME_RISK"


def test_weak_strategy_is_insufficient_evidence_regardless_of_environment():
    score = score_strategy(_weak_evidence())
    for regime in ("TREND_UP", "RANGE", "TRANSITION"):
        assessment = evaluate_opportunity(score, _env(regime), FAVORABLE, UNFAVORABLE)
        assert assessment.decision.state == INSUFFICIENT_EVIDENCE


def test_tiny_sample_high_return_is_insufficient_evidence_even_in_ideal_environment():
    score = score_strategy(_tiny_evidence())
    assessment = evaluate_opportunity(score, _env("TREND_UP"), ("TREND_UP",), ())
    assert assessment.decision.state == INSUFFICIENT_EVIDENCE


def test_unfavorable_regime_blocks_even_with_strong_evidence():
    score = score_strategy(_strong_evidence())
    assessment = evaluate_opportunity(score, _env("RANGE"), FAVORABLE, UNFAVORABLE)
    assert assessment.decision.state == BLOCKED
    assert assessment.decision.reasons[0].code == "REGIME_INCOMPATIBLE"


def test_data_quality_not_ok_blocks_regardless_of_evidence_or_regime():
    score = score_strategy(_strong_evidence())
    assessment = evaluate_opportunity(score, _env("TREND_UP", data_quality_ok=False), FAVORABLE, UNFAVORABLE)
    assert assessment.decision.state == BLOCKED
    assert assessment.decision.reasons[0].code == "DATA_QUALITY"


def test_extreme_execution_profile_with_high_drag_blocks():
    high_drag_evidence = StrategyEvidence(
        strategy_name="HIGH_DRAG", sample_size=1000, win_rate=0.6, profit_factor=1.5,
        net_expectancy=10.0, gross_expectancy=100.0,  # 90% drag.
        train_expectancy=10.0, validation_expectancy=10.0, out_of_sample_expectancy=10.0,
    )
    score = score_strategy(high_drag_evidence)
    assessment = evaluate_opportunity(score, _env("TREND_UP", execution="EXTREME"), FAVORABLE, UNFAVORABLE)
    assert assessment.decision.state == BLOCKED
    assert assessment.decision.reasons[0].code == "EXECUTION_IMPOSSIBLE"


def test_low_effective_score_never_reaches_eligible_even_in_ideal_environment():
    """A strategy just above the sufficiency floor but below the
    ELIGIBLE bar (70) must WATCH, not ELIGIBLE, even in an otherwise
    ideal environment."""
    modest_evidence = StrategyEvidence(
        strategy_name="MODEST", sample_size=1000, win_rate=0.35, profit_factor=0.6,
        net_expectancy=5.0, gross_expectancy=8.0,
        train_expectancy=5.0, validation_expectancy=5.0, out_of_sample_expectancy=5.0,
    )
    score = score_strategy(modest_evidence)
    assert score.effective_score < 70.0
    assessment = evaluate_opportunity(score, _env("TREND_UP"), ("TREND_UP",), ())
    assert assessment.decision.state == WATCH


# --------------------------------------------------------------------- #
# 3. Abstention behaviour -- poor conditions never fabricate ELIGIBLE.
# --------------------------------------------------------------------- #
def test_no_qualified_opportunity_is_a_valid_outcome_across_a_sweep():
    weak_score = score_strategy(_weak_evidence())
    environments = [
        _env("TREND_UP"), _env("RANGE"), _env("TRANSITION"),
        _env("TREND_UP", risk=mic_models.RISK_EXTREME),
        _env("RANGE", vol=mic_models.VOLATILITY_HIGH),
    ]
    states = {evaluate_opportunity(weak_score, e, FAVORABLE, UNFAVORABLE).decision.state for e in environments}
    assert ELIGIBLE not in states  # a confirmed-losing strategy must never be ELIGIBLE, under any environment.


def test_every_decision_carries_at_least_one_reason():
    score = score_strategy(_strong_evidence())
    for regime in ("TREND_UP", "RANGE", "TRANSITION"):
        assessment = evaluate_opportunity(score, _env(regime), FAVORABLE, UNFAVORABLE)
        assert len(assessment.decision.reasons) >= 1


# --------------------------------------------------------------------- #
# 4. Safety boundary
# --------------------------------------------------------------------- #
def test_no_order_or_broker_capability_in_package():
    import bujji.opportunity_intelligence as pkg
    pkg_dir = os.path.dirname(pkg.__file__)
    forbidden = ("place_order", "modify_order", "cancel_order", "PaperBroker", "FyersBroker")
    for fname in os.listdir(pkg_dir):
        if not fname.endswith(".py"):
            continue
        with open(os.path.join(pkg_dir, fname)) as f:
            source = f.read()
        for term in forbidden:
            assert term not in source, f"{fname} contains forbidden term {term!r}"


def test_invalid_qualification_state_rejected():
    from bujji.opportunity_intelligence.models import QualificationDecision, QualificationReason
    with pytest.raises(ValueError):
        QualificationDecision(state="NOT_A_REAL_STATE", reasons=(QualificationReason("X", "y"),))


def test_qualification_decision_requires_at_least_one_reason():
    from bujji.opportunity_intelligence.models import QualificationDecision
    with pytest.raises(ValueError):
        QualificationDecision(state=ELIGIBLE, reasons=())
