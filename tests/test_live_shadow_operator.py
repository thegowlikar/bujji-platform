"""Tests for bujji.live_shadow_operator (Sprint 107)."""
import ast
import json
import shutil
import time
from pathlib import Path

import pytest

from bujji.core.process_lock import LockAcquisitionError, ProcessLock
from bujji.live_shadow_operator import (
    SHADOW_MODE, assert_shadow_safe, render_shadow_banner, LiveShadowOperator,
)
from bujji.live_shadow_operator.health import build_health_snapshot, render_health_dashboard
from bujji.live_shadow_operator.journal import OperatorJournal

DAY = "2026-05-25"
D = "20260525"


def _load_real_day():
    with open("/tmp/nifty_intraday_by_day_expanded.json") as f:
        candles = json.load(f)[DAY]
    with open(f"/tmp/m1/BhavCopy_NSE_FO_0_0_0_{D}_F_0000.csv") as f:
        bhav_text = f.read()
    return candles, bhav_text


def _run_one_day(lock_name, journal_dir, prior_closes=()):
    op = LiveShadowOperator(lock_path=f"data/{lock_name}.lock", journal_dir=str(journal_dir))
    op.acquire()
    op.authenticate(None)
    candles, bhav_text = _load_real_day()
    op.start_session(prior_closes_with_ts=prior_closes)
    op.load_option_chain(bhav_text, DAY)
    for c in candles:
        op.process_tick("NIFTY", c["ts"], c["close"], source="recorded_stream")
    last_ts = candles[-1]["ts"]
    spot = next((row.underlying_price for row in op._driver._chain if row.underlying_price is not None), None)
    cadence = op.run_cadence(day=DAY, spot=spot, timestamp=last_ts)
    outcome = op.end_of_day(DAY, [cadence])
    op.shutdown()
    return op, outcome, cadence


def test_shadow_mode_is_true_and_assertion_passes():
    assert SHADOW_MODE is True
    assert_shadow_safe()  # must not raise


def test_banner_declares_no_orders_can_be_sent():
    banner = render_shadow_banner()
    assert "SHADOW MODE" in banner
    assert "NO ORDERS CAN BE SENT" in banner


