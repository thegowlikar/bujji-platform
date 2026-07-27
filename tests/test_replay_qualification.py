"""Tests for the End-to-End Replay Qualification — Engineering Series
46, Sprint 1.
"""
from __future__ import annotations

import ast
import inspect
from datetime import datetime
from pathlib import Path

import pytest

from bujji.trading_brain.nifty_contract_builder.models import (
    NiftyOptionChainEntry,
    NiftyOptionChainSnapshot,
    NiftySpotSnapshot,
)
from bujji.trading_brain.order_construction.models import ExecutionPolicy, TradingConfiguration
from bujji.trading_brain.position_sizing.config import PositionSizingConfig
from bujji.trading_brain.position_sizing.models import CapitalPolicy, LotSpecification
from bujji.qualification import replay_models, replay_report, replay_runner, replay_statistics
from bujji.journal.replay_qualification_journal import ReplayQualificationJournal

FIXED_CLOCK = lambda: datetime(2026, 1, 1, 12, 0, 0)

CURRENT_WEEK = "2026-07-31"


def _chain():
    entries = (
        NiftyOptionChainEntry(25150, "CE", CURRENT_WEEK, "NSE:NIFTY25150CE"),
        NiftyOptionChainEntry(25150, "PE", CURRENT_WEEK, "NSE:NIFTY25150PE"),
        NiftyOptionChainEntry(25200, "CE", CURRENT_WEEK, "NSE:NIFTY25200CE"),
        NiftyOptionChainEntry(25100, "PE", CURRENT_WEEK, "NSE:NIFTY25100PE"),
    )
    return NiftyOptionChainSnapshot(expiries=(CURRENT_WEEK,), entries=entries, as_of="2026-01-01T09:00:00")


def _happy_scenario(**overrides):
    base = dict(
        scenario_id="SCN-HAPPY",
        description="Clean bullish, stable, calibrated, approved market.",
        market_context="TRENDING_UP",
        market_opinion="BULLISH",
        context_stability="STABLE",
        calibration="CALIBRATED",
        governance="APPROVED",
        lifecycle="ACTIVE",
        contract="COMPLETE",
        spot_snapshot=NiftySpotSnapshot(spot=25148.0, as_of="2026-01-01T09:00:00"),
        option_chain=_chain(),
        capital_policy=CapitalPolicy(policy="STRICT"),
        lot_spec=LotSpecification(underlying="NIFTY", lot_size=75, effective_date="2026-01-01"),
        sizing_config=PositionSizingConfig(),
        execution_policy=ExecutionPolicy(policy="MARKET"),
        trading_config=TradingConfiguration(
            product="MIS", validity="DAY", session_id="SESSION-0001",
            pipeline_version="1.0.0", qualification_fingerprint="FP-abc123",
        ),
    )
    base.update(overrides)
    return replay_models.ReplayScenario(**base)


# ---------------------------------------------------------------------------
# Complete successful replay
# ---------------------------------------------------------------------------

class TestSuccessfulReplay:
    def test_happy_scenario_completes_all_stages(self):
        result = replay_runner.run_replay(_happy_scenario(), clock=FIXED_CLOCK, replay_count=1)
        assert result.completed_stages == replay_models.ALL_STAGE_NAMES
        assert result.failed_stage is None

    def test_happy_scenario_chain_is_valid(self):
        result = replay_runner.run_replay(_happy_scenario(), clock=FIXED_CLOCK, replay_count=1)
        assert result.chain_valid is True
        assert result.warnings == ()

    def test_happy_scenario_produces_contracts_and_orders(self):
        result = replay_runner.run_replay(_happy_scenario(), clock=FIXED_CLOCK, replay_count=1)
        assert result.artifact_counts["contracts"] >= 1
        assert result.artifact_counts["order_requests"] >= 1
        assert result.artifact_counts["order_requests"] == result.artifact_counts["contracts"]

    def test_runtime_execution_reaches_dispatched(self):
        artifacts, completed, _fs, _t = replay_runner._run_stages(
            _happy_scenario(), FIXED_CLOCK, replay_runner.PaperExecutorStub()
        )
        assert artifacts["runtime_execution"].execution_state == "DISPATCHED"


# ---------------------------------------------------------------------------
# Replay determinism / byte identity
# ---------------------------------------------------------------------------

