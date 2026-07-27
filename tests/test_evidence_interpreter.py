"""Tests for the Evidence Interpreter — Engineering Series 32, Sprint 1."""
from __future__ import annotations

import ast
import inspect
from datetime import datetime
from pathlib import Path

import pytest

from bujji.trading_brain.evidence_interpreter import (
    config,
    engine,
    models,
    query,
    runner,
    serialization,
    taxonomy,
)
from bujji.journal.evidence_interpreter_journal import EvidenceInterpreterJournal

FIXED_CLOCK = lambda: datetime(2026, 1, 1, 9, 15, 0)


def _full_inputs(**overrides):
    base = dict(
        market_context="TRENDING_UP",
        market_opinion="BULLISH",
        context_stability="STABLE",
        calibration="CALIBRATED",
        governance="APPROVED_WITH_WARNINGS",
        lifecycle="ACTIVE",
        contract="COMPLETE",
        clock=FIXED_CLOCK,
    )
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Worked examples from the specification itself
# ---------------------------------------------------------------------------

class TestSpecWorkedExamples:
    def test_bullish_opinion_becomes_directional_bullish_strategy_intent(self):
        interp = engine.interpret(**_full_inputs())
        assert interp.ontology_snapshot.strategy_intent == "DIRECTIONAL_BULLISH"

    def test_approved_with_warnings_governance_becomes_high_risk(self):
        interp = engine.interpret(**_full_inputs())
        assert interp.ontology_snapshot.risk_state == "HIGH"

    def test_mostly_calibrated_becomes_medium_edge_opportunity(self):
        interp = engine.interpret(**_full_inputs(calibration="MOSTLY_CALIBRATED"))
        assert interp.ontology_snapshot.opportunity_state == "MEDIUM_EDGE"


# ---------------------------------------------------------------------------
# Full mapping table coverage
# ---------------------------------------------------------------------------

class TestMappingTables:
    @pytest.mark.parametrize("raw,expected", list(taxonomy.MARKET_STATE_MAPPING.items()))
    def test_market_state_mapping(self, raw, expected):
        interp = engine.interpret(**_full_inputs(market_context=raw))
        assert interp.ontology_snapshot.market_state == expected

    @pytest.mark.parametrize("raw,expected", list(taxonomy.STRATEGY_INTENT_MAPPING.items()))
    def test_strategy_intent_mapping(self, raw, expected):
        interp = engine.interpret(**_full_inputs(market_opinion=raw))
        assert interp.ontology_snapshot.strategy_intent == expected

    @pytest.mark.parametrize("raw,expected", list(taxonomy.CONFIDENCE_MAPPING.items()))
    def test_confidence_mapping(self, raw, expected):
        interp = engine.interpret(**_full_inputs(context_stability=raw))
        assert interp.ontology_snapshot.confidence == expected

    @pytest.mark.parametrize("raw,expected", list(taxonomy.OPPORTUNITY_STATE_MAPPING.items()))
    def test_opportunity_state_mapping(self, raw, expected):
        interp = engine.interpret(**_full_inputs(calibration=raw))
        assert interp.ontology_snapshot.opportunity_state == expected

    @pytest.mark.parametrize("raw,expected", list(taxonomy.RISK_STATE_MAPPING.items()))
    def test_risk_state_mapping(self, raw, expected):
        interp = engine.interpret(**_full_inputs(governance=raw))
        assert interp.ontology_snapshot.risk_state == expected

    @pytest.mark.parametrize("raw,expected", list(taxonomy.EXECUTION_INTENT_MAPPING.items()))
    def test_execution_intent_mapping(self, raw, expected):
        interp = engine.interpret(**_full_inputs(lifecycle=raw))
        assert interp.ontology_snapshot.execution_intent == expected

    @pytest.mark.parametrize("raw,expected", list(taxonomy.CAPITAL_INTENT_MAPPING.items()))
    def test_capital_intent_mapping(self, raw, expected):
        interp = engine.interpret(**_full_inputs(contract=raw))
        assert interp.ontology_snapshot.capital_intent == expected


# ---------------------------------------------------------------------------
# Missing input -> UNKNOWN with honest provenance, never fabricated
# ---------------------------------------------------------------------------

