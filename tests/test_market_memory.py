"""Phase 20.15 -- Market Memory Write Layer tests."""
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
from bujji.shadow_decision_runtime import run_shadow_cycle
from bujji.state_persistence.store import EventStore
from bujji.market_memory import (
    build_decision_memory_record, build_index, build_known_outcome_memory_record,
    build_market_memory_record, build_memory_context, build_pending_outcome_memory_record,
    explain_memory_context, find_similar_conditions_as_of, memory_id_for,
    read_all_decision_memories, read_all_market_memories, read_all_outcome_memories,
    record_decision_memory, record_market_memory, record_outcome_memory,
    score_similarity,
)

TREND_FAVORABLE, TREND_UNFAVORABLE = ("TREND_UP", "TREND_DOWN"), ("RANGE",)


def _market_state(regime, vol=mic_models.VOLATILITY_NORMAL, risk=mic_models.RISK_NORMAL,
                   as_of_time="2026-08-17T09:30:00+05:30"):
    return MarketState(
        as_of_time=as_of_time, market_regime=regime, volatility_state=vol, risk_state=risk,
        recommended_environment=mic_models.ENV_TREND_FOLLOWING, evidence=("test_evidence",),
        confidence=ConfidenceInfo(level=mic_models.CONFIDENCE_LOW, sample_size=0, method="test"),
        event_context=EventContext(), data_quality="SUFFICIENT",
    )


def _observation(regime, as_of_time="2026-08-17T09:30:00+05:30"):
    evidence = StrategyEvidence(
        strategy_name="FAMILY_A_TREND_FOLLOWING", sample_size=11278, win_rate=0.665, profit_factor=2.30,
        net_expectancy=517.0, gross_expectancy=949.0,
        train_expectancy=512.0, validation_expectancy=481.0, out_of_sample_expectancy=573.0,
    )
    env = MarketEnvironment(mic_regime="TREND_UP" if regime == mic_models.REGIME_TREND else "TRANSITION",
                             risk_state=mic_models.RISK_NORMAL, volatility_state=mic_models.VOLATILITY_NORMAL,
                             execution_profile_name="NORMAL")
    score = score_strategy(evidence)
    assessment = evaluate_opportunity(score, env, TREND_FAVORABLE, TREND_UNFAVORABLE)
    allocation = assess_risk_allocation(OpportunityCandidate(assessment=assessment))
    portfolio = rank_portfolio_choices([allocation])
    final_decision = compose_decision(allocation, portfolio)
    market_state = _market_state(regime, as_of_time=as_of_time)
    return run_shadow_cycle(market_state, final_decision, as_of_time), score


# --------------------------------------------------------------------- #
# 1. Memory persistence
# --------------------------------------------------------------------- #
def test_memory_persists_and_reloads(tmp_path):
    observation, _ = _observation(mic_models.REGIME_TREND)
    market_record = build_market_memory_record(observation)
    decision_record = build_decision_memory_record(observation)

    store = EventStore(str(tmp_path / "memory.jsonl"))
    record_market_memory(store, market_record, session_id="test-session")
    record_decision_memory(store, decision_record, session_id="test-session")

    reloaded_market = read_all_market_memories(store)
    reloaded_decision = read_all_decision_memories(store)
    assert len(reloaded_market) == 1
    assert len(reloaded_decision) == 1
    assert reloaded_market[0] == market_record
    assert reloaded_decision[0] == decision_record


# --------------------------------------------------------------------- #
# 2. Historical records immutable
# --------------------------------------------------------------------- #
def test_no_update_or_delete_api_exists():
    import bujji.market_memory.store as store_module
    forbidden_names = ("update_market_memory", "delete_market_memory", "update_decision_memory",
                        "delete_decision_memory", "mutate", "overwrite")
    exported = dir(store_module)
    for name in forbidden_names:
        assert name not in exported


def test_reread_is_byte_identical(tmp_path):
    observation, _ = _observation(mic_models.REGIME_RANGE)
    record = build_market_memory_record(observation)
    store = EventStore(str(tmp_path / "memory.jsonl"))
    record_market_memory(store, record, session_id="s1")

    first_read = read_all_market_memories(store)
    second_read = read_all_market_memories(store)
    assert first_read == second_read


