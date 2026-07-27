"""Tests for the Capital Brain — Engineering Series 36, Sprint 1."""
from __future__ import annotations

import ast
import inspect
from datetime import datetime
from pathlib import Path

import pytest

from bujji.trading_brain.risk_brain.models import RiskAssessment
from bujji.trading_brain.capital_brain import config, engine, models, query, runner, serialization, taxonomy
from bujji.journal.capital_brain_journal import CapitalBrainJournal

FIXED_CLOCK = lambda: datetime(2026, 1, 1, 10, 0, 0)


def _risk(**overrides):
    base = dict(
        assessment_id="RA-0000000000000001",
        status="APPROVED",
        risk_level="LOW",
        approval="ALLOW",
        blocking_reason=None,
        warning_reasons=(),
        required_controls=(),
        confidence="VERY_HIGH",
        decision_trace="stub decision trace",
        strategy_decision_id="SD-0000000000000001",
        market_state_assessment_id="MSA-0000000000000001",
        timestamp="2026-01-01T09:45:00",
        version="1.0.0",
    )
    base.update(overrides)
    return RiskAssessment(**base)


# ---------------------------------------------------------------------------
# Taxonomy completeness
# ---------------------------------------------------------------------------

class TestTaxonomy:
    def test_capital_intent_values(self):
        assert taxonomy.ALL_CAPITAL_INTENTS == (
            "UNKNOWN", "NONE", "MINIMAL", "REDUCED", "STANDARD", "FULL",
        )

    def test_allocation_status_values(self):
        assert taxonomy.ALL_ALLOCATION_STATUSES == ("UNKNOWN", "DENIED", "LIMITED", "APPROVED")

    def test_constraint_values(self):
        assert taxonomy.ALL_CONSTRAINTS == (
            "NONE", "REDUCE_EXPOSURE", "MAX_SINGLE_POSITION", "REQUIRE_HEDGE", "MANUAL_REVIEW",
        )

    def test_required_control_values(self):
        assert taxonomy.ALL_REQUIRED_CONTROLS == ("NONE", "FOLLOW_RISK_CONTROLS", "FOLLOW_LIMITS")

    def test_every_capital_intent_has_description(self):
        for c in taxonomy.ALL_CAPITAL_INTENTS:
            assert c in taxonomy.CAPITAL_INTENT_DESCRIPTIONS and taxonomy.CAPITAL_INTENT_DESCRIPTIONS[c]

    def test_every_allocation_status_has_description(self):
        for a in taxonomy.ALL_ALLOCATION_STATUSES:
            assert a in taxonomy.ALLOCATION_STATUS_DESCRIPTIONS and taxonomy.ALLOCATION_STATUS_DESCRIPTIONS[a]

    def test_every_constraint_has_description(self):
        for c in taxonomy.ALL_CONSTRAINTS:
            assert c in taxonomy.CONSTRAINT_DESCRIPTIONS and taxonomy.CONSTRAINT_DESCRIPTIONS[c]

    def test_every_required_control_has_description(self):
        for c in taxonomy.ALL_REQUIRED_CONTROLS:
            assert c in taxonomy.REQUIRED_CONTROL_DESCRIPTIONS and taxonomy.REQUIRED_CONTROL_DESCRIPTIONS[c]


# ---------------------------------------------------------------------------
# Missing input
# ---------------------------------------------------------------------------

class TestMissingInput:
    def test_none_risk_assessment_yields_unknown(self):
        decision = engine.authorize(None, clock=FIXED_CLOCK)
        assert decision.capital_intent == "UNKNOWN"
        assert decision.allocation_status == "UNKNOWN"
        assert decision.confidence == "UNKNOWN"
        assert decision.risk_assessment_id is None

    def test_none_risk_assessment_never_fabricates_authorization(self):
        decision = engine.authorize(None, clock=FIXED_CLOCK)
        assert decision.allocation_constraints == ("NONE",)
        assert decision.required_controls == ("NONE",)


# ---------------------------------------------------------------------------
# Denied allocation
# ---------------------------------------------------------------------------

