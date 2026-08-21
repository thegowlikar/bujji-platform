import ast
import inspect
from datetime import datetime

import pytest

from bujji.trading_brain.risk_governor import adaptive_risk_memory as arm
from bujji.trading_brain.risk_governor import market_regime_adapter as mra
from bujji.trading_brain.risk_governor import adaptive_risk_recommendation as arr
from bujji.trading_brain.risk_governor import adaptive_risk_governor as arg
from bujji.trading_brain.risk_governor.capital_safety_governor import SAFETY_BLOCKED, SAFETY_SAFE
from bujji.trading_brain.risk_governor.risk_budget_governor import BUDGET_REJECTED
from bujji.trading_brain.risk_governor.position_lifecycle_intelligence import ACTION_BLOCK_NEW_RISK


BASE_TS = datetime(2026, 8, 1, 10, 0, 0)
clock = lambda: BASE_TS


def make_entry(
    entry_id="e1", strategy_type="IRON_CONDOR", market_regime="SIDEWAYS", volatility_regime="LOW_VOL",
    outcome=arm.OUTCOME_WIN, max_drawdown=0.02, max_profit=0.03, holding_period_days=3,
    recommended_size=5, actual_size=5, exit_reason="PROFIT_TARGET",
):
    return arm.build_risk_memory_entry(
        entry_id, strategy_type, market_regime, volatility_regime, SAFETY_SAFE, "RISK_HEALTHY",
        recommended_size, actual_size, outcome, max_drawdown, max_profit, holding_period_days,
        exit_reason, clock=clock,
    )


# --------------------------------------------------------------------- #
# Memory
# --------------------------------------------------------------------- #

def test_append_and_lookup_all_entries():
    store = arm.AdaptiveRiskMemory()
    store.append_observation(make_entry("e1"))
    store.append_observation(make_entry("e2"))
    assert len(store) == 2
    assert [e.entry_id for e in store.all_entries()] == ["e1", "e2"]


def test_lookup_by_strategy_regime_volatility():
    store = arm.AdaptiveRiskMemory()
    store.append_observation(make_entry("e1", strategy_type="IRON_CONDOR", market_regime="SIDEWAYS"))
    store.append_observation(make_entry("e2", strategy_type="IRON_FLY", market_regime="TRENDING_UP"))
    assert [e.entry_id for e in store.lookup_by_strategy("IRON_CONDOR")] == ["e1"]
    assert [e.entry_id for e in store.lookup_by_regime("TRENDING_UP")] == ["e2"]
    assert [e.entry_id for e in store.lookup_by_volatility("LOW_VOL")] == ["e1", "e2"]


def test_lookup_multiple_filters_combined():
    store = arm.AdaptiveRiskMemory()
    store.append_observation(make_entry("e1", strategy_type="IRON_CONDOR", market_regime="SIDEWAYS"))
    store.append_observation(make_entry("e2", strategy_type="IRON_CONDOR", market_regime="TRENDING_UP"))
    result = store.lookup(strategy_type="IRON_CONDOR", market_regime="SIDEWAYS")
    assert [e.entry_id for e in result] == ["e1"]


def test_history_ordering_preserved_not_resorted():
    store = arm.AdaptiveRiskMemory()
    for i in range(5):
        store.append_observation(make_entry(f"e{i}"))
    assert [e.entry_id for e in store.all_entries()] == [f"e{i}" for i in range(5)]


def test_duplicate_entry_id_rejected():
    store = arm.AdaptiveRiskMemory()
    store.append_observation(make_entry("e1"))
    with pytest.raises(arm.DuplicateRiskMemoryEntryError):
        store.append_observation(make_entry("e1"))


def test_immutability_frozen_dataclass():
    entry = make_entry("e1")
    with pytest.raises(Exception):
        entry.realized_outcome = arm.OUTCOME_LOSS


def test_store_has_no_update_or_delete_method():
    store = arm.AdaptiveRiskMemory()
    for name in ("update_observation", "delete_observation", "remove_observation", "modify_observation"):
        assert not hasattr(store, name)


def test_invalid_outcome_rejected():
    with pytest.raises(arm.IllegalRiskMemoryEntryError):
        make_entry(outcome="MAYBE")


