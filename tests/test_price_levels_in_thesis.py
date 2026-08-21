"""L-5: the structure map reaches the decision record, and nothing else.

This is the phase where levels finally touch the trading runner -- so the
tests are mostly about what must NOT happen. The map is written into the
thesis record and read by nothing in the decision chain. A missing map must
not end a session, and its absence must be recorded rather than silently
tolerated: "no map on the day of a trade" is a fact the audit trail should
carry.
"""
from __future__ import annotations

import importlib.util
import json
import logging
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from bujji.price_levels import taxonomy
from bujji.price_levels.daily import LevelsSnapshot, load_snapshot, write_snapshot
from bujji.price_levels.models import LevelSet, PriceLevel, SupplyDemandZone, ZoneSet
from bujji.shadow_observatory.thesis_artifact import build_thesis_artifact

_spec = importlib.util.spec_from_file_location(
    "runner_l5", REPO_ROOT / "bujji_options_os_runner.py")
runner_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(runner_mod)


def _level(price, touches=3, tolerance=12.0):
    return PriceLevel(price=price, kind=taxonomy.LEVEL_SWING_LOW, formed_at="T1",
                      formed_bar_index=1, touch_count=touches, last_touch_at="T2",
                      strength=taxonomy.STRENGTH_STRONG,
                      detected_at_strengths=(3, 5, 8), source_resolution="FIVE_MINUTE",
                      touch_tolerance=tolerance)


def _zone(lower, upper):
    return SupplyDemandZone(lower=lower, upper=upper, kind=taxonomy.ZONE_DEMAND,
                            formed_at="T1", formed_bar_index=1, impulse_size=50.0,
                            status=taxonomy.ZONE_FRESH, test_count=0, last_test_at=None,
                            broken_at=None, detected_at_multiples=(1.5, 2.0, 3.0),
                            source_resolution="FIVE_MINUTE")


def _snapshot(built_for="2026-08-19"):
    return LevelsSnapshot(
        built_for=built_for, built_at=f"{built_for}T18:00:00+05:30", as_of=None,
        instrument="NSE:NIFTY50-INDEX", resolution="FIVE_MINUTE",
        levels=LevelSet(status=taxonomy.LEVELS_AVAILABLE,
                        levels=(_level(23900.0), _level(24100.0))),
        zones=ZoneSet(status=taxonomy.LEVELS_AVAILABLE, zones=(_zone(23800.0, 23820.0),)),
        load={"bars": 1500, "skipped_not_a_bar": 0, "rows_seen": 1500},
    )


class _Stub:
    """Only the attributes the two new runner methods touch."""

    def __init__(self, as_of="2026-08-19", clock_value="2026-08-19T10:00:00+05:30"):
        self._as_of_date = as_of
        self._logger = logging.getLogger("l5")
        self._clock_value = clock_value

    def _clock(self):
        import datetime

        return datetime.datetime.fromisoformat(self._clock_value)


class TestTheSnapshotRoundTripsThroughDisk:
    def test_a_written_snapshot_reloads_with_its_levels_intact(self, tmp_path):
        path = write_snapshot(_snapshot(), str(tmp_path))
        loaded = load_snapshot(path)
        assert [l.price for l in loaded.levels.levels] == [23900.0, 24100.0]
        assert loaded.zones.zones[0].lower == 23800.0
        assert loaded.levels.levels[0].touch_tolerance == 12.0

    def test_a_corrupt_snapshot_raises_rather_than_degrading_to_an_empty_map(self, tmp_path):
        """An empty map reads as 'the market has no structure', which is a
        claim. A raised error reads as 'we have no map', which is the truth."""
        bad = tmp_path / "levels_2026-08-19.json"
        bad.write_text(json.dumps({"built_for": "x", "built_at": "y",
                                   "levels": {"status": "AVAILABLE",
                                              "levels": [{"price": 1.0}]},
                                   "zones": {"status": "AVAILABLE", "zones": []}}))
        with pytest.raises(KeyError):
            load_snapshot(str(bad))


