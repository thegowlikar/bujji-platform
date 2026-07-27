"""Tests — Engineering Series 56: runtime circuit breaker."""
from datetime import datetime

import pytest

from bujji.production_runtime.circuit_breaker import (
    CIRCUIT_CLOSED,
    CIRCUIT_HALF_OPEN,
    CIRCUIT_INSUFFICIENT_DATA,
    CIRCUIT_OPEN,
    CircuitDecision,
    RuntimeCircuitBreaker,
    authorize_new_work,
    guarded_run_read_only,
    guarded_run_shadow,
)
from bujji.production_runtime.config import RUNTIME_MODE_SHADOW
from bujji.production_runtime.health import RuntimeHealthAggregator
from bujji.production_runtime.runtime import PipelineInput
from bujji.production_runtime.startup import startup
from bujji.authentication.models import BrokerSession
from bujji.runtime_execution.models import ExecutionSession
from bujji.trading_brain.nifty_contract_builder import models as ncb_models

FIXED_CLOCK = lambda: datetime(2026, 1, 1, 9, 20, 0)

VALID_INPUT = PipelineInput(
    market_context="TRENDING_UP",
    market_opinion="BULLISH",
    context_stability="STABLE",
    calibration="CALIBRATED",
    governance="APPROVED",
    lifecycle="ACTIVE",
    contract="COMPLETE",
)


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


def _bad_broker_session():
    return BrokerSession(
        broker_session_id="BS-BAD",
        runtime_session_id="RS-BAD",
        broker_identity="FYERS",
        authentication_state="FAILED",
        session_state="FAILED",
        authentication_trace="simulated",
        failure_reason="simulated failure",
        created_at="2026-01-01T00:00:00",
        expires_at=None,
        updated_at="2026-01-01T00:00:00",
        version="1.0.0",
    )


def _bad_execution_session():
    return ExecutionSession(
        session_id="ES-BAD",
        order_requests=(),
        execution_state="FAILED_VALIDATION",
        dispatch_plan=(),
        validation_result="FAILED",
        execution_trace="simulated",
        failure_reason="simulated failure",
        timestamp="2026-01-01T00:00:00",
        version="1.0.0",
    )


@pytest.fixture
def breaker():
    return RuntimeCircuitBreaker(clock=FIXED_CLOCK)


@pytest.fixture
def aggregator():
    return RuntimeHealthAggregator(clock=FIXED_CLOCK)


@pytest.fixture
def started():
    return startup({"mode": RUNTIME_MODE_SHADOW, "broker_name": "paper"}, clock=FIXED_CLOCK)


def test_healthy_yields_closed(breaker, aggregator, started):
    _, sreport = started
    snap = aggregator.snapshot(startup_report=sreport)
    decision = breaker.evaluate(snap)
    assert decision.state == CIRCUIT_CLOSED
    assert decision.permit_new_work is True


def test_degraded_yields_open(breaker, aggregator, started):
    _, sreport = started
    snap = aggregator.snapshot(
        startup_report=sreport, broker_session=_bad_broker_session(), execution_session=_bad_execution_session()
    )
    assert snap.overall == "DEGRADED"
    decision = breaker.evaluate(snap)
    assert decision.state == CIRCUIT_OPEN
    assert decision.permit_new_work is False


def test_unhealthy_yields_open(breaker, aggregator):
    _, bad_report = startup({"mode": "NOT_A_MODE"}, clock=FIXED_CLOCK)
    snap = aggregator.snapshot(startup_report=bad_report)
    assert snap.overall == "UNHEALTHY"
    decision = breaker.evaluate(snap)
    assert decision.state == CIRCUIT_OPEN
    assert decision.permit_new_work is False


def test_insufficient_data_yields_insufficient_data(breaker, aggregator, started):
    _, sreport = started
    snap = aggregator.snapshot(startup_report=sreport, broker_session=None)
    assert snap.overall == "INSUFFICIENT_DATA"
    decision = breaker.evaluate(snap)
    assert decision.state == CIRCUIT_INSUFFICIENT_DATA
    assert decision.permit_new_work is False


def test_unknown_health_conservatively_yields_insufficient_data(breaker, aggregator):
    snap = aggregator.snapshot()
    assert snap.overall == "UNKNOWN"
    decision = breaker.evaluate(snap)
    assert decision.state == CIRCUIT_INSUFFICIENT_DATA
    assert decision.permit_new_work is False


def test_half_open_is_never_produced_automatically(breaker, aggregator, started):
    _, sreport = started
    for snap in (
        aggregator.snapshot(startup_report=sreport),
        aggregator.snapshot(
            startup_report=sreport, broker_session=_bad_broker_session(), execution_session=_bad_execution_session()
        ),
        aggregator.snapshot(startup_report=sreport, broker_session=None),
        aggregator.snapshot(),
    ):
        assert breaker.evaluate(snap).state != CIRCUIT_HALF_OPEN


