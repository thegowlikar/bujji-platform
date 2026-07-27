"""Tests for the Order Construction Service v1 — Engineering Series
44, Sprint 1.
"""
from __future__ import annotations

import ast
import inspect
from datetime import datetime
from pathlib import Path

import pytest

from bujji.trading_brain.nifty_contract_builder.models import NiftyOptionContract
from bujji.trading_brain.position_sizing.models import PositionPlan
from bujji.trading_brain.order_construction import config as config_module
from bujji.trading_brain.order_construction import engine, models, query, runner, serialization, taxonomy
from bujji.journal.order_construction_journal import OrderConstructionJournal

FIXED_CLOCK = lambda: datetime(2026, 1, 1, 9, 30, 0)


def _contract(strike, option_type, side, **overrides):
    base = dict(
        contract_id=f"NC-{strike}{option_type}{side}",
        underlying="NIFTY",
        expiry="2026-07-31",
        strike=strike,
        option_type=option_type,
        side=side,
        contract_symbol=f"NSE:NIFTY2026-07-31{strike}{option_type}",
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
    return (_contract(25150, "CE", "SELL"), _contract(25150, "PE", "SELL"))


def _iron_fly_legs():
    return (
        _contract(25150, "CE", "SELL"),
        _contract(25150, "PE", "SELL"),
        _contract(25200, "CE", "BUY"),
        _contract(25100, "PE", "BUY"),
    )


def _plan(**overrides):
    base = dict(
        plan_id="PP-0000000000000001",
        contracts=_straddle_legs(),
        lots_per_leg=2,
        quantity_per_leg=150,
        capital_intent="STANDARD",
        sizing_policy="STANDARD",
        validation="PASSED",
        sizing_reason="stub sizing reason",
        sizing_trace="stub sizing trace",
        failure_reason=None,
        capital_decision_id="CD-0000000000000001",
        timestamp="2026-01-01T09:25:00",
        version="1.0.0",
    )
    base.update(overrides)
    return PositionPlan(**base)


def _policy(policy="MARKET", **overrides):
    base = dict(policy=policy)
    base.update(overrides)
    return models.ExecutionPolicy(**base)


def _trading_config(**overrides):
    base = dict(
        product="MIS",
        validity="DAY",
        session_id="SESSION-0001",
        pipeline_version="1.0.0",
        qualification_fingerprint="FP-abc123",
    )
    base.update(overrides)
    return models.TradingConfiguration(**base)


def _construct(plan=None, policy=None, cfg=None):
    return engine.construct_orders(
        plan if plan is not None else _plan(),
        policy if policy is not None else _policy(),
        cfg if cfg is not None else _trading_config(),
        clock=FIXED_CLOCK,
    )


# ---------------------------------------------------------------------------
# Taxonomy completeness
# ---------------------------------------------------------------------------

class TestTaxonomy:
    def test_construction_status_values(self):
        assert taxonomy.ALL_CONSTRUCTION_STATUSES == ("UNKNOWN", "CONSTRUCTED", "FAILED")

    def test_execution_policy_values(self):
        assert taxonomy.ALL_EXECUTION_POLICIES == ("MARKET", "LIMIT", "STOP", "STOP_LIMIT")

    def test_product_values(self):
        assert taxonomy.ALL_PRODUCTS == ("MIS", "NRML")

    def test_validity_values(self):
        assert taxonomy.ALL_VALIDITIES == ("DAY", "IOC", "FOK")

    def test_failure_reason_values(self):
        assert taxonomy.ALL_FAILURE_REASONS == (
            "EMPTY_POSITION_PLAN", "INVALID_EXECUTION_POLICY", "INVALID_PRODUCT",
            "INVALID_VALIDITY", "ZERO_QUANTITY", "DUPLICATE_REQUEST", "INSUFFICIENT_DATA",
        )

    def test_every_failure_reason_has_description(self):
        for f in taxonomy.ALL_FAILURE_REASONS:
            assert f in taxonomy.FAILURE_REASON_DESCRIPTIONS and taxonomy.FAILURE_REASON_DESCRIPTIONS[f]


# ---------------------------------------------------------------------------
# Single-leg / multi-leg order creation
# ---------------------------------------------------------------------------

class TestSingleLegOrderCreation:
    def test_single_leg_yields_one_order(self):
        result = _construct(plan=_plan(contracts=(_contract(25150, "CE", "BUY"),)))
        assert result.status == "CONSTRUCTED"
        assert len(result.requests) == 1


class TestMultiLegOrderCreation:
    def test_straddle_yields_two_orders(self):
        result = _construct(plan=_plan(contracts=_straddle_legs()))
        assert len(result.requests) == 2

    def test_iron_fly_yields_four_orders(self):
        result = _construct(plan=_plan(contracts=_iron_fly_legs()))
        assert result.status == "CONSTRUCTED"
        assert len(result.requests) == 4

    def test_calendar_yields_two_orders(self):
        legs = (
            _contract(25150, "CE", "SELL", expiry="2026-07-31"),
            _contract(25150, "CE", "BUY", expiry="2026-08-07"),
        )
        result = _construct(plan=_plan(contracts=legs))
        assert len(result.requests) == 2


# ---------------------------------------------------------------------------
# Order side mapping / quantity propagation
# ---------------------------------------------------------------------------

class TestOrderSideMapping:
    def test_side_matches_contract_side(self):
        result = _construct(plan=_plan(contracts=_iron_fly_legs()))
        sides = {(r.contract.strike, r.contract.option_type): r.side for r in result.requests}
        assert sides[(25150, "CE")] == "SELL"
        assert sides[(25200, "CE")] == "BUY"


class TestQuantityPropagation:
    def test_every_leg_receives_plan_quantity(self):
        result = _construct(plan=_plan(quantity_per_leg=150, contracts=_iron_fly_legs()))
        assert all(r.quantity == 150 for r in result.requests)

    def test_different_plan_quantity_propagates(self):
        result = _construct(plan=_plan(quantity_per_leg=75, contracts=_straddle_legs()))
        assert all(r.quantity == 75 for r in result.requests)


# ---------------------------------------------------------------------------
# Product selection / execution policy selection / validity selection
# ---------------------------------------------------------------------------

class TestProductSelection:
    def test_mis_product_applied(self):
        result = _construct(cfg=_trading_config(product="MIS"))
        assert all(r.product == "MIS" for r in result.requests)

    def test_nrml_product_applied(self):
        result = _construct(cfg=_trading_config(product="NRML"))
        assert all(r.product == "NRML" for r in result.requests)


class TestExecutionPolicySelection:
    @pytest.mark.parametrize("policy_value", ["MARKET", "LIMIT", "STOP", "STOP_LIMIT"])
    def test_each_policy_applied(self, policy_value):
        result = _construct(policy=_policy(policy_value))
        assert all(r.order_type == policy_value for r in result.requests)
        assert all(r.execution_policy == policy_value for r in result.requests)


class TestValiditySelection:
    @pytest.mark.parametrize("validity_value", ["DAY", "IOC", "FOK"])
    def test_each_validity_applied(self, validity_value):
        result = _construct(cfg=_trading_config(validity=validity_value))
        assert all(r.validity == validity_value for r in result.requests)


# ---------------------------------------------------------------------------
# Client order ID determinism
# ---------------------------------------------------------------------------

class TestClientOrderIdDeterminism:
    def test_same_inputs_same_client_order_ids(self):
        r1 = _construct()
        r2 = _construct()
        ids1 = sorted(r.client_order_id for r in r1.requests)
        ids2 = sorted(r.client_order_id for r in r2.requests)
        assert ids1 == ids2

    def test_client_order_ids_unique_within_construction(self):
        result = _construct(plan=_plan(contracts=_iron_fly_legs()))
        ids = [r.client_order_id for r in result.requests]
        assert len(ids) == len(set(ids))

    def test_client_order_id_format(self):
        result = _construct()
        for r in result.requests:
            assert r.client_order_id.startswith("COID-")
            assert len(r.client_order_id) == len("COID-") + 16

    def test_different_contract_different_client_order_id(self):
        r1 = _construct(plan=_plan(contracts=(_contract(25150, "CE", "SELL"),)))
        r2 = _construct(plan=_plan(contracts=(_contract(25200, "CE", "SELL"),)))
        assert r1.requests[0].client_order_id != r2.requests[0].client_order_id


# ---------------------------------------------------------------------------
# Duplicate detection
# ---------------------------------------------------------------------------

class TestDuplicateDetection:
    def test_duplicate_client_order_id_triggers_failure(self, monkeypatch):
        # Force a collision by making every leg hash to the same seed.
        original = engine.hashlib.md5

        def _collide(data):
            return original(b"forced-collision")

        monkeypatch.setattr(engine.hashlib, "md5", _collide)
        result = _construct(plan=_plan(contracts=_iron_fly_legs()))
        assert result.status == "FAILED"
        assert result.failure_reason == "DUPLICATE_REQUEST"


# ---------------------------------------------------------------------------
# Failure handling
# ---------------------------------------------------------------------------

class TestEmptyPositionPlan:
    def test_empty_contracts_fails(self):
        result = _construct(plan=_plan(contracts=()))
        assert result.status == "FAILED"
        assert result.failure_reason == "EMPTY_POSITION_PLAN"

    def test_unpassed_validation_fails(self):
        result = _construct(plan=_plan(validation="FAILED"))
        assert result.failure_reason == "EMPTY_POSITION_PLAN"


class TestInvalidExecutionPolicy:
    def test_unrecognized_policy_fails(self):
        result = _construct(policy=_policy("NOT_A_REAL_POLICY"))
        assert result.failure_reason == "INVALID_EXECUTION_POLICY"


class TestInvalidProduct:
    def test_unrecognized_product_fails(self):
        result = _construct(cfg=_trading_config(product="NOT_A_REAL_PRODUCT"))
        assert result.failure_reason == "INVALID_PRODUCT"


class TestInvalidValidity:
    def test_unrecognized_validity_fails(self):
        result = _construct(cfg=_trading_config(validity="NOT_A_REAL_VALIDITY"))
        assert result.failure_reason == "INVALID_VALIDITY"


class TestZeroQuantity:
    def test_zero_quantity_fails(self):
        result = _construct(plan=_plan(quantity_per_leg=0))
        assert result.failure_reason == "ZERO_QUANTITY"

    def test_negative_quantity_fails(self):
        result = _construct(plan=_plan(quantity_per_leg=-75))
        assert result.failure_reason == "ZERO_QUANTITY"


class TestInsufficientData:
    def test_none_position_plan_fails(self):
        result = engine.construct_orders(None, _policy(), _trading_config(), clock=FIXED_CLOCK)
        assert result.failure_reason == "INSUFFICIENT_DATA"

    def test_none_execution_policy_fails(self):
        result = engine.construct_orders(_plan(), None, _trading_config(), clock=FIXED_CLOCK)
        assert result.failure_reason == "INSUFFICIENT_DATA"

    def test_none_trading_config_fails(self):
        result = engine.construct_orders(_plan(), _policy(), None, clock=FIXED_CLOCK)
        assert result.failure_reason == "INSUFFICIENT_DATA"


# ---------------------------------------------------------------------------
# Traceability
# ---------------------------------------------------------------------------

class TestTraceability:
    def test_creation_trace_mentions_leg_and_side_and_quantity(self):
        result = _construct(plan=_plan(contracts=(_contract(25150, "CE", "SELL"),), quantity_per_leg=150))
        trace = result.requests[0].creation_trace
        assert "25150CE" in trace
        assert "SELL" in trace
        assert "150" in trace

    def test_tags_carry_strategy_and_session(self):
        result = _construct()
        for r in result.requests:
            assert r.tags.strategy_id == "PREMIUM_VWAP_STRADDLE"
            assert r.tags.session_id == "SESSION-0001"


# ---------------------------------------------------------------------------
# Determinism / replay
# ---------------------------------------------------------------------------

class TestDeterminism:
    def test_same_inputs_same_construction_id(self):
        r1 = _construct()
        r2 = _construct()
        assert r1.construction_id == r2.construction_id
        assert r1 == r2

    def test_byte_identical_across_repeated_runs(self):
        results = [_construct(plan=_plan(contracts=_iron_fly_legs())) for _ in range(5)]
        assert all(r == results[0] for r in results)

    def test_different_inputs_different_construction_id(self):
        r1 = _construct()
        r2 = _construct(policy=_policy("LIMIT"))
        assert r1.construction_id != r2.construction_id

    def test_construction_id_uses_md5_prefix_not_uuid(self):
        result = _construct()
        assert result.construction_id.startswith("OCR-")
        assert len(result.construction_id) == len("OCR-") + 16

    def test_request_id_uses_md5_prefix_not_uuid(self):
        result = _construct()
        for r in result.requests:
            assert r.request_id.startswith("OR-")

    def test_real_clock_default_produces_wall_clock_timestamp(self):
        before = datetime.now()
        result = engine.construct_orders(_plan(), _policy(), _trading_config())
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
        result = _construct()
        with pytest.raises(Exception):
            result.status = "HACKED"

    def test_order_request_is_frozen(self):
        result = _construct()
        with pytest.raises(Exception):
            result.requests[0].quantity = 0

    def test_tags_are_frozen(self):
        result = _construct()
        with pytest.raises(Exception):
            result.requests[0].tags.strategy_id = "HACKED"

    def test_engine_does_not_mutate_inputs(self):
        p = _plan()
        p_copy = PositionPlan(**{f.name: getattr(p, f.name) for f in p.__dataclass_fields__.values()})
        _construct(plan=p)
        assert p == p_copy


# ---------------------------------------------------------------------------
# Serialization round trip
# ---------------------------------------------------------------------------

class TestSerialization:
    def test_round_trip_preserves_all_fields(self):
        result = _construct(plan=_plan(contracts=_iron_fly_legs()))
        d = serialization.result_to_dict(result)
        back = serialization.result_from_dict(d)
        assert back == result

    def test_round_trip_is_json_safe(self):
        import json

        result = _construct()
        d = serialization.result_to_dict(result)
        text = json.dumps(d)
        back = serialization.result_from_dict(json.loads(text))
        assert back == result

    def test_round_trip_with_failure(self):
        result = _construct(plan=_plan(contracts=()))
        d = serialization.result_to_dict(result)
        assert d["requests"] == []
        back = serialization.result_from_dict(d)
        assert back == result

    def test_execution_policy_round_trip(self):
        p = _policy("LIMIT", limit_price=25100.0)
        d = serialization.execution_policy_to_dict(p)
        assert serialization.execution_policy_from_dict(d) == p

    def test_trading_configuration_round_trip(self):
        c = _trading_config()
        d = serialization.trading_configuration_to_dict(c)
        assert serialization.trading_configuration_from_dict(d) == c


# ---------------------------------------------------------------------------
# Journal -- append only
# ---------------------------------------------------------------------------

class TestJournal:
    def test_record_and_read_all(self, tmp_path):
        j = OrderConstructionJournal(tmp_path / "oc.jsonl")
        result = _construct()
        j.record(result)
        assert j.read_all() == [result]

    def test_read_all_empty_when_file_missing(self, tmp_path):
        j = OrderConstructionJournal(tmp_path / "missing.jsonl")
        assert j.read_all() == []

    def test_record_many_appends_in_order(self, tmp_path):
        j = OrderConstructionJournal(tmp_path / "j.jsonl")
        r1 = _construct()
        r2 = _construct(plan=_plan(contracts=()))
        j.record_many([r1, r2])
        records = j.read_all()
        assert [r.construction_id for r in records] == [r1.construction_id, r2.construction_id]

    def test_journal_opens_only_in_append_or_read_mode(self):
        source = inspect.getsource(OrderConstructionJournal)
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
# runner.run_order_construction composition
# ---------------------------------------------------------------------------

class TestRunner:
    def test_run_without_journal(self):
        result = runner.run_order_construction(_plan(), _policy(), _trading_config(), clock=FIXED_CLOCK)
        assert isinstance(result, models.OrderConstructionResult)

    def test_run_journals_when_given_one(self, tmp_path):
        j = OrderConstructionJournal(tmp_path / "run.jsonl")
        result = runner.run_order_construction(_plan(), _policy(), _trading_config(), clock=FIXED_CLOCK, journal=j)
        assert j.read_all() == [result]


# ---------------------------------------------------------------------------
# Query API
# ---------------------------------------------------------------------------

class TestOrderConstructionIndex:
    def test_ingest_and_latest(self):
        idx = query.OrderConstructionIndex()
        r1 = _construct()
        idx.ingest(r1)
        assert idx.latest() == r1

    def test_find_by_status(self):
        idx = query.OrderConstructionIndex()
        r1 = _construct(plan=_plan(contracts=()))
        idx.ingest(r1)
        assert idx.find_by_status("FAILED") == [r1]

    def test_find_by_position_plan(self):
        idx = query.OrderConstructionIndex()
        r1 = _construct(plan=_plan(plan_id="PP-XYZ"))
        idx.ingest(r1)
        assert idx.find_by_position_plan("PP-XYZ") == [r1]

    def test_summary_counts_by_status(self):
        idx = query.OrderConstructionIndex()
        idx.ingest(_construct())
        idx.ingest(_construct(plan=_plan(contracts=())))
        summary = idx.summary()
        assert summary.get("CONSTRUCTED") == 1
        assert summary.get("FAILED") == 1


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

class TestConfig:
    def test_default_version_matches_package_version(self):
        cfg = config_module.OrderConstructionConfig()
        assert cfg.version == taxonomy.ORDER_CONSTRUCTION_VERSION

    def test_config_is_frozen(self):
        cfg = config_module.OrderConstructionConfig()
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

    def test_no_upstream_trading_brain_stage_import_beyond_position_sizing(self):
        for path in _module_source_files():
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    for forbidden in (
                        "market_state", "risk_brain", "evidence_interpreter",
                        "strategy_selector", "capital_brain", "ontology",
                        "execution_planner", "execution_engine",
                    ):
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

    def test_no_broker_or_runtime_execution_import(self):
        for path in _module_source_files():
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    lowered = node.module.lower()
                    for forbidden in ("broker", "fyers", "zerodha", "runtime_execution", "broker_adapter"):
                        assert forbidden not in lowered

    def test_no_auth_or_session_or_retry_fields(self):
        request_fields = {f.name for f in models.OrderRequest.__dataclass_fields__.values()}
        forbidden = {"access_token", "refresh_token", "auth_token", "retry_count", "broker_order_id", "fill_price"}
        assert not (request_fields & forbidden)

    def test_no_uuid4_used_anywhere_in_package(self):
        for path in _module_source_files():
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr == "uuid4":
                    raise AssertionError(f"uuid4 used in {path.name}")

    def test_no_authentication_or_execution_capability_in_source(self):
        for path in _module_source_files():
            source = path.read_text()
            for forbidden in ("place_order(", "def authenticate(", "def connect(", "def poll(", "def reconcile("):
                assert forbidden not in source