def test_negative_size_rejected():
    with pytest.raises(arm.IllegalRiskMemoryEntryError):
        make_entry(recommended_size=-1)


# --------------------------------------------------------------------- #
# Regime adapter
# --------------------------------------------------------------------- #

def test_trend_regime_splits_by_net_move_sign():
    assert mra.adapt_trend_regime("TRENDING", net_move=10.0) == mra.TREND_TRENDING_UP
    assert mra.adapt_trend_regime("TRENDING", net_move=-10.0) == mra.TREND_TRENDING_DOWN


def test_trend_regime_ranging_maps_to_sideways():
    assert mra.adapt_trend_regime("RANGING", net_move=None) == mra.TREND_SIDEWAYS


def test_trend_regime_unknown_source_maps_to_unknown():
    assert mra.adapt_trend_regime(None, None) == mra.TREND_UNKNOWN
    assert mra.adapt_trend_regime("TRENDING", net_move=None) == mra.TREND_UNKNOWN


def test_volatility_regime_relabeling():
    assert mra.adapt_volatility_regime("HIGH_VOLATILITY") == mra.VOL_HIGH
    assert mra.adapt_volatility_regime("STABLE") == mra.VOL_LOW
    assert mra.adapt_volatility_regime("COMPRESSED") == mra.VOL_CONTRACTION
    assert mra.adapt_volatility_regime("TRANSITIONING") == mra.VOL_EXPANSION
    assert mra.adapt_volatility_regime(None) == mra.VOL_UNKNOWN


# --------------------------------------------------------------------- #
# Experience
# --------------------------------------------------------------------- #

def test_experience_correct_aggregation_and_averages():
    entries = (
        make_entry("e1", outcome=arm.OUTCOME_WIN, max_drawdown=0.02, max_profit=0.05, holding_period_days=2),
        make_entry("e2", outcome=arm.OUTCOME_LOSS, max_drawdown=0.04, max_profit=0.00, holding_period_days=4),
    )
    exp = arm.summarize_strategy_experience(entries, "IRON_CONDOR", "SIDEWAYS")
    assert exp.observations == 2
    assert exp.wins == 1
    assert exp.losses == 1
    assert exp.win_rate == 0.5
    assert exp.average_drawdown == pytest.approx(0.03)
    assert exp.average_profit == pytest.approx(0.025)
    assert exp.worst_drawdown == pytest.approx(0.04)
    assert exp.best_profit == pytest.approx(0.05)
    assert exp.average_holding_period_days == pytest.approx(3.0)


def test_experience_confidence_bands_by_sample_size():
    def entries_of(n):
        return tuple(make_entry(f"e{i}") for i in range(n))

    assert arm.summarize_strategy_experience(entries_of(2), "X").confidence == arm.CONFIDENCE_NONE
    assert arm.summarize_strategy_experience(entries_of(5), "X").confidence == arm.CONFIDENCE_LOW
    assert arm.summarize_strategy_experience(entries_of(15), "X").confidence == arm.CONFIDENCE_MODERATE
    assert arm.summarize_strategy_experience(entries_of(30), "X").confidence == arm.CONFIDENCE_HIGH


def test_experience_empty_entries_returns_none_fields_not_fabricated_zero():
    exp = arm.summarize_strategy_experience((), "IRON_CONDOR")
    assert exp.observations == 0
    assert exp.win_rate is None
    assert exp.average_drawdown is None
    assert exp.confidence == arm.CONFIDENCE_NONE


# --------------------------------------------------------------------- #
# Recommendation
# --------------------------------------------------------------------- #

def _experience(sample_size, win_rate, avg_drawdown):
    wins = round(win_rate * sample_size)
    return arm.StrategyExperience(
        strategy_type="IRON_CONDOR", market_regime="SIDEWAYS", observations=sample_size,
        wins=wins, losses=sample_size - wins, win_rate=win_rate, average_drawdown=avg_drawdown,
        average_profit=0.02, worst_drawdown=avg_drawdown * 1.5, best_profit=0.05,
        average_holding_period_days=3.0, confidence=arm.CONFIDENCE_MODERATE, sample_size=sample_size,
    )


