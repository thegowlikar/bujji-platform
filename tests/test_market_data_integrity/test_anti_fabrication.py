"""Phase 19.20.4 -- mandatory anti-fabrication proof.

Proves, structurally AND behaviorally, that market_data_integrity
never writes into MicrostructureStore or HistoricalObservationStore.
"""
from __future__ import annotations

import ast
from pathlib import Path

from bujji.historical_reality.store import HistoricalObservationStore
from bujji.market_data_integrity.eod_reconciliation import build_daily_integrity_report
from bujji.market_microstructure.models import KIND_SPOT, MinuteObservation
from bujji.market_microstructure.store import MicrostructureStore

DAY = "2026-08-17"
INSTRUMENT = "NSE:NIFTY50-INDEX"

PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "bujji" / "market_data_integrity"

_MUTATING_METHOD_NAMES = ("write", "write_many", "record_capture_session", "record_ingestion_run")


def test_no_mutating_store_method_call_appears_anywhere_in_the_source():
    """AST proof: scan every module for a call to any of the known
    write-side methods on either store's class, regardless of whether
    it is ever reached at runtime."""
    for path in sorted(PACKAGE_ROOT.glob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in _MUTATING_METHOD_NAMES, (
                    f"{path.name} calls {node.func.attr}() -- market_data_integrity must never "
                    f"write to any source store"
                )


def test_behavioral_proof_report_building_never_mutates_either_store(tmp_path):
    """Behavioral, not just structural: run the real report-building
    path against real store instances and prove their row counts are
    unchanged afterward."""
    micro_path = tmp_path / "microstructure_observations.db"
    hist_path = tmp_path / "historical_observations.db"

    micro_store = MicrostructureStore(micro_path)
    hist_store = HistoricalObservationStore(hist_path)

    ws = f"{DAY}T09:15:00+05:30"
    we = f"{DAY}T09:16:00+05:30"
    obs = MinuteObservation(
        instrument=INSTRUMENT, kind=KIND_SPOT, session_date=DAY, window_start=ws, window_end=we,
        open=24500.0, high=24500.0, low=24500.0, close=24500.0, tick_count=1,
        max_tick_silence_seconds=None, avg_tick_interval_seconds=None, max_price_move=0.0,
        first_tick_timestamp=ws, last_tick_timestamp=ws,
    )
    micro_store.write(obs)
    before_micro = micro_store.count()
    before_hist = hist_store.count()

    build_daily_integrity_report(
        session_date=DAY, instrument=INSTRUMENT, minute_observations=micro_store.get_by_instrument(INSTRUMENT),
        session_start_iso=f"{DAY}T09:15:00+05:30", session_end_iso=f"{DAY}T09:20:00+05:30",
        now_iso=f"{DAY}T23:59:59+05:30", capture_session_log_rows=micro_store.get_capture_sessions(DAY),
        five_minute_historical_rows=(), bhavcopy_row=None,
    )

    assert micro_store.count() == before_micro
    assert hist_store.count() == before_hist
    micro_store.close()
    hist_store.close()