class TestReplayDeterminism:
    def test_deterministic_under_fixed_clock(self):
        result = replay_runner.run_replay(_happy_scenario(), clock=FIXED_CLOCK, replay_count=5)
        assert result.deterministic is True

    def test_real_clock_default_is_honestly_non_deterministic(self):
        result = replay_runner.run_replay(_happy_scenario(), replay_count=3)
        assert result.deterministic is False
        assert "NON_DETERMINISTIC_REPLAY" in result.warnings

    def test_same_fingerprint_across_separate_calls(self):
        r1 = replay_runner.run_replay(_happy_scenario(), clock=FIXED_CLOCK, replay_count=1)
        r2 = replay_runner.run_replay(_happy_scenario(), clock=FIXED_CLOCK, replay_count=1)
        assert r1.fingerprint == r2.fingerprint

    def test_fingerprint_uses_md5_prefix_not_uuid(self):
        result = replay_runner.run_replay(_happy_scenario(), clock=FIXED_CLOCK, replay_count=1)
        assert result.fingerprint.startswith("RFP-")
        assert len(result.fingerprint) == len("RFP-") + 16


class TestRepeatedReplayByteIdentity:
    def test_byte_identical_run_results_across_separate_invocations(self):
        r1 = replay_runner.run_replay(_happy_scenario(), clock=FIXED_CLOCK, replay_count=1)
        r2 = replay_runner.run_replay(_happy_scenario(), clock=FIXED_CLOCK, replay_count=1)
        d1 = dict(r1.__dict__)
        d2 = dict(r2.__dict__)
        # elapsed_seconds is diagnostic timing, deliberately excluded
        # from the byte-identity comparison per this package's own
        # documented invariant scope.
        d1.pop("elapsed_seconds")
        d2.pop("elapsed_seconds")
        d1.pop("stage_timings")
        d2.pop("stage_timings")
        assert d1 == d2


# ---------------------------------------------------------------------------
# Negative scenarios
# ---------------------------------------------------------------------------

class TestNegativeScenariosFullReplay:
    def test_insufficient_market_data_fails_at_contract_builder(self):
        scenario = _happy_scenario(
            market_context=None, market_opinion=None, context_stability=None,
            calibration=None, governance=None, lifecycle=None, contract=None,
        )
        result = replay_runner.run_replay(scenario, clock=FIXED_CLOCK, replay_count=1)
        assert result.failed_stage == "nifty_contract_builder"
        assert result.chain_valid is False

    def test_insufficient_market_data_fails_atomically(self):
        scenario = _happy_scenario(market_context=None, market_opinion=None)
        result = replay_runner.run_replay(scenario, clock=FIXED_CLOCK, replay_count=1)
        remaining = set(replay_models.ALL_STAGE_NAMES) - set(result.completed_stages)
        assert "position_sizing" in remaining
        assert "order_construction" in remaining
        assert "runtime_execution" in remaining

    def test_missing_option_chain_fails_at_contract_builder(self):
        scenario = _happy_scenario(option_chain=None)
        result = replay_runner.run_replay(scenario, clock=FIXED_CLOCK, replay_count=1)
        assert result.failed_stage == "nifty_contract_builder"

    def test_invalid_capital_policy_fails_at_position_sizing(self):
        scenario = _happy_scenario(capital_policy=CapitalPolicy(policy="NOT_A_REAL_POLICY"))
        result = replay_runner.run_replay(scenario, clock=FIXED_CLOCK, replay_count=1)
        assert result.failed_stage == "position_sizing"
        assert "nifty_contract_builder" in result.completed_stages
        assert "order_construction" not in result.completed_stages


