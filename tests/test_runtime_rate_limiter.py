"""Tests — Engineering Series 57: runtime rate limiter."""
from datetime import datetime, timedelta

import pytest

from bujji.production_runtime.circuit_breaker import RuntimeCircuitBreaker
from bujji.production_runtime.config import RUNTIME_MODE_SHADOW
from bujji.production_runtime.health import RuntimeHealthAggregator
from bujji.production_runtime.rate_limiter import (
    RATE_LIMIT_INSUFFICIENT_DATA,
    RATE_LIMIT_PERMITTED,
    RATE_LIMIT_RATE_LIMITED,
    RateLimiterConfig,
    RuntimeRateLimiter,
    guarded_run_read_only,
    guarded_run_shadow,
)
from bujji.production_runtime.runtime import PipelineInput
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


@pytest.fixture
def started():
    return startup({"mode": RUNTIME_MODE_SHADOW, "broker_name": "paper"}, clock=FIXED_CLOCK)


@pytest.fixture
def closed_circuit_decision(started):
    _, sreport = started
    snap = RuntimeHealthAggregator(clock=FIXED_CLOCK).snapshot(startup_report=sreport)
    return RuntimeCircuitBreaker(clock=FIXED_CLOCK).evaluate(snap)


@pytest.fixture
def open_circuit_decision():
    _, bad_report = startup({"mode": "NOT_A_MODE"}, clock=FIXED_CLOCK)
    snap = RuntimeHealthAggregator(clock=FIXED_CLOCK).snapshot(startup_report=bad_report)
    return RuntimeCircuitBreaker(clock=FIXED_CLOCK).evaluate(snap)


@pytest.fixture
def limiter():
    return RuntimeRateLimiter(config=RateLimiterConfig(min_interval_seconds=5.0), clock=FIXED_CLOCK)


def test_circuit_closed_interval_satisfied_is_permitted(limiter, closed_circuit_decision):
    previous = FIXED_CLOCK() - timedelta(seconds=10)
    decision = limiter.evaluate(closed_circuit_decision, previous_admission_timestamp=previous)
    assert decision.state == RATE_LIMIT_PERMITTED
    assert decision.permitted is True
    assert decision.elapsed_seconds == pytest.approx(10.0)


def test_circuit_closed_interval_not_satisfied_is_rate_limited(limiter, closed_circuit_decision):
    previous = FIXED_CLOCK() - timedelta(seconds=2)
    decision = limiter.evaluate(closed_circuit_decision, previous_admission_timestamp=previous)
    assert decision.state == RATE_LIMIT_RATE_LIMITED
    assert decision.permitted is False
    assert decision.elapsed_seconds == pytest.approx(2.0)


def test_circuit_open_is_rate_limited_regardless_of_interval(limiter, open_circuit_decision):
    previous = FIXED_CLOCK() - timedelta(seconds=1000)
    decision = limiter.evaluate(open_circuit_decision, previous_admission_timestamp=previous)
    assert decision.state == RATE_LIMIT_RATE_LIMITED
    assert decision.permitted is False
    assert "OPEN" in decision.reason


def test_no_previous_admission_is_permitted(limiter, closed_circuit_decision):
    decision = limiter.evaluate(closed_circuit_decision, previous_admission_timestamp=None)
    assert decision.state == RATE_LIMIT_PERMITTED
    assert decision.elapsed_seconds is None


def test_missing_configuration_is_insufficient_data(closed_circuit_decision):
    limiter = RuntimeRateLimiter(config=None, clock=FIXED_CLOCK)
    decision = limiter.evaluate(closed_circuit_decision, previous_admission_timestamp=FIXED_CLOCK())
    assert decision.state == RATE_LIMIT_INSUFFICIENT_DATA
    assert decision.permitted is False


def test_missing_clock_is_insufficient_data(closed_circuit_decision):
    limiter = RuntimeRateLimiter(config=RateLimiterConfig(), clock=None)
    decision = limiter.evaluate(closed_circuit_decision, previous_admission_timestamp=FIXED_CLOCK())
    assert decision.state == RATE_LIMIT_INSUFFICIENT_DATA


def test_missing_circuit_decision_is_rate_limited_not_permitted(limiter):
    decision = limiter.evaluate(None, previous_admission_timestamp=FIXED_CLOCK())
    assert decision.state == RATE_LIMIT_RATE_LIMITED
    assert decision.permitted is False


