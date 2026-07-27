"""Sprint 4 — Operations Layer validation. Health state model, alert
engine, incident log, and failure-injection scenarios required by the
sprint's Validation section."""
import time

import pytest

from bujji.core.runtime_status import RuntimeStatus
from bujji.ops.alerts import evaluate as evaluate_alerts
from bujji.ops.health_monitor import HealthMonitor
from bujji.ops.incident_log import IncidentLog
from bujji.ops.models import HealthState, worst


# ---------------------------------------------------------------------- #
# Health State Model -- objective transition rules
# ---------------------------------------------------------------------- #
def test_worst_combines_by_severity_never_by_average():
    assert worst([HealthState.HEALTHY, HealthState.WARNING]) == HealthState.WARNING
    assert worst([HealthState.WARNING, HealthState.CRITICAL, HealthState.HEALTHY]) == HealthState.CRITICAL
    assert worst([]) == HealthState.HEALTHY


def test_healthy_state_when_no_signals_present(logger, tmp_path):
    hm = HealthMonitor(logger, tmp_path / "restarts.json", tmp_path / "d.jsonl")
    status = RuntimeStatus()
    snap = hm.observe(status, journal_write_ok=True, latest_decision_id=None, latest_trade_id=None)
    assert snap.health_state in (HealthState.HEALTHY, HealthState.DEGRADED)  # ws_connected default False -> DEGRADED is legitimate.
    assert isinstance(snap.reasons, list)


def test_auth_expired_is_always_critical(logger, tmp_path):
    hm = HealthMonitor(logger, tmp_path / "restarts.json", tmp_path / "d.jsonl")
    status = RuntimeStatus()
    status.auth_expired = True
    snap = hm.observe(status, True, None, None)
    assert snap.health_state == HealthState.CRITICAL
    assert snap.auth_expired_since is not None
    assert snap.auth_expired_duration_seconds is not None


def test_auth_expiry_duration_grows_across_cycles_and_clears_on_recovery(logger, tmp_path):
    """Sprint 4, Authentication Monitoring requirement: first failure,
    duration, and recovery must all be tracked without inference."""
    hm = HealthMonitor(logger, tmp_path / "restarts.json", tmp_path / "d.jsonl")
    status = RuntimeStatus()
    status.auth_expired = True
    first = hm.observe(status, True, None, None)
    time.sleep(0.05)
    second = hm.observe(status, True, None, None)
    assert second.auth_expired_duration_seconds > first.auth_expired_duration_seconds
    assert first.auth_expired_since == second.auth_expired_since  # Same incident, not re-started.

    status.auth_expired = False
    recovered = hm.observe(status, True, None, None)
    assert recovered.auth_expired_since is None
    assert recovered.auth_expired_duration_seconds is None


def test_stale_market_data_is_degraded_then_critical_by_severity(logger, tmp_path):
    from datetime import timedelta
    from bujji.core.clock import now_ist
    hm = HealthMonitor(logger, tmp_path / "restarts.json", tmp_path / "d.jsonl")
    status = RuntimeStatus()
    status.last_candle_ts = (now_ist() - timedelta(seconds=500)).isoformat()  # Above WARNING, below CRITICAL.
    warn = hm.observe(status, True, None, None)
    assert warn.health_state in (HealthState.WARNING, HealthState.DEGRADED)

    status.last_candle_ts = (now_ist() - timedelta(seconds=1000)).isoformat()  # Above CRITICAL.
    crit = hm.observe(status, True, None, None)
    assert crit.health_state == HealthState.CRITICAL


def test_journal_write_failure_is_critical(logger, tmp_path):
    hm = HealthMonitor(logger, tmp_path / "restarts.json", tmp_path / "d.jsonl")
    status = RuntimeStatus()
    snap = hm.observe(status, journal_write_ok=False, latest_decision_id=None, latest_trade_id=None)
    assert snap.health_state == HealthState.CRITICAL
    assert not snap.journal_write_ok


def test_ws_disconnected_is_degraded_not_critical_by_design(logger, tmp_path):
    """The candle-driven entry/exit path does not depend on the tick
    WebSocket -- losing it degrades responsiveness, never blinds the
    system to a candle. Verified as an explicit design choice, not left
    to accidentally collapse into CRITICAL."""
    hm = HealthMonitor(logger, tmp_path / "restarts.json", tmp_path / "d.jsonl")
    status = RuntimeStatus()
    status.ws_connected = False
    snap = hm.observe(status, True, None, None)
    assert snap.health_state != HealthState.CRITICAL


def test_restart_count_persists_and_accumulates_across_instances(logger, tmp_path):
    path = tmp_path / "restarts.json"
    HealthMonitor(logger, path, tmp_path / "d.jsonl")
    HealthMonitor(logger, path, tmp_path / "d.jsonl")
    third = HealthMonitor(logger, path, tmp_path / "d.jsonl")
    snap = third.observe(RuntimeStatus(), True, None, None)
    assert snap.restart_count_last_hour == 3


