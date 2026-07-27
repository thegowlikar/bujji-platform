"""Tests — Engineering Series 55: runtime health aggregation."""
from datetime import datetime

import pytest

from bujji.authentication.models import BrokerSession
from bujji.runtime_execution.models import ExecutionSession
from bujji.runtime_session.models import RuntimeSession, RuntimeSessionPolicy
from bujji.production_runtime.config import RuntimeConfig, RUNTIME_MODE_SHADOW
from bujji.production_runtime.health import (
    HEALTH_DEGRADED,
    HEALTH_HEALTHY,
    HEALTH_INSUFFICIENT_DATA,
    HEALTH_UNHEALTHY,
    HEALTH_UNKNOWN,
    RuntimeHealthAggregator,
    RuntimeHealthSnapshot,
)
from bujji.production_runtime.runtime import PipelineInput, run_shadow
from bujji.production_runtime.startup import startup
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
def aggregator():
    return RuntimeHealthAggregator(clock=FIXED_CLOCK)


@pytest.fixture
def started():
    return startup({"mode": RUNTIME_MODE_SHADOW, "broker_name": "paper"}, clock=FIXED_CLOCK)


def test_healthy_runtime_end_to_end(aggregator, started):
    root, sreport = started
    result = run_shadow(root, VALID_INPUT, spot_snapshot=_spot(), option_chain=_chain(), clock=FIXED_CLOCK)
    snap = aggregator.snapshot_from_composition_root(
        root,
        startup_report=sreport,
        runtime_session=result.runtime_session,
        broker_session=result.broker_session,
        execution_session=result.execution_session,
    )
    assert snap.overall == HEALTH_HEALTHY
    assert all(s.status == HEALTH_HEALTHY for s in snap.subsystems)


def test_missing_configuration_is_unhealthy(aggregator):
    _, bad_report = startup({"mode": "NOT_A_REAL_MODE"}, clock=FIXED_CLOCK)
    snap = aggregator.snapshot(startup_report=bad_report)
    assert snap.overall == HEALTH_UNHEALTHY
    config_health = next(s for s in snap.subsystems if s.name == "configuration")
    assert config_health.status == HEALTH_UNHEALTHY


def test_failed_startup_is_unhealthy(aggregator, monkeypatch):
    import bujji.production_runtime.startup as startup_module
    from bujji.production_runtime.composition_root import CompositionError

    def _boom(config, logger=None):
        raise CompositionError("simulated")

    monkeypatch.setattr(startup_module, "build_composition_root", _boom)
    _, bad_report = startup({"broker_name": "paper"}, clock=FIXED_CLOCK)
    snap = aggregator.snapshot(startup_report=bad_report)
    assert snap.overall == HEALTH_UNHEALTHY
    startup_health = next(s for s in snap.subsystems if s.name == "startup")
    assert startup_health.status == HEALTH_UNHEALTHY


def test_missing_authentication_and_execution_is_degraded(aggregator, started):
    root, sreport = started
    snap = aggregator.snapshot(
        startup_report=sreport,
        broker_session=_bad_broker_session(),
        execution_session=_bad_execution_session(),
    )
    assert snap.overall == HEALTH_DEGRADED


def test_authentication_evidence_missing_entirely_is_insufficient_data(aggregator, started):
    root, sreport = started
    snap = aggregator.snapshot(startup_report=sreport, broker_session=None)
    assert snap.overall == HEALTH_INSUFFICIENT_DATA
    auth_health = next(s for s in snap.subsystems if s.name == "authentication")
    assert auth_health.status == HEALTH_INSUFFICIENT_DATA


def test_execution_evidence_missing_entirely_is_insufficient_data(aggregator, started):
    root, sreport = started
    snap = aggregator.snapshot(startup_report=sreport, execution_session=None)
    assert snap.overall == HEALTH_INSUFFICIENT_DATA


def test_missing_recovery_information_is_insufficient_data(aggregator, started):
    root, sreport = started
    snap = aggregator.snapshot(startup_report=sreport, recovery_result=None)
    assert snap.overall == HEALTH_INSUFFICIENT_DATA
    recovery_health = next(s for s in snap.subsystems if s.name == "recovery")
    assert recovery_health.status == HEALTH_INSUFFICIENT_DATA


