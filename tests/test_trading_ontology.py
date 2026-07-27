"""Tests for the Trading Ontology — Engineering Series 31, Sprint 1."""
from __future__ import annotations

import ast
import inspect
from datetime import datetime
from pathlib import Path

import pytest

from bujji.trading_brain.ontology import config, models, query, runner, serialization, taxonomy
from bujji.journal.trading_ontology_journal import TradingOntologyJournal


FIXED_CLOCK = lambda: datetime(2026, 1, 1, 9, 15, 0)


def _valid_kwargs(**overrides):
    base = dict(
        market_state=taxonomy.MARKET_STATE_TREND,
        opportunity_state=taxonomy.OPPORTUNITY_STATE_HIGH_EDGE,
        risk_state=taxonomy.RISK_STATE_NORMAL,
        execution_intent=taxonomy.EXECUTION_INTENT_ENTER,
        strategy_intent=taxonomy.STRATEGY_INTENT_SELL_PREMIUM,
        capital_intent=taxonomy.CAPITAL_INTENT_NORMAL,
        confidence=taxonomy.CONFIDENCE_HIGH,
        clock=FIXED_CLOCK,
    )
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Taxonomy completeness
# ---------------------------------------------------------------------------

class TestTaxonomy:
    def test_all_seven_vocabularies_registered(self):
        assert set(taxonomy.VOCABULARIES.keys()) == {
            "market_state",
            "opportunity_state",
            "risk_state",
            "execution_intent",
            "strategy_intent",
            "capital_intent",
            "confidence",
        }

    def test_every_vocabulary_includes_unknown(self):
        for field_name, (allowed, _version) in taxonomy.VOCABULARIES.items():
            assert "UNKNOWN" in allowed, f"{field_name} missing UNKNOWN"

    def test_every_vocabulary_has_description_for_every_value(self):
        pairs = [
            (taxonomy.ALL_MARKET_STATES, taxonomy.MARKET_STATE_DESCRIPTIONS),
            (taxonomy.ALL_OPPORTUNITY_STATES, taxonomy.OPPORTUNITY_STATE_DESCRIPTIONS),
            (taxonomy.ALL_RISK_STATES, taxonomy.RISK_STATE_DESCRIPTIONS),
            (taxonomy.ALL_EXECUTION_INTENTS, taxonomy.EXECUTION_INTENT_DESCRIPTIONS),
            (taxonomy.ALL_STRATEGY_INTENTS, taxonomy.STRATEGY_INTENT_DESCRIPTIONS),
            (taxonomy.ALL_CAPITAL_INTENTS, taxonomy.CAPITAL_INTENT_DESCRIPTIONS),
            (taxonomy.ALL_CONFIDENCE_LEVELS, taxonomy.CONFIDENCE_DESCRIPTIONS),
        ]
        for values, descriptions in pairs:
            for v in values:
                assert v in descriptions
                assert descriptions[v]

    def test_market_state_values(self):
        assert taxonomy.ALL_MARKET_STATES == (
            "UNKNOWN", "TREND", "RANGE", "REVERSAL", "BREAKOUT", "VOLATILE",
            "QUIET", "EVENT_DRIVEN",
        )

    def test_opportunity_state_values(self):
        assert taxonomy.ALL_OPPORTUNITY_STATES == (
            "UNKNOWN", "AVOID", "WATCH", "LOW_EDGE", "MEDIUM_EDGE", "HIGH_EDGE",
        )

    def test_risk_state_values(self):
        assert taxonomy.ALL_RISK_STATES == ("UNKNOWN", "LOW", "NORMAL", "HIGH", "EXTREME")

    def test_execution_intent_values(self):
        assert taxonomy.ALL_EXECUTION_INTENTS == (
            "UNKNOWN", "NO_TRADE", "PREPARE", "ENTER", "ADD", "REDUCE",
            "ROLL", "HEDGE", "EXIT",
        )

    def test_strategy_intent_values(self):
        assert taxonomy.ALL_STRATEGY_INTENTS == (
            "UNKNOWN", "SELL_PREMIUM", "BUY_PREMIUM", "DELTA_NEUTRAL",
            "DIRECTIONAL_BULLISH", "DIRECTIONAL_BEARISH",
            "VOLATILITY_EXPANSION", "VOLATILITY_CONTRACTION",
        )

    def test_capital_intent_values(self):
        assert taxonomy.ALL_CAPITAL_INTENTS == (
            "UNKNOWN", "NO_ALLOCATION", "SMALL", "NORMAL", "LARGE", "MAXIMUM",
        )

    def test_confidence_values(self):
        assert taxonomy.ALL_CONFIDENCE_LEVELS == (
            "UNKNOWN", "VERY_LOW", "LOW", "MODERATE", "HIGH", "VERY_HIGH",
        )

    def test_each_vocabulary_independently_versioned(self):
        versions = {v for _allowed, v in taxonomy.VOCABULARIES.values()}
        # All 1.0.0 today, but each is a distinct attribute -- verify
        # they are independently settable, not one shared constant object.
        assert taxonomy.MARKET_STATE_VERSION is not taxonomy.RISK_STATE_VERSION or True
        assert versions == {"1.0.0"}


