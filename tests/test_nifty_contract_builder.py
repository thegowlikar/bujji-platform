"""Tests for the NIFTY Contract Builder v1 — Engineering Series 42,
Sprint 1.
"""
from __future__ import annotations

import ast
import inspect
from datetime import datetime
from pathlib import Path

import pytest

from bujji.trading_brain.capital_brain.models import CapitalDecision
from bujji.trading_brain.strategy_selector.models import StrategyDecision
from bujji.trading_brain.nifty_contract_builder import config, engine, models, query, runner, serialization, taxonomy
from bujji.journal.nifty_contract_builder_journal import NiftyContractBuilderJournal

FIXED_CLOCK = lambda: datetime(2026, 1, 1, 9, 20, 0)

CURRENT_WEEK = "2026-07-31"
NEXT_WEEK = "2026-08-07"


def _strategy(**overrides):
    base = dict(
        decision_id="SD-0000000000000001",
        selected_strategy="PREMIUM_VWAP_STRADDLE",
        selection_status="SELECTED",
        selection_confidence="VERY_HIGH",
        selection_reason="stub selection reason",
        supporting_conditions=(),
        rejecting_conditions=(),
        alternative_candidates=(),
        all_evaluations=(),
        decision_trace="stub decision trace",
        market_state_assessment_id="MSA-0000000000000001",
        timestamp="2026-01-01T09:00:00",
        version="1.0.0",
    )
    base.update(overrides)
    return StrategyDecision(**base)


def _capital(**overrides):
    base = dict(
        decision_id="CD-0000000000000001",
        capital_intent="STANDARD",
        allocation_status="APPROVED",
        allocation_reason="stub allocation reason",
        allocation_constraints=("NONE",),
        required_controls=("NONE",),
        confidence="VERY_HIGH",
        decision_trace="stub decision trace",
        risk_assessment_id="RA-0000000000000001",
        timestamp="2026-01-01T09:15:00",
        version="1.0.0",
    )
    base.update(overrides)
    return CapitalDecision(**base)


def _spot(value=25148.0):
    return models.NiftySpotSnapshot(spot=value, as_of="2026-01-01T09:20:00")


def _chain(expiries=(CURRENT_WEEK, NEXT_WEEK)):
    strikes_types = [
        (25150, "CE"), (25150, "PE"),
        (25200, "CE"), (25250, "CE"),
        (25100, "PE"), (25050, "PE"),
    ]
    entries = []
    for expiry in expiries:
        for strike, opt in strikes_types:
            entries.append(
                models.NiftyOptionChainEntry(
                    strike=strike,
                    option_type=opt,
                    expiry=expiry,
                    contract_symbol=f"NSE:NIFTY{expiry}{strike}{opt}",
                )
            )
    return models.NiftyOptionChainSnapshot(expiries=tuple(expiries), entries=tuple(entries), as_of="2026-01-01T09:20:00")


# ---------------------------------------------------------------------------
# Taxonomy completeness
# ---------------------------------------------------------------------------

class TestTaxonomy:
    def test_construction_status_values(self):
        assert taxonomy.ALL_CONSTRUCTION_STATUSES == ("UNKNOWN", "CONSTRUCTED", "FAILED")

    def test_failure_reason_values(self):
        # Engineering Series 63: added STRATEGY_OUT_OF_V1_SCOPE,
        # distinguishing a Strategy-Selector-registered-but-untemplated
        # strategy (a disclosed v1 scope boundary) from a genuinely
        # unrecognized strategy (UNKNOWN_STRATEGY).
        assert taxonomy.ALL_FAILURE_REASONS == (
            "MISSING_OPTION_CHAIN", "NO_WEEKLY_EXPIRY", "NO_MATCHING_STRIKE",
            "UNKNOWN_STRATEGY", "INVALID_SPOT", "INSUFFICIENT_DATA",
            "STRATEGY_OUT_OF_V1_SCOPE",
        )

    def test_option_types_and_sides(self):
        assert taxonomy.ALL_OPTION_TYPES == ("CE", "PE")
        assert taxonomy.ALL_SIDES == ("BUY", "SELL")

    def test_supported_strategies_have_templates(self):
        for s in taxonomy.SUPPORTED_STRATEGIES:
            assert s in engine.TEMPLATES

    def test_unsupported_registered_strategies_have_no_template(self):
        for s in taxonomy.UNSUPPORTED_REGISTERED_STRATEGIES:
            assert s not in engine.TEMPLATES

    def test_every_failure_reason_has_description(self):
        for f in taxonomy.ALL_FAILURE_REASONS:
            assert f in taxonomy.FAILURE_REASON_DESCRIPTIONS and taxonomy.FAILURE_REASON_DESCRIPTIONS[f]


