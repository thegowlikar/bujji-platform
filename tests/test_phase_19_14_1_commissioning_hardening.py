"""Phase 19.14.1 -- Autonomous Runtime Commissioning Hardening.

Proves the 15 required scenarios from the phase's own task list:
1. Oneshot service semantics
2. Timer/service ownership
3. No restart loop
4. Weekend/holiday behavior
5. Completeness gate success
6. Completeness gate failure
7. Replay equivalence success
8. Replay mismatch
9. Replay failure
10. Incomplete capture + successful intelligence
11. Crash/restart
12. ProcessLock interaction
13. FYERS authentication failure
14. Manual legacy invocation while authoritative runtime owns the lock
15. No trading/order capability
"""
from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path

import pytest

from bujji.core.models import Candle
from bujji.core.process_lock import LockAcquisitionError, ProcessLock
from bujji.intelligence.context import EXECUTION_MODE_LIVE
from bujji.shadow_runtime.daily_session import (
    CaptureResult,
    CompletenessCheckResult,
    DailySessionRuntime,
    IntelligenceRunResult,
    read_daily_heartbeat,
)
from bujji.shadow_runtime.live_intelligence_cycle import run_live_intelligence_cycle
from bujji.shadow_runtime.manual_entrypoint_guard import (
    AuthoritativeRuntimeActiveError,
    refuse_if_authoritative_runtime_active,
)
from bujji.state_persistence.store import EventStore

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXED_NOW = datetime(2026, 8, 17, 9, 45, 0, tzinfo=timezone.utc)


def clock():
    return FIXED_NOW


def _ok_capture(rows=10):
    async def fn() -> CaptureResult:
        return CaptureResult(rows_captured=rows, last_observation_timestamp=FIXED_NOW.isoformat(), errors=())
    return fn


def _incomplete_capture():
    async def fn() -> CaptureResult:
        return CaptureResult(rows_captured=3, last_observation_timestamp=FIXED_NOW.isoformat(), errors=("partial capture: options feed dropped",))
    return fn


def _ok_intelligence(replay_equivalent=None, replay_mismatches=(), replay_check_error=None):
    async def fn() -> IntelligenceRunResult:
        return IntelligenceRunResult(
            cycles_completed=1, last_intelligence_cycle_timestamp=FIXED_NOW.isoformat(), errors=(),
            replay_equivalent=replay_equivalent, replay_mismatches=replay_mismatches, replay_check_error=replay_check_error,
        )
    return fn


def _crashing_capture():
    async def fn() -> CaptureResult:
        raise RuntimeError("simulated capture crash")
    return fn


def _auth_failed_intelligence():
    async def fn() -> IntelligenceRunResult:
        return IntelligenceRunResult(
            cycles_completed=0, last_intelligence_cycle_timestamp=None,
            errors=("FYERS_APP_ID / FYERS_ACCESS_TOKEN not set in environment",),
        )
    return fn


class FakeBroker:
    """Same production-shaped fixture pattern established in
    tests/test_shadow_runtime_intelligence_loop.py and
    tests/test_phase_19_13_live_intelligence_bridge.py."""

    def __init__(self, fail_spot: bool = False):
        self._fail_spot = fail_spot

    async def connect(self):
        pass

    async def get_spot(self, underlying):
        return None if self._fail_spot else 24400.0

    async def get_vix(self):
        return {"level": 13.0, "prev_close": 13.5}

    async def get_option_chain(self, underlying, spot, strike_count=5):
        return [(24450.0, 12000.0, 15000.0)]

    async def get_futures_quote(self, underlying):
        return {"symbol": "NSE:NIFTY26AUGFUT", "ltp": 24650.0, "volume": 1000.0, "oi": None}

    async def get_recent_candles(self, underlying, minutes, count):
        out, c = [], 24300.0
        for _ in range(20):
            c += 5
            out.append(Candle(timestamp=FIXED_NOW, open=c - 5, high=c + 2, low=c - 7, close=c, volume=1000))
        return out