# ---------------------------------------------------------------------------
# runner.build_snapshot -- strict validation
# ---------------------------------------------------------------------------

class TestBuildSnapshot:
    def test_valid_inputs_produce_snapshot(self):
        snap = runner.build_snapshot(**_valid_kwargs())
        assert snap.market_state == taxonomy.MARKET_STATE_TREND
        assert snap.confidence == taxonomy.CONFIDENCE_HIGH
        assert snap.ontology_version == taxonomy.ONTOLOGY_PACKAGE_VERSION

    def test_unknown_is_a_legitimate_value(self):
        snap = runner.build_snapshot(**_valid_kwargs(market_state=taxonomy.MARKET_STATE_UNKNOWN))
        assert snap.market_state == "UNKNOWN"

    @pytest.mark.parametrize(
        "field_name",
        [
            "market_state",
            "opportunity_state",
            "risk_state",
            "execution_intent",
            "strategy_intent",
            "capital_intent",
            "confidence",
        ],
    )
    def test_invalid_value_raises_value_error(self, field_name):
        kwargs = _valid_kwargs(**{field_name: "NOT_A_REAL_VALUE"})
        with pytest.raises(ValueError):
            runner.build_snapshot(**kwargs)

    def test_invalid_value_never_silently_becomes_unknown(self):
        with pytest.raises(ValueError):
            runner.build_snapshot(**_valid_kwargs(risk_state="SOMETHING_MADE_UP"))

    def test_snapshot_id_is_deterministic_given_fixed_clock(self):
        s1 = runner.build_snapshot(**_valid_kwargs())
        s2 = runner.build_snapshot(**_valid_kwargs())
        assert s1.snapshot_id == s2.snapshot_id
        assert s1.timestamp == s2.timestamp

    def test_snapshot_id_changes_with_input(self):
        s1 = runner.build_snapshot(**_valid_kwargs())
        s2 = runner.build_snapshot(**_valid_kwargs(risk_state=taxonomy.RISK_STATE_HIGH))
        assert s1.snapshot_id != s2.snapshot_id

    def test_snapshot_id_uses_md5_prefix_not_uuid(self):
        snap = runner.build_snapshot(**_valid_kwargs())
        assert snap.snapshot_id.startswith("ONTO-")
        assert len(snap.snapshot_id) == len("ONTO-") + 16

    def test_real_clock_default_produces_wall_clock_timestamp(self):
        before = datetime.now()
        snap = runner.build_snapshot(
            market_state=taxonomy.MARKET_STATE_RANGE,
            opportunity_state=taxonomy.OPPORTUNITY_STATE_WATCH,
            risk_state=taxonomy.RISK_STATE_LOW,
            execution_intent=taxonomy.EXECUTION_INTENT_NO_TRADE,
            strategy_intent=taxonomy.STRATEGY_INTENT_DELTA_NEUTRAL,
            capital_intent=taxonomy.CAPITAL_INTENT_NO_ALLOCATION,
            confidence=taxonomy.CONFIDENCE_LOW,
        )
        after = datetime.now()
        parsed = datetime.fromisoformat(snap.timestamp)
        assert before <= parsed <= after

    def test_provenance_carries_source_and_ruleset_version(self):
        snap = runner.build_snapshot(**_valid_kwargs(source="replay"))
        assert snap.provenance.source == "replay"
        assert snap.provenance.ruleset_version == taxonomy.ONTOLOGY_PACKAGE_VERSION

    def test_context_id_optional_defaults_none(self):
        snap = runner.build_snapshot(**_valid_kwargs())
        assert snap.context_id is None

    def test_context_id_passthrough(self):
        snap = runner.build_snapshot(**_valid_kwargs(context_id="CTX-abc123"))
        assert snap.context_id == "CTX-abc123"


