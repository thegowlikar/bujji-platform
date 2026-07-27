"""Tests for the Market State Builder — Engineering Series 33, Sprint 1."""
from __future__ import annotations

import ast
import inspect
from datetime import datetime
from pathlib import Path

import pytest

from bujji.trading_brain.evidence_interpreter import engine as ei_engine
from bujji.trading_brain.market_state import config, engine, models, query, runner, serialization, taxonomy
from bujji.journal.market_state_journal import MarketStateJournal

EI_CLOCK = lambda: datetime(2026, 1, 1, 9, 15, 0)
MSB_CLOCK = lambda: datetime(2026, 1, 1, 9, 15, 5)


def _interpretation(**overrides):
    base = dict(
        market_context="TRENDING_UP",
        market_opinion="BULLISH",
        context_stability="STABLE",
        calibration="CALIBRATED",
        governance="APPROVED",
        lifecycle="ACTIVE",
        contract="COMPLETE",
        clock=EI_CLOCK,
    )
    base.update(overrides)
    return ei_engine.interpret(**base)


def _assess(**ei_overrides):
    return engine.assess(_interpretation(**ei_overrides), clock=MSB_CLOCK)


# ---------------------------------------------------------------------------
# Taxonomy completeness
# ---------------------------------------------------------------------------

class TestTaxonomy:
    def test_market_character_values(self):
        assert taxonomy.ALL_MARKET_CHARACTERS == (
            "UNKNOWN", "CLEAR", "MIXED", "CONTESTED", "UNCERTAIN", "INSUFFICIENT_EVIDENCE",
        )

    def test_market_phase_values(self):
        assert taxonomy.ALL_MARKET_PHASES == ("UNKNOWN", "EMERGING", "ESTABLISHED", "UNSTABLE")

    def test_every_character_has_description(self):
        for c in taxonomy.ALL_MARKET_CHARACTERS:
            assert c in taxonomy.MARKET_CHARACTER_DESCRIPTIONS
            assert taxonomy.MARKET_CHARACTER_DESCRIPTIONS[c]

    def test_every_phase_has_description(self):
        for p in taxonomy.ALL_MARKET_PHASES:
            assert p in taxonomy.MARKET_PHASE_DESCRIPTIONS
            assert taxonomy.MARKET_PHASE_DESCRIPTIONS[p]

    def test_market_state_reused_verbatim_from_ontology(self):
        from bujji.trading_brain.ontology.taxonomy import ALL_MARKET_STATES
        assert taxonomy.REUSED_MARKET_STATES == ALL_MARKET_STATES

    def test_confidence_reused_verbatim_from_ontology(self):
        from bujji.trading_brain.ontology.taxonomy import ALL_CONFIDENCE_LEVELS
        assert taxonomy.REUSED_CONFIDENCE_LEVELS == ALL_CONFIDENCE_LEVELS


# ---------------------------------------------------------------------------
# CLEAR path -- all agree
# ---------------------------------------------------------------------------

class TestClearAgreement:
    def test_all_clean_signals_yield_clear(self):
        assessment = _assess()
        assert assessment.market_character == "CLEAR"
        assert assessment.market_state == "TREND"
        assert assessment.confidence == "VERY_HIGH"

    def test_clear_has_no_contradictions(self):
        assessment = _assess()
        assert assessment.contradicting_evidence == ()

    def test_clear_has_supporting_evidence(self):
        assessment = _assess()
        assert len(assessment.supporting_evidence) >= 1

    def test_downward_trend_also_reaches_trend_state(self):
        # Direction belongs to Strategy Intent, not Market State.
        assessment = _assess(market_context="TRENDING_DOWN", market_opinion="BEARISH")
        assert assessment.market_state == "TREND"
        assert assessment.market_character == "CLEAR"


# ---------------------------------------------------------------------------
# Undefined market_state -> MIXED
# ---------------------------------------------------------------------------

class TestMixed:
    def test_sideways_with_clean_support_is_range_not_mixed(self):
        # SIDEWAYS resolves to RANGE, a defined state -- not MIXED.
        assessment = _assess(market_context="SIDEWAYS")
        assert assessment.market_state == "RANGE"
        assert assessment.market_character == "CLEAR"

    def test_market_context_unknown_value_yields_mixed(self):
        assessment = _assess(market_context="UNKNOWN")
        assert assessment.market_state == "UNKNOWN"
        assert assessment.market_character == "MIXED"