class TestDeniedAllocation:
    def test_deny_approval_yields_none_denied(self):
        decision = engine.authorize(_risk(approval="DENY", risk_level="HIGH"), clock=FIXED_CLOCK)
        assert decision.capital_intent == "NONE"
        assert decision.allocation_status == "DENIED"
        assert decision.allocation_constraints == ("NONE",)

    def test_deny_preserves_confidence(self):
        decision = engine.authorize(_risk(approval="DENY", confidence="MODERATE"), clock=FIXED_CLOCK)
        assert decision.confidence == "MODERATE"

    def test_extreme_risk_overrides_to_denied_regardless_of_approval(self):
        decision = engine.authorize(_risk(approval="ALLOW", risk_level="EXTREME"), clock=FIXED_CLOCK)
        assert decision.capital_intent == "NONE"
        assert decision.allocation_status == "DENIED"
        assert "MANUAL_REVIEW" in decision.allocation_constraints

    def test_extreme_risk_downgrades_confidence(self):
        decision = engine.authorize(_risk(approval="DENY", risk_level="EXTREME", confidence="HIGH"), clock=FIXED_CLOCK)
        assert decision.confidence == "MODERATE"

    def test_unknown_approval_yields_unknown(self):
        decision = engine.authorize(_risk(approval="UNKNOWN", risk_level="UNKNOWN", confidence="UNKNOWN"), clock=FIXED_CLOCK)
        assert decision.capital_intent == "UNKNOWN"
        assert decision.allocation_status == "UNKNOWN"


# ---------------------------------------------------------------------------
# Limited allocation
# ---------------------------------------------------------------------------

class TestLimitedAllocation:
    def test_allow_with_controls_yields_reduced_limited(self):
        decision = engine.authorize(_risk(approval="ALLOW_WITH_CONTROLS", risk_level="MODERATE"), clock=FIXED_CLOCK)
        assert decision.capital_intent == "REDUCED"
        assert decision.allocation_status == "LIMITED"
        assert "REDUCE_EXPOSURE" in decision.allocation_constraints
        assert "FOLLOW_RISK_CONTROLS" in decision.required_controls

    def test_allow_moderate_risk_yields_reduced_limited(self):
        decision = engine.authorize(_risk(approval="ALLOW", risk_level="MODERATE"), clock=FIXED_CLOCK)
        assert decision.capital_intent == "REDUCED"
        assert decision.allocation_status == "LIMITED"
        assert "FOLLOW_LIMITS" in decision.required_controls

    def test_allow_high_risk_yields_minimal_limited(self):
        decision = engine.authorize(_risk(approval="ALLOW", risk_level="HIGH"), clock=FIXED_CLOCK)
        assert decision.capital_intent == "MINIMAL"
        assert decision.allocation_status == "LIMITED"
        assert "MAX_SINGLE_POSITION" in decision.allocation_constraints


# ---------------------------------------------------------------------------
# Approved allocation
# ---------------------------------------------------------------------------

class TestApprovedAllocation:
    def test_allow_low_risk_yields_standard_approved(self):
        decision = engine.authorize(_risk(approval="ALLOW", risk_level="LOW"), clock=FIXED_CLOCK)
        assert decision.capital_intent == "STANDARD"
        assert decision.allocation_status == "APPROVED"
        assert decision.allocation_constraints == ("NONE",)
        assert decision.required_controls == ("NONE",)

    def test_full_capital_intent_never_produced(self):
        # FULL is declared for completeness but never reachable this
        # sprint -- see docs' disclosed-limitation note.
        for risk_level in ("LOW", "MODERATE", "HIGH", "EXTREME"):
            for approval in ("ALLOW", "ALLOW_WITH_CONTROLS", "DENY"):
                decision = engine.authorize(_risk(approval=approval, risk_level=risk_level), clock=FIXED_CLOCK)
                assert decision.capital_intent != "FULL"


# ---------------------------------------------------------------------------
# Confidence preservation / downgrade
# ---------------------------------------------------------------------------

class TestConfidencePreservation:
    def test_clean_allow_low_risk_preserves_confidence(self):
        decision = engine.authorize(_risk(approval="ALLOW", risk_level="LOW", confidence="HIGH"), clock=FIXED_CLOCK)
        assert decision.confidence == "HIGH"

    def test_deny_preserves_confidence_unchanged(self):
        decision = engine.authorize(_risk(approval="DENY", confidence="LOW"), clock=FIXED_CLOCK)
        assert decision.confidence == "LOW"


class TestConfidenceDowngrade:
    def test_allow_with_controls_downgrades_one_step(self):
        decision = engine.authorize(_risk(approval="ALLOW_WITH_CONTROLS", confidence="HIGH"), clock=FIXED_CLOCK)
        assert decision.confidence == "MODERATE"

    def test_moderate_risk_downgrades_one_step(self):
        decision = engine.authorize(_risk(approval="ALLOW", risk_level="MODERATE", confidence="VERY_HIGH"), clock=FIXED_CLOCK)
        assert decision.confidence == "HIGH"

    def test_high_risk_downgrades_one_step(self):
        decision = engine.authorize(_risk(approval="ALLOW", risk_level="HIGH", confidence="MODERATE"), clock=FIXED_CLOCK)
        assert decision.confidence == "LOW"

    def test_never_increases_confidence(self):
        for level in taxonomy.CONFIDENCE_ORDER:
            decision = engine.authorize(_risk(approval="ALLOW", risk_level="MODERATE", confidence=level), clock=FIXED_CLOCK)
            start_idx = taxonomy.CONFIDENCE_ORDER.index(level)
            end_idx = taxonomy.CONFIDENCE_ORDER.index(decision.confidence)
            assert end_idx <= start_idx


