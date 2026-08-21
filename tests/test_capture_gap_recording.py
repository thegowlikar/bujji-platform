"""A dropped poll leaves a trace.

THE DEFECT. A poll that returned nothing was logged and abandoned. The
series simply had no row for that minute -- and on replay a missing row is
indistinguishable from a market that did not move. Two real gaps opened on
2026-08-20, a 121-second spot gap and a 601-second option-chain gap, and
neither left any trace anywhere. That does not merely lose data: it silently
corrupts the learning loop, because a flat stretch that never happened reads
as evidence about the market.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from bujji.market_reality import taxonomy
from bujji.market_reality.capture_lifecycle import POINT_REASONS, CaptureLifecycleTracker
from bujji.market_reality.store import RawObservationStore

_spec = importlib.util.spec_from_file_location(
    "capture_session", REPO_ROOT / "scripts" / "capture_market_reality_session.py")
CAP = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(CAP)


class _Gate:
    def status_for(self, *a): return taxonomy.CERTIFIED_AVAILABLE, "cert.json@t"
    def status(self, *a, **k): return taxonomy.CERTIFIED_AVAILABLE, "cert.json@t"


class _Broker:
    """`miss_on` names the 1-based cycles on which VIX returns nothing."""

    def __init__(self, miss_on=(), spot_ltp=24416.2, futures=True):
        self.n = 0
        self._miss_on = set(miss_on)
        self._spot = spot_ltp
        self._futures = futures

    async def get_spot(self, underlying): return self._spot

    async def get_vix(self):
        self.n += 1
        if self.n in self._miss_on:
            return None
        return {"level": 11.42, "prev_close": 11.2}

    async def get_futures_quote(self, underlying):
        if not self._futures:
            return None
        return {"ltp": 24461.2, "symbol": "NSE:NIFTY26AUGFUT"}


def _run(tmp_path, broker, cycles):
    store = RawObservationStore(str(tmp_path), _Gate())
    tracker = CaptureLifecycleTracker(store=store, source="fyers", access_method="rest")

    async def _go():
        for _ in range(cycles):
            await CAP._run_cycle(broker, _Gate(), store, tracker, "2026-08-27", False)

    asyncio.run(_go())
    rows = [json.loads(l) for l in open(tmp_path / "raw_observations.jsonl")]
    return ([r for r in rows if r.get("event_type") != "CAPTURE_EVENT"],
            [r for r in rows if r.get("event_type") == "CAPTURE_EVENT"])


def _misses(events):
    return [e for e in events if taxonomy.REASON_OBSERVATION_MISS in json.dumps(e)]


class TestAGapIsRecordedAsAGap:
    def test_a_dropped_poll_writes_a_capture_event(self, tmp_path):
        obs, events = _run(tmp_path, _Broker(miss_on=(2,)), cycles=3)
        assert len(_misses(events)) == 1
        assert len(obs) == 8, "3 cycles x 3 instruments minus the one miss"

    def test_the_event_names_the_instrument_that_was_missed(self, tmp_path):
        _, events = _run(tmp_path, _Broker(miss_on=(1,)), cycles=1)
        blob = json.dumps(_misses(events)[0])
        assert "vix" in blob

    def test_a_clean_session_records_no_misses(self, tmp_path):
        """The measurement must be capable of saying nothing went wrong."""
        obs, events = _run(tmp_path, _Broker(), cycles=3)
        assert _misses(events) == [] and len(obs) == 9

    def test_three_consecutive_misses_are_three_distinct_gaps(self, tmp_path):
        """Deduplicating them as 'the same condition' would erase two real
        holes in the series. This is why a miss is a POINT event."""
        _, events = _run(tmp_path, _Broker(miss_on=(1, 2, 3)), cycles=3)
        assert len(_misses(events)) == 3

    def test_futures_misses_are_recorded_too(self, tmp_path):
        _, events = _run(tmp_path, _Broker(futures=False), cycles=2)
        assert len(_misses(events)) == 2

    def test_a_spot_miss_is_recorded(self, tmp_path):
        """Spot previously had no absent-value branch at all -- a None ltp
        went straight into an observation."""
        _, events = _run(tmp_path, _Broker(spot_ltp=None), cycles=1)
        assert any("spot" in json.dumps(e) for e in _misses(events))


class TestTheMissIsModelledCorrectly:
    def test_it_is_a_point_reason_not_an_opening_one(self):
        """A poll that returned nothing has happened and is over. Modelling
        it as an ongoing condition would claim a state the collector is not
        in, and would suppress every subsequent miss as a duplicate."""
        assert taxonomy.REASON_OBSERVATION_MISS in POINT_REASONS

    def test_it_is_a_known_capture_reason(self):
        assert taxonomy.REASON_OBSERVATION_MISS in taxonomy.ALL_CAPTURE_REASONS

    def test_recording_it_as_a_condition_is_refused(self, tmp_path):
        """The tracker's own guard. If this ever stops raising, a miss could
        be silently modelled as an open disconnect."""
        store = RawObservationStore(str(tmp_path), _Gate())
        tracker = CaptureLifecycleTracker(store=store, source="fyers", access_method="rest")
        with pytest.raises(ValueError):
            tracker.record_condition(
                reason=taxonomy.REASON_OBSERVATION_MISS,
                event_time="2026-08-20T09:15:00+05:30",
                knowledge_time="2026-08-20T09:15:00+05:30")


class TestTheSessionSurvivesTheGap:
    def test_a_miss_does_not_stop_the_session(self, tmp_path):
        """One absent quote must not end a capture session -- the remaining
        instruments and the remaining cycles are still real observations."""
        obs, _ = _run(tmp_path, _Broker(miss_on=(1, 2, 3)), cycles=3)
        assert len(obs) == 6, "spot and futures still captured on all 3 cycles"

    def test_other_instruments_still_capture_on_a_miss_cycle(self, tmp_path):
        obs, _ = _run(tmp_path, _Broker(miss_on=(1,)), cycles=1)
        blob = json.dumps(obs)
        assert "NIFTY50-INDEX" in blob and "NIFTY26AUGFUT" in blob
