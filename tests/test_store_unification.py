"""CP-D: live spot and VIX reach the store their consumers actually read.

Bujji keeps observations in two places. layer0_data/raw_observations.jsonl is
the market-reality capture's append-only Layer 0 record; the normalized
SQLite store is what EOD completeness, backfill and every lookback consumer
read. The capture wrote only to Layer 0, so completeness reported VIX MISSING
every evening while real live VIX rows sat on disk the whole time -- 168,157
backfilled VIX rows in the store and ZERO live ones, measured.

The fix is a second, additive projection. These tests pin what it writes,
what it refuses to write, and -- as much as anything -- what it deliberately
leaves alone.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

_spec = importlib.util.spec_from_file_location(
    "capture_market_reality", REPO_ROOT / "scripts" / "capture_market_reality_session.py")
capture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(capture)

from bujji.historical_reality.store import HistoricalObservationStore
from bujji.market_reality import taxonomy as reality_taxonomy

TS = "2026-08-20T09:15:01+05:30"


def _store(tmp_path):
    return HistoricalObservationStore(str(tmp_path / "hist.db"))


def _rows(store, identity):
    import sqlite3

    conn = sqlite3.connect(store._path if hasattr(store, "_path") else store.path)
    return list(conn.execute(
        "SELECT instrument_identity, instrument_type, resolution, source FROM "
        "historical_observations WHERE instrument_identity = ?", (identity,)))


class TestVixFinallyReachesTheStore:
    def test_a_certified_vix_sample_is_written(self, tmp_path):
        store = _store(tmp_path)
        ok = capture._write_normalized(
            store, kind="vix", identity="NSE:INDIAVIX-INDEX", value=12.85,
            capture_timestamp=TS, cert_status=reality_taxonomy.CERTIFIED_AVAILABLE,
            cert_ref="fyers_india_vix_certification_20260813.json@x")
        assert ok is True
        rows = _rows(store, "NSE:INDIAVIX-INDEX")
        assert len(rows) == 1
        assert rows[0][1] == "INDEX"

    def test_the_vix_identity_matches_the_backfills_own_series(self):
        """Live and backfilled rows must land on ONE series -- a second
        identity would make every lookback silently half-blind."""
        assert capture._NORMALIZED_IDENTITY["vix"][0] == "NSE:INDIAVIX-INDEX"

    def test_the_spot_identity_matches_the_backfills_own_series(self):
        assert capture._NORMALIZED_IDENTITY["spot"][0] == "NSE:NIFTY50-INDEX"

    def test_sixty_second_samples_are_not_labelled_as_five_minute_bars(self, tmp_path):
        """The chain capture writes 5-minute spot rows for the SAME
        instrument. Labelling these 60s samples the same way would both
        misdescribe them and collide in the store's natural key."""
        store = _store(tmp_path)
        capture._write_normalized(
            store, kind="spot", identity="NSE:NIFTY50-INDEX", value=24100.0,
            capture_timestamp=TS, cert_status=reality_taxonomy.CERTIFIED_AVAILABLE,
            cert_ref="x")
        assert _rows(store, "NSE:NIFTY50-INDEX")[0][2] == "ONE_MINUTE"


class TestItRefusesToWriteWhatItShouldNot:
    def test_an_uncertified_source_never_reaches_the_store(self, tmp_path):
        """The store feeds decision-making and completeness. An uncertified
        source must not enter it -- the same gate the Layer 0 write applies."""
        store = _store(tmp_path)
        ok = capture._write_normalized(
            store, kind="vix", identity="NSE:INDIAVIX-INDEX", value=12.85,
            capture_timestamp=TS, cert_status="CERTIFICATION_MISSING", cert_ref=None)
        assert ok is False and _rows(store, "NSE:INDIAVIX-INDEX") == []

    def test_a_missing_value_is_not_written_as_anything(self, tmp_path):
        store = _store(tmp_path)
        ok = capture._write_normalized(
            store, kind="vix", identity="NSE:INDIAVIX-INDEX", value=None,
            capture_timestamp=TS, cert_status=reality_taxonomy.CERTIFIED_AVAILABLE,
            cert_ref="x")
        assert ok is False and _rows(store, "NSE:INDIAVIX-INDEX") == []

    def test_no_store_is_a_no_op_not_a_crash(self):
        assert capture._write_normalized(
            None, kind="vix", identity="NSE:INDIAVIX-INDEX", value=12.85,
            capture_timestamp=TS, cert_status=reality_taxonomy.CERTIFIED_AVAILABLE,
            cert_ref="x") is False

    def test_a_store_failure_never_propagates(self):
        """Layer 0 is the source of truth. A failure in this secondary
        projection must not take down a live capture session."""
        class _Exploding:
            def write(self, obs):
                raise RuntimeError("disk gone")

        assert capture._write_normalized(
            _Exploding(), kind="spot", identity="NSE:NIFTY50-INDEX", value=24100.0,
            capture_timestamp=TS, cert_status=reality_taxonomy.CERTIFIED_AVAILABLE,
            cert_ref="x") is False


class TestFuturesIsDeliberatelyLeftAlone:
    def test_futures_is_not_projected(self):
        """The backfill's futures series is NIFTY_FUT_CONTINUOUS -- a ROLLED
        synthetic contract. Writing a live near-month quote under that name
        would assert a splice this code cannot justify; writing it under its
        real contract identity would create a third series no consumer
        reads. Both are worse than the honest status quo, so this stays an
        operator data-modelling decision rather than a silent choice here."""
        assert "futures" not in capture._NORMALIZED_IDENTITY

    def test_the_exclusion_is_documented_where_someone_would_look(self):
        source = (REPO_ROOT / "scripts" / "capture_market_reality_session.py").read_text()
        assert "NIFTY_FUT_CONTINUOUS" in source, (
            "the futures exclusion must state its reason in the code, or the next "
            "person will 'fix' it by inventing a series")


class TestLayerZeroIsUntouched:
    def test_the_layer0_write_still_happens_first_and_unconditionally(self):
        """This phase adds a projection; it must not have made the Layer 0
        record conditional on the projection succeeding."""
        import inspect

        for fn in (capture._capture_spot, capture._capture_vix):
            body = inspect.getsource(fn)
            layer0 = body.index("store.append(")
            projection = body.index("_write_normalized(")
            assert layer0 < projection, f"{fn.__name__}: Layer 0 must be written first"

    def test_futures_capture_takes_no_projection_argument_at_all(self):
        import inspect

        assert "normalized" not in inspect.signature(capture._capture_futures).parameters
