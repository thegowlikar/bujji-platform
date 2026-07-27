"""Tests for the Position Sizing Engine v1 — Engineering Series 43,
Sprint 1.
"""
from __future__ import annotations

import ast
import inspect
from datetime import datetime
from pathlib import Path

import pytest

from bujji.trading_brain.capital_brain.models import CapitalDecision
from bujji.trading_brain.nifty_contract_builder.models import NiftyOptionContract
from bujji.trading_brain.position_sizing import config as config_module
from bujji.trading_brain.position_sizing import engine, models, query, runner, serialization, taxonomy
from bujji.journal.position_sizing_journal import PositionSizingJournal

FIXED_CLOCK = lambda: datetime(2026, 1, 1, 9, 25, 0)


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


def _contract(strike, option_type, side, expiry="2026-07-31", **overrides):
    base = dict(
        contract_id=f"NC-{strike}{option_type}{side}",
        underlying="NIFTY",
        expiry=expiry,
        strike=strike,
        option_type=option_type,
        side=side,
        contract_symbol=f"NSE:NIFTY{expiry}{strike}{option_type}",
        capital_intent="STANDARD",
        strategy_id="PREMIUM_VWAP_STRADDLE",
        selection_reason="stub selection reason",
        construction_trace="stub construction trace",
        timestamp="2026-01-01T09:20:00",
        version="1.0.0",
    )
    base.update(overrides)
    return NiftyOptionContract(**base)


def _straddle_legs():
    return (
        _contract(25150, "CE", "SELL"),
        _contract(25150, "PE", "SELL"),
    )


def _iron_fly_legs():
    return (
        _contract(25150, "CE", "SELL"),
        _contract(25150, "PE", "SELL"),
        _contract(25200, "CE", "BUY"),
        _contract(25100, "PE", "BUY"),
    )


def _policy(policy="STRICT"):
    return models.CapitalPolicy(policy=policy)


def _lot_spec(**overrides):
    base = dict(underlying="NIFTY", lot_size=75, effective_date="2026-01-01")
    base.update(overrides)
    return models.LotSpecification(**base)


def _config(**overrides):
    return config_module.PositionSizingConfig(**overrides)


def _size(capital=None, contracts=None, policy=None, lot_spec=None, cfg=None):
    return engine.size_position(
        capital if capital is not None else _capital(),
        contracts if contracts is not None else _straddle_legs(),
        policy if policy is not None else _policy(),
        lot_spec if lot_spec is not None else _lot_spec(),
        cfg if cfg is not None else _config(),
        clock=FIXED_CLOCK,
    )


# ---------------------------------------------------------------------------
# Taxonomy completeness
# ---------------------------------------------------------------------------

class TestTaxonomy:
    def test_capital_intent_reused_from_capital_brain(self):
        from bujji.trading_brain.capital_brain.taxonomy import ALL_CAPITAL_INTENTS
        assert taxonomy.REUSED_CAPITAL_INTENTS == ALL_CAPITAL_INTENTS

    def test_capital_policy_values(self):
        assert taxonomy.ALL_CAPITAL_POLICIES == ("STRICT", "ESTIMATED", "SIMULATION", "CERTIFIED")

    def test_failure_reason_values(self):
        assert taxonomy.ALL_FAILURE_REASONS == (
            "UNKNOWN_CAPITAL_INTENT", "INVALID_LOT_SPECIFICATION", "EMPTY_CONTRACT_SET",
            "INVALID_CONFIGURATION", "ZERO_QUANTITY", "INSUFFICIENT_DATA",
        )

    def test_every_failure_reason_has_description(self):
        for f in taxonomy.ALL_FAILURE_REASONS:
            assert f in taxonomy.FAILURE_REASON_DESCRIPTIONS and taxonomy.FAILURE_REASON_DESCRIPTIONS[f]


# ---------------------------------------------------------------------------
# Every capital intent
# ---------------------------------------------------------------------------

class TestEveryCapitalIntent:
    def test_minimal_uses_minimum_lots(self):
        result = _size(capital=_capital(capital_intent="MINIMAL"), cfg=_config(minimum_lots=1))
        assert result.validation == "PASSED"
        assert result.lots_per_leg == 1

    def test_reduced_uses_reduced_lots(self):
        result = _size(capital=_capital(capital_intent="REDUCED"), cfg=_config(reduced_lots=1))
        assert result.lots_per_leg == 1

    def test_standard_uses_standard_lots(self):
        result = _size(capital=_capital(capital_intent="STANDARD"), cfg=_config(standard_lots=2))
        assert result.lots_per_leg == 2

    def test_full_uses_full_lots(self):
        result = _size(capital=_capital(capital_intent="FULL"), cfg=_config(full_lots=3, max_lots=5))
        assert result.lots_per_leg == 3

    def test_none_yields_zero_quantity_failure(self):
        result = _size(capital=_capital(capital_intent="NONE"))
        assert result.validation == "FAILED"
        assert result.failure_reason == "ZERO_QUANTITY"

    def test_unknown_intent_fails(self):
        result = _size(capital=_capital(capital_intent="UNKNOWN"))
        assert result.failure_reason in ("ZERO_QUANTITY", "UNKNOWN_CAPITAL_INTENT")