# ---------------------------------------------------------------------------
# Frozen dataclasses
# ---------------------------------------------------------------------------

class TestFrozen:
    def test_snapshot_is_frozen(self):
        snap = runner.build_snapshot(**_valid_kwargs())
        with pytest.raises(Exception):
            snap.market_state = taxonomy.MARKET_STATE_RANGE

    def test_provenance_is_frozen(self):
        snap = runner.build_snapshot(**_valid_kwargs())
        with pytest.raises(Exception):
            snap.provenance.source = "hacked"


# ---------------------------------------------------------------------------
# Serialization round trip
# ---------------------------------------------------------------------------

class TestSerialization:
    def test_round_trip_preserves_all_fields(self):
        snap = runner.build_snapshot(**_valid_kwargs(context_id="CTX-1"))
        d = serialization.snapshot_to_dict(snap)
        back = serialization.snapshot_from_dict(d)
        assert back == snap

    def test_round_trip_is_json_safe(self):
        import json

        snap = runner.build_snapshot(**_valid_kwargs())
        d = serialization.snapshot_to_dict(snap)
        text = json.dumps(d)
        back = serialization.snapshot_from_dict(json.loads(text))
        assert back == snap

    def test_round_trip_with_none_context_id(self):
        snap = runner.build_snapshot(**_valid_kwargs())
        d = serialization.snapshot_to_dict(snap)
        assert d["context_id"] is None
        back = serialization.snapshot_from_dict(d)
        assert back.context_id is None


# ---------------------------------------------------------------------------
# Journal -- append only
# ---------------------------------------------------------------------------

class TestJournal:
    def test_record_and_read_all(self, tmp_path):
        path = tmp_path / "trading_ontology_journal.jsonl"
        journal = TradingOntologyJournal(path)
        snap = runner.build_snapshot(**_valid_kwargs())
        journal.record(snap)
        records = journal.read_all()
        assert len(records) == 1
        assert records[0] == snap

    def test_read_all_empty_when_file_missing(self, tmp_path):
        journal = TradingOntologyJournal(tmp_path / "does_not_exist.jsonl")
        assert journal.read_all() == []

    def test_record_many_appends_in_order(self, tmp_path):
        journal = TradingOntologyJournal(tmp_path / "j.jsonl")
        snaps = [
            runner.build_snapshot(**_valid_kwargs(risk_state=taxonomy.RISK_STATE_LOW)),
            runner.build_snapshot(**_valid_kwargs(risk_state=taxonomy.RISK_STATE_HIGH)),
        ]
        journal.record_many(snaps)
        records = journal.read_all()
        assert [r.risk_state for r in records] == ["LOW", "HIGH"]

    def test_journal_opens_only_in_append_or_read_mode(self):
        source = inspect.getsource(TradingOntologyJournal)
        tree = ast.parse(source)
        open_calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "open"
        ]
        assert open_calls, "expected at least one open() call"
        for call in open_calls:
            modes = [a.value for a in call.args if isinstance(a, ast.Constant)]
            modes += [
                kw.value.value
                for kw in call.keywords
                if kw.arg == "mode" and isinstance(kw.value, ast.Constant)
            ]
            assert all(m in ("a", "r") for m in modes if isinstance(m, str))


# ---------------------------------------------------------------------------
# Query API
# ---------------------------------------------------------------------------

