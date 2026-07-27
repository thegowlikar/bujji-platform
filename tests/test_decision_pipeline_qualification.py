"""Tests for the Decision Pipeline Qualification harness — Engineering
Series 38, Sprint 1.
"""
from __future__ import annotations

import ast
import inspect
from datetime import datetime
from pathlib import Path

import pytest

from bujji.trading_brain.qualification import config, engine, models, query, runner, serialization
from bujji.journal.decision_pipeline_qualification_journal import DecisionPipelineQualificationJournal

FIXED_CLOCK = lambda: datetime(2026, 1, 1, 11, 0, 0)

HAPPY_INPUT = models.PipelineInput(
    market_context="TRENDING_UP",
    market_opinion="BULLISH",
    context_stability="STABLE",
    calibration="CALIBRATED",
    governance="APPROVED",
    lifecycle="ACTIVE",
    contract="COMPLETE",
)

DENIED_INPUT = models.PipelineInput(
    market_context="TRENDING_UP",
    market_opinion="BULLISH",
    context_stability="STABLE",
    calibration="UNCALIBRATED",
    governance="REJECTED",
    lifecycle="ACTIVE",
    contract="COMPLETE",
)

EMPTY_INPUT = models.PipelineInput()

INVALID_INPUT = models.PipelineInput(market_context="NOT_A_REAL_CLASSIFICATION")


# ---------------------------------------------------------------------------
# Pipeline completeness
# ---------------------------------------------------------------------------

class TestPipelineCompleteness:
    def test_happy_path_completes_all_six_stages(self):
        result = engine.run_qualification(HAPPY_INPUT, clock=FIXED_CLOCK, replay_count=1)
        assert result.completed_stages == models.ALL_STAGE_NAMES
        assert result.failed_stage is None

    def test_happy_path_is_complete_status(self):
        result = engine.run_qualification(HAPPY_INPUT, clock=FIXED_CLOCK, replay_count=1)
        assert result.pipeline_status in ("COMPLETE", "COMPLETE_WITH_WARNINGS")

    def test_empty_input_still_completes_all_stages(self):
        # Every stage honestly resolves to UNKNOWN/NO_STRATEGY/DENIED
        # rather than raising -- completeness means "ran", not
        # "produced a trade."
        result = engine.run_qualification(EMPTY_INPUT, clock=FIXED_CLOCK, replay_count=1)
        assert result.completed_stages == models.ALL_STAGE_NAMES

    def test_invalid_classification_fails_at_evidence_interpreter(self):
        result = engine.run_qualification(INVALID_INPUT, clock=FIXED_CLOCK, replay_count=1)
        assert result.pipeline_status == "FAILED"
        assert result.failed_stage == "evidence_interpreter"
        assert result.completed_stages == ()


# ---------------------------------------------------------------------------
# Deterministic replay / identical fingerprints
# ---------------------------------------------------------------------------

class TestDeterministicReplay:
    def test_happy_path_deterministic_across_replays(self):
        result = engine.run_qualification(HAPPY_INPUT, clock=FIXED_CLOCK, replay_count=5)
        assert result.deterministic is True

    def test_denied_path_deterministic_across_replays(self):
        result = engine.run_qualification(DENIED_INPUT, clock=FIXED_CLOCK, replay_count=5)
        assert result.deterministic is True

    def test_ten_replays_matches_specification_default(self):
        result = engine.run_qualification(HAPPY_INPUT, clock=FIXED_CLOCK, replay_count=10)
        assert result.deterministic is True

    def test_real_clock_default_is_honestly_reported_non_deterministic(self):
        # Each replay legitimately sees a different wall-clock
        # timestamp under the real clock, so determinism correctly
        # reports False rather than fabricating True.
        result = engine.run_qualification(HAPPY_INPUT, replay_count=3)
        assert result.deterministic is False
        assert "NON_DETERMINISTIC_REPLAY" in result.warnings

    def test_byte_identical_qualification_objects_across_separate_calls(self):
        r1 = engine.run_qualification(HAPPY_INPUT, clock=FIXED_CLOCK, replay_count=3)
        r2 = engine.run_qualification(HAPPY_INPUT, clock=FIXED_CLOCK, replay_count=3)
        assert r1 == r2