class TestMissingInputs:
    def test_all_missing_yields_all_unknown(self):
        interp = engine.interpret(clock=FIXED_CLOCK)
        snap = interp.ontology_snapshot
        assert snap.market_state == "UNKNOWN"
        assert snap.opportunity_state == "UNKNOWN"
        assert snap.risk_state == "UNKNOWN"
        assert snap.execution_intent == "UNKNOWN"
        assert snap.strategy_intent == "UNKNOWN"
        assert snap.capital_intent == "UNKNOWN"
        assert snap.confidence == "UNKNOWN"

    def test_missing_layer_has_none_source_value_and_explains_absence(self):
        interp = engine.interpret(clock=FIXED_CLOCK)
        market_state_prov = next(
            p for p in interp.translation_provenance if p.ontology_field == "market_state"
        )
        assert market_state_prov.source_value is None
        assert "No market_context supplied" in market_state_prov.reason

    def test_source_versions_none_for_missing_layer(self):
        interp = engine.interpret(clock=FIXED_CLOCK)
        assert interp.source_versions["market_context"] is None

    def test_evidence_says_inconclusive_differs_from_absent(self):
        # MIXED opinion (evidence exists but is inconclusive) must be
        # distinguishable from no opinion supplied at all.
        interp_mixed = engine.interpret(**_full_inputs(market_opinion="MIXED"))
        interp_absent = engine.interpret(**_full_inputs(market_opinion=None))
        mixed_prov = next(
            p for p in interp_mixed.translation_provenance if p.ontology_field == "strategy_intent"
        )
        absent_prov = next(
            p for p in interp_absent.translation_provenance if p.ontology_field == "strategy_intent"
        )
        assert mixed_prov.ontology_value == "UNKNOWN"
        assert absent_prov.ontology_value == "UNKNOWN"
        assert mixed_prov.source_value == "MIXED"
        assert absent_prov.source_value is None


# ---------------------------------------------------------------------------
# Unrecognized value -> loud failure, never silently coerced
# ---------------------------------------------------------------------------

class TestInvalidInputs:
    @pytest.mark.parametrize(
        "kwarg",
        [
            "market_context",
            "market_opinion",
            "context_stability",
            "calibration",
            "governance",
            "lifecycle",
            "contract",
        ],
    )
    def test_unrecognized_classification_raises(self, kwarg):
        with pytest.raises(ValueError):
            engine.interpret(**_full_inputs(**{kwarg: "NOT_A_REAL_CLASSIFICATION"}))


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------

class TestDeterminism:
    def test_same_inputs_same_interpretation_id(self):
        i1 = engine.interpret(**_full_inputs())
        i2 = engine.interpret(**_full_inputs())
        assert i1.interpretation_id == i2.interpretation_id
        assert i1 == i2

    def test_different_inputs_different_interpretation_id(self):
        i1 = engine.interpret(**_full_inputs())
        i2 = engine.interpret(**_full_inputs(governance="REJECTED"))
        assert i1.interpretation_id != i2.interpretation_id

    def test_interpretation_id_uses_md5_prefix_not_uuid(self):
        interp = engine.interpret(**_full_inputs())
        assert interp.interpretation_id.startswith("EI-")
        assert len(interp.interpretation_id) == len("EI-") + 16

    def test_real_clock_default_produces_wall_clock_timestamp(self):
        before = datetime.now()
        interp = engine.interpret(market_context="RANGE".replace("RANGE", "SIDEWAYS"))
        after = datetime.now()
        parsed = datetime.fromisoformat(interp.timestamp)
        assert before <= parsed <= after

    def test_snapshot_and_interpretation_share_same_timestamp(self):
        interp = engine.interpret(**_full_inputs())
        assert interp.timestamp == interp.ontology_snapshot.timestamp


# ---------------------------------------------------------------------------
# Frozen dataclasses
# ---------------------------------------------------------------------------

class TestFrozen:
    def test_interpretation_is_frozen(self):
        interp = engine.interpret(**_full_inputs())
        with pytest.raises(Exception):
            interp.interpreter_version = "9.9.9"

    def test_provenance_is_frozen(self):
        interp = engine.interpret(**_full_inputs())
        with pytest.raises(Exception):
            interp.translation_provenance[0].ontology_value = "HACKED"


# ---------------------------------------------------------------------------
# Serialization round trip
# ---------------------------------------------------------------------------

class TestSerialization:
    def test_round_trip_preserves_all_fields(self):
        interp = engine.interpret(**_full_inputs())
        d = serialization.interpretation_to_dict(interp)
        back = serialization.interpretation_from_dict(d)
        assert back == interp

    def test_round_trip_is_json_safe(self):
        import json

        interp = engine.interpret(**_full_inputs())
        d = serialization.interpretation_to_dict(interp)
        text = json.dumps(d)
        back = serialization.interpretation_from_dict(json.loads(text))
        assert back == interp

    def test_round_trip_with_missing_layers(self):
        interp = engine.interpret(market_context="TRENDING_UP", clock=FIXED_CLOCK)
        d = serialization.interpretation_to_dict(interp)
        back = serialization.interpretation_from_dict(d)
        assert back == interp


# ---------------------------------------------------------------------------
# Journal -- append only
# ---------------------------------------------------------------------------

class TestJournal:
    def test_record_and_read_all(self, tmp_path):
        j = EvidenceInterpreterJournal(tmp_path / "ei.jsonl")
        interp = engine.interpret(**_full_inputs())
        j.record(interp)
        records = j.read_all()
        assert len(records) == 1
        assert records[0] == interp

    def test_read_all_empty_when_file_missing(self, tmp_path):
        j = EvidenceInterpreterJournal(tmp_path / "missing.jsonl")
        assert j.read_all() == []

    def test_record_many_appends_in_order(self, tmp_path):
        j = EvidenceInterpreterJournal(tmp_path / "j.jsonl")
        i1 = engine.interpret(**_full_inputs(governance="APPROVED"))
        i2 = engine.interpret(**_full_inputs(governance="REJECTED"))
        j.record_many([i1, i2])
        records = j.read_all()
        assert [r.ontology_snapshot.risk_state for r in records] == ["NORMAL", "EXTREME"]

    def test_journal_opens_only_in_append_or_read_mode(self):
        source = inspect.getsource(EvidenceInterpreterJournal)
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
# runner.run_interpretation composition
# ---------------------------------------------------------------------------

