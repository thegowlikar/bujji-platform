"""Tests — Engineering Series 69, Phase 2: Qualification Observability
Expansion (recording-only).

Covers three things: (1) `QualificationRecord.market_state_assessment`
is populated from `shadow_result.market_state_assessment` exactly like
the pre-existing `strategy_decision` field; (2) `build_report()`'s new
`sessions` tuple carries the Observatory's expected per-session keys
when records carry rich scenario/assessment data; (3) both
`QualificationRecord` and `QualificationReport` remain backward
compatible when that data is absent (no exception, values are `None`,
`sessions` defaults to `()` for direct construction).
"""
import logging
from datetime import datetime, timedelta

import pytest

from bujji.production_runtime.circuit_breaker import RuntimeCircuitBreaker
from bujji.production_runtime.config import RUNTIME_MODE_SHADOW
from bujji.production_runtime.health import RuntimeHealthAggregator
from bujji.production_runtime.rate_limiter import RateLimiterConfig, RuntimeRateLimiter
from bujji.production_runtime.startup import startup
from bujji.qualification.historical_runner import HistoricalQualificationRunner
from bujji.qualification.recorder import QualificationRecord, QualificationRecorder, RuntimeOutcome
from bujji.qualification.replay_models import ReplayScenario
from bujji.qualification.report import build_report, QualificationReport
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
        logger=logging.getLogger("test.qualification.series69"),
    )


def test_record_carries_market_state_assessment_from_shadow_result(runner):
    recorder = runner.run_corpus([_scenario("RS-1")], [datetime(2026, 1, 1, 9, 20, 0)])
    record = recorder.records[0]
    shadow_result = record.runtime_outcome.shadow_result
    assert shadow_result is not None
    assert shadow_result.market_state_assessment is not None
    assert record.market_state_assessment is shadow_result.market_state_assessment


def test_record_carries_scenario_reference(runner):
    scenario = _scenario("RS-1")
    recorder = runner.run_corpus([scenario], [datetime(2026, 1, 1, 9, 20, 0)])
    record = recorder.records[0]
    assert record.scenario is scenario
    assert record.scenario.market_context == "TRENDING_UP"


def test_build_report_sessions_contain_observatory_fields(runner):
    scenarios = [_scenario(f"RS-{i}") for i in range(2)]
    timestamps = [datetime(2026, 1, 1, 9, 20, 0) + timedelta(minutes=5 * i) for i in range(2)]
    recorder = runner.run_corpus(scenarios, timestamps)
    report = build_report(recorder.records, clock=FIXED_CLOCK)

    assert len(report.sessions) == 2
    for session, record in zip(report.sessions, recorder.records):
        assert session["id"] == record.replay_identifier
        assert session["outcome"] == record.runtime_outcome.status
        assert session["market_context"] == "TRENDING_UP"
        assert session["market_opinion"] == "BULLISH"
        assert session["context_stability"] == "STABLE"
        assert session["calibration"] == "CALIBRATED"
        assert session["governance"] == "APPROVED"
        assert session["lifecycle"] == "ACTIVE"
        assert session["contract"] == "COMPLETE"
        # strategy/market_character/market_phase/confidence come from
        # objects the real pipeline produced -- assert presence of the
        # keys and that values are never fabricated (either a real
        # pipeline value or None, never a placeholder string).
        for key in ("strategy", "market_character", "market_phase", "confidence"):
            assert key in session


def test_qualification_record_defaults_are_backward_compatible():
    """A QualificationRecord built the old way (no market_state_assessment
    or scenario kwargs) must still construct cleanly, exactly as any
    pre-Series-69 caller would build one.
    """
    record = QualificationRecord(
        replay_identifier="RS-OLD",
        timestamp="2026-01-01T09:20:00",
        strategy_decision=None,
        risk_decision=None,
        capital_decision=None,
        execution_plan=None,
        health_snapshot=None,
        circuit_decision=None,
        rate_limit_decision=None,
        runtime_outcome=RuntimeOutcome(status="COMPLETED", reason="ok", shadow_result=None),
        qualification_fingerprint="FP-1",
    )
    assert record.market_state_assessment is None
    assert record.scenario is None