class TestTheRunnerLoadsTheMapWithoutDependingOnIt:
    def test_a_loaded_map_is_reported_with_its_age(self, tmp_path, monkeypatch):
        write_snapshot(_snapshot("2026-08-14"), str(tmp_path / "data" / "price_levels"))
        monkeypatch.setattr(runner_mod, "REPO_ROOT", tmp_path)
        stub = _Stub(as_of="2026-08-19")
        runner_mod.OptionsOSRunner._load_price_levels(stub)
        info = stub._levels_snapshot_info
        assert info["status"] == "LOADED"
        assert info["age_days"] == 5, "staleness must be visible, not discovered by its effects"
        assert info["levels"] == 2 and info["live_zones"] == 1

    def test_a_missing_map_is_recorded_and_never_fatal(self, tmp_path, monkeypatch):
        monkeypatch.setattr(runner_mod, "REPO_ROOT", tmp_path)
        stub = _Stub()
        runner_mod.OptionsOSRunner._load_price_levels(stub)
        assert stub._levels is None
        assert stub._levels_snapshot_info["status"] == "NO_SNAPSHOT"

    def test_an_unreadable_map_is_recorded_and_never_fatal(self, tmp_path, monkeypatch):
        directory = tmp_path / "data" / "price_levels"
        directory.mkdir(parents=True)
        (directory / "levels_2026-08-19.json").write_text("{not json")
        monkeypatch.setattr(runner_mod, "REPO_ROOT", tmp_path)
        stub = _Stub()
        runner_mod.OptionsOSRunner._load_price_levels(stub)
        assert stub._levels is None
        assert stub._levels_snapshot_info["status"].startswith("FAILED:")

    def test_a_future_dated_map_is_not_picked_up(self, tmp_path, monkeypatch):
        write_snapshot(_snapshot("2026-08-25"), str(tmp_path / "data" / "price_levels"))
        monkeypatch.setattr(runner_mod, "REPO_ROOT", tmp_path)
        stub = _Stub(as_of="2026-08-19")
        runner_mod.OptionsOSRunner._load_price_levels(stub)
        assert stub._levels_snapshot_info["status"] == "NO_SNAPSHOT"


class TestTheContextReachesTheRecord:
    def _loaded_stub(self):
        stub = _Stub()
        snap = _snapshot()
        stub._levels, stub._zones = snap.levels, snap.zones
        stub._levels_snapshot_info = {"status": "LOADED", "age_days": 0}
        stub._last_spot = 24000.0
        return stub

    def test_a_context_is_produced_from_the_loaded_map_and_live_spot(self):
        payload = runner_mod.OptionsOSRunner._level_context_dict(self._loaded_stub())
        assert payload["status"] == taxonomy.LEVELS_AVAILABLE
        assert payload["spot"] == 24000.0
        assert payload["snapshot"]["status"] == "LOADED"

    def test_the_live_sample_updates_touch_counts_before_the_context_is_built(self):
        """A sample TESTS structure -- it never forms or breaks it."""
        stub = self._loaded_stub()
        stub._last_spot = 23905.0                       # inside the 23900 level's band
        payload = runner_mod.OptionsOSRunner._level_context_dict(stub)
        assert payload["live_sample"]["levels_touched"] == 1
        assert stub._levels.levels[0].touch_count == 4   # was 3

    def test_no_map_yields_no_context_rather_than_an_empty_one(self):
        stub = _Stub()
        stub._levels = None
        stub._last_spot = 24000.0
        assert runner_mod.OptionsOSRunner._level_context_dict(stub) is None

    def test_no_spot_yields_no_context(self):
        stub = self._loaded_stub()
        stub._last_spot = None
        assert runner_mod.OptionsOSRunner._level_context_dict(stub) is None

    def test_a_context_failure_is_captured_not_raised(self):
        stub = self._loaded_stub()
        stub._levels = object()                         # not a LevelSet
        payload = runner_mod.OptionsOSRunner._level_context_dict(stub)
        assert payload["status"].startswith("FAILED:")


class TestTheThesisRecordCarriesIt:
    def test_the_artifact_carries_the_level_context(self):
        artifact = build_thesis_artifact(
            thesis=type("T", (), {"market_regime": "RANGE_BOUND"})(),
            cycle_record=None, level_context={"status": "AVAILABLE", "spot": 24000.0})
        assert artifact["level_context"]["spot"] == 24000.0

    def test_no_map_is_recorded_as_a_real_absence(self):
        """'No map on the day of a trade' is a fact the audit trail should
        carry, not a field quietly missing."""
        artifact = build_thesis_artifact(
            thesis=type("T", (), {"market_regime": "RANGE_BOUND"})(), cycle_record=None)
        assert "level_context" in artifact and artifact["level_context"] is None

    def test_the_artifact_stays_json_serialisable(self):
        artifact = build_thesis_artifact(
            thesis=type("T", (), {"market_regime": "X"})(), cycle_record=None,
            level_context={"status": "AVAILABLE", "spot": 24000.0})
        assert json.loads(json.dumps(artifact)) == artifact


class TestObservationOnly:
    def test_no_decision_module_imports_price_levels(self):
        """The gate is the operator's. Until they open it, nothing in the
        decision chain may consume this."""
        forbidden = [
            "bujji/production_runtime/trading_brain_runtime.py",
            "bujji/production_runtime/trading_session_governor/session_governor.py",
            "bujji/trading_brain/risk_governor/msi_entry_bridge.py",
        ]
        for rel in forbidden:
            source = (REPO_ROOT / rel).read_text()
            assert "price_levels" not in source, f"{rel} consumes the levels layer"

    def test_the_runner_only_uses_it_for_the_record(self):
        source = (REPO_ROOT / "bujji_options_os_runner.py").read_text()
        # The only consumer is the thesis artifact's own field.
        assert "level_context=self._level_context_dict()" in source
        assert source.count("_level_context_dict()") == 1