class TestRunner:
    def test_run_interpretation_without_journal(self):
        interp = runner.run_interpretation(**_full_inputs())
        assert isinstance(interp, models.EvidenceInterpretation)

    def test_run_interpretation_journals_when_given_one(self, tmp_path):
        j = EvidenceInterpreterJournal(tmp_path / "run.jsonl")
        interp = runner.run_interpretation(**_full_inputs(), journal=j)
        assert j.read_all() == [interp]


# ---------------------------------------------------------------------------
# Query API
# ---------------------------------------------------------------------------

class TestEvidenceInterpretationIndex:
    def test_ingest_and_latest(self):
        idx = query.EvidenceInterpretationIndex()
        i1 = engine.interpret(**_full_inputs())
        idx.ingest(i1)
        assert idx.latest() == i1

    def test_latest_none_when_empty(self):
        assert query.EvidenceInterpretationIndex().latest() is None

    def test_history_preserves_order(self):
        idx = query.EvidenceInterpretationIndex()
        i1 = engine.interpret(**_full_inputs(governance="APPROVED"))
        i2 = engine.interpret(**_full_inputs(governance="REJECTED"))
        idx.ingest(i1)
        idx.ingest(i2)
        assert idx.history() == [i1, i2]

    def test_find_by_id(self):
        idx = query.EvidenceInterpretationIndex()
        i1 = engine.interpret(**_full_inputs())
        idx.ingest(i1)
        assert idx.find_by_id(i1.interpretation_id) == i1
        assert idx.find_by_id("nope") is None

    def test_find_by_market_state(self):
        idx = query.EvidenceInterpretationIndex()
        i1 = engine.interpret(**_full_inputs(market_context="TRENDING_UP"))
        i2 = engine.interpret(**_full_inputs(market_context="SIDEWAYS"))
        idx.ingest(i1)
        idx.ingest(i2)
        assert idx.find_by_market_state("TREND") == [i1]
        assert idx.find_by_market_state("RANGE") == [i2]

    def test_find_by_risk_state(self):
        idx = query.EvidenceInterpretationIndex()
        i1 = engine.interpret(**_full_inputs(governance="REJECTED"))
        idx.ingest(i1)
        assert idx.find_by_risk_state("EXTREME") == [i1]
        assert idx.find_by_risk_state("NORMAL") == []

    def test_summary_counts_by_market_state(self):
        idx = query.EvidenceInterpretationIndex()
        idx.ingest(engine.interpret(**_full_inputs(market_context="TRENDING_UP")))
        idx.ingest(engine.interpret(**_full_inputs(market_context="TRENDING_DOWN")))
        idx.ingest(engine.interpret(**_full_inputs(market_context="SIDEWAYS")))
        assert idx.summary() == {"TREND": 2, "RANGE": 1}


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

class TestConfig:
    def test_default_interpreter_version_matches_package_version(self):
        cfg = config.EvidenceInterpreterConfig()
        assert cfg.interpreter_version == taxonomy.INTERPRETER_VERSION

    def test_config_is_frozen(self):
        cfg = config.EvidenceInterpreterConfig()
        with pytest.raises(Exception):
            cfg.interpreter_version = "9.9.9"


# ---------------------------------------------------------------------------
# Isolation guarantees -- the architectural firewall itself
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
                        assert "mic_v2" not in alias.name, f"{path.name} imports mic_v2"
                if isinstance(node, ast.ImportFrom) and node.module:
                    assert "mic_v2" not in node.module, f"{path.name} imports mic_v2"

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
                    for alias in node.names:
                        assert alias.name not in ("PublicationRecord", "ConsumerRecord")

    def test_no_engine_side_decision_fields(self):
        interp_fields = {f.name for f in models.EvidenceInterpretation.__dataclass_fields__.values()}
        prov_fields = {f.name for f in models.TranslationProvenance.__dataclass_fields__.values()}
        forbidden = {"pnl", "score", "probability", "expected_value", "recommendation"}
        assert not (interp_fields & forbidden)
        assert not (prov_fields & forbidden)

    def test_no_uuid4_used_anywhere_in_package(self):
        for path in _module_source_files():
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr == "uuid4":
                    raise AssertionError(f"uuid4 used in {path.name}")

    def test_one_ontology_field_has_exactly_one_source_layer(self):
        source_layers = [source for source, _mapping, _v in taxonomy.MAPPINGS.values()]
        assert len(source_layers) == len(set(source_layers)), "a source layer drives more than one field"
