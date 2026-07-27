"""Tests for the Risk Brain — Engineering Series 35, Sprint 1."""
from __future__ import annotations

import ast
import inspect
from datetime import datetime
from pathlib import Path

import pytest

from bujji.trading_brain.market_state.models import MarketStateAssessment
from bujji.trading_brain.strategy_selector.models import StrategyDecision
from bujji.trading_brain.risk_brain import config, engine, models, query, runner, serialization, taxonomy
from bujji.journal.risk_brain_journal import RiskBrainJournal

FIXED_CLOCK = lambda: datetime(2026, 1, 1, 9, 45, 0)


def _market(**overrides):
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


def _decision(**overrides):
    base = dict(
        decision_id="SD-0000000000000001",
        selected_strategy="PREMIUM_VWAP_STRADDLE",
        selection_status="SELECTED",
        selection_confidence="VERY_HIGH",
        selection_reason="stub selection reason",
        supporting_conditions=("stub supporting condition",),
        rejecting_conditions=(),
        alternative_candidates=(),
        all_evaluations=(),
        decision_trace="stub decision trace",
        market_state_assessment_id="MSA-0000000000000001",
        timestamp="2026-01-01T09:30:00",
        version="1.0.0",
    )
    base.update(overrides)
    return StrategyDecision(**base)


# ---------------------------------------------------------------------------
# Taxonomy completeness
# ---------------------------------------------------------------------------

class TestTaxonomy:
    def test_status_values(self):
        assert taxonomy.ALL_STATUSES == (
            "UNKNOWN", "APPROVED", "APPROVED_WITH_WARNINGS", "REJECTED", "INSUFFICIENT_EVIDENCE",
        )

    def test_risk_level_values(self):
        assert taxonomy.ALL_RISK_LEVELS == ("UNKNOWN", "LOW", "MODERATE", "HIGH", "EXTREME")

    def test_approval_values(self):
        assert taxonomy.ALL_APPROVALS == ("UNKNOWN", "ALLOW", "ALLOW_WITH_CONTROLS", "DENY")

    def test_required_control_values(self):
        assert taxonomy.ALL_REQUIRED_CONTROLS == (
            "UNKNOWN", "REDUCE_SIZE", "REQUIRE_TIGHTER_STOP", "MONITOR_MORE_FREQUENTLY",
        )

    def test_blocking_reason_values(self):
        assert taxonomy.ALL_BLOCKING_REASONS == (
            "LOW_CONFIDENCE", "CONTRADICTORY_EVIDENCE", "UNKNOWN_MARKET",
            "NO_STRATEGY", "INSUFFICIENT_DATA", "UNSUPPORTED_STRATEGY",
        )

    def test_warning_reason_values(self):
        assert taxonomy.ALL_WARNING_REASONS == (
            "CONTESTED_MARKET", "LOW_CONFIDENCE", "HIGH_VARIABILITY", "WEAK_EVIDENCE",
        )

    def test_every_status_has_description(self):
        for s in taxonomy.ALL_STATUSES:
            assert s in taxonomy.STATUS_DESCRIPTIONS and taxonomy.STATUS_DESCRIPTIONS[s]

    def test_every_risk_level_has_description(self):
        for r in taxonomy.ALL_RISK_LEVELS:
            assert r in taxonomy.RISK_LEVEL_DESCRIPTIONS and taxonomy.RISK_LEVEL_DESCRIPTIONS[r]

    def test_every_approval_has_description(self):
        for a in taxonomy.ALL_APPROVALS:
            assert a in taxonomy.APPROVAL_DESCRIPTIONS and taxonomy.APPROVAL_DESCRIPTIONS[a]


# ---------------------------------------------------------------------------
# Approved
# ---------------------------------------------------------------------------

class TestApproved:
    def test_clear_high_confidence_is_approved(self):
        assessment = engine.assess(_decision(), _market(), clock=FIXED_CLOCK)
        assert assessment.status == "APPROVED"
        assert assessment.approval == "ALLOW"
        assert assessment.risk_level == "LOW"
        assert assessment.warning_reasons == ()
        assert assessment.required_controls == ()
        assert assessment.blocking_reason is None

    def test_approved_confidence_preserved(self):
        assessment = engine.assess(_decision(), _market(confidence="HIGH"), clock=FIXED_CLOCK)
        assert assessment.confidence == "HIGH"


# ---------------------------------------------------------------------------
# Approved with warnings
# ---------------------------------------------------------------------------

