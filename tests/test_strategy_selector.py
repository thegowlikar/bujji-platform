"""Tests for the Strategy Selector — Engineering Series 34, Sprint 1."""
from __future__ import annotations

import ast
import inspect
from datetime import datetime
from pathlib import Path

import pytest

from bujji.trading_brain.market_state.models import MarketStateAssessment
from bujji.trading_brain.strategy_selector import config, engine, models, query, registry, runner, serialization, taxonomy
from bujji.journal.strategy_selector_journal import StrategySelectorJournal

FIXED_CLOCK = lambda: datetime(2026, 1, 1, 9, 30, 0)


def _assessment(**overrides):
    base = dict(
        assessment_id="MSA-0000000000000001",
        market_state="RANGE",
        market_phase="ESTABLISHED",
        market_character="CLEAR",
        market_conviction="VERY_HIGH",
        confidence="VERY_HIGH",
        supporting_evidence=("stub supporting evidence",),
        contradicting_evidence=(),
        reasoning_trace="stub reasoning trace",
        interpretation_id="EI-0000000000000001",
        timestamp="2026-01-01T09:15:00",
        version="1.0.0",
    )
    base.update(overrides)
    return MarketStateAssessment(**base)


# ---------------------------------------------------------------------------
# Taxonomy completeness
# ---------------------------------------------------------------------------

class TestTaxonomy:
    def test_eligibility_states(self):
        assert taxonomy.ALL_ELIGIBILITY_STATES == ("ELIGIBLE", "NOT_ELIGIBLE", "UNKNOWN")

    def test_selection_statuses(self):
        assert taxonomy.ALL_SELECTION_STATUSES == ("SELECTED", "NO_STRATEGY", "UNKNOWN")

    def test_every_eligibility_state_has_description(self):
        for e in taxonomy.ALL_ELIGIBILITY_STATES:
            assert e in taxonomy.ELIGIBILITY_DESCRIPTIONS
            assert taxonomy.ELIGIBILITY_DESCRIPTIONS[e]

    def test_every_selection_status_has_description(self):
        for s in taxonomy.ALL_SELECTION_STATUSES:
            assert s in taxonomy.SELECTION_STATUS_DESCRIPTIONS
            assert taxonomy.SELECTION_STATUS_DESCRIPTIONS[s]


# ---------------------------------------------------------------------------
# Registry sanity
# ---------------------------------------------------------------------------

class TestRegistry:
    def test_eleven_strategies_registered(self):
        assert len(registry.ALL_STRATEGIES) == 11

    def test_all_strategy_ids_unique(self):
        ids = [s.strategy_id for s in registry.ALL_STRATEGIES]
        assert len(ids) == len(set(ids))

    def test_get_by_id_resolves(self):
        s = registry.get_by_id("IRON_FLY")
        assert s is not None
        assert s.name == "Iron Fly"

    def test_get_by_id_unknown_returns_none(self):
        assert registry.get_by_id("NOT_A_REAL_STRATEGY") is None

    def test_no_executable_logic_fields(self):
        forbidden = {"entry_rule", "exit_rule", "lot_size", "position_size", "order_type"}
        fields = {f.name for f in registry.StrategyDefinition.__dataclass_fields__.values()}
        assert not (fields & forbidden)


# ---------------------------------------------------------------------------
# Every registered strategy -- eligible under its own ideal conditions
# ---------------------------------------------------------------------------

class TestEveryRegisteredStrategy:
    @pytest.mark.parametrize("strategy", registry.ALL_STRATEGIES, ids=lambda s: s.strategy_id)
    def test_eligible_under_its_own_ideal_conditions(self, strategy):
        assessment = _assessment(
            market_state=strategy.required_market_states[0],
            market_character="CLEAR",
            confidence="VERY_HIGH",
        )
        evaluation = engine.evaluate_strategy(strategy, assessment)
        assert evaluation.eligibility == "ELIGIBLE"
        assert evaluation.rejecting_conditions == ()

    @pytest.mark.parametrize("strategy", registry.ALL_STRATEGIES, ids=lambda s: s.strategy_id)
    def test_not_eligible_under_forbidden_state(self, strategy):
        if not strategy.forbidden_market_states:
            pytest.skip("no forbidden states declared")
        assessment = _assessment(
            market_state=strategy.forbidden_market_states[0],
            market_character="CLEAR",
            confidence="VERY_HIGH",
        )
        evaluation = engine.evaluate_strategy(strategy, assessment)
        assert evaluation.eligibility == "NOT_ELIGIBLE"
        assert evaluation.rejecting_conditions