# --------------------------------------------------------------------- #
# 3. Similar-condition retrieval
# --------------------------------------------------------------------- #
def test_similar_conditions_found_by_exact_regime_match():
    obs1, _ = _observation(mic_models.REGIME_TREND, as_of_time="2026-08-10T09:30:00+05:30")
    obs2, _ = _observation(mic_models.REGIME_TREND, as_of_time="2026-08-11T09:30:00+05:30")
    obs3, _ = _observation(mic_models.REGIME_RANGE, as_of_time="2026-08-12T09:30:00+05:30")
    target, _ = _observation(mic_models.REGIME_TREND, as_of_time="2026-08-17T09:30:00+05:30")

    records = [build_market_memory_record(o) for o in (obs1, obs2, obs3)]
    target_record = build_market_memory_record(target)
    index = build_index(records)

    similar = find_similar_conditions_as_of(target_record, index, as_of_time=target_record.as_of_time)
    assert len(similar) == 3
    # The two TREND-regime records should score higher than the RANGE one.
    top_two_ids = {rec.memory_id for rec, _ in similar[:2]}
    assert build_market_memory_record(obs1).memory_id in top_two_ids
    assert build_market_memory_record(obs2).memory_id in top_two_ids


def test_no_lookahead_future_records_excluded():
    earlier, _ = _observation(mic_models.REGIME_TREND, as_of_time="2026-08-10T09:30:00+05:30")
    later, _ = _observation(mic_models.REGIME_TREND, as_of_time="2026-08-20T09:30:00+05:30")
    target, _ = _observation(mic_models.REGIME_TREND, as_of_time="2026-08-15T09:30:00+05:30")

    records = [build_market_memory_record(earlier), build_market_memory_record(later)]
    target_record = build_market_memory_record(target)
    index = build_index(records)

    similar = find_similar_conditions_as_of(target_record, index, as_of_time=target_record.as_of_time)
    found_ids = {rec.memory_id for rec, _ in similar}
    assert build_market_memory_record(earlier).memory_id in found_ids
    assert build_market_memory_record(later).memory_id not in found_ids


# --------------------------------------------------------------------- #
# 4. Missing memory honesty
# --------------------------------------------------------------------- #
def test_empty_universe_returns_honest_empty_result():
    target, _ = _observation(mic_models.REGIME_TREND)
    target_record = build_market_memory_record(target)
    index = build_index([])
    similar = find_similar_conditions_as_of(target_record, index, as_of_time=target_record.as_of_time)
    assert similar == []

    context = build_memory_context(target_record, index, [], as_of_time=target_record.as_of_time)
    assert context.similar_count == 0
    text = explain_memory_context(context)
    assert "No similar historical conditions" in text
    assert "honest absence" in text.lower()


# --------------------------------------------------------------------- #
# 5. No strategy score modification
# --------------------------------------------------------------------- #
def test_evidence_score_unchanged_by_memory_context():
    observation, score_before = _observation(mic_models.REGIME_TREND)
    market_record = build_market_memory_record(observation)
    index = build_index([market_record])

    _ = build_memory_context(market_record, index, [], as_of_time=market_record.as_of_time)

    evidence = StrategyEvidence(
        strategy_name="FAMILY_A_TREND_FOLLOWING", sample_size=11278, win_rate=0.665, profit_factor=2.30,
        net_expectancy=517.0, gross_expectancy=949.0,
        train_expectancy=512.0, validation_expectancy=481.0, out_of_sample_expectancy=573.0,
    )
    score_after = score_strategy(evidence)
    assert score_before.evidence_score == score_after.evidence_score
    assert score_before.effective_score == score_after.effective_score


# --------------------------------------------------------------------- #
# 6. MIC boundary respected
# --------------------------------------------------------------------- #
def test_market_memory_never_imports_mic_v0_engine():
    import bujji.market_memory as pkg
    pkg_dir = os.path.dirname(pkg.__file__)
    for fname in os.listdir(pkg_dir):
        if not fname.endswith(".py"):
            continue
        with open(os.path.join(pkg_dir, fname)) as f:
            source = f.read()
        assert "mic_v0.engine" not in source, f"{fname} imports mic_v0.engine -- MIC boundary violated"
        assert "compose_market_state" not in source, f"{fname} calls compose_market_state -- MIC boundary violated"