# ---------------------------------------------------------------------------
# Contested -- exactly one contradiction -> downgrade one step
# ---------------------------------------------------------------------------

class TestGovernanceDowngrade:
    def test_approved_with_warnings_contests_and_downgrades_confidence(self):
        clean = _assess()
        contested = _assess(governance="APPROVED_WITH_WARNINGS")
        assert contested.market_character == "CONTESTED"
        assert contested.market_state == "TREND"
        assert contested.confidence != clean.confidence
        assert taxonomy.CONFIDENCE_ORDER.index(contested.confidence) < taxonomy.CONFIDENCE_ORDER.index(clean.confidence)

    def test_rejected_governance_also_contests(self):
        assessment = _assess(governance="REJECTED")
        assert assessment.market_character == "CONTESTED"
        assert any("Governance" in c for c in assessment.contradicting_evidence)


class TestCalibrationDowngrade:
    def test_uncalibrated_contests_and_downgrades_confidence(self):
        clean = _assess()
        contested = _assess(calibration="UNCALIBRATED")
        assert contested.market_character == "CONTESTED"
        assert contested.confidence != clean.confidence
        assert any("Calibration" in c for c in contested.contradicting_evidence)


class TestStabilityTransition:
    def test_transitioning_stability_contests_a_clean_trend(self):
        # TRANSITION itself maps to REVERSAL at the Evidence Interpreter,
        # so use a stability value that contests without redefining
        # market_state: HIGHLY_VARIABLE.
        assessment = _assess(context_stability="HIGHLY_VARIABLE")
        assert assessment.market_character == "CONTESTED"
        assert assessment.market_phase == "UNSTABLE"
        assert any("Context Stability" in c for c in assessment.contradicting_evidence)

    def test_mostly_stable_yields_established_phase(self):
        assessment = _assess(context_stability="MOSTLY_STABLE")
        assert assessment.market_phase == "ESTABLISHED"

    def test_insufficient_history_yields_unknown_phase(self):
        assessment = _assess(context_stability="INSUFFICIENT_HISTORY")
        assert assessment.market_phase == "UNKNOWN"


# ---------------------------------------------------------------------------
# Uncertain -- two or more contradictions
# ---------------------------------------------------------------------------

class TestConflictingEvidence:
    def test_two_contradictions_yield_uncertain_and_unknown_state(self):
        assessment = _assess(governance="REJECTED", calibration="UNCALIBRATED")
        assert assessment.market_character == "UNCERTAIN"
        assert assessment.market_state == "UNKNOWN"
        assert assessment.confidence == "UNKNOWN"

    def test_three_contradictions_still_uncertain(self):
        assessment = _assess(
            governance="REJECTED", calibration="UNCALIBRATED", context_stability="HIGHLY_VARIABLE"
        )
        assert assessment.market_character == "UNCERTAIN"
        assert len(assessment.contradicting_evidence) == 3

    def test_uncertain_never_fabricates_certainty(self):
        assessment = _assess(governance="REJECTED", calibration="UNCALIBRATED")
        assert assessment.confidence == "UNKNOWN"
        assert assessment.market_conviction == "UNKNOWN"


# ---------------------------------------------------------------------------
# Missing / incomplete evidence
# ---------------------------------------------------------------------------

class TestIncompleteEvidence:
    def test_one_missing_layer_still_reasons(self):
        assessment = _assess(governance=None)
        assert assessment.market_character in ("CLEAR", "MIXED", "CONTESTED", "UNCERTAIN")
        assert assessment.market_character != "INSUFFICIENT_EVIDENCE"

    def test_two_missing_layers_still_reasons(self):
        assessment = _assess(governance=None, calibration=None)
        assert assessment.market_character != "INSUFFICIENT_EVIDENCE"


class TestMissingEvidence:
    def test_three_missing_layers_yields_insufficient_evidence(self):
        assessment = _assess(governance=None, calibration=None, context_stability=None)
        assert assessment.market_character == "INSUFFICIENT_EVIDENCE"
        assert assessment.market_state == "UNKNOWN"
        assert assessment.confidence == "UNKNOWN"

    def test_all_four_missing_yields_insufficient_evidence(self):
        assessment = _assess(
            governance=None, calibration=None, context_stability=None, market_context=None
        )
        assert assessment.market_character == "INSUFFICIENT_EVIDENCE"

    def test_honest_unknown_never_fabricated(self):
        assessment = _assess(
            governance=None, calibration=None, context_stability=None, market_context=None
        )
        assert assessment.market_state == "UNKNOWN"
        assert assessment.market_phase == "UNKNOWN"
        assert assessment.confidence == "UNKNOWN"
        assert assessment.market_conviction == "UNKNOWN"


