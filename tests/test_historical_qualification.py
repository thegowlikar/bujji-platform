"""Tests — Engineering Series 58: historical qualification framework."""
import logging
from datetime import datetime, timedelta

import pytest

from bujji.production_runtime.circuit_breaker import RuntimeCircuitBreaker
from bujji.production_runtime.config import RUNTIME_MODE_SHADOW
from bujji.production_runtime.health import RuntimeHealthAggregator
from bujji.production_runtime.rate_limiter import RateLimiterConfig, RuntimeRateLimiter
from bujji.production_runtime.startup import startup
from bujji.qualification.historical_runner import HistoricalQualificationRunner
from bujji.qualification.recorder import (
    RUNTIME_OUTCOME_COMPLETED,
    RUNTIME_OUTCOME_FAILED,
    RUNTIME_OUTCOME_REJECTED_CIRCUIT,
    RUNTIME_OUTCOME_REJECTED_RATE_LIMIT,
    QualificationRecorder,
)
from bujji.qualification.replay_models import ReplayScenario
from bujji.qualification.report import build_report
from bujji.broker.paper import PaperBroker
from bujji.trading_brain.nifty_contract_builder import models as ncb_models

FIXED_CLOCK = lambda: datetime(2026, 1, 1, 9, 20, 0)


def _spot():
    return ncb_models.NiftySpotSnapshot(spot=25148.0, as_of="2026-01-01T09:20:00")


def _chain():
    entries = []
    for expiry in ("2026-07-31", "2026-08-07"):
        for strike, opt in [(25150, "CE"), (25150, "PE"), (25200, "CE"), (25250, "CE"), (25100, "PE"), (25050, "PE")]:
            entries.append(
                ncb_models.NiftyOptionChainEntry(
                    strike=strike, option_type=opt, expiry=expiry, contract_symbol=f"NSE:NIFTY{expiry}{strike}{opt}"
                )
            )
    return ncb_models.NiftyOptionChainSnapshot(
        expiries=("2026-07-31", "2026-08-07"), entries=tuple(entries), as_of="2026-01-01T09:20:00"
    )


def _sparse_chain():
    return ncb_models.NiftyOptionChainSnapshot(
        expiries=("2026-07-31",),
        entries=(ncb_models.NiftyOptionChainEntry(25150, "CE", "2026-07-31", "NSE:X"),),
        as_of="x",
    )


def _scenario(scenario_id, spot=None, chain=None):
    return ReplayScenario(
        scenario_id=scenario_id,
        description="test session",
        market_context="TRENDING_UP",
        market_opinion="BULLISH",
        context_stability="STABLE",
        calibration="CALIBRATED",
        governance="APPROVED",
        lifecycle="ACTIVE",
        contract="COMPLETE",
        spot_snapshot=spot if spot is not None else _spot(),
        option_chain=chain if chain is not None else _chain(),
    )


@pytest.fixture
def started():
    return startup({"mode": RUNTIME_MODE_SHADOW, "broker_name": "paper"}, clock=FIXED_CLOCK)


@pytest.fixture
def runner(started):
    root, sreport = started
    return HistoricalQualificationRunner(
        root=root,
        startup_report=sreport,
        health_aggregator=RuntimeHealthAggregator(),
        circuit_breaker=RuntimeCircuitBreaker(),
        rate_limiter=RuntimeRateLimiter(config=RateLimiterConfig(min_interval_seconds=1.0)),
        logger=logging.getLogger("test.qualification"),
    )


def test_single_replay_session(runner):
    recorder = runner.run_corpus([_scenario("RS-1")], [datetime(2026, 1, 1, 9, 20, 0)])
    assert len(recorder) == 1
    record = recorder.records[0]
    assert record.replay_identifier == "RS-1"
    assert record.runtime_outcome.status == RUNTIME_OUTCOME_COMPLETED


def test_multiple_replay_sessions(runner):
    scenarios = [_scenario(f"RS-{i}") for i in range(4)]
    timestamps = [datetime(2026, 1, 1, 9, 20, 0) + timedelta(minutes=5 * i) for i in range(4)]
    recorder = runner.run_corpus(scenarios, timestamps)
    assert len(recorder) == 4
    assert [r.replay_identifier for r in recorder.records] == ["RS-0", "RS-1", "RS-2", "RS-3"]


def test_empty_replay_corpus(runner):
    recorder = runner.run_corpus([], [])
    assert len(recorder) == 0
    report = build_report(recorder.records, clock=FIXED_CLOCK)
    assert report.total_replay_sessions == 0
    assert report.completed_runs == 0
    assert report.rejected_runs == 0


def test_scenarios_timestamps_length_mismatch_raises(runner):
    with pytest.raises(ValueError):
        runner.run_corpus([_scenario("RS-1")], [])


def test_deterministic_report_generation(runner):
    scenarios = [_scenario(f"RS-{i}") for i in range(3)]
    timestamps = [datetime(2026, 1, 1, 9, 20, 0) + timedelta(minutes=5 * i) for i in range(3)]
    recorder_a = runner.run_corpus(scenarios, timestamps)
    recorder_b = runner.run_corpus(scenarios, timestamps)
    report_a = build_report(recorder_a.records, clock=FIXED_CLOCK)
    report_b = build_report(recorder_b.records, clock=FIXED_CLOCK)
    assert report_a == report_b


def test_qualification_records_are_immutable(runner):
    recorder = runner.run_corpus([_scenario("RS-1")], [datetime(2026, 1, 1, 9, 20, 0)])
    record = recorder.records[0]
    with pytest.raises(Exception):
        record.replay_identifier = "RS-CHANGED"  # type: ignore[misc]