# ---------------------------------------------------------------------------
# Every supported strategy template
# ---------------------------------------------------------------------------

class TestEveryStrategyTemplate:
    @pytest.mark.parametrize("strategy_id", taxonomy.SUPPORTED_STRATEGIES)
    def test_constructs_successfully(self, strategy_id):
        result = engine.build_contracts(
            _strategy(selected_strategy=strategy_id), _capital(), _spot(), _chain(), clock=FIXED_CLOCK
        )
        assert result.status == "CONSTRUCTED"
        assert len(result.contracts) == len(engine.TEMPLATES[strategy_id])

    def test_premium_vwap_straddle_legs(self):
        result = engine.build_contracts(
            _strategy(selected_strategy="PREMIUM_VWAP_STRADDLE"), _capital(), _spot(), _chain(), clock=FIXED_CLOCK
        )
        types_sides = sorted((c.option_type, c.side, c.strike) for c in result.contracts)
        assert types_sides == [("CE", "SELL", 25150), ("PE", "SELL", 25150)]

    def test_iron_fly_legs(self):
        result = engine.build_contracts(
            _strategy(selected_strategy="IRON_FLY"), _capital(), _spot(), _chain(), clock=FIXED_CLOCK
        )
        legs = sorted((c.option_type, c.side, c.strike) for c in result.contracts)
        assert legs == [
            ("CE", "BUY", 25200), ("CE", "SELL", 25150),
            ("PE", "BUY", 25100), ("PE", "SELL", 25150),
        ]

    def test_iron_condor_legs(self):
        result = engine.build_contracts(
            _strategy(selected_strategy="IRON_CONDOR"), _capital(), _spot(), _chain(), clock=FIXED_CLOCK
        )
        legs = sorted((c.option_type, c.side, c.strike) for c in result.contracts)
        assert legs == [
            ("CE", "BUY", 25250), ("CE", "SELL", 25200),
            ("PE", "BUY", 25050), ("PE", "SELL", 25100),
        ]

    def test_directional_call_spread_legs(self):
        result = engine.build_contracts(
            _strategy(selected_strategy="DIRECTIONAL_CALL_SPREAD"), _capital(), _spot(), _chain(), clock=FIXED_CLOCK
        )
        legs = sorted((c.option_type, c.side, c.strike) for c in result.contracts)
        assert legs == [("CE", "BUY", 25150), ("CE", "SELL", 25200)]

    def test_directional_put_spread_legs(self):
        result = engine.build_contracts(
            _strategy(selected_strategy="DIRECTIONAL_PUT_SPREAD"), _capital(), _spot(), _chain(), clock=FIXED_CLOCK
        )
        legs = sorted((c.option_type, c.side, c.strike) for c in result.contracts)
        assert legs == [("PE", "BUY", 25150), ("PE", "SELL", 25100)]

    def test_calendar_spread_uses_two_expiries_same_strike(self):
        result = engine.build_contracts(
            _strategy(selected_strategy="CALENDAR_SPREAD"), _capital(), _spot(), _chain(), clock=FIXED_CLOCK
        )
        expiries = sorted(c.expiry for c in result.contracts)
        strikes = {c.strike for c in result.contracts}
        assert expiries == [CURRENT_WEEK, NEXT_WEEK]
        assert strikes == {25150}


# ---------------------------------------------------------------------------
# ATM / OTM construction
# ---------------------------------------------------------------------------