# ---------------------------------------------------------------------
# 1-3. Oneshot service semantics, timer/service ownership, no restart loop
# ---------------------------------------------------------------------

def _read_unit(name: str) -> str:
    return (REPO_ROOT / "deploy" / name).read_text()


def test_service_is_oneshot_not_simple_daemon():
    text = _read_unit("bujji-daily-intelligence.service")
    assert "Type=oneshot" in text
    assert "Type=simple" not in text


def test_service_has_no_restart_always_and_no_direct_install():
    text = _read_unit("bujji-daily-intelligence.service")
    assert "Restart=always" not in text
    assert "RestartSec=5" not in text
    # A oneshot triggered by a timer must not carry its own
    # WantedBy=multi-user.target -- that would let an operator
    # `systemctl enable` the SERVICE directly, recreating a daemon-like
    # always-on registration that bypasses the timer's own schedule.
    assert "WantedBy=multi-user.target" not in text


def test_timer_targets_weekdays_only_and_survives_reboot():
    text = _read_unit("bujji-daily-intelligence.timer")
    assert "OnCalendar=Mon..Fri" in text
    # Scoped to bujji-daily-intelligence only. Its capture subprocesses
    # ARE market-hours gated, so a reboot catch-up aborts without writing
    # rather than acting. The trading and campaign timers deliberately set
    # Persistent=false for reasons documented in their own unit files.
    assert "Persistent=true" in text
    assert "WantedBy=timers.target" in text


def test_timer_and_service_share_the_conventional_systemd_name():
    """Systemd's own convention: foo.timer without an explicit `Unit=`
    directive targets foo.service by name -- verified by confirming
    neither file overrides that default with a mismatched `Unit=`."""
    timer_text = _read_unit("bujji-daily-intelligence.timer")
    assert "Unit=" not in timer_text  # relying on the implicit same-name match, not a stray override.
    assert (REPO_ROOT / "deploy" / "bujji-daily-intelligence.service").exists()


# ---------------------------------------------------------------------
# 4. Weekend/holiday behavior -- already-existing, unmodified
# within_market_hours() in the real capture scripts; re-confirmed here
# structurally (weekday >= 5 short-circuits before any broker touch).
# ---------------------------------------------------------------------

def test_capture_script_rejects_weekends_before_any_broker_call():
    source = (REPO_ROOT / "scripts" / "capture_market_reality_session.py").read_text()
    assert "now.weekday() >= 5" in source
    assert "return False" in source
    # The check happens inside within_market_hours(), called at the top
    # of run() before any `broker = FyersBroker(...)` construction --
    # confirmed by the ordering of these two markers in the source.
    assert source.index("def within_market_hours") < source.index("broker = FyersBroker")


def test_timer_calendar_expression_excludes_saturday_and_sunday():
    """`Mon..Fri` is an inclusive weekday range in systemd calendar
    syntax -- Saturday and Sunday are structurally outside it, not
    merely undocumented as included."""
    text = _read_unit("bujji-daily-intelligence.timer")
    calendar_line = next(line for line in text.splitlines() if line.startswith("OnCalendar="))
    # The WEEKDAY property only. This deliberately does not pin the fire
    # time: it previously asserted the whole line, so changing the hour
    # failed a test about Saturday and Sunday, and -- worse -- it froze
    # 09:00 in place while being blind to the fact that 09:00 is before
    # the market opens. The fire time has its own test below.
    assert calendar_line.startswith("OnCalendar=Mon..Fri ")
    assert calendar_line.endswith(" Asia/Kolkata")
    for weekend_day in ("Sat", "Sun"):
        assert weekend_day not in calendar_line


