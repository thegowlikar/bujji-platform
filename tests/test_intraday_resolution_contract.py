"""Intraday snapshots read BOTH honest series, and relabel neither.

THE REGRESSION THIS PINS (2026-08-20, first live continuous session).
`capture_market_reality_session.py` writes 60-second point samples as
ONE_MINUTE -- correctly, because they are not 5-minute bars and because the
distinct resolution is what keeps them from colliding with the chain
capture's FIVE_MINUTE rows in the store's natural key. Every intraday
builder queried FIVE_MINUTE alone. Spot survived only via the chain's
5-minute spot sentinel; VIX has no sentinel, so 383 certified VIX
observations were invisible, `missing=['vix']` was reported, and a fully
successful 6h25m session exited 1.

These tests assert the reader now sees both series -- and that nothing
which worked before changed.
"""
from __future__ import annotations

import datetime
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from bujji.historical_reality.capture import build_historical_observation
from bujji.historical_reality.store import HistoricalObservationStore
from bujji.market_observation import taxonomy as moc_taxonomy
from bujji.market_reality import taxonomy as reality_taxonomy
from bujji.market_reality_snapshot import builder as B
from bujji.market_reality_snapshot.models import RESOLUTION_FIVE_MINUTE
from bujji.shadow_runtime.completeness import validate_end_of_day_completeness

DAY = "2026-08-20"
AS_OF = f"{DAY}T15:30:00+05:30"
NOW = datetime.datetime.fromisoformat(f"{DAY}T16:00:00+05:30")


def _store(tmp_path) -> HistoricalObservationStore:
    return HistoricalObservationStore(str(tmp_path / "obs.db"))


def _write(store, identity, instrument_type, resolution, timestamp, payload):
    obs = build_historical_observation(
        instrument_identity=identity, instrument_type=instrument_type,
        resolution=resolution, timestamp=timestamp, payload=payload, source="fyers",
        access_method="rest", value_kind=moc_taxonomy.VALUE_KIND_MAPPING,
        source_epoch=int(datetime.datetime.fromisoformat(timestamp).timestamp()),
        source_symbol=identity, raw_artifact_ref="",
        ingestion_run_id=f"TEST-{identity}-{timestamp}", retrieved_at=timestamp,
        certification_status=reality_taxonomy.CERTIFIED_AVAILABLE,
        certification_ref="test_cert.json@t",
    )
    store.write(obs)
    return obs


class TestThePointSampleIsSeen:
    def test_vix_one_minute_point_sample_is_found(self, tmp_path):
        """The exact row shape that vanished on 2026-08-20."""
        s = _store(tmp_path)
        _write(s, B.VIX_SYMBOL, "INDEX", moc_taxonomy.RESOLUTION_ONE_MINUTE,
               f"{DAY}T09:15:00.866135+05:30", {"ltp": 11.42})
        snap = B.build_market_reality_snapshot(
            DAY, historical_store=s, now=NOW,
            resolution=RESOLUTION_FIVE_MINUTE, as_of_time=AS_OF)
        assert snap.vix is not None
        assert snap.vix.close == 11.42

    def test_a_point_sample_becomes_a_degenerate_bar_not_an_invented_range(self, tmp_path):
        """o=h=l=c asserts one price at one instant. A wider range would be
        fabricated data, which is the thing this codebase forbids."""
        s = _store(tmp_path)
        _write(s, B.VIX_SYMBOL, "INDEX", moc_taxonomy.RESOLUTION_ONE_MINUTE,
               f"{DAY}T09:15:00+05:30", {"ltp": 11.42})
        v = B.build_market_reality_snapshot(
            DAY, historical_store=s, now=NOW,
            resolution=RESOLUTION_FIVE_MINUTE, as_of_time=AS_OF).vix
        assert v.open == v.high == v.low == v.close == 11.42

    def test_spot_point_sample_is_found_without_a_five_minute_sentinel(self, tmp_path):
        """Spot previously survived only by accident of the chain sentinel."""
        s = _store(tmp_path)
        _write(s, B.SPOT_SYMBOL, "SPOT", moc_taxonomy.RESOLUTION_ONE_MINUTE,
               f"{DAY}T09:15:00+05:30", {"ltp": 24416.2})
        snap = B.build_market_reality_snapshot(
            DAY, historical_store=s, now=NOW,
            resolution=RESOLUTION_FIVE_MINUTE, as_of_time=AS_OF)
        assert snap.spot is not None and snap.spot.close == 24416.2


class TestTheEodGateNoLongerFailsAGoodSession:
    def test_vix_present_from_one_minute_rows_alone(self, tmp_path):
        """The whole failure chain, end to end: point samples -> vix_present."""
        s = _store(tmp_path)
        _write(s, B.SPOT_SYMBOL, "SPOT", moc_taxonomy.RESOLUTION_ONE_MINUTE,
               f"{DAY}T09:15:00+05:30", {"ltp": 24416.2})
        _write(s, B.VIX_SYMBOL, "INDEX", moc_taxonomy.RESOLUTION_ONE_MINUTE,
               f"{DAY}T09:15:00+05:30", {"ltp": 11.42})
        _write(s, "NIFTY|2026-08-25|24400|CE", "OPTION", RESOLUTION_FIVE_MINUTE,
               f"{DAY}T09:15:02+05:30", {"ltp": 120.5, "bid": 120.0, "ask": 121.0})
        r = validate_end_of_day_completeness(
            DAY, historical_store=s, now=NOW,
            resolution=RESOLUTION_FIVE_MINUTE, as_of_time=AS_OF)
        assert r.vix_present is True
        assert r.is_complete is True