# ---------------------------------------------------------------------- #
# Alert Engine
# ---------------------------------------------------------------------- #
def test_auth_expired_always_produces_an_alert(logger, tmp_path):
    hm = HealthMonitor(logger, tmp_path / "restarts.json", tmp_path / "d.jsonl")
    status = RuntimeStatus()
    status.auth_expired = True
    snap = hm.observe(status, True, None, None)
    alerts = evaluate_alerts(snap, None)
    assert any(a.category == "auth_expired" for a in alerts)
    assert all(a.severity == HealthState.CRITICAL for a in alerts if a.category == "auth_expired")


def test_journal_failure_produces_an_alert(logger, tmp_path):
    hm = HealthMonitor(logger, tmp_path / "restarts.json", tmp_path / "d.jsonl")
    snap = hm.observe(RuntimeStatus(), journal_write_ok=False, latest_decision_id=None, latest_trade_id=None)
    alerts = evaluate_alerts(snap, None)
    assert any(a.category == "journal_failure" for a in alerts)


def test_no_alerts_when_fully_healthy(logger, tmp_path):
    hm = HealthMonitor(logger, tmp_path / "restarts.json", tmp_path / "d.jsonl")
    status = RuntimeStatus()
    status.ws_connected = True
    snap = hm.observe(status, True, None, None)
    alerts = evaluate_alerts(snap, HealthState.HEALTHY)
    assert alerts == []


# ---------------------------------------------------------------------- #
# Incident Log -- separate from TradeJournal/DecisionJournal
# ---------------------------------------------------------------------- #
def test_incident_log_is_a_separate_artifact_from_trade_and_decision_journals(logger, tmp_path):
    il = IncidentLog(tmp_path / "incidents.jsonl", logger)
    incident = il.open_incident("CRITICAL", "auth_expired", "broker")
    assert incident.status == "OPEN"
    assert (tmp_path / "incidents.jsonl").exists()

    # Re-opening the same subsystem while already open is a no-op, not a
    # duplicate incident -- prevents an incident flood from a persistent state.
    same = il.open_incident("CRITICAL", "auth_expired", "broker")
    assert same.incident_id == incident.incident_id

    resolved = il.resolve_incident("broker", "token refreshed")
    assert resolved.status == "RESOLVED"
    assert resolved.duration_seconds is not None
    assert il.open_incidents() == []


@pytest.mark.asyncio
async def test_ops_health_flows_through_a_real_orchestrator_cycle(config, logger, tmp_path):
    """End-to-end: RuntimeStatus.ops is populated after a real on_candle
    cycle, without affecting the trade itself."""
    from bujji.broker.paper import PaperBroker
    from bujji.core.orchestrator import Orchestrator
    from bujji.core.session_state import SessionStore
    from bujji.execution.engine import ExecutionEngine
    from bujji.journal.journal import TradeJournal
    from bujji.signal.engine import SignalEngine
    from bujji.trade.manager import TradeManager
    from tests.conftest import c

    config.paths.journal_csv = tmp_path / "j.csv"
    config.paths.database = tmp_path / "b.db"
    config.paths.state_file = tmp_path / "s.json"
    config.paths.decision_journal = tmp_path / "d.jsonl"
    config.paths.ops_restart_count = tmp_path / "r.json"
    config.paths.incident_log = tmp_path / "i.jsonl"

    status = RuntimeStatus()
    broker = PaperBroker()
    execn = ExecutionEngine(broker, config, logger)
    signal = SignalEngine(config, logger)
    trade = TradeManager(config, logger)
    journal = TradeJournal(config.paths.journal_csv, config.paths.database)
    store = SessionStore(config.paths.state_file)
    orch = Orchestrator(config, logger, signal, trade, execn, journal, store, status)

    await orch.startup()
    await orch.on_candle(c(9, 20, 22000, 22010, 21990, 22005))
    assert status.ops  # Populated -- observation only, never blocked the entry above.
    assert status.ops["health_state"] in [s.value for s in HealthState]
    assert "latest_decision_id" in status.ops


def test_ops_health_update_failure_never_raises(logger, tmp_path):
    """A broken HealthMonitor must never crash the caller -- mirrors the
    ExecutionEngine/DecisionJournal never-raises discipline."""
    class _BrokenMonitor:
        def observe(self, *a, **k):
            raise RuntimeError("simulated ops failure")

    import logging as _logging
    from bujji.core.orchestrator import Orchestrator
    # Directly exercise the guarded path via a monkeypatched monitor on a
    # minimal stand-in rather than a full Orchestrator construction --
    # the guard itself is what's under test.
    class _Stub:
        _health_monitor = _BrokenMonitor()
        _status = RuntimeStatus()
        _log = _logging.getLogger("test")
        _ops_previous_state = None
        _journal = type("J", (), {"all_trades": lambda self: []})()
        _last_decision_snapshot = None
        _cfg = config = None
        _incident_log = None

        def _update_ops_health(self):
            return Orchestrator._update_ops_health(self)

    # Bind cfg.paths for the os.access check inside the method.
    import types
    stub = _Stub()
    stub._cfg = types.SimpleNamespace(paths=types.SimpleNamespace(journal_csv=tmp_path / "j.csv"))
    stub._update_ops_health()  # Must not raise.