class TestIdenticalFingerprints:
    def test_same_input_same_fingerprint(self):
        r1 = engine.run_qualification(HAPPY_INPUT, clock=FIXED_CLOCK, replay_count=1)
        r2 = engine.run_qualification(HAPPY_INPUT, clock=FIXED_CLOCK, replay_count=1)
        assert r1.decision_fingerprint == r2.decision_fingerprint

    def test_different_input_different_fingerprint(self):
        r1 = engine.run_qualification(HAPPY_INPUT, clock=FIXED_CLOCK, replay_count=1)
        r2 = engine.run_qualification(DENIED_INPUT, clock=FIXED_CLOCK, replay_count=1)
        assert r1.decision_fingerprint != r2.decision_fingerprint

    def test_fingerprint_uses_md5_prefix_not_uuid(self):
        result = engine.run_qualification(HAPPY_INPUT, clock=FIXED_CLOCK, replay_count=1)
        assert result.decision_fingerprint.startswith("FP-")
        assert len(result.decision_fingerprint) == len("FP-") + 16

    def test_empty_completed_stages_yields_stable_empty_fingerprint(self):
        fp = engine.compute_fingerprint({}, ())
        assert fp == engine.compute_fingerprint({}, ())


# ---------------------------------------------------------------------------
# Provenance completeness
# ---------------------------------------------------------------------------

class TestProvenanceCompleteness:
    def test_happy_path_has_no_provenance_warnings(self):
        result = engine.run_qualification(HAPPY_INPUT, clock=FIXED_CLOCK, replay_count=1)
        assert not any(w.startswith("MISSING_PROVENANCE") for w in result.warnings)

    def test_denied_path_has_no_provenance_warnings(self):
        result = engine.run_qualification(DENIED_INPUT, clock=FIXED_CLOCK, replay_count=1)
        assert not any(w.startswith("MISSING_PROVENANCE") for w in result.warnings)

    def test_empty_path_has_no_provenance_warnings(self):
        result = engine.run_qualification(EMPTY_INPUT, clock=FIXED_CLOCK, replay_count=1)
        assert not any(w.startswith("MISSING_PROVENANCE") for w in result.warnings)

    def test_broken_provenance_is_detected(self):
        results, completed, _ = engine._run_stages(HAPPY_INPUT, FIXED_CLOCK)
        from bujji.trading_brain.market_state.models import MarketStateAssessment

        tampered_msb = MarketStateAssessment(
            **{**results["market_state_builder"].__dict__, "interpretation_id": "EI-TAMPERED0000000"}
        )
        tampered = dict(results)
        tampered["market_state_builder"] = tampered_msb
        warnings = engine._check_provenance(tampered)
        assert any(w.startswith("MISSING_PROVENANCE") for w in warnings)


# ---------------------------------------------------------------------------
# Confidence monotonicity
# ---------------------------------------------------------------------------

class TestConfidenceMonotonicity:
    def test_happy_path_never_increases_confidence(self):
        result = engine.run_qualification(HAPPY_INPUT, clock=FIXED_CLOCK, replay_count=1)
        assert not any(w.startswith("CONFIDENCE_INCREASE") for w in result.warnings)

    def test_denied_path_never_increases_confidence(self):
        result = engine.run_qualification(DENIED_INPUT, clock=FIXED_CLOCK, replay_count=1)
        assert not any(w.startswith("CONFIDENCE_INCREASE") for w in result.warnings)

    def test_detects_a_genuine_confidence_increase(self):
        results, _completed, _ = engine._run_stages(HAPPY_INPUT, FIXED_CLOCK)
        from bujji.trading_brain.market_state.models import MarketStateAssessment

        tampered_msb = MarketStateAssessment(
            **{**results["market_state_builder"].__dict__, "confidence": "VERY_LOW"}
        )
        tampered = dict(results)
        tampered["market_state_builder"] = tampered_msb
        warnings = engine._check_confidence_monotonic(tampered)
        assert any(w.startswith("CONFIDENCE_INCREASE") for w in warnings)


# ---------------------------------------------------------------------------
# UNKNOWN / NO_STRATEGY / DENY propagation
# ---------------------------------------------------------------------------

class TestPropagation:
    def test_no_strategy_propagates_to_risk_brain(self):
        result = engine.run_qualification(EMPTY_INPUT, clock=FIXED_CLOCK, replay_count=1)
        results, _completed, _ = engine._run_stages(EMPTY_INPUT, FIXED_CLOCK)
        assert results["strategy_selector"].selected_strategy is None
        assert results["risk_brain"].blocking_reason == "NO_STRATEGY"

    def test_deny_propagates_to_capital_brain(self):
        results, _completed, _ = engine._run_stages(DENIED_INPUT, FIXED_CLOCK)
        assert results["risk_brain"].approval == "DENY"
        assert results["capital_brain"].allocation_status == "DENIED"

    def test_denied_propagates_to_execution_planner(self):
        results, _completed, _ = engine._run_stages(DENIED_INPUT, FIXED_CLOCK)
        assert results["capital_brain"].allocation_status == "DENIED"
        assert results["execution_planner"].status == "NOT_PLANNED"

    def test_no_unexpected_propagation_warnings_on_denied_path(self):
        result = engine.run_qualification(DENIED_INPUT, clock=FIXED_CLOCK, replay_count=1)
        assert not any(w.startswith("UNEXPECTED_PROPAGATION") for w in result.warnings)