class TestApprovedWithWarnings:
    def test_contested_market_yields_warnings(self):
        assessment = engine.assess(_decision(), _market(market_character="CONTESTED"), clock=FIXED_CLOCK)
        assert assessment.status == "APPROVED_WITH_WARNINGS"
        assert assessment.approval == "ALLOW_WITH_CONTROLS"
        assert "CONTESTED_MARKET" in assessment.warning_reasons
        assert "MONITOR_MORE_FREQUENTLY" in assessment.required_controls

    def test_moderate_confidence_clear_market_yields_warnings(self):
        assessment = engine.assess(_decision(), _market(confidence="MODERATE"), clock=FIXED_CLOCK)
        assert assessment.status == "APPROVED_WITH_WARNINGS"
        assert "LOW_CONFIDENCE" in assessment.warning_reasons
        assert assessment.risk_level == "MODERATE"

    def test_low_confidence_clear_market_yields_reduce_size_control(self):
        assessment = engine.assess(_decision(), _market(confidence="LOW"), clock=FIXED_CLOCK)
        assert assessment.status == "APPROVED_WITH_WARNINGS"
        assert "REDUCE_SIZE" in assessment.required_controls
        assert assessment.risk_level == "HIGH"

    def test_weak_evidence_triggers_warning(self):
        assessment = engine.assess(
            _decision(), _market(supporting_evidence=()), clock=FIXED_CLOCK
        )
        assert "WEAK_EVIDENCE" in assessment.warning_reasons
        assert assessment.status == "APPROVED_WITH_WARNINGS"

    def test_unstable_phase_triggers_high_variability_and_bumps_risk(self):
        assessment = engine.assess(_decision(), _market(market_phase="UNSTABLE"), clock=FIXED_CLOCK)
        assert "HIGH_VARIABILITY" in assessment.warning_reasons
        assert assessment.risk_level == "MODERATE"  # bumped from LOW
        assert assessment.status == "APPROVED_WITH_WARNINGS"


class TestWarningGeneration:
    def test_warning_generation_never_produces_free_text(self):
        assessment = engine.assess(_decision(), _market(market_character="CONTESTED"), clock=FIXED_CLOCK)
        for w in assessment.warning_reasons:
            assert w in taxonomy.ALL_WARNING_REASONS


class TestRequiredControls:
    def test_approved_has_no_controls(self):
        assessment = engine.assess(_decision(), _market(), clock=FIXED_CLOCK)
        assert assessment.required_controls == ()

    def test_controls_only_from_finite_vocabulary(self):
        assessment = engine.assess(_decision(), _market(confidence="LOW"), clock=FIXED_CLOCK)
        for c in assessment.required_controls:
            assert c in taxonomy.ALL_REQUIRED_CONTROLS


# ---------------------------------------------------------------------------
# Confidence downgrade -- never invented, never increased
# ---------------------------------------------------------------------------

class TestConfidenceDowngrade:
    def test_downgrade_exactly_one_step_on_warning(self):
        assessment = engine.assess(_decision(), _market(market_character="CONTESTED", confidence="HIGH"), clock=FIXED_CLOCK)
        assert assessment.confidence == "MODERATE"

    def test_no_downgrade_beyond_one_step_for_multiple_warnings(self):
        assessment = engine.assess(
            _decision(),
            _market(confidence="LOW", supporting_evidence=(), market_phase="UNSTABLE"),
            clock=FIXED_CLOCK,
        )
        assert assessment.confidence == "VERY_LOW"

    def test_never_upgrades_confidence(self):
        for level in taxonomy.CONFIDENCE_ORDER:
            assessment = engine.assess(_decision(), _market(market_character="CONTESTED", confidence=level), clock=FIXED_CLOCK)
            start_idx = taxonomy.CONFIDENCE_ORDER.index(level)
            end_idx = taxonomy.CONFIDENCE_ORDER.index(assessment.confidence)
            assert end_idx <= start_idx


# ---------------------------------------------------------------------------
# Rejected
# ---------------------------------------------------------------------------