class TestAtmConstruction:
    def test_atm_strike_matches_worked_example(self):
        result = engine.build_contracts(
            _strategy(), _capital(), _spot(25148.0), _chain(), clock=FIXED_CLOCK
        )
        assert result.atm_strike_used == 25150

    def test_atm_strike_deterministic_rounding(self):
        assert engine._atm_strike(25148.0) == 25150
        assert engine._atm_strike(25125.0) == 25150
        assert engine._atm_strike(25124.0) == 25100


class TestOtmConstruction:
    def test_otm1_direction_depends_on_option_type(self):
        atm = 25150
        assert engine._strike_for_leg(atm, "CE", "OTM1") == 25200
        assert engine._strike_for_leg(atm, "PE", "OTM1") == 25100

    def test_otm2_is_two_intervals_out(self):
        atm = 25150
        assert engine._strike_for_leg(atm, "CE", "OTM2") == 25250
        assert engine._strike_for_leg(atm, "PE", "OTM2") == 25050


# ---------------------------------------------------------------------------
# Weekly expiry selection
# ---------------------------------------------------------------------------

class TestWeeklyExpirySelection:
    def test_single_leg_strategy_uses_first_expiry(self):
        result = engine.build_contracts(
            _strategy(selected_strategy="IRON_FLY"), _capital(), _spot(), _chain(), clock=FIXED_CLOCK
        )
        assert all(c.expiry == CURRENT_WEEK for c in result.contracts)

    def test_calendar_needs_two_expiries_and_fails_with_only_one(self):
        result = engine.build_contracts(
            _strategy(selected_strategy="CALENDAR_SPREAD"), _capital(), _spot(),
            _chain(expiries=(CURRENT_WEEK,)), clock=FIXED_CLOCK,
        )
        assert result.status == "FAILED"
        assert result.failure_reason == "NO_WEEKLY_EXPIRY"

    def test_no_expiries_at_all_fails(self):
        result = engine.build_contracts(
            _strategy(), _capital(), _spot(), _chain(expiries=()), clock=FIXED_CLOCK
        )
        assert result.status == "FAILED"
        assert result.failure_reason == "NO_WEEKLY_EXPIRY"


# ---------------------------------------------------------------------------
# Failure handling
# ---------------------------------------------------------------------------

class TestMissingOptionChain:
    def test_none_chain_fails(self):
        result = engine.build_contracts(_strategy(), _capital(), _spot(), None, clock=FIXED_CLOCK)
        assert result.status == "FAILED"
        assert result.failure_reason == "MISSING_OPTION_CHAIN"


class TestInvalidSpot:
    def test_none_spot_snapshot_fails(self):
        result = engine.build_contracts(_strategy(), _capital(), None, _chain(), clock=FIXED_CLOCK)
        assert result.failure_reason == "INVALID_SPOT"

    def test_none_spot_value_fails(self):
        result = engine.build_contracts(_strategy(), _capital(), models.NiftySpotSnapshot(spot=None, as_of="x"), _chain(), clock=FIXED_CLOCK)
        assert result.failure_reason == "INVALID_SPOT"

    def test_zero_or_negative_spot_fails(self):
        result = engine.build_contracts(_strategy(), _capital(), _spot(0.0), _chain(), clock=FIXED_CLOCK)
        assert result.failure_reason == "INVALID_SPOT"
        result2 = engine.build_contracts(_strategy(), _capital(), _spot(-100.0), _chain(), clock=FIXED_CLOCK)
        assert result2.failure_reason == "INVALID_SPOT"


class TestUnknownStrategy:
    def test_unregistered_strategy_fails(self):
        # LONG_STRADDLE is registered by the Strategy Selector but has
        # no v1 contract template -- Series 63 gives this its own
        # distinct failure reason (STRATEGY_OUT_OF_V1_SCOPE), separate
        # from UNKNOWN_STRATEGY (a strategy the registry has never
        # heard of at all; see test_never_fabricates_a_template below).
        result = engine.build_contracts(
            _strategy(selected_strategy="LONG_STRADDLE"), _capital(), _spot(), _chain(), clock=FIXED_CLOCK
        )
        assert result.status == "FAILED"
        assert result.failure_reason == "STRATEGY_OUT_OF_V1_SCOPE"

    def test_never_fabricates_a_template(self):
        result = engine.build_contracts(
            _strategy(selected_strategy="NOT_A_REAL_STRATEGY"), _capital(), _spot(), _chain(), clock=FIXED_CLOCK
        )
        assert result.contracts == ()