# ---------------------------------------------------------------------------
# Selection outcomes
# ---------------------------------------------------------------------------

class TestSelectedOutcome:
    def test_single_eligible_strategy_is_selected(self):
        assessment = _assessment(
            market_state="BREAKOUT", market_character="CONTESTED", confidence="LOW"
        )
        decision = engine.select(assessment, clock=FIXED_CLOCK)
        assert decision.selection_status == "SELECTED"
        assert decision.selected_strategy == "LONG_STRANGLE"
        assert decision.alternative_candidates == ()

    def test_selection_reason_matches_supporting_conditions(self):
        assessment = _assessment(
            market_state="BREAKOUT", market_character="CONTESTED", confidence="LOW"
        )
        decision = engine.select(assessment, clock=FIXED_CLOCK)
        assert decision.selection_reason == "; ".join(decision.supporting_conditions)


class TestMultipleEligibleAndTieBreak:
    def test_multiple_eligible_yields_deterministic_first_in_registry_order(self):
        assessment = _assessment(market_state="RANGE", market_character="CLEAR", confidence="VERY_HIGH")
        decision = engine.select(assessment, clock=FIXED_CLOCK)
        eligible_ids = [
            ev.strategy_id for ev in decision.all_evaluations if ev.eligibility == "ELIGIBLE"
        ]
        assert len(eligible_ids) > 1
        assert decision.selected_strategy == eligible_ids[0]
        assert decision.selected_strategy == "PREMIUM_VWAP_STRADDLE"

    def test_alternatives_recorded(self):
        assessment = _assessment(market_state="RANGE", market_character="CLEAR", confidence="VERY_HIGH")
        decision = engine.select(assessment, clock=FIXED_CLOCK)
        assert "IRON_FLY" in decision.alternative_candidates

    def test_tie_break_downgrades_confidence_one_step(self):
        assessment = _assessment(market_state="RANGE", market_character="CLEAR", confidence="VERY_HIGH")
        decision = engine.select(assessment, clock=FIXED_CLOCK)
        assert decision.selection_confidence == "HIGH"

    def test_single_eligible_never_downgrades_confidence(self):
        assessment = _assessment(
            market_state="BREAKOUT", market_character="CONTESTED", confidence="LOW"
        )
        decision = engine.select(assessment, clock=FIXED_CLOCK)
        assert decision.selection_confidence == "LOW"

    def test_tie_break_documented_in_trace(self):
        assessment = _assessment(market_state="RANGE", market_character="CLEAR", confidence="VERY_HIGH")
        decision = engine.select(assessment, clock=FIXED_CLOCK)
        assert "tie-break" in decision.decision_trace.lower()


class TestNoEligibleStrategy:
    def test_weak_evidence_yields_no_strategy(self):
        assessment = _assessment(market_state="RANGE", market_character="UNCERTAIN", confidence="VERY_LOW")
        decision = engine.select(assessment, clock=FIXED_CLOCK)
        assert decision.selection_status == "NO_STRATEGY"
        assert decision.selected_strategy is None

    def test_no_strategy_never_forces_a_trade(self):
        assessment = _assessment(market_state="RANGE", market_character="UNCERTAIN", confidence="VERY_LOW")
        decision = engine.select(assessment, clock=FIXED_CLOCK)
        assert decision.selected_strategy is None
        assert decision.alternative_candidates == ()

    def test_no_strategy_records_all_rejections(self):
        assessment = _assessment(market_state="RANGE", market_character="UNCERTAIN", confidence="VERY_LOW")
        decision = engine.select(assessment, clock=FIXED_CLOCK)
        assert len(decision.rejecting_conditions) == len(registry.ALL_STRATEGIES)