def test_package_never_imports_execution_or_broker_place_order_and_never_calls_it():
    """AST-based, per this project's own false-positive-safe convention
    (raw string search would trip on this very module's own docstrings
    explaining what is NOT imported)."""
    pkg_dir = Path("bujji/live_shadow_operator")
    forbidden_modules = {"bujji.execution", "fyers_apiv3.order"}
    for py_file in pkg_dir.glob("*.py"):
        tree = ast.parse(py_file.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not any(node.module.startswith(f) for f in forbidden_modules), (py_file, node.module)
            if isinstance(node, ast.Attribute) and node.attr in ("place_order", "submit_and_confirm"):
                pytest.fail(f"{py_file} references forbidden execution attribute {node.attr}")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("place_order", "submit_and_confirm"):
                pytest.fail(f"{py_file} calls forbidden execution function {node.func.id}")


def test_acquire_rejects_a_second_instance():
    op1 = LiveShadowOperator(lock_path="data/test_dup_op.lock", journal_dir="data/test_dup_op_journal")
    op1.acquire()
    op2 = LiveShadowOperator(lock_path="data/test_dup_op.lock", journal_dir="data/test_dup_op_journal2")
    with pytest.raises(LockAcquisitionError):
        op2.acquire()
    op1.shutdown()


def test_full_simulated_day_produces_a_real_decision_and_report():
    op, outcome, cadence = _run_one_day("test_full_day", "data/test_full_day_journal")
    assert outcome.decisions == 1
    assert cadence.decision is not None
    assert "End of Day Report" in outcome.report_text
    assert "Virtual P&L" in outcome.report_text


def test_restart_recovery_threads_prior_closes_across_processes():
    journal_dir = Path("data/test_restart_journal")
    if journal_dir.exists():
        shutil.rmtree(journal_dir)
    op1, outcome1, cadence1 = _run_one_day("test_restart_1", journal_dir)

    # Simulate a fresh process: a brand-new operator instance reads the
    # SAME on-disk journal to recover prior closes (Deliverable 6).
    op2 = LiveShadowOperator(lock_path="data/test_restart_2.lock", journal_dir=str(journal_dir))
    resumed = op2.resume_prior_closes()
    assert len(resumed) == 25  # exactly day 1's real tick count, recovered from disk


def test_duplicate_tick_never_produces_a_duplicate_decision():
    candles, bhav_text = _load_real_day()
    op = LiveShadowOperator(lock_path="data/test_dup_tick.lock", journal_dir="data/test_dup_tick_journal")
    op.acquire()
    op.start_session()
    op.load_option_chain(bhav_text, DAY)
    for c in candles:
        op.process_tick("NIFTY", c["ts"], c["close"])
    op.process_tick("NIFTY", candles[0]["ts"], candles[0]["close"])  # re-fed, must be dropped
    assert op._driver.result.dropped_ticks == 1
    last_ts = candles[-1]["ts"]
    spot = next((row.underlying_price for row in op._driver._chain if row.underlying_price is not None), None)
    cadence1 = op.run_cadence(day=DAY, spot=spot, timestamp=last_ts)
    cadence2 = op.run_cadence(day=DAY, spot=spot, timestamp=last_ts)
    # Re-running the cadence with identical inputs must produce the SAME
    # decision_id (content hash) -- not a second, distinct decision.
    assert cadence1.decision.decision_id == cadence2.decision.decision_id
    op.shutdown()


def test_health_snapshot_reflects_real_session_counters():
    op, outcome, cadence = _run_one_day("test_health", "data/test_health_journal")
    snapshot = build_health_snapshot(op._driver.result, reconnect_count=2, process_start_monotonic=0.0)
    assert snapshot.reconnect_count == 2
    assert snapshot.peak_memory_kb > 0
    text = render_health_dashboard(snapshot)
    assert "reconnect_count" in text
    assert "UNKNOWN (no authenticated broker session supplied)" in text


# ------------------------------------------------------------------------ #
# cadence_duration_seconds fix (TODO.md P2-4). Live-confirmed dead in
# Session #3 and LSQ-1 Day 1: previously reported total session uptime
# (elapsed hours) instead of a real per-cadence duration (sub-second).
# Root cause: close_cadence_metrics() computed
# `time.monotonic() - self._started_monotonic` -- session-start-to-now --
# rather than reading the real t2-t0 measurement run_cadence() already
# computes and journals correctly. Fixed by retaining that real value on
# the operator instance.
# ------------------------------------------------------------------------ #

def test_cadence_duration_seconds_reflects_real_per_cadence_time_not_session_uptime():
    op = LiveShadowOperator(
        lock_path="data/test_cadence_duration.lock", journal_dir="data/test_cadence_duration_journal"
    )
    op.acquire()
    op.authenticate(None)
    candles, bhav_text = _load_real_day()
    op.start_session()
    time.sleep(0.05)  # session uptime must end up meaningfully larger than one cadence's real duration
    op.load_option_chain(bhav_text, DAY)
    for c in candles:
        op.process_tick("NIFTY", c["ts"], c["close"], source="recorded_stream")
    last_ts = candles[-1]["ts"]
    spot = next((row.underlying_price for row in op._driver._chain if row.underlying_price is not None), None)
    op.run_cadence(day=DAY, spot=spot, timestamp=last_ts)

    metrics = op.close_cadence_metrics(DAY)
    session_uptime_seconds = time.monotonic() - op._started_monotonic

    assert metrics.cadence_duration_seconds > 0.0
    # The original bug: this metric equalled (approximately) session
    # uptime. The fix: it must be a real, much-smaller, single-cadence
    # measurement -- proven here by requiring it stay well under the
    # uptime accumulated since start_session(), not just "not exactly equal".
    assert metrics.cadence_duration_seconds < session_uptime_seconds / 2


def test_cadence_duration_seconds_is_zero_before_any_cadence_has_run():
    op = LiveShadowOperator(
        lock_path="data/test_cadence_duration_zero.lock",
        journal_dir="data/test_cadence_duration_zero_journal",
    )
    op.acquire()
    op.start_session()
    metrics = op.close_cadence_metrics(DAY)
    assert metrics.cadence_duration_seconds == 0.0


def test_journal_is_append_only_and_survives_reopen():
    journal_dir = Path("data/test_journal_append_only")
    if journal_dir.exists():
        shutil.rmtree(journal_dir)
    j1 = OperatorJournal(journal_dir)
    op, outcome, cadence = _run_one_day("test_append_1", journal_dir)
    j2 = OperatorJournal(journal_dir)  # fresh instance, same directory
    resumed = j2.read_last_closes_with_ts()
    assert len(resumed) == 25