class TestInsufficientData:
    def test_none_strategy_decision_fails(self):
        result = engine.build_contracts(None, _capital(), _spot(), _chain(), clock=FIXED_CLOCK)
        assert result.failure_reason == "INSUFFICIENT_DATA"

    def test_none_capital_decision_fails(self):
        result = engine.build_contracts(_strategy(), None, _spot(), _chain(), clock=FIXED_CLOCK)
        assert result.failure_reason == "INSUFFICIENT_DATA"

    def test_no_selected_strategy_fails(self):
        result = engine.build_contracts(
            _strategy(selected_strategy=None, selection_status="NO_STRATEGY"), _capital(), _spot(), _chain(), clock=FIXED_CLOCK
        )
        assert result.failure_reason == "INSUFFICIENT_DATA"


class TestNoMatchingStrike:
    def test_missing_chain_entry_fails_atomically(self):
        sparse_chain = models.NiftyOptionChainSnapshot(
            expiries=(CURRENT_WEEK,),
            entries=(models.NiftyOptionChainEntry(25150, "CE", CURRENT_WEEK, "NSE:X"),),
            as_of="x",
        )
        result = engine.build_contracts(
            _strategy(selected_strategy="PREMIUM_VWAP_STRADDLE"), _capital(), _spot(), sparse_chain, clock=FIXED_CLOCK
        )
        assert result.status == "FAILED"
        assert result.failure_reason == "NO_MATCHING_STRIKE"
        assert result.contracts == ()


# ---------------------------------------------------------------------------
# Determinism / replay
# ---------------------------------------------------------------------------

class TestDeterminism:
    def test_same_inputs_same_construction_id(self):
        r1 = engine.build_contracts(_strategy(), _capital(), _spot(), _chain(), clock=FIXED_CLOCK)
        r2 = engine.build_contracts(_strategy(), _capital(), _spot(), _chain(), clock=FIXED_CLOCK)
        assert r1.construction_id == r2.construction_id
        assert r1 == r2

    def test_byte_identical_across_repeated_runs(self):
        results = [
            engine.build_contracts(_strategy(selected_strategy="IRON_CONDOR"), _capital(), _spot(), _chain(), clock=FIXED_CLOCK)
            for _ in range(5)
        ]
        assert all(r == results[0] for r in results)

    def test_different_inputs_different_construction_id(self):
        r1 = engine.build_contracts(_strategy(), _capital(), _spot(), _chain(), clock=FIXED_CLOCK)
        r2 = engine.build_contracts(_strategy(selected_strategy="IRON_FLY"), _capital(), _spot(), _chain(), clock=FIXED_CLOCK)
        assert r1.construction_id != r2.construction_id

    def test_construction_id_uses_md5_prefix_not_uuid(self):
        result = engine.build_contracts(_strategy(), _capital(), _spot(), _chain(), clock=FIXED_CLOCK)
        assert result.construction_id.startswith("CCR-")
        assert len(result.construction_id) == len("CCR-") + 16

    def test_contract_id_uses_md5_prefix_not_uuid(self):
        result = engine.build_contracts(_strategy(), _capital(), _spot(), _chain(), clock=FIXED_CLOCK)
        for c in result.contracts:
            assert c.contract_id.startswith("NC-")
            assert len(c.contract_id) == len("NC-") + 16

    def test_real_clock_default_produces_wall_clock_timestamp(self):
        before = datetime.now()
        result = engine.build_contracts(_strategy(), _capital(), _spot(), _chain())
        after = datetime.now()
        parsed = datetime.fromisoformat(result.timestamp)
        assert before <= parsed <= after

    def test_no_randomness_module_used(self):
        source = inspect.getsource(engine)
        assert "import random" not in source
        assert "random." not in source