# ---------------------------------------------------------------------------
# Reasoning trace and provenance
# ---------------------------------------------------------------------------

class TestReasoningTrace:
    def test_trace_mentions_final_market_state(self):
        assessment = _assess()
        assert assessment.market_state in assessment.reasoning_trace

    def test_trace_mentions_contradiction_when_contested(self):
        assessment = _assess(governance="APPROVED_WITH_WARNINGS")
        assert "contested" in assessment.reasoning_trace.lower()

    def test_trace_mentions_insufficient_evidence(self):
        assessment = _assess(governance=None, calibration=None, context_stability=None)
        assert "insufficient" in assessment.reasoning_trace.lower()

    def test_interpretation_id_provenance_preserved(self):
        interp = _interpretation()
        assessment = engine.assess(interp, clock=MSB_CLOCK)
        assert assessment.interpretation_id == interp.interpretation_id


# ---------------------------------------------------------------------------
# Determinism / replay
# ---------------------------------------------------------------------------

class TestDeterminism:
    def test_same_interpretation_same_assessment_id(self):
        interp = _interpretation()
        a1 = engine.assess(interp, clock=MSB_CLOCK)
        a2 = engine.assess(interp, clock=MSB_CLOCK)
        assert a1.assessment_id == a2.assessment_id
        assert a1 == a2

    def test_byte_identical_across_repeated_runs(self):
        interp = _interpretation()
        results = [engine.assess(interp, clock=MSB_CLOCK) for _ in range(5)]
        assert all(r == results[0] for r in results)

    def test_different_interpretation_different_assessment_id(self):
        a1 = _assess()
        a2 = _assess(governance="REJECTED")
        assert a1.assessment_id != a2.assessment_id

    def test_assessment_id_uses_md5_prefix_not_uuid(self):
        assessment = _assess()
        assert assessment.assessment_id.startswith("MSA-")
        assert len(assessment.assessment_id) == len("MSA-") + 16

    def test_real_clock_default_produces_wall_clock_timestamp(self):
        before = datetime.now()
        assessment = engine.assess(_interpretation())
        after = datetime.now()
        parsed = datetime.fromisoformat(assessment.timestamp)
        assert before <= parsed <= after


# ---------------------------------------------------------------------------
# Frozen dataclasses / no mutation
# ---------------------------------------------------------------------------

class TestFrozen:
    def test_assessment_is_frozen(self):
        assessment = _assess()
        with pytest.raises(Exception):
            assessment.market_state = "HACKED"

    def test_supporting_evidence_is_a_tuple_not_a_list(self):
        assessment = _assess()
        assert isinstance(assessment.supporting_evidence, tuple)
        assert isinstance(assessment.contradicting_evidence, tuple)

    def test_engine_does_not_mutate_input_interpretation(self):
        interp = _interpretation()
        original_snapshot = interp.ontology_snapshot
        engine.assess(interp, clock=MSB_CLOCK)
        assert interp.ontology_snapshot == original_snapshot


# ---------------------------------------------------------------------------
# Serialization round trip
# ---------------------------------------------------------------------------

class TestSerialization:
    def test_round_trip_preserves_all_fields(self):
        assessment = _assess()
        d = serialization.assessment_to_dict(assessment)
        back = serialization.assessment_from_dict(d)
        assert back == assessment

    def test_round_trip_is_json_safe(self):
        import json

        assessment = _assess(governance="REJECTED", calibration="UNCALIBRATED")
        d = serialization.assessment_to_dict(assessment)
        text = json.dumps(d)
        back = serialization.assessment_from_dict(json.loads(text))
        assert back == assessment

    def test_round_trip_preserves_empty_contradictions(self):
        assessment = _assess()
        d = serialization.assessment_to_dict(assessment)
        assert d["contradicting_evidence"] == []
        back = serialization.assessment_from_dict(d)
        assert back.contradicting_evidence == ()


# ---------------------------------------------------------------------------
# Journal -- append only
# ---------------------------------------------------------------------------