# --------------------------------------------------------------------- #
# 7. Decision chain preserved
# --------------------------------------------------------------------- #
def test_decision_orchestration_never_imported_by_market_memory():
    import bujji.market_memory as pkg
    pkg_dir = os.path.dirname(pkg.__file__)
    for fname in os.listdir(pkg_dir):
        if not fname.endswith(".py"):
            continue
        with open(os.path.join(pkg_dir, fname)) as f:
            source = f.read()
        assert "compose_decision" not in source, f"{fname} calls compose_decision -- decision chain not preserved"


# --------------------------------------------------------------------- #
# 8. Explainability survives storage/retrieval
# --------------------------------------------------------------------- #
def test_explainability_survives_storage_and_retrieval(tmp_path):
    observation, _ = _observation(mic_models.REGIME_TREND, as_of_time="2026-08-10T09:30:00+05:30")
    target, _ = _observation(mic_models.REGIME_TREND, as_of_time="2026-08-17T09:30:00+05:30")

    store = EventStore(str(tmp_path / "memory.jsonl"))
    market_record = build_market_memory_record(observation)
    record_market_memory(store, market_record, session_id="s1")

    reloaded = read_all_market_memories(store)
    index = build_index(reloaded)
    target_record = build_market_memory_record(target)
    context = build_memory_context(target_record, index, [], as_of_time=target_record.as_of_time)
    text = explain_memory_context(context)
    assert "Similar historical conditions found: 1" in text
    assert market_record.memory_id in text


# --------------------------------------------------------------------- #
# 9. No execution capability
# --------------------------------------------------------------------- #
def test_no_trading_capability_in_package():
    import bujji.market_memory as pkg
    pkg_dir = os.path.dirname(pkg.__file__)
    forbidden = (".place_order(", ".modify_order(", ".cancel_order(", ".get_open_positions(",
                 "PaperBroker", "FyersBroker", "entry_price", "exit_price", "order_id", "position_size")
    for fname in os.listdir(pkg_dir):
        if not fname.endswith(".py"):
            continue
        with open(os.path.join(pkg_dir, fname)) as f:
            source = f.read()
        for term in forbidden:
            assert term not in source, f"{fname} contains forbidden term {term!r}"


def test_no_broker_module_imports():
    import bujji.market_memory as pkg
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
# Outcome memory: NOT_YET_OBSERVED -> KNOWN transition
# --------------------------------------------------------------------- #
def test_outcome_memory_honest_pending_then_known(tmp_path):
    original, _ = _observation(mic_models.REGIME_TREND, as_of_time="2026-08-10T09:30:00+05:30")
    later, _ = _observation(mic_models.REGIME_TREND, as_of_time="2026-08-15T09:30:00+05:30")

    original_record = build_market_memory_record(original)
    pending = build_pending_outcome_memory_record(original_record.memory_id)
    assert pending.status == "NOT_YET_OBSERVED"
    assert pending.observed_at is None

    later_record = build_market_memory_record(later)
    later_decision = build_decision_memory_record(later)
    known = build_known_outcome_memory_record(original_record, later_record, later_decision)
    assert known.status == "KNOWN"
    assert known.observed_at == later_record.as_of_time
    assert known.regime_unchanged is True

    store = EventStore(str(tmp_path / "memory.jsonl"))
    record_outcome_memory(store, pending, session_id="s1", recorded_at="2026-08-10T09:35:00+05:30")
    record_outcome_memory(store, known, session_id="s1", recorded_at="2026-08-15T09:35:00+05:30")
    reloaded = read_all_outcome_memories(store)
    assert len(reloaded) == 2
    statuses = {r.status for r in reloaded}
    assert statuses == {"NOT_YET_OBSERVED", "KNOWN"}


def test_outcome_memory_rejects_fabricated_known_without_observed_at():
    from bujji.market_memory.models import OutcomeMemoryRecord
    with pytest.raises(ValueError):
        OutcomeMemoryRecord(memory_id="MKTMEM-x", status="KNOWN", observed_at=None)