# ---------------------------------------------------------------------------
# Decision trace
# ---------------------------------------------------------------------------

class TestDecisionTrace:
    def test_trace_mentions_capital_intent(self):
        decision = engine.authorize(_risk(), clock=FIXED_CLOCK)
        assert decision.capital_intent in decision.decision_trace

    def test_trace_mentions_allocation_status(self):
        decision = engine.authorize(_risk(), clock=FIXED_CLOCK)
        assert decision.allocation_status in decision.decision_trace


# ---------------------------------------------------------------------------
# Determinism / replay
# ---------------------------------------------------------------------------

class TestDeterminism:
    def test_same_input_same_decision_id(self):
        r = _risk()
        d1 = engine.authorize(r, clock=FIXED_CLOCK)
        d2 = engine.authorize(r, clock=FIXED_CLOCK)
        assert d1.decision_id == d2.decision_id
        assert d1 == d2

    def test_byte_identical_across_repeated_runs(self):
        r = _risk(approval="ALLOW_WITH_CONTROLS")
        results = [engine.authorize(r, clock=FIXED_CLOCK) for _ in range(5)]
        assert all(x == results[0] for x in results)

    def test_different_input_different_decision_id(self):
        d1 = engine.authorize(_risk(), clock=FIXED_CLOCK)
        d2 = engine.authorize(_risk(approval="DENY"), clock=FIXED_CLOCK)
        assert d1.decision_id != d2.decision_id

    def test_decision_id_uses_md5_prefix_not_uuid(self):
        decision = engine.authorize(_risk(), clock=FIXED_CLOCK)
        assert decision.decision_id.startswith("CD-")
        assert len(decision.decision_id) == len("CD-") + 16

    def test_real_clock_default_produces_wall_clock_timestamp(self):
        before = datetime.now()
        decision = engine.authorize(_risk())
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


# ---------------------------------------------------------------------------
# Frozen dataclasses / no mutation
# ---------------------------------------------------------------------------

class TestFrozen:
    def test_decision_is_frozen(self):
        decision = engine.authorize(_risk(), clock=FIXED_CLOCK)
        with pytest.raises(Exception):
            decision.capital_intent = "HACKED"

    def test_engine_does_not_mutate_input(self):
        r = _risk()
        r_copy = RiskAssessment(**{f.name: getattr(r, f.name) for f in r.__dataclass_fields__.values()})
        engine.authorize(r, clock=FIXED_CLOCK)
        assert r == r_copy


# ---------------------------------------------------------------------------
# Serialization round trip
# ---------------------------------------------------------------------------

class TestSerialization:
    def test_round_trip_preserves_all_fields(self):
        decision = engine.authorize(_risk(approval="ALLOW_WITH_CONTROLS"), clock=FIXED_CLOCK)
        d = serialization.decision_to_dict(decision)
        back = serialization.decision_from_dict(d)
        assert back == decision

    def test_round_trip_is_json_safe(self):
        import json

        decision = engine.authorize(_risk(risk_level="HIGH"), clock=FIXED_CLOCK)
        d = serialization.decision_to_dict(decision)
        text = json.dumps(d)
        back = serialization.decision_from_dict(json.loads(text))
        assert back == decision

    def test_round_trip_with_none_risk_assessment_id(self):
        decision = engine.authorize(None, clock=FIXED_CLOCK)
        d = serialization.decision_to_dict(decision)
        assert d["risk_assessment_id"] is None
        back = serialization.decision_from_dict(d)
        assert back.risk_assessment_id is None


# ---------------------------------------------------------------------------
# Journal -- append only
# ---------------------------------------------------------------------------

class TestJournal:
    def test_record_and_read_all(self, tmp_path):
        j = CapitalBrainJournal(tmp_path / "cb.jsonl")
        decision = engine.authorize(_risk(), clock=FIXED_CLOCK)
        j.record(decision)
        assert j.read_all() == [decision]

    def test_read_all_empty_when_file_missing(self, tmp_path):
        j = CapitalBrainJournal(tmp_path / "missing.jsonl")
        assert j.read_all() == []

    def test_record_many_appends_in_order(self, tmp_path):
        j = CapitalBrainJournal(tmp_path / "j.jsonl")
        d1 = engine.authorize(_risk(), clock=FIXED_CLOCK)
        d2 = engine.authorize(_risk(approval="DENY"), clock=FIXED_CLOCK)
        j.record_many([d1, d2])
        records = j.read_all()
        assert [r.decision_id for r in records] == [d1.decision_id, d2.decision_id]

    def test_journal_opens_only_in_append_or_read_mode(self):
        source = inspect.getsource(CapitalBrainJournal)
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
# runner.run_capital_authorization composition
# ---------------------------------------------------------------------------