class TestRejected:
    def test_uncertain_market_is_rejected(self):
        assessment = engine.assess(
            _decision(), _market(market_character="UNCERTAIN", market_state="UNKNOWN", confidence="UNKNOWN"),
            clock=FIXED_CLOCK,
        )
        # Confidence UNKNOWN would hit rule 5 first; use a defensively
        # inconsistent but structurally possible combination instead.
        assert assessment.status in ("REJECTED", "INSUFFICIENT_EVIDENCE")

    def test_uncertain_market_with_real_confidence_is_rejected(self):
        assessment = engine.assess(
            _decision(), _market(market_character="UNCERTAIN"), clock=FIXED_CLOCK
        )
        assert assessment.status == "REJECTED"
        assert assessment.approval == "DENY"
        assert assessment.blocking_reason == "CONTRADICTORY_EVIDENCE"
        assert assessment.risk_level == "HIGH"

    def test_mixed_market_is_rejected(self):
        assessment = engine.assess(_decision(), _market(market_character="MIXED"), clock=FIXED_CLOCK)
        assert assessment.status == "REJECTED"
        assert assessment.blocking_reason == "UNKNOWN_MARKET"

    def test_no_strategy_selected_is_rejected(self):
        decision = _decision(selected_strategy=None, selection_status="NO_STRATEGY")
        assessment = engine.assess(decision, _market(), clock=FIXED_CLOCK)
        assert assessment.status == "REJECTED"
        assert assessment.blocking_reason == "NO_STRATEGY"

    def test_missing_strategy_decision_is_rejected(self):
        assessment = engine.assess(None, _market(), clock=FIXED_CLOCK)
        assert assessment.status == "REJECTED"
        assert assessment.blocking_reason == "NO_STRATEGY"

    def test_unsupported_strategy_defensive_check(self):
        decision = _decision(selected_strategy="", selection_status="SELECTED")
        assessment = engine.assess(decision, _market(), clock=FIXED_CLOCK)
        assert assessment.status == "REJECTED"
        assert assessment.blocking_reason == "UNSUPPORTED_STRATEGY"

    def test_rejected_never_has_warnings_or_controls(self):
        assessment = engine.assess(_decision(), _market(market_character="UNCERTAIN"), clock=FIXED_CLOCK)
        assert assessment.warning_reasons == ()
        assert assessment.required_controls == ()


# ---------------------------------------------------------------------------
# Insufficient evidence
# ---------------------------------------------------------------------------

class TestInsufficientEvidence:
    def test_missing_market_assessment_is_insufficient_evidence(self):
        assessment = engine.assess(_decision(), None, clock=FIXED_CLOCK)
        assert assessment.status == "INSUFFICIENT_EVIDENCE"
        assert assessment.approval == "UNKNOWN"
        assert assessment.blocking_reason == "INSUFFICIENT_DATA"
        assert assessment.confidence == "UNKNOWN"

    def test_market_character_insufficient_evidence_propagates(self):
        assessment = engine.assess(
            _decision(), _market(market_character="INSUFFICIENT_EVIDENCE", market_state="UNKNOWN"), clock=FIXED_CLOCK
        )
        assert assessment.status == "INSUFFICIENT_EVIDENCE"
        assert assessment.blocking_reason == "INSUFFICIENT_DATA"

    def test_unknown_confidence_yields_insufficient_evidence(self):
        assessment = engine.assess(
            _decision(), _market(confidence="UNKNOWN", market_character="MIXED", market_state="UNKNOWN"), clock=FIXED_CLOCK
        )
        assert assessment.status == "INSUFFICIENT_EVIDENCE"

    def test_insufficient_evidence_never_fabricates_confidence(self):
        assessment = engine.assess(_decision(), None, clock=FIXED_CLOCK)
        assert assessment.confidence == "UNKNOWN"


# ---------------------------------------------------------------------------
# Decision trace
# ---------------------------------------------------------------------------

class TestDecisionTrace:
    def test_trace_mentions_strategy(self):
        assessment = engine.assess(_decision(), _market(), clock=FIXED_CLOCK)
        assert "PREMIUM_VWAP_STRADDLE" in assessment.decision_trace

    def test_trace_mentions_approval(self):
        assessment = engine.assess(_decision(), _market(), clock=FIXED_CLOCK)
        assert "ALLOW" in assessment.decision_trace

    def test_trace_mentions_warnings_when_present(self):
        assessment = engine.assess(_decision(), _market(market_character="CONTESTED"), clock=FIXED_CLOCK)
        assert "CONTESTED_MARKET" in assessment.decision_trace


# ---------------------------------------------------------------------------
# Determinism / replay
# ---------------------------------------------------------------------------