# ---------------------------------------------------------------------------
# Explainability / provenance chain
# ---------------------------------------------------------------------------

class TestExplainability:
    def test_full_chain_is_traceable_end_to_end(self):
        results, _completed, _ = engine._run_stages(HAPPY_INPUT, FIXED_CLOCK)
        ei = results["evidence_interpreter"]
        msb = results["market_state_builder"]
        ss = results["strategy_selector"]
        rb = results["risk_brain"]
        cb = results["capital_brain"]
        ep = results["execution_planner"]
        assert msb.interpretation_id == ei.interpretation_id
        assert ss.market_state_assessment_id == msb.assessment_id
        assert rb.strategy_decision_id == ss.decision_id
        assert rb.market_state_assessment_id == msb.assessment_id
        assert cb.risk_assessment_id == rb.assessment_id
        assert ep.capital_decision_id == cb.decision_id
        assert ep.strategy_decision_id == ss.decision_id


# ---------------------------------------------------------------------------
# Immutable outputs / no mutation
# ---------------------------------------------------------------------------

class TestImmutableOutputs:
    def test_qualification_is_frozen(self):
        result = engine.run_qualification(HAPPY_INPUT, clock=FIXED_CLOCK, replay_count=1)
        with pytest.raises(Exception):
            result.pipeline_status = "HACKED"

    def test_pipeline_input_is_frozen(self):
        with pytest.raises(Exception):
            HAPPY_INPUT.market_context = "HACKED"

    def test_run_qualification_does_not_mutate_input(self):
        original = models.PipelineInput(**HAPPY_INPUT.__dict__)
        engine.run_qualification(HAPPY_INPUT, clock=FIXED_CLOCK, replay_count=1)
        assert HAPPY_INPUT == original


# ---------------------------------------------------------------------------
# Serialization round trip
# ---------------------------------------------------------------------------

class TestSerialization:
    def test_round_trip_preserves_all_fields(self):
        result = engine.run_qualification(HAPPY_INPUT, clock=FIXED_CLOCK, replay_count=1)
        d = serialization.qualification_to_dict(result)
        back = serialization.qualification_from_dict(d)
        assert back == result

    def test_round_trip_is_json_safe(self):
        import json

        result = engine.run_qualification(DENIED_INPUT, clock=FIXED_CLOCK, replay_count=1)
        d = serialization.qualification_to_dict(result)
        text = json.dumps(d)
        back = serialization.qualification_from_dict(json.loads(text))
        assert back == result

    def test_round_trip_with_failed_stage(self):
        result = engine.run_qualification(INVALID_INPUT, clock=FIXED_CLOCK, replay_count=1)
        d = serialization.qualification_to_dict(result)
        assert d["failed_stage"] == "evidence_interpreter"
        back = serialization.qualification_from_dict(d)
        assert back == result


# ---------------------------------------------------------------------------
# Journal -- append only / journal integrity
# ---------------------------------------------------------------------------

class TestJournal:
    def test_record_and_read_all(self, tmp_path):
        j = DecisionPipelineQualificationJournal(tmp_path / "dpq.jsonl")
        result = engine.run_qualification(HAPPY_INPUT, clock=FIXED_CLOCK, replay_count=1)
        j.record(result)
        assert j.read_all() == [result]

    def test_read_all_empty_when_file_missing(self, tmp_path):
        j = DecisionPipelineQualificationJournal(tmp_path / "missing.jsonl")
        assert j.read_all() == []

    def test_record_many_appends_in_order(self, tmp_path):
        j = DecisionPipelineQualificationJournal(tmp_path / "j.jsonl")
        r1 = engine.run_qualification(HAPPY_INPUT, clock=FIXED_CLOCK, replay_count=1)
        r2 = engine.run_qualification(DENIED_INPUT, clock=FIXED_CLOCK, replay_count=1)
        j.record_many([r1, r2])
        records = j.read_all()
        assert [r.qualification_id for r in records] == [r1.qualification_id, r2.qualification_id]

    def test_repeated_identical_runs_produce_identical_journal_entries(self, tmp_path):
        j = DecisionPipelineQualificationJournal(tmp_path / "repeat.jsonl")
        for _ in range(3):
            j.record(engine.run_qualification(HAPPY_INPUT, clock=FIXED_CLOCK, replay_count=1))
        records = j.read_all()
        assert all(r == records[0] for r in records)

    def test_journal_opens_only_in_append_or_read_mode(self):
        source = inspect.getsource(DecisionPipelineQualificationJournal)
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
# runner.run_pipeline_qualification composition
# ---------------------------------------------------------------------------