class TestNegativeScenariosDirectCall:
    def test_unknown_strategy_at_contract_builder(self):
        from bujji.trading_brain.nifty_contract_builder import engine as ncb_engine
        from bujji.trading_brain.strategy_selector.models import StrategyDecision
        from bujji.trading_brain.capital_brain.models import CapitalDecision

        strategy = StrategyDecision(
            decision_id="SD-X", selected_strategy="LONG_STRADDLE", selection_status="SELECTED",
            selection_confidence="HIGH", selection_reason="x", supporting_conditions=(),
            rejecting_conditions=(), alternative_candidates=(), all_evaluations=(),
            decision_trace="x", market_state_assessment_id="MSA-X",
            timestamp="2026-01-01T09:00:00", version="1.0.0",
        )
        capital = CapitalDecision(
            decision_id="CD-X", capital_intent="STANDARD", allocation_status="APPROVED",
            allocation_reason="x", allocation_constraints=("NONE",), required_controls=("NONE",),
            confidence="HIGH", decision_trace="x", risk_assessment_id="RA-X",
            timestamp="2026-01-01T09:00:00", version="1.0.0",
        )
        result = ncb_engine.build_contracts(
            strategy, capital, NiftySpotSnapshot(25148.0, "x"), _chain(), clock=FIXED_CLOCK
        )
        assert result.status == "FAILED"
        # Engineering Series 63: LONG_STRADDLE is registered by the
        # Strategy Selector but has no v1 contract template, so it now
        # gets its own distinct failure reason (STRATEGY_OUT_OF_V1_SCOPE),
        # separate from UNKNOWN_STRATEGY (a strategy the registry has
        # never heard of at all).
        assert result.failure_reason == "STRATEGY_OUT_OF_V1_SCOPE"
        assert result.contracts == ()

    def test_empty_position_plan_at_order_construction(self):
        from bujji.trading_brain.order_construction import engine as oc_engine
        from bujji.trading_brain.position_sizing.models import PositionPlan

        plan = PositionPlan(
            plan_id="PP-X", contracts=(), lots_per_leg=0, quantity_per_leg=0,
            capital_intent="NONE", sizing_policy="NONE", validation="FAILED",
            sizing_reason="x", sizing_trace="x", failure_reason="ZERO_QUANTITY",
            capital_decision_id="CD-X", timestamp="2026-01-01T09:00:00", version="1.0.0",
        )
        result = oc_engine.construct_orders(
            plan, ExecutionPolicy(policy="MARKET"),
            TradingConfiguration(product="MIS", validity="DAY", session_id="S", pipeline_version="1.0.0", qualification_fingerprint="FP"),
            clock=FIXED_CLOCK,
        )
        assert result.status == "FAILED"
        assert result.failure_reason == "EMPTY_POSITION_PLAN"
        assert result.requests == ()

    def test_duplicate_client_order_ids_at_runtime_execution(self):
        from bujji.trading_brain.nifty_contract_builder.models import NiftyOptionContract
        from bujji.trading_brain.order_construction.models import OrderRequest, OrderTags
        from bujji.runtime_execution import engine as re_engine

        contract = NiftyOptionContract(
            contract_id="NC-X", underlying="NIFTY", expiry=CURRENT_WEEK, strike=25150,
            option_type="CE", side="SELL", contract_symbol="NSE:X", capital_intent="STANDARD",
            strategy_id="PREMIUM_VWAP_STRADDLE", selection_reason="x", construction_trace="x",
            timestamp="2026-01-01T09:00:00", version="1.0.0",
        )
        tags = OrderTags(strategy_id="X", session_id="S", pipeline_version="1.0.0", qualification_fingerprint="FP")
        o1 = OrderRequest(
            request_id="OR-1", contract=contract, side="SELL", quantity=75, order_type="MARKET",
            product="MIS", validity="DAY", execution_policy="MARKET", client_order_id="COID-SAME",
            tags=tags, creation_trace="x", timestamp="2026-01-01T09:00:00", version="1.0.0",
        )
        o2 = OrderRequest(
            request_id="OR-2", contract=contract, side="SELL", quantity=75, order_type="MARKET",
            product="MIS", validity="DAY", execution_policy="MARKET", client_order_id="COID-SAME",
            tags=tags, creation_trace="x", timestamp="2026-01-01T09:00:00", version="1.0.0",
        )
        session = re_engine.build_session((o1, o2), clock=FIXED_CLOCK)
        assert session.execution_state == "FAILED_VALIDATION"
        assert session.failure_reason == "DUPLICATE_CLIENT_ORDER_ID"
        assert session.dispatch_plan == ()


class TestDispatchFailureNegative:
    def test_dispatch_should_fail_yields_aborted_runtime_execution(self):
        scenario = _happy_scenario(dispatch_should_fail=True)
        artifacts, completed, _fs, _t = replay_runner._run_stages(
            scenario, FIXED_CLOCK, replay_runner.PaperExecutorStub(should_raise=True)
        )
        assert artifacts["runtime_execution"].execution_state == "ABORTED"