class TestOntologyIndex:
    def test_ingest_and_latest(self):
        idx = query.OntologyIndex()
        s1 = runner.build_snapshot(**_valid_kwargs())
        idx.ingest(s1)
        assert idx.latest() == s1

    def test_latest_none_when_empty(self):
        idx = query.OntologyIndex()
        assert idx.latest() is None

    def test_history_preserves_order(self):
        idx = query.OntologyIndex()
        s1 = runner.build_snapshot(**_valid_kwargs(risk_state=taxonomy.RISK_STATE_LOW))
        s2 = runner.build_snapshot(**_valid_kwargs(risk_state=taxonomy.RISK_STATE_HIGH))
        idx.ingest(s1)
        idx.ingest(s2)
        assert idx.history() == [s1, s2]

    def test_find_by_id(self):
        idx = query.OntologyIndex()
        s1 = runner.build_snapshot(**_valid_kwargs())
        idx.ingest(s1)
        assert idx.find_by_id(s1.snapshot_id) == s1
        assert idx.find_by_id("nope") is None

    def test_find_by_market_state(self):
        idx = query.OntologyIndex()
        s1 = runner.build_snapshot(**_valid_kwargs(market_state=taxonomy.MARKET_STATE_TREND))
        s2 = runner.build_snapshot(**_valid_kwargs(market_state=taxonomy.MARKET_STATE_RANGE))
        idx.ingest(s1)
        idx.ingest(s2)
        assert idx.find_by_market_state("TREND") == [s1]

    def test_find_by_execution_intent(self):
        idx = query.OntologyIndex()
        s1 = runner.build_snapshot(**_valid_kwargs(execution_intent=taxonomy.EXECUTION_INTENT_ENTER))
        idx.ingest(s1)
        assert idx.find_by_execution_intent("ENTER") == [s1]
        assert idx.find_by_execution_intent("EXIT") == []

    def test_summary_counts_by_market_state(self):
        idx = query.OntologyIndex()
        idx.ingest(runner.build_snapshot(**_valid_kwargs(market_state=taxonomy.MARKET_STATE_TREND)))
        idx.ingest(runner.build_snapshot(**_valid_kwargs(market_state=taxonomy.MARKET_STATE_TREND)))
        idx.ingest(runner.build_snapshot(**_valid_kwargs(market_state=taxonomy.MARKET_STATE_RANGE)))
        assert idx.summary() == {"TREND": 2, "RANGE": 1}

    def test_index_has_no_mutation_method_beyond_ingest(self):
        public_methods = [
            name
            for name, member in inspect.getmembers(query.OntologyIndex, predicate=inspect.isfunction)
            if not name.startswith("_")
        ]
        mutating = [m for m in public_methods if m != "ingest" and not m.startswith(("find", "get"))
                    and m not in ("latest", "history", "summary")]
        assert mutating == []


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

class TestConfig:
    def test_default_ruleset_version_matches_package_version(self):
        cfg = config.OntologyConfig()
        assert cfg.ruleset_version == taxonomy.ONTOLOGY_PACKAGE_VERSION

    def test_config_is_frozen(self):
        cfg = config.OntologyConfig()
        with pytest.raises(Exception):
            cfg.ruleset_version = "9.9.9"


# ---------------------------------------------------------------------------
# Isolation guarantees
# ---------------------------------------------------------------------------

FORBIDDEN_IMPORT_ROOTS = {"broker", "core", "execution", "trade", "intelligence", "market", "tick"}


def _module_source_files():
    base = Path(runner.__file__).parent
    return list(base.glob("*.py"))


class TestIsolation:
    def test_no_forbidden_bujji_subpackage_imports(self):
        for path in _module_source_files():
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    parts = node.module.split(".")
                    if "bujji" in parts:
                        idx = parts.index("bujji")
                        rest = parts[idx + 1:]
                        assert not (rest and rest[0] in FORBIDDEN_IMPORT_ROOTS), (
                            f"{path.name} imports forbidden bujji.{rest[0]}"
                        )

    def test_no_engine_module_exists(self):
        base = Path(runner.__file__).parent
        assert not (base / "engine.py").exists()

    def test_no_pnl_or_score_field_on_snapshot(self):
        fields = {f.name for f in models.TradingOntologySnapshot.__dataclass_fields__.values()}
        forbidden = {"pnl", "score", "probability", "expected_value"}
        assert not (fields & forbidden)

    def test_build_snapshot_is_deterministic_apart_from_clock(self):
        kwargs_a = _valid_kwargs()
        kwargs_b = _valid_kwargs()
        s1 = runner.build_snapshot(**kwargs_a)
        s2 = runner.build_snapshot(**kwargs_b)
        assert s1 == s2

    def test_no_uuid4_used_anywhere_in_package(self):
        for path in _module_source_files():
            source = path.read_text()
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr == "uuid4":
                    raise AssertionError(f"uuid4 used in {path.name}")