def test_deterministic_given_same_inputs(limiter, closed_circuit_decision):
    previous = FIXED_CLOCK() - timedelta(seconds=10)
    a = limiter.evaluate(closed_circuit_decision, previous_admission_timestamp=previous)
    b = limiter.evaluate(closed_circuit_decision, previous_admission_timestamp=previous)
    assert a.state == b.state
    assert a.permitted == b.permitted
    assert a.elapsed_seconds == b.elapsed_seconds
    assert a.timestamp == b.timestamp
    assert a.reason == b.reason


def test_config_is_immutable():
    config = RateLimiterConfig(min_interval_seconds=5.0)
    with pytest.raises(Exception):
        config.min_interval_seconds = 1.0  # type: ignore[misc]


def test_negative_interval_rejected_at_construction():
    with pytest.raises(ValueError):
        RateLimiterConfig(min_interval_seconds=-1.0)


def test_decision_is_immutable(limiter, closed_circuit_decision):
    decision = limiter.evaluate(closed_circuit_decision, previous_admission_timestamp=None)
    with pytest.raises(Exception):
        decision.state = RATE_LIMIT_RATE_LIMITED  # type: ignore[misc]
    with pytest.raises(Exception):
        decision.permitted = False  # type: ignore[misc]


def test_decision_traces_back_to_exact_circuit_decision(limiter, closed_circuit_decision):
    decision = limiter.evaluate(closed_circuit_decision, previous_admission_timestamp=None)
    assert decision.circuit_decision is closed_circuit_decision


def test_composition_root_integration_permits_when_closed_and_interval_met(limiter, started, closed_circuit_decision):
    root, _ = started
    previous = FIXED_CLOCK() - timedelta(seconds=10)
    decision, result = guarded_run_read_only(
        closed_circuit_decision, limiter, previous, root, VALID_INPUT, clock=FIXED_CLOCK
    )
    assert decision.state == RATE_LIMIT_PERMITTED
    assert result is not None
    assert result.mode == "READ_ONLY"


def test_composition_root_integration_rejects_when_interval_not_met(limiter, started, closed_circuit_decision):
    root, _ = started
    previous = FIXED_CLOCK() - timedelta(seconds=1)
    decision, result = guarded_run_read_only(
        closed_circuit_decision, limiter, previous, root, VALID_INPUT, clock=FIXED_CLOCK
    )
    assert decision.state == RATE_LIMIT_RATE_LIMITED
    assert result is None


def test_composition_root_integration_rejects_when_circuit_open(limiter, started, open_circuit_decision):
    root, _ = started
    decision, result = guarded_run_shadow(
        open_circuit_decision, limiter, None, root, VALID_INPUT, _spot(), _chain(), clock=FIXED_CLOCK
    )
    assert decision.state == RATE_LIMIT_RATE_LIMITED
    assert result is None


def test_existing_work_unaffected_by_a_later_rate_limited_decision(limiter, started, closed_circuit_decision):
    root, _ = started
    _, first_result = guarded_run_shadow(
        closed_circuit_decision, limiter, None, root, VALID_INPUT, _spot(), _chain(), clock=FIXED_CLOCK
    )
    assert first_result is not None
    before_state = first_result.execution_session.execution_state

    too_soon = FIXED_CLOCK() - timedelta(seconds=1)
    decision, second_result = guarded_run_shadow(
        closed_circuit_decision, limiter, too_soon, root, VALID_INPUT, _spot(), _chain(), clock=FIXED_CLOCK
    )
    assert decision.state == RATE_LIMIT_RATE_LIMITED
    assert second_result is None
    assert first_result.execution_session.execution_state == before_state


def test_no_broker_authentication_dispatch_or_recovery_calls(limiter, started, closed_circuit_decision, monkeypatch):
    root, _ = started

    def _forbidden(*args, **kwargs):
        raise AssertionError("rate limiter must never call broker/auth/dispatch/recovery methods")

    monkeypatch.setattr(root.broker, "_apply_fill", _forbidden, raising=False)
    monkeypatch.setattr(root.broker, "_check_auth", _forbidden, raising=False)

    previous = FIXED_CLOCK() - timedelta(seconds=10)
    decision = limiter.evaluate(closed_circuit_decision, previous_admission_timestamp=previous)
    assert decision.state == RATE_LIMIT_PERMITTED