# ---------------------------------------------------------------------------
# Artifact integrity / chain consistency
# ---------------------------------------------------------------------------

class TestArtifactIntegrity:
    def test_chain_consistency_detects_tampering(self):
        artifacts, completed, _fs, _t = replay_runner._run_stages(
            _happy_scenario(), FIXED_CLOCK, replay_runner.PaperExecutorStub()
        )
        from bujji.trading_brain.market_state.models import MarketStateAssessment

        tampered = dict(artifacts)
        original_msb = tampered["market_state_builder"]
        tampered["market_state_builder"] = MarketStateAssessment(
            **{**original_msb.__dict__, "interpretation_id": "EI-TAMPERED"}
        )
        violations = replay_runner._check_chain_consistency(tampered)
        assert any("evidence_interpreter" in v for v in violations)

    def test_no_violations_on_untampered_chain(self):
        artifacts, completed, _fs, _t = replay_runner._run_stages(
            _happy_scenario(), FIXED_CLOCK, replay_runner.PaperExecutorStub()
        )
        violations = replay_runner._check_chain_consistency(artifacts)
        assert violations == []


# ---------------------------------------------------------------------------
# Journal integrity
# ---------------------------------------------------------------------------

class TestJournalIntegrity:
    def test_record_and_read_all_runs(self, tmp_path):
        j = ReplayQualificationJournal(tmp_path / "rq.jsonl")
        result = replay_runner.run_replay(_happy_scenario(), clock=FIXED_CLOCK, replay_count=1)
        j.record_run(result)
        assert j.read_all_runs() == [result]

    def test_record_and_read_all_reports(self, tmp_path):
        j = ReplayQualificationJournal(tmp_path / "rq2.jsonl")
        result = replay_runner.run_replay(_happy_scenario(), clock=FIXED_CLOCK, replay_count=1)
        report = replay_report.build_qualification_report([result], clock=FIXED_CLOCK)
        j.record_report(report)
        assert j.read_all_reports() == [report]

    def test_read_all_empty_when_file_missing(self, tmp_path):
        j = ReplayQualificationJournal(tmp_path / "missing.jsonl")
        assert j.read_all_runs() == []
        assert j.read_all_reports() == []

    def test_journal_opens_only_in_append_or_read_mode(self):
        source = inspect.getsource(ReplayQualificationJournal)
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

    def test_mixed_runs_and_reports_are_distinguished(self, tmp_path):
        j = ReplayQualificationJournal(tmp_path / "mixed.jsonl")
        result = replay_runner.run_replay(_happy_scenario(), clock=FIXED_CLOCK, replay_count=1)
        report = replay_report.build_qualification_report([result], clock=FIXED_CLOCK)
        j.record_run(result)
        j.record_report(report)
        assert j.read_all_runs() == [result]
        assert j.read_all_reports() == [report]


# ---------------------------------------------------------------------------
# Immutable models
# ---------------------------------------------------------------------------

class TestImmutableModels:
    def test_run_result_is_frozen(self):
        result = replay_runner.run_replay(_happy_scenario(), clock=FIXED_CLOCK, replay_count=1)
        with pytest.raises(Exception):
            result.deterministic = False

    def test_scenario_is_frozen(self):
        scenario = _happy_scenario()
        with pytest.raises(Exception):
            scenario.market_context = "HACKED"

    def test_report_is_frozen(self):
        result = replay_runner.run_replay(_happy_scenario(), clock=FIXED_CLOCK, replay_count=1)
        report = replay_report.build_qualification_report([result], clock=FIXED_CLOCK)
        with pytest.raises(Exception):
            report.total_scenarios = 99

    def test_run_replay_does_not_mutate_scenario(self):
        scenario = _happy_scenario()
        scenario_copy = replay_models.ReplayScenario(**{f.name: getattr(scenario, f.name) for f in scenario.__dataclass_fields__.values()})
        replay_runner.run_replay(scenario, clock=FIXED_CLOCK, replay_count=1)
        assert scenario == scenario_copy


# ---------------------------------------------------------------------------
# Serialization round trip
# ---------------------------------------------------------------------------