# ---------------------------------------------------------------------------
# Single-leg / multi-leg strategies
# ---------------------------------------------------------------------------

class TestSingleAndMultiLeg:
    def test_single_leg_strategy(self):
        result = _size(contracts=(_contract(25150, "CE", "BUY"),))
        assert result.validation == "PASSED"
        assert len(result.contracts) == 1

    def test_multi_leg_strategy_iron_fly(self):
        result = _size(contracts=_iron_fly_legs())
        assert result.validation == "PASSED"
        assert len(result.contracts) == 4

    def test_all_legs_receive_identical_lots(self):
        result = _size(contracts=_iron_fly_legs(), cfg=_config(standard_lots=2))
        # lots_per_leg is a single value applied uniformly -- there is
        # no per-leg breakdown that could differ.
        assert result.lots_per_leg == 2
        assert result.quantity_per_leg == 2 * 75


# ---------------------------------------------------------------------------
# One lot / multiple lots / quantity calculation
# ---------------------------------------------------------------------------

class TestQuantityCalculation:
    def test_one_lot_quantity(self):
        result = _size(capital=_capital(capital_intent="MINIMAL"), cfg=_config(minimum_lots=1), lot_spec=_lot_spec(lot_size=75))
        assert result.quantity_per_leg == 75

    def test_multiple_lots_quantity(self):
        result = _size(capital=_capital(capital_intent="STANDARD"), cfg=_config(standard_lots=2), lot_spec=_lot_spec(lot_size=75))
        assert result.quantity_per_leg == 150

    def test_quantity_is_exactly_lots_times_lot_size(self):
        result = _size(cfg=_config(standard_lots=3), lot_spec=_lot_spec(lot_size=50))
        assert result.quantity_per_leg == 3 * 50


# ---------------------------------------------------------------------------
# Unknown capital intent
# ---------------------------------------------------------------------------

class TestUnknownCapitalIntent:
    def test_bogus_capital_intent_fails(self):
        result = _size(capital=_capital(capital_intent="NOT_A_REAL_INTENT"))
        assert result.validation == "FAILED"
        assert result.failure_reason == "UNKNOWN_CAPITAL_INTENT"


# ---------------------------------------------------------------------------
# Invalid configuration
# ---------------------------------------------------------------------------

class TestInvalidConfiguration:
    def test_negative_lot_count_is_invalid(self):
        result = _size(cfg=_config(standard_lots=-1))
        assert result.validation == "FAILED"
        assert result.failure_reason == "INVALID_CONFIGURATION"

    def test_full_lots_exceeding_max_lots_is_invalid(self):
        result = _size(capital=_capital(capital_intent="FULL"), cfg=_config(full_lots=20, max_lots=10))
        assert result.failure_reason == "INVALID_CONFIGURATION"

    def test_unrecognized_capital_policy_is_invalid(self):
        result = _size(policy=_policy("NOT_A_REAL_POLICY"))
        assert result.failure_reason == "INVALID_CONFIGURATION"

    def test_zero_max_lots_is_invalid(self):
        result = _size(cfg=_config(max_lots=0))
        assert result.failure_reason == "INVALID_CONFIGURATION"


# ---------------------------------------------------------------------------
# Invalid lot specification
# ---------------------------------------------------------------------------

class TestInvalidLotSpecification:
    def test_non_positive_lot_size_is_invalid(self):
        result = _size(lot_spec=_lot_spec(lot_size=0))
        assert result.failure_reason == "INVALID_LOT_SPECIFICATION"

    def test_negative_lot_size_is_invalid(self):
        result = _size(lot_spec=_lot_spec(lot_size=-75))
        assert result.failure_reason == "INVALID_LOT_SPECIFICATION"

    def test_non_nifty_underlying_is_invalid(self):
        result = _size(lot_spec=_lot_spec(underlying="BANKNIFTY"))
        assert result.failure_reason == "INVALID_LOT_SPECIFICATION"


# ---------------------------------------------------------------------------
# Empty contract set
# ---------------------------------------------------------------------------