def test_recovery_not_applicable_when_omitted_does_not_penalize_overall(aggregator, started):
    root, sreport = started
    result_no_market = run_shadow(root, VALID_INPUT, spot_snapshot=_spot(), option_chain=_chain(), clock=FIXED_CLOCK)
    snap = aggregator.snapshot(
        startup_report=sreport,
        runtime_session=result_no_market.runtime_session,
        broker_session=result_no_market.broker_session,
        execution_session=result_no_market.execution_session,
        # recovery_result intentionally omitted -- not applicable, no restart occurred
    )
    assert "recovery" not in {s.name for s in snap.subsystems}
    assert snap.overall == HEALTH_HEALTHY


def test_no_evidence_supplied_at_all_is_unknown(aggregator):
    snap = aggregator.snapshot()
    assert snap.overall == HEALTH_UNKNOWN
    assert snap.subsystems == ()


def test_aggregation_is_deterministic(aggregator, started):
    root, sreport = started
    result = run_shadow(root, VALID_INPUT, spot_snapshot=_spot(), option_chain=_chain(), clock=FIXED_CLOCK)
    kwargs = dict(
        startup_report=sreport,
        runtime_session=result.runtime_session,
        broker_session=result.broker_session,
        execution_session=result.execution_session,
    )
    a = aggregator.snapshot(**kwargs)
    b = aggregator.snapshot(**kwargs)
    assert a.overall == b.overall
    assert a.subsystems == b.subsystems
    assert a.evidence_used == b.evidence_used
    assert a.issues == b.issues
    assert a.timestamp == b.timestamp


def test_snapshot_is_immutable(aggregator):
    snap = aggregator.snapshot()
    with pytest.raises(Exception):
        snap.overall = HEALTH_HEALTHY  # type: ignore[misc]
    with pytest.raises(Exception):
        snap.subsystems = ()  # type: ignore[misc]


def test_subsystem_health_is_immutable(aggregator, started):
    root, sreport = started
    snap = aggregator.snapshot(startup_report=sreport)
    subsystem = snap.subsystems[0]
    with pytest.raises(Exception):
        subsystem.status = HEALTH_HEALTHY  # type: ignore[misc]


def test_composition_root_integration_via_convenience_method(aggregator, started):
    root, sreport = started
    snap = aggregator.snapshot_from_composition_root(root, startup_report=sreport)
    assert snap.runtime_mode == root.config.mode
    assert snap.broker_mode == root.config.broker_name


def test_health_check_is_read_only_no_broker_or_session_mutation(aggregator, started):
    root, sreport = started
    result = run_shadow(root, VALID_INPUT, spot_snapshot=_spot(), option_chain=_chain(), clock=FIXED_CLOCK)
    before_runtime_state = result.runtime_session.session_state
    before_broker_state = result.broker_session.session_state
    before_exec_state = result.execution_session.execution_state

    aggregator.snapshot_from_composition_root(
        root,
        startup_report=sreport,
        runtime_session=result.runtime_session,
        broker_session=result.broker_session,
        execution_session=result.execution_session,
    )

    assert result.runtime_session.session_state == before_runtime_state
    assert result.broker_session.session_state == before_broker_state
    assert result.execution_session.execution_state == before_exec_state


def test_health_check_never_calls_broker_or_network(aggregator, started, monkeypatch):
    root, sreport = started

    def _forbidden(*args, **kwargs):
        raise AssertionError("health aggregation must never call broker/network methods")

    monkeypatch.setattr(root.broker, "_apply_fill", _forbidden, raising=False)
    monkeypatch.setattr(root.broker, "_check_auth", _forbidden, raising=False)
    aggregator.snapshot_from_composition_root(root, startup_report=sreport)
    # No assertion error raised means neither broker method was invoked.


def test_evidence_used_never_fabricated_matches_subsystem_count(aggregator, started):
    root, sreport = started
    snap = aggregator.snapshot(startup_report=sreport)
    assert len(snap.evidence_used) == len(snap.subsystems)