def test_decision_is_deterministic(breaker, aggregator, started):
    _, sreport = started
    snap = aggregator.snapshot(startup_report=sreport)
    a = breaker.evaluate(snap)
    b = breaker.evaluate(snap)
    assert a.state == b.state
    assert a.permit_new_work == b.permit_new_work
    assert a.reason == b.reason
    assert a.timestamp == b.timestamp


def test_decision_is_immutable(breaker, aggregator, started):
    _, sreport = started
    snap = aggregator.snapshot(startup_report=sreport)
    decision = breaker.evaluate(snap)
    with pytest.raises(Exception):
        decision.state = CIRCUIT_OPEN  # type: ignore[misc]
    with pytest.raises(Exception):
        decision.permit_new_work = False  # type: ignore[misc]


def test_decision_traces_back_to_exact_snapshot(breaker, aggregator, started):
    _, sreport = started
    snap = aggregator.snapshot(startup_report=sreport)
    decision = breaker.evaluate(snap)
    assert decision.health_snapshot is snap


def test_authorize_new_work_matches_evaluate(breaker, aggregator, started):
    _, sreport = started
    snap = aggregator.snapshot(startup_report=sreport)
    decision = authorize_new_work(breaker, snap)
    assert decision.state == CIRCUIT_CLOSED


def test_runtime_rejects_new_work_when_open(breaker, aggregator, started):
    root, sreport = started
    _, bad_report = startup({"mode": "NOT_A_MODE"}, clock=FIXED_CLOCK)
    bad_snap = aggregator.snapshot(startup_report=bad_report)
    decision, result = guarded_run_shadow(breaker, bad_snap, root, VALID_INPUT, _spot(), _chain(), clock=FIXED_CLOCK)
    assert decision.state == CIRCUIT_OPEN
    assert result is None


def test_runtime_rejects_new_work_when_insufficient_data(breaker, started):
    root, sreport = started
    empty_snap = RuntimeHealthAggregator(clock=FIXED_CLOCK).snapshot()
    decision, result = guarded_run_read_only(breaker, empty_snap, root, VALID_INPUT, clock=FIXED_CLOCK)
    assert decision.state == CIRCUIT_INSUFFICIENT_DATA
    assert result is None


def test_runtime_accepts_new_work_when_closed(breaker, aggregator, started):
    root, sreport = started
    healthy_snap = aggregator.snapshot(startup_report=sreport)
    decision, result = guarded_run_read_only(breaker, healthy_snap, root, VALID_INPUT, clock=FIXED_CLOCK)
    assert decision.state == CIRCUIT_CLOSED
    assert result is not None
    assert result.mode == "READ_ONLY"


def test_existing_work_unaffected_by_a_later_open_decision(breaker, aggregator, started):
    root, sreport = started
    healthy_snap = aggregator.snapshot(startup_report=sreport)
    _, existing_result = guarded_run_shadow(
        breaker, healthy_snap, root, VALID_INPUT, _spot(), _chain(), clock=FIXED_CLOCK
    )
    assert existing_result is not None
    before_state = existing_result.execution_session.execution_state

    _, bad_report = startup({"mode": "NOT_A_MODE"}, clock=FIXED_CLOCK)
    bad_snap = aggregator.snapshot(startup_report=bad_report)
    decision, new_result = guarded_run_shadow(
        breaker, bad_snap, root, VALID_INPUT, _spot(), _chain(), clock=FIXED_CLOCK
    )
    assert decision.state == CIRCUIT_OPEN
    assert new_result is None
    # The prior, already-completed result is untouched by the later refusal.
    assert existing_result.execution_session.execution_state == before_state


def test_no_broker_authentication_dispatch_or_recovery_calls(breaker, aggregator, started, monkeypatch):
    root, sreport = started

    def _forbidden(*args, **kwargs):
        raise AssertionError("circuit breaker must never call broker/auth/dispatch/recovery methods")

    monkeypatch.setattr(root.broker, "_apply_fill", _forbidden, raising=False)
    monkeypatch.setattr(root.broker, "_check_auth", _forbidden, raising=False)

    snap = aggregator.snapshot(startup_report=sreport)
    decision = breaker.evaluate(snap, runtime_mode=root.config.mode)
    assert decision.state == CIRCUIT_CLOSED


def test_evaluate_performs_no_runtime_mutation(breaker, aggregator, started):
    root, sreport = started
    snap = aggregator.snapshot(startup_report=sreport)
    config_before = root.config
    breaker.evaluate(snap)
    assert root.config == config_before


def test_evidence_used_and_reason_reference_overall_health(breaker, aggregator, started):
    _, sreport = started
    snap = aggregator.snapshot(startup_report=sreport)
    decision = breaker.evaluate(snap, runtime_mode="SHADOW", qualification_mode="PASSED")
    assert "HEALTHY" in decision.evidence_used
    assert "SHADOW" in decision.reason
    assert "PASSED" in decision.reason