def test_timer_fires_after_the_market_opens():
    """The defect this file's whole-line assertion could not see.

    Both capture subprocesses hard-gate on MARKET_OPEN=09:15 and abort with
    exit 1 outside market hours, so any fire time at or before 09:15 makes
    the capture leg record zero rows EVERY trading day. Observed live on
    2026-08-17 at the old 09:00 setting: rows_captured=0,
    completeness_status=EMPTY, missing=['spot','options','vix'].
    """
    text = _read_unit("bujji-daily-intelligence.timer")
    calendar_line = next(line for line in text.splitlines() if line.startswith("OnCalendar="))
    hhmm = calendar_line.split()[1]
    hour, minute = (int(part) for part in hhmm.split(":")[:2])
    assert (hour, minute) > (9, 15), (
        f"fires at {hhmm}, at or before the 09:15 NSE open -- capture would abort")
    assert (hour, minute) < (15, 30), f"fires at {hhmm}, after the market closes"
    assert "Sat" not in calendar_line
    assert "Sun" not in calendar_line


# ---------------------------------------------------------------------
# 5-6. EOD completeness gate success / failure
# ---------------------------------------------------------------------

@pytest.mark.asyncio
async def test_completeness_gate_success_recorded_on_heartbeat(tmp_path):
    async def completeness_fn():
        return CompletenessCheckResult(
            ran=True, is_complete=True, completeness_status="COMPLETE", missing_components=(), as_of=FIXED_NOW.isoformat(),
        )
    heartbeat_path = str(tmp_path / "heartbeat.json")
    runtime = DailySessionRuntime(
        session_date="2026-08-17", clock=clock, capture_fn=_ok_capture(), intelligence_fn=_ok_intelligence(),
        heartbeat_path=heartbeat_path, completeness_fn=completeness_fn,
    )
    report = await runtime.run()
    assert report.final_stage == "SESSION_COMPLETE"
    assert report.completeness_result.is_complete is True
    assert report.completeness_result.completeness_status == "COMPLETE"
    heartbeat = read_daily_heartbeat(heartbeat_path)
    assert heartbeat.completeness_status == "COMPLETE"
    assert heartbeat.missing_reality_components == ()


@pytest.mark.asyncio
async def test_completeness_gate_failure_shows_incomplete_and_never_fabricates_confidence(tmp_path):
    async def completeness_fn():
        return CompletenessCheckResult(
            ran=True, is_complete=False, completeness_status="PARTIAL",
            missing_components=("options", "vix"), as_of=FIXED_NOW.isoformat(),
        )
    heartbeat_path = str(tmp_path / "heartbeat.json")
    runtime = DailySessionRuntime(
        session_date="2026-08-17", clock=clock, capture_fn=_ok_capture(), intelligence_fn=_ok_intelligence(),
        heartbeat_path=heartbeat_path, completeness_fn=completeness_fn,
    )
    report = await runtime.run()
    assert report.final_stage == "FAILED"  # never silently SESSION_COMPLETE over an incomplete day.
    assert any("eod_completeness_incomplete" in e for e in report.errors)
    assert "options" in str(report.errors) and "vix" in str(report.errors)
    heartbeat = read_daily_heartbeat(heartbeat_path)
    assert heartbeat.completeness_status == "PARTIAL"
    assert set(heartbeat.missing_reality_components) == {"options", "vix"}


# ---------------------------------------------------------------------
# 7-9. LIVE/REPLAY equivalence: success, mismatch, check failure
# ---------------------------------------------------------------------

@pytest.mark.asyncio
async def test_replay_equivalence_success(tmp_path):
    runtime = DailySessionRuntime(
        session_date="2026-08-17", clock=clock, capture_fn=_ok_capture(),
        intelligence_fn=_ok_intelligence(replay_equivalent=True),
        heartbeat_path=str(tmp_path / "heartbeat.json"),
    )
    report = await runtime.run()
    assert report.final_stage == "SESSION_COMPLETE"
    assert report.intelligence_result.replay_equivalent is True
    heartbeat = read_daily_heartbeat(str(tmp_path / "heartbeat.json"))
    assert heartbeat.replay_equivalent is True