class TestEmptyContractSet:
    def test_empty_tuple_fails(self):
        result = _size(contracts=())
        assert result.validation == "FAILED"
        assert result.failure_reason == "EMPTY_CONTRACT_SET"


# ---------------------------------------------------------------------------
# Duplicate legs / insufficient data
# ---------------------------------------------------------------------------

class TestDuplicateLegsAndInsufficientData:
    def test_duplicate_legs_fail(self):
        dup = (_contract(25150, "CE", "SELL"), _contract(25150, "CE", "SELL"))
        result = _size(contracts=dup)
        assert result.validation == "FAILED"
        assert result.failure_reason == "INSUFFICIENT_DATA"

    def test_none_capital_decision_fails(self):
        result = engine.size_position(None, _straddle_legs(), _policy(), _lot_spec(), _config(), clock=FIXED_CLOCK)
        assert result.failure_reason == "INSUFFICIENT_DATA"

    def test_none_contracts_fails(self):
        result = engine.size_position(_capital(), None, _policy(), _lot_spec(), _config(), clock=FIXED_CLOCK)
        assert result.failure_reason == "INSUFFICIENT_DATA"

    def test_none_capital_policy_fails(self):
        result = engine.size_position(_capital(), _straddle_legs(), None, _lot_spec(), _config(), clock=FIXED_CLOCK)
        assert result.failure_reason == "INSUFFICIENT_DATA"

    def test_none_lot_spec_fails(self):
        result = engine.size_position(_capital(), _straddle_legs(), _policy(), None, _config(), clock=FIXED_CLOCK)
        assert result.failure_reason == "INSUFFICIENT_DATA"


# ---------------------------------------------------------------------------
# Traceability
# ---------------------------------------------------------------------------

class TestTraceability:
    def test_trace_mentions_capital_intent_and_quantity(self):
        result = _size()
        assert "STANDARD" in result.sizing_trace
        assert str(result.quantity_per_leg) in result.sizing_trace

    def test_trace_mentions_every_leg(self):
        result = _size(contracts=_iron_fly_legs())
        for c in result.contracts:
            assert f"{c.strike}{c.option_type}" in result.sizing_trace


# ---------------------------------------------------------------------------
# Determinism / replay
# ---------------------------------------------------------------------------

class TestDeterminism:
    def test_same_inputs_same_plan_id(self):
        r1 = _size()
        r2 = _size()
        assert r1.plan_id == r2.plan_id
        assert r1 == r2

    def test_byte_identical_across_repeated_runs(self):
        results = [_size(contracts=_iron_fly_legs()) for _ in range(5)]
        assert all(r == results[0] for r in results)

    def test_different_inputs_different_plan_id(self):
        r1 = _size()
        r2 = _size(capital=_capital(capital_intent="MINIMAL"))
        assert r1.plan_id != r2.plan_id

    def test_plan_id_uses_md5_prefix_not_uuid(self):
        result = _size()
        assert result.plan_id.startswith("PP-")
        assert len(result.plan_id) == len("PP-") + 16

    def test_real_clock_default_produces_wall_clock_timestamp(self):
        before = datetime.now()
        result = engine.size_position(_capital(), _straddle_legs(), _policy(), _lot_spec(), _config())
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
    def test_plan_is_frozen(self):
        result = _size()
        with pytest.raises(Exception):
            result.validation = "HACKED"

    def test_capital_policy_is_frozen(self):
        p = _policy()
        with pytest.raises(Exception):
            p.policy = "HACKED"

    def test_lot_specification_is_frozen(self):
        s = _lot_spec()
        with pytest.raises(Exception):
            s.lot_size = 0

    def test_engine_does_not_mutate_inputs(self):
        c = _capital()
        legs = _straddle_legs()
        c_copy = CapitalDecision(**{f.name: getattr(c, f.name) for f in c.__dataclass_fields__.values()})
        _size(capital=c, contracts=legs)
        assert c == c_copy


# ---------------------------------------------------------------------------
# Serialization round trip
# ---------------------------------------------------------------------------

class TestSerialization:
    def test_round_trip_preserves_all_fields(self):
        result = _size(contracts=_iron_fly_legs())
        d = serialization.plan_to_dict(result)
        back = serialization.plan_from_dict(d)
        assert back == result

    def test_round_trip_is_json_safe(self):
        import json

        result = _size()
        d = serialization.plan_to_dict(result)
        text = json.dumps(d)
        back = serialization.plan_from_dict(json.loads(text))
        assert back == result

    def test_round_trip_with_failure(self):
        result = _size(contracts=())
        d = serialization.plan_to_dict(result)
        assert d["contracts"] == []
        back = serialization.plan_from_dict(d)
        assert back == result

    def test_capital_policy_round_trip(self):
        p = _policy("CERTIFIED")
        d = serialization.capital_policy_to_dict(p)
        assert serialization.capital_policy_from_dict(d) == p

    def test_lot_specification_round_trip(self):
        s = _lot_spec()
        d = serialization.lot_specification_to_dict(s)
        assert serialization.lot_specification_from_dict(d) == s


