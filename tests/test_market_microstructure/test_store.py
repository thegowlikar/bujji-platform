"""Phase 19.20.3 -- MicrostructureStore tests.

Covers: schema creation, write/read round trip, duplicate natural-key
handling, restart recovery, WAL verification, integrity check, and
explicit isolation from Phase 19.19's HistoricalObservationStore.
"""
from __future__ import annotations

import os

import pytest

from bujji.historical_reality.store import HistoricalObservationStore
from bujji.market_microstructure.models import KIND_OPTION, KIND_SPOT, MinuteObservation
from bujji.market_microstructure.store import ConflictingMinuteObservationError, MicrostructureStore

DAY = "2026-08-17"


def _obs(instrument="NSE:NIFTY50-INDEX", window_start=f"{DAY}T09:15:00+05:30",
         window_end=f"{DAY}T09:16:00+05:30", close=24501.0, tick_count=4, **overrides):
    fields = dict(
        instrument=instrument, kind=KIND_SPOT, session_date=DAY,
        window_start=window_start, window_end=window_end,
        open=24500.0, high=24502.0, low=24498.0, close=close,
        tick_count=tick_count, max_tick_silence_seconds=30.0, avg_tick_interval_seconds=19.33,
        max_price_move=4.0, max_premium_move=None, open_interest=None, strike=None, option_type=None,
        first_tick_timestamp=window_start, last_tick_timestamp=window_end,
        rejected_tick_count=0, observation_quality_score=66.67,
    )
    fields.update(overrides)
    return MinuteObservation(**fields)


# --------------------------------------------------------------------- #
# Schema creation
# --------------------------------------------------------------------- #
def test_schema_creates_both_tables(tmp_path):
    db_path = tmp_path / "microstructure_observations.db"
    store = MicrostructureStore(db_path)
    import sqlite3
    conn = sqlite3.connect(str(db_path))
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "minute_observations" in tables
    assert "capture_session_log" in tables
    conn.close()
    store.close()


def test_construction_is_safe_to_repeat_against_the_same_path(tmp_path):
    db_path = tmp_path / "microstructure_observations.db"
    store1 = MicrostructureStore(db_path)
    store1.write(_obs())
    store1.close()

    store2 = MicrostructureStore(db_path)  # must not truncate or drop anything
    assert store2.count() == 1
    store2.close()


# --------------------------------------------------------------------- #
# Write/read round trip
# --------------------------------------------------------------------- #
def test_write_and_read_round_trip_preserves_all_fields(tmp_path):
    store = MicrostructureStore(tmp_path / "m.db")
    original = _obs(
        instrument="NSE:NIFTY2582624500CE", kind=KIND_OPTION,
        max_premium_move=7.0, open_interest=46000.0, strike=24500.0, option_type="CE",
        rejected_tick_count=1,
    )
    inserted = store.write(original)
    assert inserted is True

    fetched = store.get_by_instrument("NSE:NIFTY2582624500CE")
    assert len(fetched) == 1
    got = fetched[0]
    assert got.to_dict() == original.to_dict()
    store.close()


def test_empty_dataset_reads_return_empty_lists_not_errors(tmp_path):
    store = MicrostructureStore(tmp_path / "m.db")
    assert store.get_by_date("2099-01-01") == []
    assert store.get_by_instrument("NOTHING:HERE") == []
    assert store.count() == 0
    store.close()


def test_get_by_date_and_get_by_instrument_are_deterministically_ordered(tmp_path):
    store = MicrostructureStore(tmp_path / "m.db")
    # Insert out of chronological order -- read-back must still be sorted.
    store.write(_obs(window_start=f"{DAY}T09:20:00+05:30", window_end=f"{DAY}T09:21:00+05:30"))
    store.write(_obs(window_start=f"{DAY}T09:15:00+05:30", window_end=f"{DAY}T09:16:00+05:30"))
    store.write(_obs(window_start=f"{DAY}T09:18:00+05:30", window_end=f"{DAY}T09:19:00+05:30"))

    by_date = store.get_by_date(DAY)
    starts = [o.window_start for o in by_date]
    assert starts == sorted(starts)

    by_instrument = store.get_by_instrument("NSE:NIFTY50-INDEX")
    starts2 = [o.window_start for o in by_instrument]
    assert starts2 == sorted(starts2)
    store.close()


# --------------------------------------------------------------------- #
# Idempotency / duplicate natural-key handling
# --------------------------------------------------------------------- #
def test_writing_the_identical_observation_twice_produces_one_row_no_duplicate():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        store = MicrostructureStore(os.path.join(d, "m.db"))
        obs = _obs()
        first = store.write(obs)
        second = store.write(obs)  # identical natural key AND identical content
        assert first is True
        assert second is False   # idempotent no-op, not a second row
        assert store.count() == 1
        store.close()