def test_recommendation_small_sample_insufficient_data():
    rec = arr.recommend_adaptive_risk_adjustment(_experience(3, 0.9, 0.01))
    assert rec.recommendation == arr.RECOMMENDATION_INSUFFICIENT_DATA


def test_recommendation_good_history_increases_size():
    rec = arr.recommend_adaptive_risk_adjustment(_experience(20, 0.80, 0.02))
    assert rec.recommendation == arr.RECOMMENDATION_INCREASE_SIZE_20


def test_recommendation_moderately_good_history_increases_size_10():
    rec = arr.recommend_adaptive_risk_adjustment(_experience(20, 0.65, 0.02))
    assert rec.recommendation == arr.RECOMMENDATION_INCREASE_SIZE_10


def test_recommendation_bad_history_reduces_30():
    rec = arr.recommend_adaptive_risk_adjustment(_experience(20, 0.30, 0.15))
    assert rec.recommendation == arr.RECOMMENDATION_REDUCE_SIZE_30


def test_recommendation_large_drawdown_reduces_20():
    rec = arr.recommend_adaptive_risk_adjustment(_experience(20, 0.55, 0.09))
    assert rec.recommendation == arr.RECOMMENDATION_REDUCE_SIZE_20


def test_recommendation_low_win_rate_reduces_10():
    rec = arr.recommend_adaptive_risk_adjustment(_experience(20, 0.35, 0.02))
    assert rec.recommendation == arr.RECOMMENDATION_REDUCE_SIZE_10


def test_recommendation_mixed_neutral_no_change():
    rec = arr.recommend_adaptive_risk_adjustment(_experience(20, 0.50, 0.03))
    assert rec.recommendation == arr.RECOMMENDATION_NO_CHANGE


def test_recommendation_unknown_regime_still_insufficient_data_if_small():
    exp = _experience(3, 0.9, 0.01)
    exp2 = arm.StrategyExperience(**{**exp.__dict__, "market_regime": "UNKNOWN"})
    rec = arr.recommend_adaptive_risk_adjustment(exp2)
    assert rec.recommendation == arr.RECOMMENDATION_INSUFFICIENT_DATA


# --------------------------------------------------------------------- #
# Integration
# --------------------------------------------------------------------- #

def test_blocked_trade_stays_blocked():
    rec = arr.recommend_adaptive_risk_adjustment(_experience(20, 0.80, 0.02))  # would be INCREASE_SIZE_20
    decision = arg.evaluate_adaptive_governor_decision(SAFETY_BLOCKED, 10, 15, rec)
    assert decision.is_blocked is True
    assert decision.final_suggested_size == 0


def test_budget_rejected_stays_blocked():
    rec = arr.recommend_adaptive_risk_adjustment(_experience(20, 0.80, 0.02))
    decision = arg.evaluate_adaptive_governor_decision(BUDGET_REJECTED, 10, 15, rec)
    assert decision.final_suggested_size == 0


def test_block_new_risk_stays_blocked():
    rec = arr.recommend_adaptive_risk_adjustment(_experience(20, 0.80, 0.02))
    decision = arg.evaluate_adaptive_governor_decision(ACTION_BLOCK_NEW_RISK, 10, 15, rec)
    assert decision.final_suggested_size == 0


def test_healthy_trade_receives_adaptive_size_reduction():
    rec = arr.recommend_adaptive_risk_adjustment(_experience(20, 0.30, 0.15))  # REDUCE_SIZE_30
    decision = arg.evaluate_adaptive_governor_decision(SAFETY_SAFE, 10, 10, rec)
    assert decision.final_suggested_size == 7  # floor(10 * 0.70)


def test_recommendation_never_exceeds_d3_maximum_even_on_increase():
    rec = arr.recommend_adaptive_risk_adjustment(_experience(20, 0.80, 0.02))  # INCREASE_SIZE_20
    decision = arg.evaluate_adaptive_governor_decision(SAFETY_SAFE, 10, 11, rec)  # max only slightly above base
    assert decision.final_suggested_size <= 11