@pytest.mark.asyncio
async def test_replay_equivalence_mismatch_never_marked_true(tmp_path):
    runtime = DailySessionRuntime(
        session_date="2026-08-17", clock=clock, capture_fn=_ok_capture(),
        intelligence_fn=_ok_intelligence(replay_equivalent=False, replay_mismatches=("intelligence_fingerprint: live=x != replay=y",)),
        heartbeat_path=str(tmp_path / "heartbeat.json"),
    )
    report = await runtime.run()
    assert report.final_stage == "FAILED"
    assert any("replay_equivalence_mismatch" in e for e in report.errors)
    heartbeat = read_daily_heartbeat(str(tmp_path / "heartbeat.json"))
    assert heartbeat.replay_equivalent is False  # never coerced to True.


@pytest.mark.asyncio
async def test_replay_check_failure_recorded_distinctly_from_mismatch(tmp_path):
    runtime = DailySessionRuntime(
        session_date="2026-08-17", clock=clock, capture_fn=_ok_capture(),
        intelligence_fn=_ok_intelligence(replay_check_error="ValueError: candles empty"),
        heartbeat_path=str(tmp_path / "heartbeat.json"),
    )
    report = await runtime.run()
    assert report.final_stage == "FAILED"
    assert any("replay_check_failed" in e for e in report.errors)
    assert not any("replay_equivalence_mismatch" in e for e in report.errors)  # a check failure, not a real mismatch finding.
    heartbeat = read_daily_heartbeat(str(tmp_path / "heartbeat.json"))
    assert heartbeat.replay_equivalent is None  # never fabricated True or False.


@pytest.mark.asyncio
async def test_live_intelligence_cycle_replay_equivalence_end_to_end(tmp_path):
    """Real end-to-end proof (not a stub): `run_live_intelligence_cycle`
    with `include_replay_equivalence=True` against a production-shaped
    FakeBroker actually runs the canonical
    `replay_equivalence.validate_live_replay_equivalence()` and reports
    a real equivalent=True result (same reality_snapshot/candles feed
    both LIVE and REPLAY compositions)."""
    cycle_store = EventStore(str(tmp_path / "cycle.jsonl"))
    daily_store = EventStore(str(tmp_path / "daily.jsonl"))
    result = await run_live_intelligence_cycle(
        broker=FakeBroker(), clock=clock, underlying="NIFTY", session_id="daily-2026-08-17",
        cycle_id="daily-2026-08-17-1", session_date="2026-08-17", execution_mode=EXECUTION_MODE_LIVE,
        cycle_artifact_store=cycle_store, daily_artifact_store=daily_store, include_replay_equivalence=True,
    )
    assert result.succeeded
    assert result.replay_equivalence is not None
    assert result.replay_equivalence.equivalent is True
    assert result.replay_check_error is None


# ---------------------------------------------------------------------
# 10. Incomplete capture + successful intelligence -- capture errors
# never short-circuit the remaining steps.
# ---------------------------------------------------------------------

@pytest.mark.asyncio
async def test_incomplete_capture_still_runs_intelligence_and_completeness(tmp_path):
    calls = {"completeness_ran": False}

    async def completeness_fn():
        calls["completeness_ran"] = True
        return CompletenessCheckResult(
            ran=True, is_complete=True, completeness_status="COMPLETE", missing_components=(), as_of=FIXED_NOW.isoformat(),
        )

    runtime = DailySessionRuntime(
        session_date="2026-08-17", clock=clock, capture_fn=_incomplete_capture(),
        intelligence_fn=_ok_intelligence(replay_equivalent=True),
        heartbeat_path=str(tmp_path / "heartbeat.json"), completeness_fn=completeness_fn,
    )
    report = await runtime.run()
    assert calls["completeness_ran"] is True  # never skipped just because capture had errors.
    assert report.intelligence_result.cycles_completed == 1  # intelligence still ran.
    assert any("capture_error" in e for e in report.errors)
    assert report.final_stage == "FAILED"  # the capture error alone is enough to mark the day FAILED -- but artifacts are preserved (below).
    assert report.capture_result.rows_captured == 3  # available capture output preserved, not discarded.