class TestConflictingMarketState:
    def test_unknown_market_state_yields_unknown_eligibility_for_all(self):
        assessment = _assessment(market_state="UNKNOWN", market_character="UNCERTAIN", confidence="UNKNOWN")
        decision = engine.select(assessment, clock=FIXED_CLOCK)
        assert all(ev.eligibility == "UNKNOWN" for ev in decision.all_evaluations)

    def test_unknown_market_state_yields_no_strategy_overall(self):
        assessment = _assessment(market_state="UNKNOWN", market_character="UNCERTAIN", confidence="UNKNOWN")
        decision = engine.select(assessment, clock=FIXED_CLOCK)
        assert decision.selection_status == "NO_STRATEGY"
        assert decision.selection_confidence == "UNKNOWN"


class TestMissingMarketState:
    def test_none_assessment_yields_unknown_status(self):
        decision = engine.select(None, clock=FIXED_CLOCK)
        assert decision.selection_status == "UNKNOWN"
        assert decision.selected_strategy is None
        assert decision.all_evaluations == ()
        assert decision.selection_confidence == "UNKNOWN"

    def test_none_assessment_never_fabricates_a_strategy(self):
        decision = engine.select(None, clock=FIXED_CLOCK)
        assert decision.selected_strategy is None
        assert decision.market_state_assessment_id is None


# ---------------------------------------------------------------------------
# Determinism / replay / no randomness / no ML / no ranking
# ---------------------------------------------------------------------------

class TestDeterminism:
    def test_same_assessment_same_decision_id(self):
        a = _assessment()
        d1 = engine.select(a, clock=FIXED_CLOCK)
        d2 = engine.select(a, clock=FIXED_CLOCK)
        assert d1.decision_id == d2.decision_id
        assert d1 == d2

    def test_byte_identical_across_repeated_runs(self):
        a = _assessment(market_state="BREAKOUT", market_character="CONTESTED", confidence="LOW")
        results = [engine.select(a, clock=FIXED_CLOCK) for _ in range(5)]
        assert all(r == results[0] for r in results)

    def test_different_assessment_different_decision_id(self):
        d1 = engine.select(_assessment(), clock=FIXED_CLOCK)
        d2 = engine.select(_assessment(market_state="BREAKOUT", market_character="CONTESTED", confidence="LOW"), clock=FIXED_CLOCK)
        assert d1.decision_id != d2.decision_id

    def test_decision_id_uses_md5_prefix_not_uuid(self):
        decision = engine.select(_assessment(), clock=FIXED_CLOCK)
        assert decision.decision_id.startswith("SD-")
        assert len(decision.decision_id) == len("SD-") + 16

    def test_real_clock_default_produces_wall_clock_timestamp(self):
        before = datetime.now()
        decision = engine.select(_assessment())
        after = datetime.now()
        parsed = datetime.fromisoformat(decision.timestamp)
        assert before <= parsed <= after

    def test_no_randomness_module_used(self):
        source = inspect.getsource(engine)
        assert "import random" not in source
        assert "random." not in source

    def test_no_ml_library_imports(self):
        source = inspect.getsource(engine)
        for forbidden in ("sklearn", "torch", "tensorflow", "numpy"):
            assert forbidden not in source

    def test_no_sorting_or_ranking_by_score(self):
        source = inspect.getsource(engine)
        assert "sorted(" not in source
        assert ".sort(" not in source


# ---------------------------------------------------------------------------
# Frozen dataclasses / no mutation
# ---------------------------------------------------------------------------

class TestFrozen:
    def test_decision_is_frozen(self):
        decision = engine.select(_assessment(), clock=FIXED_CLOCK)
        with pytest.raises(Exception):
            decision.selected_strategy = "HACKED"

    def test_evaluation_is_frozen(self):
        decision = engine.select(_assessment(), clock=FIXED_CLOCK)
        with pytest.raises(Exception):
            decision.all_evaluations[0].eligibility = "HACKED"

    def test_engine_does_not_mutate_input_assessment(self):
        a = _assessment()
        original = MarketStateAssessment(**{f.name: getattr(a, f.name) for f in a.__dataclass_fields__.values()})
        engine.select(a, clock=FIXED_CLOCK)
        assert a == original

    def test_registry_definitions_are_frozen(self):
        strategy = registry.ALL_STRATEGIES[0]
        with pytest.raises(Exception):
            strategy.name = "HACKED"