def test_increase_uses_headroom_below_maximum():
    rec = arr.recommend_adaptive_risk_adjustment(_experience(20, 0.80, 0.02))  # INCREASE_SIZE_20 -> factor 1.2
    decision = arg.evaluate_adaptive_governor_decision(SAFETY_SAFE, 10, 20, rec)
    assert decision.final_suggested_size == 12  # floor(10*1.2)=12, within maximum 20


def test_explanation_matches_computation_across_all_recommendation_types():
    for win_rate, drawdown in ((0.80, 0.02), (0.65, 0.02), (0.50, 0.03), (0.35, 0.02), (0.55, 0.09), (0.30, 0.15)):
        rec = arr.recommend_adaptive_risk_adjustment(_experience(20, win_rate, drawdown))
        decision = arg.evaluate_adaptive_governor_decision(SAFETY_SAFE, 10, 15, rec)
        expected = max(0, min(15, __import__("math").floor(10 * arr.SIZE_ADJUSTMENT_FACTOR[rec.recommendation])))
        assert decision.final_suggested_size == expected
        assert rec.recommendation in decision.reason


# --------------------------------------------------------------------- #
# Safety
# --------------------------------------------------------------------- #

def test_no_broker_execution_or_order_imports_across_all_d5_modules():
    modules = (arm, mra, arr, arg)
    forbidden = ("broker", "fyers", "order", "execution")
    for module in modules:
        tree = ast.parse(open(module.__file__).read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not any(f in alias.name.lower() for f in forbidden), (module.__name__, alias.name)
            elif isinstance(node, ast.ImportFrom):
                mod = (node.module or "").lower()
                assert not any(f in mod for f in forbidden), (module.__name__, node.module)


def test_no_ml_or_probability_or_random_imports():
    modules = (arm, mra, arr, arg)
    forbidden = ("numpy", "scipy", "sklearn", "torch", "tensorflow", "random")
    for module in modules:
        tree = ast.parse(open(module.__file__).read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not any(f in alias.name.lower() for f in forbidden), (module.__name__, alias.name)
            elif isinstance(node, ast.ImportFrom):
                mod = (node.module or "").lower()
                assert not any(f in mod for f in forbidden), (module.__name__, node.module)


def test_no_database_or_persistence_mutation_imports():
    modules = (arm, mra, arr, arg)
    forbidden = ("sqlite3", "psycopg", "sqlalchemy", "pymongo")
    for module in modules:
        tree = ast.parse(open(module.__file__).read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not any(f in alias.name.lower() for f in forbidden), (module.__name__, alias.name)


def test_no_force_override_bypass_parameter_anywhere():
    fns = (
        arm.build_risk_memory_entry, arm.summarize_strategy_experience,
        arr.recommend_adaptive_risk_adjustment, arg.evaluate_adaptive_governor_decision,
        mra.adapt_trend_regime, mra.adapt_volatility_regime,
    )
    for fn in fns:
        sig = inspect.signature(fn)
        for name in sig.parameters:
            assert "force" not in name.lower()
            assert "override" not in name.lower()
            assert "bypass" not in name.lower()


def test_same_input_produces_identical_output_deterministic():
    exp = _experience(20, 0.65, 0.02)
    rec1 = arr.recommend_adaptive_risk_adjustment(exp)
    rec2 = arr.recommend_adaptive_risk_adjustment(exp)
    assert rec1.recommendation == rec2.recommendation
    assert rec1.explanation == rec2.explanation
    d1 = arg.evaluate_adaptive_governor_decision(SAFETY_SAFE, 10, 15, rec1)
    d2 = arg.evaluate_adaptive_governor_decision(SAFETY_SAFE, 10, 15, rec2)
    assert d1 == d2


def test_empty_memory_returns_insufficient_data():
    exp = arm.summarize_strategy_experience((), "IRON_CONDOR")
    rec = arr.recommend_adaptive_risk_adjustment(exp)
    assert rec.recommendation == arr.RECOMMENDATION_INSUFFICIENT_DATA


def test_unknown_regime_never_fabricates_confidence_with_small_sample():
    exp = arm.summarize_strategy_experience((make_entry(),), "IRON_CONDOR", "UNKNOWN")
    assert exp.confidence == arm.CONFIDENCE_NONE