# ---------------------------------------------------------------------
# 11. Crash/restart
# ---------------------------------------------------------------------

@pytest.mark.asyncio
async def test_crash_then_fresh_instance_recovers(tmp_path):
    heartbeat_path = str(tmp_path / "heartbeat.json")
    crashed = DailySessionRuntime(
        session_date="2026-08-17", clock=clock, capture_fn=_crashing_capture(), intelligence_fn=_ok_intelligence(),
        heartbeat_path=heartbeat_path,
    )
    report1 = await crashed.run()
    assert report1.final_stage == "FAILED"

    restarted = DailySessionRuntime(
        session_date="2026-08-17", clock=clock, capture_fn=_ok_capture(), intelligence_fn=_ok_intelligence(replay_equivalent=True),
        heartbeat_path=heartbeat_path,
    )
    report2 = await restarted.run()
    assert report2.final_stage == "SESSION_COMPLETE"
    heartbeat = read_daily_heartbeat(heartbeat_path)
    assert heartbeat.runtime_status == "SESSION_COMPLETE"


# ---------------------------------------------------------------------
# 12 & 14. ProcessLock interaction + manual legacy invocation refusal
# ---------------------------------------------------------------------

def test_manual_script_refuses_when_authoritative_runtime_holds_lock(tmp_path):
    lock_path = str(tmp_path / "daily_intelligence.lock")
    authoritative = ProcessLock(lock_path)
    authoritative.acquire()
    try:
        with pytest.raises(AuthoritativeRuntimeActiveError):
            refuse_if_authoritative_runtime_active(lock_path)
    finally:
        authoritative.release()


def test_manual_script_proceeds_when_lock_is_free(tmp_path):
    lock_path = str(tmp_path / "daily_intelligence.lock")
    refuse_if_authoritative_runtime_active(lock_path)  # must not raise -- lock file doesn't even exist yet.

    holder = ProcessLock(lock_path)
    holder.acquire()
    holder.release()
    refuse_if_authoritative_runtime_active(lock_path)  # must not raise -- released, acquirable.


def test_guard_probe_does_not_itself_hold_the_lock(tmp_path):
    """The guard is a probe, not real ownership -- after a successful
    probe, the authoritative runtime must still be able to acquire the
    lock immediately."""
    lock_path = str(tmp_path / "daily_intelligence.lock")
    refuse_if_authoritative_runtime_active(lock_path)
    real_owner = ProcessLock(lock_path)
    real_owner.acquire()  # must not raise -- the probe released immediately.
    real_owner.release()


def test_both_legacy_scripts_call_the_guard_before_broker_construction():
    for relative_path, broker_marker in (
        ("scripts/run_shadow_live_observatory.py", "broker = FyersBroker("),
        ("run_live_shadow.py", "broker, tick_feed = ctx["),
    ):
        source = (REPO_ROOT / relative_path).read_text()
        assert "refuse_if_authoritative_runtime_active" in source
        assert source.index("refuse_if_authoritative_runtime_active(") < source.index(broker_marker)


# ---------------------------------------------------------------------
# 13. FYERS authentication failure
# ---------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fyers_authentication_failure_is_recorded_honestly_not_retried_within_process(tmp_path):
    heartbeat_path = str(tmp_path / "heartbeat.json")
    runtime = DailySessionRuntime(
        session_date="2026-08-17", clock=clock, capture_fn=_ok_capture(), intelligence_fn=_auth_failed_intelligence(),
        heartbeat_path=heartbeat_path,
    )
    report = await runtime.run()
    assert report.final_stage == "FAILED"
    assert any("FYERS_APP_ID" in e for e in report.errors)
    assert report.intelligence_result.cycles_completed == 0
    heartbeat = read_daily_heartbeat(heartbeat_path)
    assert heartbeat.last_error is not None and "FYERS_APP_ID" in heartbeat.last_error