class TestRunner:
    def test_run_without_journal(self):
        decision = runner.run_capital_authorization(_risk(), clock=FIXED_CLOCK)
        assert isinstance(decision, models.CapitalDecision)

    def test_run_journals_when_given_one(self, tmp_path):
        j = CapitalBrainJournal(tmp_path / "run.jsonl")
        decision = runner.run_capital_authorization(_risk(), clock=FIXED_CLOCK, journal=j)
        assert j.read_all() == [decision]


# ---------------------------------------------------------------------------
# Query API
# ---------------------------------------------------------------------------

class TestCapitalDecisionIndex:
    def test_ingest_and_latest(self):
        idx = query.CapitalDecisionIndex()
        d1 = engine.authorize(_risk(), clock=FIXED_CLOCK)
        idx.ingest(d1)
        assert idx.latest() == d1

    def test_latest_none_when_empty(self):
        assert query.CapitalDecisionIndex().latest() is None

    def test_history_preserves_order(self):
        idx = query.CapitalDecisionIndex()
        d1 = engine.authorize(_risk(), clock=FIXED_CLOCK)
        d2 = engine.authorize(_risk(approval="DENY"), clock=FIXED_CLOCK)
        idx.ingest(d1)
        idx.ingest(d2)
        assert idx.history() == [d1, d2]

    def test_find_by_id(self):
        idx = query.CapitalDecisionIndex()
        d1 = engine.authorize(_risk(), clock=FIXED_CLOCK)
        idx.ingest(d1)
        assert idx.find_by_id(d1.decision_id) == d1
        assert idx.find_by_id("nope") is None

    def test_find_by_capital_intent(self):
        idx = query.CapitalDecisionIndex()
        d1 = engine.authorize(_risk(), clock=FIXED_CLOCK)
        idx.ingest(d1)
        assert idx.find_by_capital_intent("STANDARD") == [d1]

    def test_find_by_allocation_status(self):
        idx = query.CapitalDecisionIndex()
        d1 = engine.authorize(_risk(approval="DENY"), clock=FIXED_CLOCK)
        idx.ingest(d1)
        assert idx.find_by_allocation_status("DENIED") == [d1]

    def test_summary_counts_by_capital_intent(self):
        idx = query.CapitalDecisionIndex()
        idx.ingest(engine.authorize(_risk(), clock=FIXED_CLOCK))
        idx.ingest(engine.authorize(_risk(approval="DENY"), clock=FIXED_CLOCK))
        assert idx.summary() == {"STANDARD": 1, "NONE": 1}


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

class TestConfig:
    def test_default_version_matches_package_version(self):
        cfg = config.CapitalBrainConfig()
        assert cfg.version == taxonomy.CAPITAL_BRAIN_VERSION

    def test_config_is_frozen(self):
        cfg = config.CapitalBrainConfig()
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

    def test_no_strategy_selector_or_market_state_or_evidence_interpreter_import(self):
        for path in _module_source_files():
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    for forbidden in ("strategy_selector", "market_state", "evidence_interpreter"):
                        assert forbidden not in node.module

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

    def test_no_broker_or_order_or_replay_import(self):
        for path in _module_source_files():
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    lowered = node.module.lower()
                    for forbidden in ("broker", "order", "replay", "execution"):
                        assert forbidden not in lowered

    def test_no_lot_or_quantity_or_margin_or_balance_fields(self):
        fields = {f.name for f in models.CapitalDecision.__dataclass_fields__.values()}
        forbidden = {
            "lots", "lot_size", "quantity", "margin", "account_balance",
            "exposure", "pnl", "expected_value", "expectancy", "probability", "score",
        }
        assert not (fields & forbidden)

    def test_no_uuid4_used_anywhere_in_package(self):
        for path in _module_source_files():
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr == "uuid4":
                    raise AssertionError(f"uuid4 used in {path.name}")

    def test_no_execution_or_lot_calculation_capability_in_source(self):
        for path in _module_source_files():
            source = path.read_text()
            for forbidden in ("place_order(", "def execute(", "def calculate_lots", "def calculate_quantity", "def calculate_margin"):
                assert forbidden not in source

    def test_no_optimization_terms_in_executable_code(self):
        tree = ast.parse(inspect.getsource(engine))
        tree.body = [
            node
            for node in tree.body
            if not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant))
        ]
        code_only = ast.unparse(tree).lower()
        for forbidden in ("kelly", "sharpe", "sortino", "monte carlo", "monte_carlo", "expected_value", "expectancy"):
            assert forbidden not in code_only