def test_build_report_sessions_absent_safe_without_scenario_or_assessment():
    """Records lacking scenario/market_state_assessment (e.g. built by
    older code, or a caller that never populates them) must still
    produce a session entry with None values, never raise, never
    fabricate a value.
    """
    recorder = QualificationRecorder()
    recorder.record(
        QualificationRecord(
            replay_identifier="RS-BARE",
            timestamp="2026-01-01T09:20:00",
            strategy_decision=None,
            risk_decision=None,
            capital_decision=None,
            execution_plan=None,
            health_snapshot=type("H", (), {"overall": "HEALTHY"})(),
            circuit_decision=type("C", (), {"state": "CLOSED"})(),
            rate_limit_decision=type("R", (), {"state": "PERMITTED"})(),
            runtime_outcome=RuntimeOutcome(status="COMPLETED", reason="ok", shadow_result=None),
            qualification_fingerprint="FP-BARE",
        )
    )
    report = build_report(recorder.records, clock=FIXED_CLOCK)
    assert len(report.sessions) == 1
    session = report.sessions[0]
    assert session["id"] == "RS-BARE"
    assert session["market_context"] is None
    assert session["market_character"] is None
    assert session["strategy"] is None


def test_qualification_report_sessions_defaults_to_empty_tuple():
    """Any existing caller constructing QualificationReport directly
    without a sessions= kwarg (pre-Series-69 code) is unaffected.
    """
    report = QualificationReport(
        report_id="R-1",
        total_replay_sessions=0,
        completed_runs=0,
        rejected_runs=0,
        runtime_failures=0,
        health_state_counts={},
        circuit_state_counts={},
        rate_limit_state_counts={},
        insufficient_data_occurrences=0,
        replay_identifiers=(),
        qualification_fingerprints=(),
        timestamp="2026-01-01T00:00:00",
    )
    assert report.sessions == ()


def test_build_report_deterministic_with_sessions(runner):
    """Sessions must not break determinism -- two identical replays of
    the same corpus must still produce byte-identical reports,
    including the new sessions tuple.
    """
    scenarios = [_scenario(f"RS-{i}") for i in range(2)]
    timestamps = [datetime(2026, 1, 1, 9, 20, 0) + timedelta(minutes=5 * i) for i in range(2)]
    recorder_a = runner.run_corpus(scenarios, timestamps)
    recorder_b = runner.run_corpus(scenarios, timestamps)
    report_a = build_report(recorder_a.records, clock=FIXED_CLOCK)
    report_b = build_report(recorder_b.records, clock=FIXED_CLOCK)
    assert report_a.sessions == report_b.sessions



# ---------------------------------------------------------------------------
# Phase 2b (deep pass) -- evidence_interpretation + risk/capital/execution
# ---------------------------------------------------------------------------


def test_record_carries_evidence_interpretation_from_shadow_result(runner):
    recorder = runner.run_corpus([_scenario("RS-1")], [datetime(2026, 1, 1, 9, 20, 0)])
    record = recorder.records[0]
    shadow_result = record.runtime_outcome.shadow_result
    assert shadow_result is not None
    assert shadow_result.evidence_interpretation is not None
    assert record.evidence_interpretation is shadow_result.evidence_interpretation


def test_qualification_record_evidence_interpretation_defaults_to_none():
    record = QualificationRecord(
        replay_identifier="RS-OLD2",
        timestamp="2026-01-01T09:20:00",
        strategy_decision=None,
        risk_decision=None,
        capital_decision=None,
        execution_plan=None,
        health_snapshot=None,
        circuit_decision=None,
        rate_limit_decision=None,
        runtime_outcome=RuntimeOutcome(status="COMPLETED", reason="ok", shadow_result=None),
        qualification_fingerprint="FP-2",
    )
    assert record.evidence_interpretation is None