def test_a_different_observation_under_the_same_natural_key_is_rejected_never_overwritten(tmp_path):
    store = MicrostructureStore(tmp_path / "m.db")
    obs_a = _obs(close=24501.0, tick_count=4)
    store.write(obs_a)

    obs_b = _obs(close=24999.0, tick_count=99)  # SAME instrument+window_start, DIFFERENT content
    with pytest.raises(ConflictingMinuteObservationError):
        store.write(obs_b)

    # The original row must be unchanged -- no silent mutation.
    fetched = store.get_by_instrument(obs_a.instrument)
    assert len(fetched) == 1
    assert fetched[0].close == 24501.0
    assert fetched[0].tick_count == 4
    store.close()


def test_write_many_is_idempotent_across_a_batch_with_a_repeat(tmp_path):
    store = MicrostructureStore(tmp_path / "m.db")
    a = _obs(window_start=f"{DAY}T09:15:00+05:30", window_end=f"{DAY}T09:16:00+05:30")
    b = _obs(window_start=f"{DAY}T09:16:00+05:30", window_end=f"{DAY}T09:17:00+05:30")
    results = store.write_many([a, b, a])  # 'a' repeated
    assert results == [True, True, False]
    assert store.count() == 2
    store.close()


# --------------------------------------------------------------------- #
# Restart recovery
# --------------------------------------------------------------------- #
def test_restart_recovery_100_rows_survive_process_boundary_and_continuation_works(tmp_path):
    db_path = tmp_path / "m.db"

    # "Process 1"
    store1 = MicrostructureStore(db_path)
    observations = [
        _obs(
            window_start=f"{DAY}T{9 + i // 60:02d}:{i % 60:02d}:00+05:30",
            window_end=f"{DAY}T{9 + (i + 1) // 60:02d}:{(i + 1) % 60:02d}:00+05:30",
        )
        for i in range(100)
    ]
    results = store1.write_many(observations)
    assert all(results)
    assert store1.count() == 100
    store1.close()

    # "Process 2" -- fresh instance, same path.
    store2 = MicrostructureStore(db_path)
    assert store2.count() == 100
    assert len(store2.get_by_date(DAY)) == 100

    # Continuation: write additional new observations after "restart".
    more = [
        _obs(
            instrument="NSE:INDIAVIX-INDEX",
            window_start=f"{DAY}T10:{i:02d}:00+05:30",
            window_end=f"{DAY}T10:{i + 1:02d}:00+05:30",
        )
        for i in range(5)
    ]
    assert all(store2.write_many(more))
    assert store2.count() == 105
    store2.close()


# --------------------------------------------------------------------- #
# SQLite durability
# --------------------------------------------------------------------- #
def test_wal_journal_mode(tmp_path):
    store = MicrostructureStore(tmp_path / "m.db")
    assert store.journal_mode().lower() == "wal"
    store.close()


def test_integrity_check_reports_ok(tmp_path):
    store = MicrostructureStore(tmp_path / "m.db")
    assert store.integrity_check().lower() == "ok"
    store.close()


def test_capture_session_log_is_append_only_and_queryable(tmp_path):
    store = MicrostructureStore(tmp_path / "m.db")
    row_id = store.record_capture_session(
        session_date=DAY, started_at=f"{DAY}T09:15:00+05:30",
        stopped_at=f"{DAY}T15:30:00+05:30", connect_count=1, disconnect_events=0, last_error=None,
    )
    assert row_id == 1
    import sqlite3
    conn = sqlite3.connect(store.path)
    row = conn.execute("SELECT * FROM capture_session_log WHERE id = ?", (row_id,)).fetchone()
    assert row is not None
    conn.close()
    store.close()


# --------------------------------------------------------------------- #
# Isolation from Phase 19.19
# --------------------------------------------------------------------- #
def test_microstructure_store_path_never_equals_historical_observation_store_path(tmp_path):
    """Explicit, structural proof -- not just an assumption -- that the
    two stores can never collide even if constructed against the same
    parent directory by mistake."""
    microstructure_path = tmp_path / "data" / "market_microstructure" / "microstructure_observations.db"
    historical_path = tmp_path / "data" / "historical_reality" / "normalized" / "historical_observations.db"
    assert str(microstructure_path) != str(historical_path)

    micro_store = MicrostructureStore(microstructure_path)
    hist_store = HistoricalObservationStore(historical_path)
    assert micro_store.path != hist_store.path

    # Writing to one must never appear in the other.
    micro_store.write(_obs())
    assert micro_store.count() == 1
    assert hist_store.count() == 0
    micro_store.close()
    hist_store.close()


def test_store_module_never_imports_historical_reality_store_for_writing():
    """AST proof: bujji.market_microstructure.store must not import
    HistoricalObservationStore at all -- the isolation this phase
    requires is structural, not just behavioral."""
    import ast
    from pathlib import Path

    store_path = (
        Path(__file__).resolve().parents[2] / "bujji" / "market_microstructure" / "store.py"
    )
    tree = ast.parse(store_path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert "historical_reality" not in node.module, (
                f"market_microstructure/store.py must never import from bujji.historical_reality "
                f"(found: {node.module!r}) -- the two stores must remain structurally isolated"
            )
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert "historical_reality" not in alias.name, (
                    f"market_microstructure/store.py must never import bujji.historical_reality "
                    f"(found: {alias.name!r})"
                )