class TestNothingThatWorkedBeforeChanged:
    def test_a_five_minute_bar_still_wins_and_keeps_its_real_range(self, tmp_path):
        """Bars must not be degraded into degenerate bars."""
        s = _store(tmp_path)
        _write(s, B.VIX_SYMBOL, "INDEX", RESOLUTION_FIVE_MINUTE,
               f"{DAY}T09:20:00+05:30",
               {"open": 11.0, "high": 11.9, "low": 10.8, "close": 11.5})
        v = B.build_market_reality_snapshot(
            DAY, historical_store=s, now=NOW,
            resolution=RESOLUTION_FIVE_MINUTE, as_of_time=AS_OF).vix
        assert (v.open, v.high, v.low, v.close) == (11.0, 11.9, 10.8, 11.5)

    def test_on_an_exact_tie_the_bar_beats_the_point_sample(self, tmp_path):
        """A bar carries range information a point sample does not."""
        s = _store(tmp_path)
        ts = f"{DAY}T09:20:00+05:30"
        _write(s, B.VIX_SYMBOL, "INDEX", moc_taxonomy.RESOLUTION_ONE_MINUTE, ts, {"ltp": 99.0})
        _write(s, B.VIX_SYMBOL, "INDEX", RESOLUTION_FIVE_MINUTE, ts,
               {"open": 11.0, "high": 11.9, "low": 10.8, "close": 11.5})
        v = B.build_market_reality_snapshot(
            DAY, historical_store=s, now=NOW,
            resolution=RESOLUTION_FIVE_MINUTE, as_of_time=AS_OF).vix
        assert v.close == 11.5, "the bar must win the tie, not the point sample"

    def test_the_most_recent_row_wins_across_series(self, tmp_path):
        s = _store(tmp_path)
        _write(s, B.VIX_SYMBOL, "INDEX", RESOLUTION_FIVE_MINUTE,
               f"{DAY}T09:20:00+05:30",
               {"open": 11.0, "high": 11.9, "low": 10.8, "close": 11.5})
        _write(s, B.VIX_SYMBOL, "INDEX", moc_taxonomy.RESOLUTION_ONE_MINUTE,
               f"{DAY}T14:00:00+05:30", {"ltp": 12.75})
        v = B.build_market_reality_snapshot(
            DAY, historical_store=s, now=NOW,
            resolution=RESOLUTION_FIVE_MINUTE, as_of_time=AS_OF).vix
        assert v.close == 12.75, "the later point sample is the more recent truth"

    def test_no_look_ahead_a_later_row_is_never_read(self, tmp_path):
        """as_of_time is a hard boundary -- the store filters before we sort."""
        s = _store(tmp_path)
        _write(s, B.VIX_SYMBOL, "INDEX", moc_taxonomy.RESOLUTION_ONE_MINUTE,
               f"{DAY}T09:20:00+05:30", {"ltp": 11.42})
        _write(s, B.VIX_SYMBOL, "INDEX", moc_taxonomy.RESOLUTION_ONE_MINUTE,
               f"{DAY}T15:29:00+05:30", {"ltp": 99.99})
        v = B.build_market_reality_snapshot(
            DAY, historical_store=s, now=NOW, resolution=RESOLUTION_FIVE_MINUTE,
            as_of_time=f"{DAY}T10:00:00+05:30").vix
        assert v.close == 11.42


class TestAbsenceStaysAbsent:
    def test_no_rows_at_all_is_absent_not_zero(self, tmp_path):
        s = _store(tmp_path)
        snap = B.build_market_reality_snapshot(
            DAY, historical_store=s, now=NOW,
            resolution=RESOLUTION_FIVE_MINUTE, as_of_time=AS_OF)
        assert snap.vix is None and snap.spot is None

    def test_an_unknown_payload_shape_is_absent_not_invented(self, tmp_path):
        """Neither a bar nor a point sample -- absent beats invented."""
        s = _store(tmp_path)
        _write(s, B.VIX_SYMBOL, "INDEX", moc_taxonomy.RESOLUTION_ONE_MINUTE,
               f"{DAY}T09:15:00+05:30", {"something_else": 1.0})
        snap = B.build_market_reality_snapshot(
            DAY, historical_store=s, now=NOW,
            resolution=RESOLUTION_FIVE_MINUTE, as_of_time=AS_OF)
        assert snap.vix is None


class TestTheWriterContractIsNotFlattened:
    def test_one_minute_is_not_a_callable_snapshot_mode(self):
        """ONE_MINUTE is a resolution rows are STORED at, never a mode a
        caller selects. Adding it to ALL_RESOLUTIONS would let a caller ask
        for a snapshot mode the builder does not implement."""
        assert moc_taxonomy.RESOLUTION_ONE_MINUTE not in B.ALL_RESOLUTIONS

    def test_the_capture_session_still_writes_one_minute(self):
        """If the writer is ever 'fixed' to say FIVE_MINUTE, it would be
        asserting a bar nobody observed AND would collide in the natural
        key. This test fails first if someone tries."""
        src = (REPO_ROOT / "scripts" / "capture_market_reality_session.py").read_text()
        assert "RESOLUTION_ONE_MINUTE" in src