class TestJournal:
    def test_record_and_read_all(self, tmp_path):
        j = MarketStateJournal(tmp_path / "ms.jsonl")
        assessment = _assess()
        j.record(assessment)
        assert j.read_all() == [assessment]

    def test_read_all_empty_when_file_missing(self, tmp_path):
        j = MarketStateJournal(tmp_path / "missing.jsonl")
        assert j.read_all() == []

    def test_record_many_appends_in_order(self, tmp_path):
        j = MarketStateJournal(tmp_path / "j.jsonl")
        a1 = _assess(governance="APPROVED")
        a2 = _assess(governance="REJECTED")
        j.record_many([a1, a2])
        records = j.read_all()
        assert [r.assessment_id for r in records] == [a1.assessment_id, a2.assessment_id]

    def test_journal_opens_only_in_append_or_read_mode(self):
        source = inspect.getsource(MarketStateJournal)
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
# runner.run_assessment composition
# ---------------------------------------------------------------------------

class TestRunner:
    def test_run_assessment_without_journal(self):
        assessment = runner.run_assessment(_interpretation(), clock=MSB_CLOCK)
        assert isinstance(assessment, models.MarketStateAssessment)

    def test_run_assessment_journals_when_given_one(self, tmp_path):
        j = MarketStateJournal(tmp_path / "run.jsonl")
        assessment = runner.run_assessment(_interpretation(), clock=MSB_CLOCK, journal=j)
        assert j.read_all() == [assessment]


# ---------------------------------------------------------------------------
# Query API
# ---------------------------------------------------------------------------

class TestMarketStateIndex:
    def test_ingest_and_latest(self):
        idx = query.MarketStateIndex()
        a1 = _assess()
        idx.ingest(a1)
        assert idx.latest() == a1

    def test_latest_none_when_empty(self):
        assert query.MarketStateIndex().latest() is None

    def test_history_preserves_order(self):
        idx = query.MarketStateIndex()
        a1 = _assess(governance="APPROVED")
        a2 = _assess(governance="REJECTED")
        idx.ingest(a1)
        idx.ingest(a2)
        assert idx.history() == [a1, a2]

    def test_find_by_id(self):
        idx = query.MarketStateIndex()
        a1 = _assess()
        idx.ingest(a1)
        assert idx.find_by_id(a1.assessment_id) == a1
        assert idx.find_by_id("nope") is None

    def test_find_by_market_state(self):
        idx = query.MarketStateIndex()
        a1 = _assess(market_context="TRENDING_UP")
        a2 = _assess(market_context="SIDEWAYS")
        idx.ingest(a1)
        idx.ingest(a2)
        assert idx.find_by_market_state("TREND") == [a1]
        assert idx.find_by_market_state("RANGE") == [a2]

    def test_find_by_market_character(self):
        idx = query.MarketStateIndex()
        a1 = _assess()
        a2 = _assess(governance="REJECTED", calibration="UNCALIBRATED")
        idx.ingest(a1)
        idx.ingest(a2)
        assert idx.find_by_market_character("CLEAR") == [a1]
        assert idx.find_by_market_character("UNCERTAIN") == [a2]

    def test_summary_counts_by_character(self):
        idx = query.MarketStateIndex()
        idx.ingest(_assess())
        idx.ingest(_assess())
        idx.ingest(_assess(governance="APPROVED_WITH_WARNINGS"))
        assert idx.summary() == {"CLEAR": 2, "CONTESTED": 1}


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

class TestConfig:
    def test_default_version_matches_package_version(self):
        cfg = config.MarketStateConfig()
        assert cfg.version == taxonomy.MARKET_STATE_BUILDER_VERSION

    def test_config_is_frozen(self):
        cfg = config.MarketStateConfig()
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

    def test_no_strategy_or_execution_or_risk_or_capital_field(self):
        fields = {f.name for f in models.MarketStateAssessment.__dataclass_fields__.values()}
        forbidden = {
            "strategy", "strategy_intent", "recommended_strategy", "execution_intent",
            "capital_intent", "capital_allocation", "risk_allocation", "position_size",
            "pnl", "score", "probability", "expected_value", "expectancy", "rank",
        }
        assert not (fields & forbidden)

    def test_no_uuid4_used_anywhere_in_package(self):
        for path in _module_source_files():
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr == "uuid4":
                    raise AssertionError(f"uuid4 used in {path.name}")

    def test_only_consumes_evidence_interpretation_type(self):
        sig = inspect.signature(engine.assess)
        assert "interpretation" in sig.parameters
        assert list(sig.parameters.keys())[0] == "interpretation"
