"""L-4: build the map once from finished bars; test it with live samples.

Two refusals carry this phase. Live 60-second point samples cannot FORM
structure (a pivot needs bars either side; a zone needs a range price traded
in) and cannot BREAK a zone (L-2 decides breaks on a close, precisely so a
wick through and back does not retire a zone on first contact -- and a point
sample is closer to a wick than to a close). What a sample can do is what it
is actually evidence of: price was here.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from bujji.price_levels import taxonomy
from bujji.price_levels.daily import (
    build_snapshot, latest_snapshot_path, read_snapshot_raw, snapshot_age_days, write_snapshot,
)
from bujji.price_levels.live import apply_sample, apply_sample_to_levels, apply_sample_to_zones
from bujji.price_levels.models import LevelSet, PriceLevel, SupplyDemandZone, ZoneSet
from bujji.price_levels.store_reader import load_bars

_SCHEMA = """
CREATE TABLE historical_observations (
    observation_id TEXT, instrument_identity TEXT, instrument_type TEXT,
    resolution TEXT, timestamp TEXT, source TEXT, payload TEXT,
    record TEXT, natural_key TEXT
)
"""


def _db(tmp_path, rows):
    path = str(tmp_path / "hist.db")
    conn = sqlite3.connect(path)
    conn.execute(_SCHEMA)
    for i, (ts, payload) in enumerate(rows):
        conn.execute(
            "INSERT INTO historical_observations VALUES (?,?,?,?,?,?,?,?,?)",
            (f"OBS{i}", "NSE:NIFTY50-INDEX", "SPOT", "FIVE_MINUTE", ts,
             "fyers_historical", json.dumps(payload), "{}", f"NK{i}"))
    conn.commit()
    conn.close()
    return path


def _ohlc(i, base=24000.0):
    return (f"2026-08-{10 + i // 20:02d}T{9 + i % 20:02d}:00:00+05:30",
            {"open": base, "high": base + 5, "low": base - 5, "close": base})


class TestTheReaderRefusesPointSamples:
    def test_live_point_samples_are_skipped_and_counted(self, tmp_path):
        """The store now holds both shapes for one instrument. Deriving a
        high from a single traded price would invent a value never observed."""
        rows = [_ohlc(0), _ohlc(1)]
        rows.append(("2026-08-20T09:15:01+05:30", {"ltp": 24100.0}))
        result = load_bars(db_path=_db(tmp_path, rows), instrument="NSE:NIFTY50-INDEX",
                           resolution="FIVE_MINUTE")
        assert len(result.bars) == 2
        assert result.skipped_not_a_bar == 1
        assert result.rows_seen == 3

    def test_the_skip_count_is_reported_not_swallowed(self, tmp_path):
        """If skips ever exceed bars, the caller is reading the wrong series
        and the levels would be thin with nothing looking wrong."""
        rows = [("T1", {"ltp": 1.0}), ("T2", {"ltp": 2.0}), _ohlc(0)]
        result = load_bars(db_path=_db(tmp_path, rows), instrument="NSE:NIFTY50-INDEX",
                           resolution="FIVE_MINUTE")
        assert result.skipped_not_a_bar > len(result.bars)
        assert result.to_dict()["skipped_not_a_bar"] == 2

    def test_the_before_cut_is_applied_in_sql(self, tmp_path):
        rows = [("2026-08-10T09:00:00+05:30", {"open": 1, "high": 2, "low": 0.5, "close": 1}),
                ("2026-08-20T09:00:00+05:30", {"open": 1, "high": 2, "low": 0.5, "close": 1})]
        result = load_bars(db_path=_db(tmp_path, rows), instrument="NSE:NIFTY50-INDEX",
                           resolution="FIVE_MINUTE", before="2026-08-15T00:00:00+05:30")
        assert result.rows_seen == 1 and len(result.bars) == 1

    def test_limit_keeps_the_most_recent_bars(self, tmp_path):
        rows = [_ohlc(i, 24000.0 + i) for i in range(10)]
        result = load_bars(db_path=_db(tmp_path, rows), instrument="NSE:NIFTY50-INDEX",
                           resolution="FIVE_MINUTE", limit=3)
        assert len(result.bars) == 3
        assert result.bars[-1].close == pytest.approx(24009.0)

    def test_an_unknown_series_yields_no_bars_rather_than_an_error(self, tmp_path):
        result = load_bars(db_path=_db(tmp_path, [_ohlc(0)]), instrument="NOPE",
                           resolution="FIVE_MINUTE")
        assert result.bars == () and result.usable is False


class TestTheDailySnapshot:
    def _snapshot(self, tmp_path, n=60):
        db = _db(tmp_path, [_ohlc(i, 24000.0 + (i % 7) * 10) for i in range(n)])
        return build_snapshot(built_for="2026-08-19", built_at="2026-08-19T18:00:00+05:30",
                              db_path=db, bar_limit=1000)

    def test_a_snapshot_carries_the_evidence_of_how_it_was_built(self, tmp_path):
        snap = self._snapshot(tmp_path)
        assert snap.load["bars"] > 0
        assert snap.built_for == "2026-08-19" and snap.built_at
        assert snap.levels.status and snap.zones.status

    def test_thin_data_produces_an_honest_snapshot_not_a_crash(self, tmp_path):
        """INSUFFICIENT_HISTORY is a perfectly good snapshot: it says
        truthfully that we could not see."""
        snap = self._snapshot(tmp_path, n=5)
        assert snap.levels.status == taxonomy.LEVELS_INSUFFICIENT_HISTORY
        assert snap.levels.reason

    def test_write_and_read_round_trip(self, tmp_path):
        snap = self._snapshot(tmp_path)
        path = write_snapshot(snap, str(tmp_path / "out"))
        assert read_snapshot_raw(path)["built_for"] == "2026-08-19"

    def test_snapshots_are_dated_never_overwritten(self, tmp_path):
        """Overwriting a single latest.json would destroy the record of what
        Bujji believed on the day it made a decision."""
        out = str(tmp_path / "out")
        snap = self._snapshot(tmp_path)
        write_snapshot(snap, out)
        from dataclasses import replace

        write_snapshot(replace(snap, built_for="2026-08-20"), out)
        assert len(list(Path(out).glob("levels_*.json"))) == 2


class TestChoosingTheRightSnapshot:
    def _make(self, directory, *dates):
        Path(directory).mkdir(parents=True, exist_ok=True)
        for d in dates:
            (Path(directory) / f"levels_{d}.json").write_text("{}")

    def test_the_newest_on_or_before_the_date_is_chosen(self, tmp_path):
        d = str(tmp_path / "snaps")
        self._make(d, "2026-08-17", "2026-08-18", "2026-08-19")
        assert latest_snapshot_path("2026-08-18", d).endswith("levels_2026-08-18.json")

    def test_a_future_snapshot_is_never_picked_up(self, tmp_path):
        """A session replaying an older date must not silently read a map
        built from bars that had not happened yet."""
        d = str(tmp_path / "snaps")
        self._make(d, "2026-08-17", "2026-08-25")
        assert latest_snapshot_path("2026-08-18", d).endswith("levels_2026-08-17.json")

    def test_nothing_qualifying_returns_none_not_an_empty_map(self, tmp_path):
        d = str(tmp_path / "snaps")
        self._make(d, "2026-08-25")
        assert latest_snapshot_path("2026-08-18", d) is None

    def test_a_missing_directory_returns_none(self, tmp_path):
        assert latest_snapshot_path("2026-08-18", str(tmp_path / "nope")) is None

    def test_staleness_is_measurable(self, tmp_path):
        assert snapshot_age_days("2026-08-14", "2026-08-19") == 5


def _level(price, touches=0, tolerance=1.0):
    return PriceLevel(price=price, kind=taxonomy.LEVEL_SWING_LOW, formed_at="T1",
                      formed_bar_index=1, touch_count=touches,
                      last_touch_at="T2" if touches else None,
                      strength=taxonomy.STRENGTH_UNTESTED if not touches
                      else taxonomy.STRENGTH_TESTED,
                      detected_at_strengths=(3, 5, 8), source_resolution="FIVE_MINUTE",
                      touch_tolerance=tolerance)


def _levelset(*levels):
    return LevelSet(status=taxonomy.LEVELS_AVAILABLE, levels=tuple(levels))


def _zone(lower, upper, status=taxonomy.ZONE_FRESH, tests=0):
    return SupplyDemandZone(lower=lower, upper=upper, kind=taxonomy.ZONE_DEMAND,
                            formed_at="T1", formed_bar_index=1, impulse_size=50.0,
                            status=status, test_count=tests, last_test_at=None,
                            broken_at="T9" if status == taxonomy.ZONE_BROKEN else None,
                            detected_at_multiples=(1.5, 2.0, 3.0),
                            source_resolution="FIVE_MINUTE")


def _zoneset(*zones):
    return ZoneSet(status=taxonomy.LEVELS_AVAILABLE, zones=tuple(zones))


class TestLiveSamplesTest:
    def test_a_sample_inside_the_band_counts_as_a_touch(self):
        levels, touched = apply_sample_to_levels(_levelset(_level(24000.0)), 24000.5, "T5")
        assert touched == 1
        assert levels.levels[0].touch_count == 1 and levels.levels[0].last_touch_at == "T5"

    def test_a_sample_outside_the_band_changes_nothing(self):
        original = _levelset(_level(24000.0))
        levels, touched = apply_sample_to_levels(original, 24010.0, "T5")
        assert touched == 0 and levels is original

    def test_the_bands_are_the_levels_own_published_tolerance(self):
        """Intraday and historical counts must mean the same thing, or
        adding them together is meaningless."""
        levels, touched = apply_sample_to_levels(
            _levelset(_level(24000.0, tolerance=12.0)), 24011.0, "T5")
        assert touched == 1

    def test_strength_follows_the_updated_count(self):
        levels, _ = apply_sample_to_levels(_levelset(_level(24000.0, touches=2)), 24000.0, "T5")
        assert levels.levels[0].strength == taxonomy.STRENGTH_STRONG

    def test_a_sample_inside_a_zone_is_a_test(self):
        zones, tested = apply_sample_to_zones(_zoneset(_zone(23990.0, 24010.0)), 24000.0, "T5")
        assert tested == 1
        assert zones.zones[0].status == taxonomy.ZONE_TESTED
        assert zones.zones[0].test_count == 1


class TestLiveSamplesNeverBreakOrForm:
    def test_a_sample_far_beyond_a_zone_does_not_break_it(self):
        """The rule this phase exists to protect. A point sample is one
        instant inside an unfinished bar -- closer to a wick than a close.
        Breaks wait for tomorrow's refresh over completed bars."""
        zones, tested = apply_sample_to_zones(_zoneset(_zone(23990.0, 24010.0)), 23000.0, "T5")
        assert tested == 0
        assert all(z.status != taxonomy.ZONE_BROKEN for z in zones.zones)

    def test_an_already_broken_zone_is_left_alone(self):
        """Counting further visits would make a dead zone look active."""
        broken = _zoneset(_zone(23990.0, 24010.0, taxonomy.ZONE_BROKEN))
        zones, tested = apply_sample_to_zones(broken, 24000.0, "T5")
        assert tested == 0 and zones is broken

    def test_a_sample_never_adds_a_level_or_a_zone(self):
        levels, zones, _, _ = apply_sample(_levelset(_level(24000.0)),
                                           _zoneset(_zone(23990.0, 24010.0)), 24500.0, "T5")
        assert len(levels.levels) == 1 and len(zones.zones) == 1

    def test_an_unavailable_set_is_returned_untouched(self):
        unavailable = LevelSet(status=taxonomy.LEVELS_INSUFFICIENT_HISTORY)
        levels, touched = apply_sample_to_levels(unavailable, 24000.0, "T5")
        assert levels is unavailable and touched == 0

    def test_a_nonsense_price_is_refused(self):
        original = _levelset(_level(24000.0))
        assert apply_sample_to_levels(original, 0.0, "T5")[0] is original
        assert apply_sample_to_levels(original, None, "T5")[0] is original

    def test_zones_are_optional_in_the_combined_call(self):
        levels, zones, touched, tested = apply_sample(
            _levelset(_level(24000.0)), None, 24000.0, "T5")
        assert zones is None and touched == 1 and tested == 0