def test_qualification_report_is_immutable(runner):
    recorder = runner.run_corpus([_scenario("RS-1")], [datetime(2026, 1, 1, 9, 20, 0)])
    report = build_report(recorder.records, clock=FIXED_CLOCK)
    with pytest.raises(Exception):
        report.total_replay_sessions = 99  # type: ignore[misc]


def test_recorder_never_overwrites(runner):
    recorder = QualificationRecorder()
    runner.run_corpus([_scenario("RS-1")], [datetime(2026, 1, 1, 9, 20, 0)], recorder=recorder)
    first_snapshot = recorder.records
    runner.run_corpus([_scenario("RS-2")], [datetime(2026, 1, 1, 9, 25, 0)], recorder=recorder)
    assert recorder.records[: len(first_snapshot)] == first_snapshot
    assert len(recorder) == 2


def test_fingerprint_preserved_across_records(runner, started):
    root, _ = started
    scenarios = [_scenario(f"RS-{i}") for i in range(2)]
    timestamps = [datetime(2026, 1, 1, 9, 20, 0), datetime(2026, 1, 1, 9, 25, 0)]
    recorder = runner.run_corpus(scenarios, timestamps)
    for record in recorder.records:
        assert record.qualification_fingerprint == root.config.qualification_fingerprint


def test_health_snapshot_is_recorded(runner):
    recorder = runner.run_corpus([_scenario("RS-1")], [datetime(2026, 1, 1, 9, 20, 0)])
    record = recorder.records[0]
    assert record.health_snapshot is not None
    assert record.health_snapshot.overall == "HEALTHY"


def test_circuit_decision_is_recorded(runner):
    recorder = runner.run_corpus([_scenario("RS-1")], [datetime(2026, 1, 1, 9, 20, 0)])
    record = recorder.records[0]
    assert record.circuit_decision is not None
    assert record.circuit_decision.state == "CLOSED"


def test_rate_limit_decision_is_recorded(runner):
    recorder = runner.run_corpus([_scenario("RS-1")], [datetime(2026, 1, 1, 9, 20, 0)])
    record = recorder.records[0]
    assert record.rate_limit_decision is not None
    assert record.rate_limit_decision.state == "PERMITTED"


def test_runtime_rejection_is_recorded_when_rate_limited(runner):
    scenarios = [_scenario("RS-1"), _scenario("RS-2")]
    timestamps = [datetime(2026, 1, 1, 9, 20, 0), datetime(2026, 1, 1, 9, 20, 0, 500000)]
    recorder = runner.run_corpus(scenarios, timestamps)
    second = recorder.records[1]
    assert second.runtime_outcome.status == RUNTIME_OUTCOME_REJECTED_RATE_LIMIT
    assert second.strategy_decision is None


def test_failed_execution_is_recorded_not_fabricated_as_completed(runner):
    recorder = runner.run_corpus(
        [_scenario("RS-SPARSE", chain=_sparse_chain())], [datetime(2026, 1, 1, 9, 20, 0)]
    )
    record = recorder.records[0]
    assert record.runtime_outcome.status == RUNTIME_OUTCOME_FAILED


def test_shadow_only_guarantee_dispatch_target_is_paper_broker(runner, started):
    root, _ = started
    assert isinstance(root.broker, PaperBroker)
    runner.run_corpus([_scenario("RS-1")], [datetime(2026, 1, 1, 9, 20, 0)])
    assert isinstance(root.broker, PaperBroker)


def test_no_live_broker_authentication_or_dispatch_calls(runner, started, monkeypatch):
    root, _ = started

    def _forbidden(*args, **kwargs):
        raise AssertionError("historical qualification must never call broker/auth/dispatch methods")

    monkeypatch.setattr(root.broker, "_check_auth", _forbidden, raising=False)

    recorder = runner.run_corpus([_scenario("RS-1")], [datetime(2026, 1, 1, 9, 20, 0)])
    assert len(recorder) == 1


def test_no_runtime_mutation_between_sessions(runner, started):
    root, _ = started
    config_before = root.config
    runner.run_corpus([_scenario("RS-1")], [datetime(2026, 1, 1, 9, 20, 0)])
    assert root.config == config_before


def test_scenarios_are_never_mutated(runner):
    scenario = _scenario("RS-1")
    original_repr = repr(scenario)
    runner.run_corpus([scenario], [datetime(2026, 1, 1, 9, 20, 0)])
    assert repr(scenario) == original_repr


def test_report_never_computes_profitability_fields(runner):
    recorder = runner.run_corpus([_scenario("RS-1")], [datetime(2026, 1, 1, 9, 20, 0)])
    report = build_report(recorder.records, clock=FIXED_CLOCK)
    report_fields = report.__dataclass_fields__.keys()
    forbidden_terms = ("pnl", "profit", "loss", "return", "sharpe")
    assert not any(any(term in field.lower() for term in forbidden_terms) for field in report_fields)


def test_report_traceable_to_replay_identifiers_and_fingerprints(runner):
    scenarios = [_scenario(f"RS-{i}") for i in range(2)]
    timestamps = [datetime(2026, 1, 1, 9, 20, 0), datetime(2026, 1, 1, 9, 25, 0)]
    recorder = runner.run_corpus(scenarios, timestamps)
    report = build_report(recorder.records, clock=FIXED_CLOCK)
    assert report.replay_identifiers == ("RS-0", "RS-1")
    assert len(report.qualification_fingerprints) == 2
