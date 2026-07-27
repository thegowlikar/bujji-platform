"""Tests — Engineering Series 54: startup/shutdown sequence."""
from datetime import datetime

import pytest

from bujji.production_runtime.config import RuntimeConfig, RUNTIME_MODE_SHADOW
from bujji.production_runtime.startup import StartupReport, startup
from bujji.production_runtime.shutdown import ShutdownReport, shutdown

FIXED_CLOCK = lambda: datetime(2026, 1, 1, 9, 0, 0)


def test_startup_succeeds_and_reports_ready():
    root, report = startup({"mode": RUNTIME_MODE_SHADOW, "broker_name": "paper"}, clock=FIXED_CLOCK)
    assert isinstance(report, StartupReport)
    assert report.ready is True
    assert report.config_valid is True
    assert report.dependencies_constructed is True
    assert report.authentication_path_reachable is True
    assert report.execution_path_reachable is True
    assert report.runtime_initialized is True
    assert report.failure_reason is None
    assert root is not None


def test_startup_step_order_matches_specification():
    _, report = startup({"broker_name": "paper"}, clock=FIXED_CLOCK)
    expected_prefixes = [
        "load_config",
        "construct_object_graph",
        "initialize_broker_objects",
        "initialize_execution_engine",
        "verify_authentication_path",
        "verify_execution_path",
        "runtime",
    ]
    seen_prefixes = []
    for s in report.steps:
        prefix = s.split(":")[0]
        if not seen_prefixes or seen_prefixes[-1] != prefix:
            seen_prefixes.append(prefix)
    assert seen_prefixes == expected_prefixes


def test_no_market_activity_occurs_during_startup():
    _, report = startup({"broker_name": "paper"}, clock=FIXED_CLOCK)
    joined = " ".join(report.steps).lower()
    assert "order" not in joined
    assert "dispatch" not in joined
    assert "no market activity has occurred" in joined


def test_startup_fails_gracefully_on_invalid_config():
    root, report = startup({"mode": "NOT_A_MODE"}, clock=FIXED_CLOCK)
    assert root is None
    assert report.ready is False
    assert report.config_valid is False
    assert report.failure_reason is not None


def test_startup_fails_gracefully_on_construction_error(monkeypatch):
    import bujji.production_runtime.startup as startup_module

    def _boom(config, logger=None):
        from bujji.production_runtime.composition_root import CompositionError

        raise CompositionError("simulated construction failure")

    monkeypatch.setattr(startup_module, "build_composition_root", _boom)
    root, report = startup({"broker_name": "paper"}, clock=FIXED_CLOCK)
    assert root is None
    assert report.config_valid is True
    assert report.dependencies_constructed is False
    assert "simulated construction failure" in report.failure_reason


def test_startup_never_raises_uncaught_exception():
    try:
        startup({"broker_name": "totally_invalid"}, clock=FIXED_CLOCK)
    except Exception as exc:  # noqa: BLE001
        pytest.fail(f"startup() must never raise -- got {exc!r}")


def test_shutdown_after_successful_startup_is_clean():
    root, report = startup({"broker_name": "paper"}, clock=FIXED_CLOCK)
    assert report.ready is True
    shutdown_report = shutdown(root, clock=FIXED_CLOCK)
    assert isinstance(shutdown_report, ShutdownReport)
    assert shutdown_report.clean is True
    assert any("runtime: STOPPED" in s for s in shutdown_report.steps)


def test_shutdown_handles_no_root_gracefully():
    report = shutdown(None, clock=FIXED_CLOCK)
    assert report.clean is True
    assert "nothing to release" in report.steps[0]


def test_shutdown_no_forced_termination_language():
    root, _ = startup({"broker_name": "paper"}, clock=FIXED_CLOCK)
    report = shutdown(root, clock=FIXED_CLOCK)
    joined = " ".join(report.steps).lower()
    assert "kill" not in joined
    assert "terminate" not in joined or "termination" not in joined