class TestDeterminism:
    def test_same_inputs_same_assessment_id(self):
        a1 = engine.assess(_decision(), _market(), clock=FIXED_CLOCK)
        a2 = engine.assess(_decision(), _market(), clock=FIXED_CLOCK)
        assert a1.assessment_id == a2.assessment_id
        assert a1 == a2

    def test_byte_identical_across_repeated_runs(self):
        results = [engine.assess(_decision(), _market(market_character="CONTESTED"), clock=FIXED_CLOCK) for _ in range(5)]
        assert all(r == results[0] for r in results)

    def test_different_inputs_different_assessment_id(self):
        a1 = engine.assess(_decision(), _market(), clock=FIXED_CLOCK)
        a2 = engine.assess(_decision(), _market(market_character="CONTESTED"), clock=FIXED_CLOCK)
        assert a1.assessment_id != a2.assessment_id

    def test_assessment_id_uses_md5_prefix_not_uuid(self):
        assessment = engine.assess(_decision(), _market(), clock=FIXED_CLOCK)
        assert assessment.assessment_id.startswith("RA-")
        assert len(assessment.assessment_id) == len("RA-") + 16

    def test_real_clock_default_produces_wall_clock_timestamp(self):
        before = datetime.now()
        assessment = engine.assess(_decision(), _market())
        after = datetime.now()
        parsed = datetime.fromisoformat(assessment.timestamp)
        assert before <= parsed <= after

    def test_no_randomness_module_used(self):
        source = inspect.getsource(engine)
        assert "import random" not in source
        assert "random." not in source

    def test_no_ml_library_imports(self):
        source = inspect.getsource(engine)
        for forbidden in ("sklearn", "torch", "tensorflow", "numpy"):
            assert forbidden not in source

    def test_no_optimization_terms_in_executable_code(self):
        # The module docstring legitimately disclaims these terms in
        # prose (e.g. "no Kelly/Sharpe/Sortino"); check only the
        # executable code, not the docstring, to avoid a false
        # positive on the disclosure itself.
        tree = ast.parse(inspect.getsource(engine))
        tree.body = [
            node
            for node in tree.body
            if not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant))
        ]
        code_only = ast.unparse(tree).lower()
        for forbidden in ("kelly", "sharpe", "sortino", "monte carlo", "monte_carlo", "expected_value", "expectancy"):
            assert forbidden not in code_only


# ---------------------------------------------------------------------------
# Frozen dataclasses / no mutation
# ---------------------------------------------------------------------------

class TestFrozen:
    def test_assessment_is_frozen(self):
        assessment = engine.assess(_decision(), _market(), clock=FIXED_CLOCK)
        with pytest.raises(Exception):
            assessment.status = "HACKED"

    def test_engine_does_not_mutate_inputs(self):
        d = _decision()
        m = _market()
        d_copy = StrategyDecision(**{f.name: getattr(d, f.name) for f in d.__dataclass_fields__.values()})
        m_copy = MarketStateAssessment(**{f.name: getattr(m, f.name) for f in m.__dataclass_fields__.values()})
        engine.assess(d, m, clock=FIXED_CLOCK)
        assert d == d_copy
        assert m == m_copy


# ---------------------------------------------------------------------------
# Serialization round trip
# ---------------------------------------------------------------------------

class TestSerialization:
    def test_round_trip_preserves_all_fields(self):
        assessment = engine.assess(_decision(), _market(market_character="CONTESTED"), clock=FIXED_CLOCK)
        d = serialization.assessment_to_dict(assessment)
        back = serialization.assessment_from_dict(d)
        assert back == assessment

    def test_round_trip_is_json_safe(self):
        import json

        assessment = engine.assess(_decision(), _market(confidence="LOW"), clock=FIXED_CLOCK)
        d = serialization.assessment_to_dict(assessment)
        text = json.dumps(d)
        back = serialization.assessment_from_dict(json.loads(text))
        assert back == assessment

    def test_round_trip_with_none_blocking_reason(self):
        assessment = engine.assess(_decision(), _market(), clock=FIXED_CLOCK)
        d = serialization.assessment_to_dict(assessment)
        assert d["blocking_reason"] is None
        back = serialization.assessment_from_dict(d)
        assert back.blocking_reason is None


# ---------------------------------------------------------------------------
# Journal -- append only
# ---------------------------------------------------------------------------