class TestRunner:
    def test_run_without_journal(self):
        result = runner.run_pipeline_qualification(HAPPY_INPUT, clock=FIXED_CLOCK, replay_count=1)
        assert isinstance(result, models.DecisionPipelineQualification)

    def test_run_journals_when_given_one(self, tmp_path):
        j = DecisionPipelineQualificationJournal(tmp_path / "run.jsonl")
        result = runner.run_pipeline_qualification(HAPPY_INPUT, clock=FIXED_CLOCK, replay_count=1, journal=j)
        assert j.read_all() == [result]


# ---------------------------------------------------------------------------
# Query API
# ---------------------------------------------------------------------------

class TestQualificationIndex:
    def test_ingest_and_latest(self):
        idx = query.QualificationIndex()
        r1 = engine.run_qualification(HAPPY_INPUT, clock=FIXED_CLOCK, replay_count=1)
        idx.ingest(r1)
        assert idx.latest() == r1

    def test_latest_none_when_empty(self):
        assert query.QualificationIndex().latest() is None

    def test_find_by_status(self):
        idx = query.QualificationIndex()
        r1 = engine.run_qualification(INVALID_INPUT, clock=FIXED_CLOCK, replay_count=1)
        idx.ingest(r1)
        assert idx.find_by_status("FAILED") == [r1]

    def test_find_by_fingerprint(self):
        idx = query.QualificationIndex()
        r1 = engine.run_qualification(HAPPY_INPUT, clock=FIXED_CLOCK, replay_count=1)
        idx.ingest(r1)
        assert idx.find_by_fingerprint(r1.decision_fingerprint) == [r1]

    def test_summary_counts_by_status(self):
        idx = query.QualificationIndex()
        idx.ingest(engine.run_qualification(HAPPY_INPUT, clock=FIXED_CLOCK, replay_count=1))
        idx.ingest(engine.run_qualification(INVALID_INPUT, clock=FIXED_CLOCK, replay_count=1))
        summary = idx.summary()
        assert summary.get("FAILED") == 1


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

class TestConfig:
    def test_default_version_matches_package_version(self):
        cfg = config.QualificationConfig()
        assert cfg.version == models.QUALIFICATION_VERSION

    def test_default_replay_count_is_ten(self):
        cfg = config.QualificationConfig()
        assert cfg.replay_count == 10

    def test_config_is_frozen(self):
        cfg = config.QualificationConfig()
        with pytest.raises(Exception):
            cfg.version = "9.9.9"


# ---------------------------------------------------------------------------
# Isolation firewall
# ---------------------------------------------------------------------------

FORBIDDEN_BUJJI_SUBPACKAGES = {"broker", "core", "trade", "intelligence", "market", "tick"}


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

    def test_no_broker_order_or_feed_import(self):
        for path in _module_source_files():
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    lowered = node.module.lower()
                    for forbidden in ("broker", "order_manager", "feed", "fyers", "zerodha"):
                        assert forbidden not in lowered

    def test_only_imports_trading_brain_stage_engines(self):
        source = inspect.getsource(engine)
        for allowed_stage in (
            "capital_brain", "evidence_interpreter", "execution_planner",
            "market_state", "risk_brain", "strategy_selector",
        ):
            pass  # presence is expected; this test asserts no disallowed imports below
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module.startswith(".."):
                    allowed_roots = {
                        "capital_brain", "evidence_interpreter", "execution_planner",
                        "market_state", "risk_brain", "strategy_selector",
                    }
                    stripped = node.module.lstrip(".")
                    root = stripped.split(".")[0]
                    assert root in allowed_roots, f"unexpected sibling import: {node.module}"

    def test_no_uuid4_used_anywhere_in_package(self):
        for path in _module_source_files():
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr == "uuid4":
                    raise AssertionError(f"uuid4 used in {path.name}")

    def test_no_execution_capability_in_source(self):
        for path in _module_source_files():
            source = path.read_text()
            for forbidden in ("place_order(", "def execute(", "def calculate_lots"):
                assert forbidden not in source