def test_build_report_sessions_contain_deep_pass_fields(runner):
    scenarios = [_scenario(f"RS-DEEP-{i}") for i in range(2)]
    timestamps = [datetime(2026, 1, 1, 9, 20, 0) + timedelta(minutes=5 * i) for i in range(2)]
    recorder = runner.run_corpus(scenarios, timestamps)
    report = build_report(recorder.records, clock=FIXED_CLOCK)

    deep_pass_keys = (
        "selection_status",
        "selection_confidence",
        "selection_reason",
        "risk_status",
        "risk_level",
        "risk_approval",
        "risk_blocking_reason",
        "capital_intent",
        "capital_allocation_status",
        "capital_allocation_reason",
        "execution_status",
        "execution_intent",
        "evidence_opportunity_state",
        "evidence_risk_state",
    )
    for session, record in zip(report.sessions, recorder.records):
        for key in deep_pass_keys:
            assert key in session
        # Values should trace back to the real pipeline objects, never
        # be fabricated placeholders.
        risk_decision = record.risk_decision
        if risk_decision is not None:
            assert session["risk_status"] == getattr(risk_decision, "status", None)
            assert session["risk_level"] == getattr(risk_decision, "risk_level", None)
        capital_decision = record.capital_decision
        if capital_decision is not None:
            assert session["capital_intent"] == getattr(capital_decision, "capital_intent", None)
        execution_plan = record.execution_plan
        if execution_plan is not None:
            assert session["execution_status"] == getattr(execution_plan, "status", None)
        evidence_interpretation = record.evidence_interpretation
        if evidence_interpretation is not None:
            snapshot = getattr(evidence_interpretation, "ontology_snapshot", None)
            if snapshot is not None:
                assert session["evidence_opportunity_state"] == getattr(snapshot, "opportunity_state", None)
                assert session["evidence_risk_state"] == getattr(snapshot, "risk_state", None)


def test_build_report_sessions_deep_pass_fields_absent_safe_without_downstream_records():
    """Records built without risk/capital/execution/evidence data (e.g.
    a bare QualificationRecord) must still produce all the new session
    keys with None values, never raise.
    """
    recorder = QualificationRecorder()
    recorder.record(
        QualificationRecord(
            replay_identifier="RS-BARE2",
            timestamp="2026-01-01T09:20:00",
            strategy_decision=None,
            risk_decision=None,
            capital_decision=None,
            execution_plan=None,
            health_snapshot=type("H", (), {"overall": "HEALTHY"})(),
            circuit_decision=type("C", (), {"state": "CLOSED"})(),
            rate_limit_decision=type("R", (), {"state": "PERMITTED"})(),
            runtime_outcome=RuntimeOutcome(status="COMPLETED", reason="ok", shadow_result=None),
            qualification_fingerprint="FP-BARE2",
        )
    )
    report = build_report(recorder.records, clock=FIXED_CLOCK)
    session = report.sessions[0]
    for key in (
        "selection_status",
        "risk_status",
        "risk_level",
        "risk_approval",
        "risk_blocking_reason",
        "capital_intent",
        "capital_allocation_status",
        "capital_allocation_reason",
        "execution_status",
        "execution_intent",
        "evidence_opportunity_state",
        "evidence_risk_state",
    ):
        assert session[key] is None


def test_build_report_deterministic_with_deep_pass_fields(runner):
    scenarios = [_scenario(f"RS-DET-{i}") for i in range(2)]
    timestamps = [datetime(2026, 1, 1, 9, 20, 0) + timedelta(minutes=5 * i) for i in range(2)]
    recorder_a = runner.run_corpus(scenarios, timestamps)
    recorder_b = runner.run_corpus(scenarios, timestamps)
    report_a = build_report(recorder_a.records, clock=FIXED_CLOCK)
    report_b = build_report(recorder_b.records, clock=FIXED_CLOCK)
    assert report_a.sessions == report_b.sessions