# ---------------------------------------------------------------------------
# Serialization round trip
# ---------------------------------------------------------------------------

class TestSerialization:
    def test_round_trip_preserves_all_fields(self):
        decision = engine.select(_assessment(), clock=FIXED_CLOCK)
        d = serialization.decision_to_dict(decision)
        back = serialization.decision_from_dict(d)
        assert back == decision

    def test_round_trip_is_json_safe(self):
        import json

        decision = engine.select(_assessment(market_state="RANGE", market_character="CLEAR", confidence="VERY_HIGH"), clock=FIXED_CLOCK)
        d = serialization.decision_to_dict(decision)
        text = json.dumps(d)
        back = serialization.decision_from_dict(json.loads(text))
        assert back == decision

    def test_round_trip_with_none_assessment(self):
        decision = engine.select(None, clock=FIXED_CLOCK)
        d = serialization.decision_to_dict(decision)
        assert d["selected_strategy"] is None
        assert d["market_state_assessment_id"] is None
        back = serialization.decision_from_dict(d)
        assert back == decision


# ---------------------------------------------------------------------------
# Journal -- append only
# ---------------------------------------------------------------------------

class TestJournal:
    def test_record_and_read_all(self, tmp_path):
        j = StrategySelectorJournal(tmp_path / "ss.jsonl")
        decision = engine.select(_assessment(), clock=FIXED_CLOCK)
        j.record(decision)
        assert j.read_all() == [decision]

    def test_read_all_empty_when_file_missing(self, tmp_path):
        j = StrategySelectorJournal(tmp_path / "missing.jsonl")
        assert j.read_all() == []

    def test_record_many_appends_in_order(self, tmp_path):
        j = StrategySelectorJournal(tmp_path / "j.jsonl")
        d1 = engine.select(_assessment(market_state="BREAKOUT", market_character="CONTESTED", confidence="LOW"), clock=FIXED_CLOCK)
        d2 = engine.select(None, clock=FIXED_CLOCK)
        j.record_many([d1, d2])
        records = j.read_all()
        assert [r.decision_id for r in records] == [d1.decision_id, d2.decision_id]

    def test_journal_opens_only_in_append_or_read_mode(self):
        source = inspect.getsource(StrategySelectorJournal)
        tree = ast.parse(source)
        open_calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "open"
        ]
        assert open_calls
        for call in open_calls:
            modes = [a.value for a in call.args if isinstance(a, ast.Constant)]
            modes += [
                kw.value.value
                for kw in call.keywords
                if kw.arg == "mode" and isinstance(kw.value, ast.Constant)
            ]
            assert all(m in ("a", "r") for m in modes if isinstance(m, str))


# ---------------------------------------------------------------------------
# runner.run_selection composition
# ---------------------------------------------------------------------------

class TestRunner:
    def test_run_selection_without_journal(self):
        decision = runner.run_selection(_assessment(), clock=FIXED_CLOCK)
        assert isinstance(decision, models.StrategyDecision)

    def test_run_selection_journals_when_given_one(self, tmp_path):
        j = StrategySelectorJournal(tmp_path / "run.jsonl")
        decision = runner.run_selection(_assessment(), clock=FIXED_CLOCK, journal=j)
        assert j.read_all() == [decision]


# ---------------------------------------------------------------------------
# Query API
# ---------------------------------------------------------------------------