# ---------------------------------------------------------------------------
# Journal -- append only
# ---------------------------------------------------------------------------

class TestJournal:
    def test_record_and_read_all(self, tmp_path):
        j = PositionSizingJournal(tmp_path / "ps.jsonl")
        result = _size()
        j.record(result)
        assert j.read_all() == [result]

    def test_read_all_empty_when_file_missing(self, tmp_path):
        j = PositionSizingJournal(tmp_path / "missing.jsonl")
        assert j.read_all() == []

    def test_record_many_appends_in_order(self, tmp_path):
        j = PositionSizingJournal(tmp_path / "j.jsonl")
        r1 = _size(capital=_capital(capital_intent="MINIMAL"))
        r2 = _size(capital=_capital(capital_intent="STANDARD"))
        j.record_many([r1, r2])
        records = j.read_all()
        assert [r.plan_id for r in records] == [r1.plan_id, r2.plan_id]

    def test_journal_opens_only_in_append_or_read_mode(self):
        source = inspect.getsource(PositionSizingJournal)
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
# runner.run_sizing composition
# ---------------------------------------------------------------------------

class TestRunner:
    def test_run_without_journal(self):
        result = runner.run_sizing(_capital(), _straddle_legs(), _policy(), _lot_spec(), _config(), clock=FIXED_CLOCK)
        assert isinstance(result, models.PositionPlan)

    def test_run_journals_when_given_one(self, tmp_path):
        j = PositionSizingJournal(tmp_path / "run.jsonl")
        result = runner.run_sizing(_capital(), _straddle_legs(), _policy(), _lot_spec(), _config(), clock=FIXED_CLOCK, journal=j)
        assert j.read_all() == [result]


# ---------------------------------------------------------------------------
# Query API
# ---------------------------------------------------------------------------

class TestPositionPlanIndex:
    def test_ingest_and_latest(self):
        idx = query.PositionPlanIndex()
        r1 = _size()
        idx.ingest(r1)
        assert idx.latest() == r1

    def test_find_by_validation(self):
        idx = query.PositionPlanIndex()
        r1 = _size(contracts=())
        idx.ingest(r1)
        assert idx.find_by_validation("FAILED") == [r1]

    def test_find_by_capital_intent(self):
        idx = query.PositionPlanIndex()
        r1 = _size(capital=_capital(capital_intent="MINIMAL"))
        idx.ingest(r1)
        assert idx.find_by_capital_intent("MINIMAL") == [r1]

    def test_summary_counts_by_validation(self):
        idx = query.PositionPlanIndex()
        idx.ingest(_size())
        idx.ingest(_size(contracts=()))
        summary = idx.summary()
        assert summary.get("PASSED") == 1
        assert summary.get("FAILED") == 1


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

class TestConfig:
    def test_default_version_matches_package_version(self):
        cfg = config_module.PositionSizingConfig()
        assert cfg.version == taxonomy.POSITION_SIZING_VERSION

    def test_config_is_frozen(self):
        cfg = config_module.PositionSizingConfig()
        with pytest.raises(Exception):
            cfg.standard_lots = 99


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

    def test_no_market_state_or_risk_brain_or_strategy_selector_import(self):
        for path in _module_source_files():
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    for forbidden in ("market_state", "risk_brain", "evidence_interpreter", "strategy_selector", "ontology"):
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

    def test_no_margin_leverage_or_exposure_fields(self):
        plan_fields = {f.name for f in models.PositionPlan.__dataclass_fields__.values()}
        forbidden = {"margin", "leverage", "exposure", "delta", "gamma", "theta", "vega", "probability", "score"}
        assert not (plan_fields & forbidden)

    def test_no_uuid4_used_anywhere_in_package(self):
        for path in _module_source_files():
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr == "uuid4":
                    raise AssertionError(f"uuid4 used in {path.name}")

    def test_no_authentication_or_execution_capability_in_source(self):
        for path in _module_source_files():
            source = path.read_text()
            for forbidden in ("place_order(", "def authenticate(", "def connect(", "def execute("):
                assert forbidden not in source

    def test_no_runtime_dependency_terms_in_executable_code(self):
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
        for forbidden in ("fyers", "zerodha", "requests.", "websocket"):
            assert forbidden not in code_only