class TestSerialization:
    def test_run_result_round_trip(self):
        result = replay_runner.run_replay(_happy_scenario(), clock=FIXED_CLOCK, replay_count=1)
        d = replay_models.run_result_to_dict(result)
        back = replay_models.run_result_from_dict(d)
        assert back == result

    def test_run_result_round_trip_is_json_safe(self):
        import json

        result = replay_runner.run_replay(_happy_scenario(), clock=FIXED_CLOCK, replay_count=1)
        d = replay_models.run_result_to_dict(result)
        text = json.dumps(d)
        back = replay_models.run_result_from_dict(json.loads(text))
        assert back == result

    def test_report_round_trip(self):
        result = replay_runner.run_replay(_happy_scenario(), clock=FIXED_CLOCK, replay_count=1)
        report = replay_report.build_qualification_report([result], clock=FIXED_CLOCK)
        d = replay_models.qualification_report_to_dict(report)
        back = replay_models.qualification_report_from_dict(d)
        assert back == report


# ---------------------------------------------------------------------------
# Statistics / invariants / report
# ---------------------------------------------------------------------------

class TestStatisticsAndInvariants:
    def test_invariants_all_true_for_healthy_run(self):
        result = replay_runner.run_replay(_happy_scenario(), clock=FIXED_CLOCK, replay_count=3)
        invariants = replay_statistics.compute_invariants([result])
        assert all(invariants.values())

    def test_invariants_empty_results_trivially_true(self):
        invariants = replay_statistics.compute_invariants([])
        assert all(invariants.values())

    def test_summary_counts(self):
        good = replay_runner.run_replay(_happy_scenario(), clock=FIXED_CLOCK, replay_count=1)
        bad = replay_runner.run_replay(_happy_scenario(option_chain=None), clock=FIXED_CLOCK, replay_count=1)
        counts = replay_statistics.compute_summary_counts([good, bad])
        assert counts["total"] == 2
        assert counts["passed"] == 1
        assert counts["failed"] == 1

    def test_report_reflects_summary(self):
        good = replay_runner.run_replay(_happy_scenario(), clock=FIXED_CLOCK, replay_count=1)
        bad = replay_runner.run_replay(_happy_scenario(option_chain=None), clock=FIXED_CLOCK, replay_count=1)
        report = replay_report.build_qualification_report([good, bad], clock=FIXED_CLOCK)
        assert report.total_scenarios == 2
        assert report.passed_scenarios == 1
        assert report.failed_scenarios == 1


# ---------------------------------------------------------------------------
# Performance (informational only)
# ---------------------------------------------------------------------------

class TestPerformanceInformationalOnly:
    def test_elapsed_seconds_is_non_negative(self):
        result = replay_runner.run_replay(_happy_scenario(), clock=FIXED_CLOCK, replay_count=1)
        assert result.elapsed_seconds >= 0

    def test_stage_timings_cover_completed_stages(self):
        result = replay_runner.run_replay(_happy_scenario(), clock=FIXED_CLOCK, replay_count=1)
        for stage in result.completed_stages:
            assert stage in result.stage_timings


# ---------------------------------------------------------------------------
# Isolation firewall
# ---------------------------------------------------------------------------

def _module_source_files():
    base = Path(replay_runner.__file__).parent
    return list(base.glob("*.py"))


class TestIsolation:
    def test_no_broker_sdk_or_network_import(self):
        for path in _module_source_files():
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                for name in names:
                    for forbidden in ("fyers", "zerodha", "requests", "websocket", "bujji.broker"):
                        assert forbidden not in name.lower()

    def test_no_production_broker_module_import(self):
        for path in _module_source_files():
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    assert "broker.paper" not in node.module
                    assert "broker.fyers" not in node.module

    def test_no_authentication_or_retry_or_circuit_breaker_capability(self):
        for path in _module_source_files():
            source = path.read_text()
            for forbidden in (
                "def authenticate(", "def connect(", "def refresh_token(",
                "def retry(", "def circuit_breaker(", "def rate_limit(",
            ):
                assert forbidden not in source

    def test_no_uuid4_used_anywhere_in_package(self):
        for path in _module_source_files():
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr == "uuid4":
                    raise AssertionError(f"uuid4 used in {path.name}")

    def test_no_random_module_used(self):
        for path in _module_source_files():
            source = path.read_text()
            assert "import random" not in source