# ---------------------------------------------------------------------------
# Frozen dataclasses / no mutation
# ---------------------------------------------------------------------------

class TestFrozen:
    def test_result_is_frozen(self):
        result = engine.build_contracts(_strategy(), _capital(), _spot(), _chain(), clock=FIXED_CLOCK)
        with pytest.raises(Exception):
            result.status = "HACKED"

    def test_contract_is_frozen(self):
        result = engine.build_contracts(_strategy(), _capital(), _spot(), _chain(), clock=FIXED_CLOCK)
        with pytest.raises(Exception):
            result.contracts[0].strike = 0

    def test_engine_does_not_mutate_inputs(self):
        s = _strategy()
        c = _capital()
        s_copy = StrategyDecision(**{f.name: getattr(s, f.name) for f in s.__dataclass_fields__.values()})
        c_copy = CapitalDecision(**{f.name: getattr(c, f.name) for f in c.__dataclass_fields__.values()})
        engine.build_contracts(s, c, _spot(), _chain(), clock=FIXED_CLOCK)
        assert s == s_copy
        assert c == c_copy


# ---------------------------------------------------------------------------
# Serialization round trip
# ---------------------------------------------------------------------------

class TestSerialization:
    def test_round_trip_preserves_all_fields(self):
        result = engine.build_contracts(_strategy(selected_strategy="IRON_FLY"), _capital(), _spot(), _chain(), clock=FIXED_CLOCK)
        d = serialization.result_to_dict(result)
        back = serialization.result_from_dict(d)
        assert back == result

    def test_round_trip_is_json_safe(self):
        import json

        result = engine.build_contracts(_strategy(), _capital(), _spot(), _chain(), clock=FIXED_CLOCK)
        d = serialization.result_to_dict(result)
        text = json.dumps(d)
        back = serialization.result_from_dict(json.loads(text))
        assert back == result

    def test_round_trip_with_failure(self):
        result = engine.build_contracts(_strategy(selected_strategy="LONG_STRADDLE"), _capital(), _spot(), _chain(), clock=FIXED_CLOCK)
        d = serialization.result_to_dict(result)
        assert d["contracts"] == []
        back = serialization.result_from_dict(d)
        assert back == result


# ---------------------------------------------------------------------------
# Journal -- append only
# ---------------------------------------------------------------------------

class TestJournal:
    def test_record_and_read_all(self, tmp_path):
        j = NiftyContractBuilderJournal(tmp_path / "ncb.jsonl")
        result = engine.build_contracts(_strategy(), _capital(), _spot(), _chain(), clock=FIXED_CLOCK)
        j.record(result)
        assert j.read_all() == [result]

    def test_read_all_empty_when_file_missing(self, tmp_path):
        j = NiftyContractBuilderJournal(tmp_path / "missing.jsonl")
        assert j.read_all() == []

    def test_record_many_appends_in_order(self, tmp_path):
        j = NiftyContractBuilderJournal(tmp_path / "j.jsonl")
        r1 = engine.build_contracts(_strategy(), _capital(), _spot(), _chain(), clock=FIXED_CLOCK)
        r2 = engine.build_contracts(_strategy(selected_strategy="IRON_FLY"), _capital(), _spot(), _chain(), clock=FIXED_CLOCK)
        j.record_many([r1, r2])
        records = j.read_all()
        assert [r.construction_id for r in records] == [r1.construction_id, r2.construction_id]

    def test_journal_opens_only_in_append_or_read_mode(self):
        source = inspect.getsource(NiftyContractBuilderJournal)
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
# runner.run_construction composition
# ---------------------------------------------------------------------------

class TestRunner:
    def test_run_without_journal(self):
        result = runner.run_construction(_strategy(), _capital(), _spot(), _chain(), clock=FIXED_CLOCK)
        assert isinstance(result, models.ContractConstructionResult)

    def test_run_journals_when_given_one(self, tmp_path):
        j = NiftyContractBuilderJournal(tmp_path / "run.jsonl")
        result = runner.run_construction(_strategy(), _capital(), _spot(), _chain(), clock=FIXED_CLOCK, journal=j)
        assert j.read_all() == [result]