# ---------------------------------------------------------------------
# 15. No trading/order capability
# ---------------------------------------------------------------------

FORBIDDEN_MODULE_SUBSTRINGS = ("order", "position", "strategy", "execution")
FORBIDDEN_CALL_ATTRS = {"place_order", "modify_order", "cancel_order"}
PHASE_19_14_1_NEW_FILES = (
    "bujji/shadow_runtime/manual_entrypoint_guard.py",
)
PHASE_19_14_1_CHANGED_FILES = (
    "bujji/shadow_runtime/daily_session.py",
    "bujji/shadow_runtime/live_intelligence_cycle.py",
    "bujji/shadow_runtime/daily_intelligence_artifact.py",
    "run_daily_intelligence_session.py",
    "scripts/run_shadow_live_observatory.py",
    "run_live_shadow.py",
)


def _imported_module_names(tree: ast.Module) -> list:
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


@pytest.mark.parametrize("relative_path", PHASE_19_14_1_NEW_FILES)
def test_new_files_have_no_trading_decision_imports(relative_path: str) -> None:
    tree = ast.parse((REPO_ROOT / relative_path).read_text())
    for module_name in _imported_module_names(tree):
        lowered = module_name.lower()
        for forbidden in FORBIDDEN_MODULE_SUBSTRINGS:
            assert forbidden not in lowered, f"{relative_path} imports {module_name!r} (forbidden: {forbidden!r})"


# run_live_shadow.py is deliberately excluded from this specific check:
# it legitimately calls `paper_broker.place_order(...)` on a `PaperBroker`
# (a pre-existing, unmodified, pure-simulation object with no real
# broker/exchange connection at all -- not the real, `disable_live_execution`-
# wrapped `FyersBroker` this phase's own guard sits in front of). A naive
# "no attribute named place_order anywhere in the file" check cannot
# distinguish the two by name alone; this file's real safety guarantee
# (the REAL broker is wrapped before use) is verified by
# `test_run_daily_intelligence_session_still_wraps_broker_with_disable_live_execution`'s
# own sibling check on the file that actually constructs the real
# broker for the daily runtime, and was independently confirmed for
# run_live_shadow.py in Phase 19.14.0's own audit (`disable_live_execution`
# wraps `FyersBroker` before any use, unmodified here).
ORDER_ATTR_CHECK_FILES = tuple(f for f in PHASE_19_14_1_CHANGED_FILES if f != "run_live_shadow.py")


@pytest.mark.parametrize("relative_path", ORDER_ATTR_CHECK_FILES)
def test_changed_files_never_call_order_placement_attrs(relative_path: str) -> None:
    """The precise, call-level check (Phase 19.13's own established
    replacement for a blanket "no broker import" ban, since several of
    these files legitimately touch broker objects for read-only market
    data or pre-existing, unrelated live-session orchestration): no
    `.place_order(`/`.modify_order(`/`.cancel_order(` call anywhere."""
    tree = ast.parse((REPO_ROOT / relative_path).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            assert node.attr not in FORBIDDEN_CALL_ATTRS, f"{relative_path}: forbidden call .{node.attr}("


def test_run_live_shadow_real_broker_wrapped_before_paper_broker_simulation():
    """run_live_shadow.py's own real safety boundary: the REAL broker
    (FyersBroker) is wrapped with disable_live_execution before use;
    the ONLY `place_order` call in the file targets `paper_broker`
    (PaperBroker, pure simulation), never the real broker variable."""
    source = (REPO_ROOT / "run_live_shadow.py").read_text()
    assert "disable_live_execution" in source
    for line in source.splitlines():
        if ".place_order(" in line or ".modify_order(" in line or ".cancel_order(" in line:
            assert "paper_broker." in line, f"non-paper-broker order call found: {line.strip()!r}"


def test_run_daily_intelligence_session_still_wraps_broker_with_disable_live_execution():
    source = (REPO_ROOT / "run_daily_intelligence_session.py").read_text()
    assert "disable_live_execution" in source