class TestJournal:
    def test_record_and_read_all(self, tmp_path):
        j = RiskBrainJournal(tmp_path / "rb.jsonl")
        assessment = engine.assess(_decision(), _market(), clock=FIXED_CLOCK)
        j.record(assessment)
        assert j.read_all() == [assessment]

    def test_read_all_empty_when_file_missing(self, tmp_path):
        j = RiskBrainJournal(tmp_path / "missing.jsonl")
        assert j.read_all() == []

    def test_record_many_appends_in_order(self, tmp_path):
        j = RiskBrainJournal(tmp_path / "j.jsonl")
        a1 = engine.assess(_decision(), _market(), clock=FIXED_CLOCK)
        a2 = engine.assess(_decision(), _market(market_character="UNCERTAIN"), clock=FIXED_CLOCK)
        j.record_many([a1, a2])
        records = j.read_all()
        assert [r.assessment_id for r in records] == [a1.assessment_id, a2.assessment_id]

    def test_journal_opens_only_in_append_or_read_mode(self):
        source = inspect.getsource(RiskBrainJournal)
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
# runner.run_risk_assessment composition
# ---------------------------------------------------------------------------

class TestRunner:
    def test_run_without_journal(self):
        assessment = runner.run_risk_assessment(_decision(), _market(), clock=FIXED_CLOCK)
        assert isinstance(assessment, models.RiskAssessment)

    def test_run_journals_when_given_one(self, tmp_path):
        j = RiskBrainJournal(tmp_path / "run.jsonl")
        assessment = runner.run_risk_assessment(_decision(), _market(), clock=FIXED_CLOCK, journal=j)
        assert j.read_all() == [assessment]


# ---------------------------------------------------------------------------
# Query API
# ---------------------------------------------------------------------------

class TestRiskAssessmentIndex:
    def test_ingest_and_latest(self):
        idx = query.RiskAssessmentIndex()
        a1 = engine.assess(_decision(), _market(), clock=FIXED_CLOCK)
        idx.ingest(a1)
        assert idx.latest() == a1

    def test_latest_none_when_empty(self):
        assert query.RiskAssessmentIndex().latest() is None

    def test_history_preserves_order(self):
        idx = query.RiskAssessmentIndex()
        a1 = engine.assess(_decision(), _market(), clock=FIXED_CLOCK)
        a2 = engine.assess(_decision(), _market(market_character="UNCERTAIN"), clock=FIXED_CLOCK)
        idx.ingest(a1)
        idx.ingest(a2)
        assert idx.history() == [a1, a2]

    def test_find_by_id(self):
        idx = query.RiskAssessmentIndex()
        a1 = engine.assess(_decision(), _market(), clock=FIXED_CLOCK)
        idx.ingest(a1)
        assert idx.find_by_id(a1.assessment_id) == a1
        assert idx.find_by_id("nope") is None

    def test_find_by_status(self):
        idx = query.RiskAssessmentIndex()
        a1 = engine.assess(_decision(), _market(market_character="UNCERTAIN"), clock=FIXED_CLOCK)
        idx.ingest(a1)
        assert idx.find_by_status("REJECTED") == [a1]

    def test_find_by_approval(self):
        idx = query.RiskAssessmentIndex()
        a1 = engine.assess(_decision(), _market(), clock=FIXED_CLOCK)
        idx.ingest(a1)
        assert idx.find_by_approval("ALLOW") == [a1]

    def test_summary_counts_by_status(self):
        idx = query.RiskAssessmentIndex()
        idx.ingest(engine.assess(_decision(), _market(), clock=FIXED_CLOCK))
        idx.ingest(engine.assess(_decision(), _market(market_character="CONTESTED"), clock=FIXED_CLOCK))
        assert idx.summary() == {"APPROVED": 1, "APPROVED_WITH_WARNINGS": 1}


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

class TestConfig:
    def test_default_version_matches_package_version(self):
        cfg = config.RiskBrainConfig()
        assert cfg.version == taxonomy.RISK_BRAIN_VERSION

    def test_config_is_frozen(self):
        cfg = config.RiskBrainConfig()
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

    def test_no_execution_or_capital_or_position_fields(self):
        fields = {f.name for f in models.RiskAssessment.__dataclass_fields__.values()}
        forbidden = {
            "position_size", "lot_size", "order_id", "capital_allocation",
            "pnl", "expected_value", "expectancy", "win_rate", "probability", "score",
        }
        assert not (fields & forbidden)

    def test_no_uuid4_used_anywhere_in_package(self):
        for path in _module_source_files():
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr == "uuid4":
                    raise AssertionError(f"uuid4 used in {path.name}")

    def test_no_execution_capability_anywhere(self):
        for path in _module_source_files():
            source = path.read_text()
            for forbidden in ("place_order(", "def execute(", "broker."):
                assert forbidden not in source