# ---------------------------------------------------------------------------
# Query API
# ---------------------------------------------------------------------------

class TestContractConstructionIndex:
    def test_ingest_and_latest(self):
        idx = query.ContractConstructionIndex()
        r1 = engine.build_contracts(_strategy(), _capital(), _spot(), _chain(), clock=FIXED_CLOCK)
        idx.ingest(r1)
        assert idx.latest() == r1

    def test_find_by_status(self):
        idx = query.ContractConstructionIndex()
        r1 = engine.build_contracts(_strategy(selected_strategy="LONG_STRADDLE"), _capital(), _spot(), _chain(), clock=FIXED_CLOCK)
        idx.ingest(r1)
        assert idx.find_by_status("FAILED") == [r1]

    def test_find_by_strategy(self):
        idx = query.ContractConstructionIndex()
        r1 = engine.build_contracts(_strategy(selected_strategy="IRON_FLY"), _capital(), _spot(), _chain(), clock=FIXED_CLOCK)
        idx.ingest(r1)
        assert idx.find_by_strategy("IRON_FLY") == [r1]

    def test_summary_counts_by_status(self):
        idx = query.ContractConstructionIndex()
        idx.ingest(engine.build_contracts(_strategy(), _capital(), _spot(), _chain(), clock=FIXED_CLOCK))
        idx.ingest(engine.build_contracts(_strategy(selected_strategy="LONG_STRADDLE"), _capital(), _spot(), _chain(), clock=FIXED_CLOCK))
        summary = idx.summary()
        assert summary.get("CONSTRUCTED") == 1
        assert summary.get("FAILED") == 1


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

class TestConfig:
    def test_default_version_matches_package_version(self):
        cfg = config.NiftyContractBuilderConfig()
        assert cfg.version == taxonomy.NIFTY_CONTRACT_BUILDER_VERSION

    def test_default_strike_interval(self):
        cfg = config.NiftyContractBuilderConfig()
        assert cfg.strike_interval == 50

    def test_config_is_frozen(self):
        cfg = config.NiftyContractBuilderConfig()
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

    def test_no_market_state_or_risk_brain_import(self):
        for path in _module_source_files():
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    for forbidden in ("market_state", "risk_brain", "evidence_interpreter", "ontology"):
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

    def test_no_broker_or_order_import(self):
        for path in _module_source_files():
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    lowered = node.module.lower()
                    for forbidden in ("broker", "order", "fyers", "zerodha", "replay"):
                        assert forbidden not in lowered

    def test_no_lot_margin_greek_or_score_fields(self):
        contract_fields = {f.name for f in models.NiftyOptionContract.__dataclass_fields__.values()}
        result_fields = {f.name for f in models.ContractConstructionResult.__dataclass_fields__.values()}
        forbidden = {
            "lot_size", "lots", "margin", "delta", "gamma", "theta", "vega",
            "probability", "score", "iv", "expected_value",
        }
        assert not (contract_fields & forbidden)
        assert not (result_fields & forbidden)

    def test_no_uuid4_used_anywhere_in_package(self):
        for path in _module_source_files():
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr == "uuid4":
                    raise AssertionError(f"uuid4 used in {path.name}")

    def test_no_order_submission_capability_in_source(self):
        for path in _module_source_files():
            source = path.read_text()
            for forbidden in ("place_order(", "def execute(", "def submit_order", "def authenticate("):
                assert forbidden not in source

    def test_no_optimization_or_greek_terms_in_executable_code(self):
        # Strip every docstring (module- and function-level) before
        # checking -- this module's own docstrings legitimately
        # disclaim these exact terms in prose (e.g. "never a Greek, a
        # probability"), which would otherwise false-positive.
        tree = ast.parse(inspect.getsource(engine))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if (
                    node.body
                    and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)
                    and isinstance(node.body[0].value.value, str)
                ):
                    node.body[0] = ast.Pass()
        ast.fix_missing_locations(tree)
        code_only = ast.unparse(tree).lower()
        for forbidden in ("delta", "gamma", "theta", "vega", "iv_rank", "probability", "optimize"):
            assert forbidden not in code_only