class TestStrategyDecisionIndex:
    def test_ingest_and_latest(self):
        idx = query.StrategyDecisionIndex()
        d1 = engine.select(_assessment(), clock=FIXED_CLOCK)
        idx.ingest(d1)
        assert idx.latest() == d1

    def test_latest_none_when_empty(self):
        assert query.StrategyDecisionIndex().latest() is None

    def test_history_preserves_order(self):
        idx = query.StrategyDecisionIndex()
        d1 = engine.select(_assessment(market_state="BREAKOUT", market_character="CONTESTED", confidence="LOW"), clock=FIXED_CLOCK)
        d2 = engine.select(None, clock=FIXED_CLOCK)
        idx.ingest(d1)
        idx.ingest(d2)
        assert idx.history() == [d1, d2]

    def test_find_by_id(self):
        idx = query.StrategyDecisionIndex()
        d1 = engine.select(_assessment(), clock=FIXED_CLOCK)
        idx.ingest(d1)
        assert idx.find_by_id(d1.decision_id) == d1
        assert idx.find_by_id("nope") is None

    def test_find_by_selected_strategy(self):
        idx = query.StrategyDecisionIndex()
        d1 = engine.select(_assessment(), clock=FIXED_CLOCK)
        idx.ingest(d1)
        assert idx.find_by_selected_strategy("PREMIUM_VWAP_STRADDLE") == [d1]

    def test_find_by_status(self):
        idx = query.StrategyDecisionIndex()
        d1 = engine.select(None, clock=FIXED_CLOCK)
        idx.ingest(d1)
        assert idx.find_by_status("UNKNOWN") == [d1]


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

class TestConfig:
    def test_default_version_matches_package_version(self):
        cfg = config.StrategySelectorConfig()
        assert cfg.version == taxonomy.STRATEGY_SELECTOR_VERSION

    def test_config_is_frozen(self):
        cfg = config.StrategySelectorConfig()
        with pytest.raises(Exception):
            cfg.version = "9.9.9"


# ---------------------------------------------------------------------------
# Isolation firewall
# ---------------------------------------------------------------------------

FORBIDDEN_BUJJI_SUBPACKAGES = {
    "broker", "core", "execution", "trade", "intelligence", "market", "tick",
}


def _module_source_files():
    base = Path(engine.__file__).parent
    return list(base.glob("*.py"))


class TestIsolation:
    def test_no_mic_v2_import_anywhere(self):
        for path in _module_source_files():
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        assert "mic_v2" not in alias.name
                if isinstance(node, ast.ImportFrom) and node.module:
                    assert "mic_v2" not in node.module

    def test_no_evidence_interpreter_import(self):
        for path in _module_source_files():
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    assert "evidence_interpreter" not in node.module

    def test_no_forbidden_bujji_subpackage_imports(self):
        for path in _module_source_files():
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    parts = node.module.split(".")
                    if "bujji" in parts:
                        idx = parts.index("bujji")
                        rest = parts[idx + 1:]
                        assert not (rest and rest[0] in FORBIDDEN_BUJJI_SUBPACKAGES), (
                            f"{path.name} imports forbidden bujji.{rest[0]}"
                        )

    def test_no_publication_or_consumer_model_import(self):
        for path in _module_source_files():
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    assert "publication" not in node.module.lower()
                    assert "consumer" not in node.module.lower()

    def test_no_broker_or_candle_or_feed_import(self):
        for path in _module_source_files():
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    lowered = node.module.lower()
                    for forbidden in ("broker", "candle", "feed", "replay"):
                        assert forbidden not in lowered

    def test_no_execution_or_risk_or_capital_fields(self):
        decision_fields = {f.name for f in models.StrategyDecision.__dataclass_fields__.values()}
        forbidden = {
            "position_size", "lot_size", "order_id", "risk_allocation", "capital_allocation",
            "pnl", "expected_value", "expectancy", "rank", "probability", "score",
        }
        assert not (decision_fields & forbidden)

    def test_no_uuid4_used_anywhere_in_package(self):
        for path in _module_source_files():
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr == "uuid4":
                    raise AssertionError(f"uuid4 used in {path.name}")

    def test_registry_has_no_execute_or_place_order_methods(self):
        source = inspect.getsource(registry)
        for forbidden in ("place_order", "execute(", "def run(", "def size_position"):
            assert forbidden not in source
