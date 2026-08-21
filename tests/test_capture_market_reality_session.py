"""Phase 17I.6 — Minimum Market Reality Capture Loop.

`scripts/capture_market_reality_session.py` is an operational script
loaded directly from its file path, same posture as every other
capture-script test in this project. It requires live credentials to
run `run()` end-to-end against a real broker, so most of what's tested
here uses fakes injected at the function-parameter level
(`_run_cycle(broker, gate, store, tracker, ...)` takes all four as
explicit arguments, so it's exercised directly without touching
`run()`'s internal `AppConfig`/`FyersBroker`/`InstrumentMaster`
construction) plus a small number of `run()`-level tests with the
broker/config/instrument-master construction monkeypatched, to prove
the loop bound, the poll interval, and the single-tracker guarantee.
"""
import asyncio
import datetime
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from bujji.market_reality import taxonomy
from bujji.market_reality.capture_lifecycle import CaptureLifecycleTracker
from bujji.market_reality.certification import CertificationGate
from bujji.market_reality.store import RawObservationStore

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "capture_market_reality_session.py"
_spec = importlib.util.spec_from_file_location(
    "capture_market_reality_session", _SCRIPT_PATH
)
capture_script = importlib.util.module_from_spec(_spec)
sys.modules["capture_market_reality_session"] = capture_script
_spec.loader.exec_module(capture_script)

REAL_CERT_DIR = _REPO_ROOT / "data_certification"
NOW = "2026-08-13T09:30:00+05:30"
EXPIRY_ISO = "2026-08-25"


class FakeBroker:
    """Each list may contain real return values or Exception instances --
    the Nth call to the corresponding method consumes the Nth item."""

    def __init__(self, spot=(), futures=(), vix=()):
        self._spot = iter(spot)
        self._futures = iter(futures)
        self._vix = iter(vix)
        self.calls = {"spot": 0, "futures": 0, "vix": 0}
        self.connected = False

    async def connect(self):
        self.connected = True

    async def get_spot(self, underlying):
        self.calls["spot"] += 1
        v = next(self._spot)
        if isinstance(v, Exception):
            raise v
        return v

    async def get_futures_quote(self, underlying):
        self.calls["futures"] += 1
        v = next(self._futures)
        if isinstance(v, Exception):
            raise v
        return v

    async def get_vix(self):
        self.calls["vix"] += 1
        v = next(self._vix)
        if isinstance(v, Exception):
            raise v
        return v


def _gate():
    return CertificationGate(str(REAL_CERT_DIR))


# --- Market-hours gate ------------------------------------------------------
def test_within_market_hours_true_during_session():
    weekday = datetime.datetime(2026, 8, 12, 10, 0, tzinfo=capture_script.IST)
    assert capture_script.within_market_hours(weekday) is True


def test_within_market_hours_false_before_open():
    early = datetime.datetime(2026, 8, 12, 9, 0, tzinfo=capture_script.IST)
    assert capture_script.within_market_hours(early) is False


def test_within_market_hours_false_after_close():
    late = datetime.datetime(2026, 8, 12, 15, 45, tzinfo=capture_script.IST)
    assert capture_script.within_market_hours(late) is False


def test_within_market_hours_false_on_weekend():
    saturday = datetime.datetime(2026, 8, 15, 10, 0, tzinfo=capture_script.IST)
    assert capture_script.within_market_hours(saturday) is False


def test_poll_interval_is_60_seconds():
    assert capture_script.POLL_INTERVAL_SECONDS == 60.0


# --- Observation construction ------------------------------------------------
def test_build_spot_observation_shape():
    raw = capture_script.build_spot_observation(
        24345.9, NOW, taxonomy.CERTIFIED_AVAILABLE, "spot_ref@ts",
    )
    assert raw.kind == taxonomy.KIND_QUOTE
    assert raw.instrument_type == taxonomy.INSTRUMENT_SPOT
    assert raw.observation.value.payload == {"ltp": 24345.9}
    assert raw.identity_fields == {}


def test_build_vix_observation_maps_level_to_ltp():
    raw = capture_script.build_vix_observation(
        {"level": 13.02, "prev_close": 13.15}, NOW,
        taxonomy.CERTIFIED_AVAILABLE, "vix_ref@ts",
    )
    assert raw.instrument_type == taxonomy.INSTRUMENT_INDEX
    assert raw.observation.value.payload == {"ltp": 13.02, "prev_close": 13.15}


def test_build_futures_observation_shape():
    raw = capture_script.build_futures_observation(
        {"symbol": "NSE:NIFTY26AUGFUT", "ltp": 24429.1, "volume": 1000, "oi": 12645685},
        EXPIRY_ISO, NOW, taxonomy.CERTIFIED_AVAILABLE, "fut_ref@ts",
    )
    assert raw.kind == taxonomy.KIND_QUOTE
    assert raw.instrument_type == taxonomy.INSTRUMENT_FUTURE
    assert raw.instrument == "NSE:NIFTY26AUGFUT"
    assert raw.identity_fields == {"expiry": EXPIRY_ISO}
    assert raw.observation.value.payload == {"ltp": 24429.1, "volume": 1000, "oi": 12645685}


def test_build_futures_observation_omits_absent_volume_and_oi():
    raw = capture_script.build_futures_observation(
        {"symbol": "NSE:NIFTY26AUGFUT", "ltp": 24429.1, "volume": None, "oi": None},
        EXPIRY_ISO, NOW, taxonomy.CERTIFIED_AVAILABLE, "fut_ref@ts",
    )
    assert raw.observation.value.payload == {"ltp": 24429.1}


# --- Certification linkage against REAL artifacts ---------------------------
@pytest.mark.parametrize("instrument_type,expected_prefix", [
    (taxonomy.INSTRUMENT_SPOT, "fyers_nifty_spot_certification"),
    (taxonomy.INSTRUMENT_FUTURE, "fyers_nifty_future_certification"),
    (taxonomy.INSTRUMENT_INDEX, "fyers_india_vix_certification"),
])
def test_real_certification_covers_all_three_instrument_types(instrument_type, expected_prefix):
    status, ref = _gate().status_for(capture_script.ACCESS_METHOD, instrument_type)
    assert status == taxonomy.CERTIFIED_AVAILABLE
    assert ref is not None and ref.startswith(expected_prefix)


# --- Append persistence ------------------------------------------------------
def test_capture_spot_persists_one_line_to_the_real_jsonl_file(tmp_path):
    gate = _gate()
    store = RawObservationStore(tmp_path, gate, session_id="test-session")
    broker = FakeBroker(spot=[24345.9])

    asyncio.run(capture_script._capture_spot(broker, gate, store, NOW, log_raw=False))

    accepted_path = Path(store.accepted_path)
    lines = accepted_path.read_text().strip().splitlines()
    assert len(lines) == 1


def test_capture_futures_persists_with_resolved_expiry(tmp_path):
    gate = _gate()
    store = RawObservationStore(tmp_path, gate, session_id="test-session")
    broker = FakeBroker(futures=[{"symbol": "NSE:NIFTY26AUGFUT", "ltp": 24429.1, "volume": None, "oi": None}])

    asyncio.run(capture_script._capture_futures(broker, gate, store, EXPIRY_ISO, NOW, log_raw=False))

    events = list(store.read_accepted_events())
    assert len(events) == 1
    assert events[0].payload["identity_fields"] == {"expiry": EXPIRY_ISO}


def test_capture_vix_persists_with_level_mapped_to_ltp(tmp_path):
    gate = _gate()
    store = RawObservationStore(tmp_path, gate, session_id="test-session")
    broker = FakeBroker(vix=[{"level": 13.02}])

    asyncio.run(capture_script._capture_vix(broker, gate, store, NOW, log_raw=False))

    events = list(store.read_accepted_events())
    assert len(events) == 1
    assert events[0].payload["observation"]["value"]["payload"] == {"ltp": 13.02}


# --- Duplicate execution behavior -------------------------------------------
def test_duplicate_capture_is_idempotent_across_all_three_instruments(tmp_path):
    gate = _gate()
    store = RawObservationStore(tmp_path, gate, session_id="test-session")

    spot_raw = capture_script.build_spot_observation(24345.9, NOW, taxonomy.CERTIFIED_AVAILABLE, "ref")
    fut_raw = capture_script.build_futures_observation(
        {"symbol": "NSE:NIFTY26AUGFUT", "ltp": 24429.1, "volume": None, "oi": None},
        EXPIRY_ISO, NOW, taxonomy.CERTIFIED_AVAILABLE, "ref",
    )
    vix_raw = capture_script.build_vix_observation({"level": 13.02}, NOW, taxonomy.CERTIFIED_AVAILABLE, "ref")

    for raw in (spot_raw, fut_raw, vix_raw):
        first = store.append(raw, now=NOW)
        second = store.append(raw, now=NOW)
        assert first.outcome == taxonomy.OUTCOME_ACCEPTED
        assert second.outcome == taxonomy.OUTCOME_DUPLICATE

    assert len(list(store.read_accepted_events())) == 3
    assert store.rejection_count() == 0


# --- Partial-failure continuation (the critical new behavior) --------------
def test_partial_failure_continues_and_persists_the_others(tmp_path):
    """spot succeeds, futures fails (bare exception, not auth), vix
    succeeds -> spot+vix persisted, futures logged only, no fake
    CaptureEvent, no fake RejectedObservation, loop continues."""
    gate = _gate()
    store = RawObservationStore(tmp_path, gate, session_id="test-session")
    tracker = CaptureLifecycleTracker(
        store=store, source=capture_script.SOURCE, access_method=capture_script.ACCESS_METHOD,
    )
    broker = FakeBroker(
        spot=[24345.9],
        futures=[RuntimeError("futures quote temporarily unavailable")],
        vix=[{"level": 13.02}],
    )

    keep_going = asyncio.run(
        capture_script._run_cycle(broker, gate, store, tracker, EXPIRY_ISO, log_raw=False)
    )

    assert keep_going is True
    accepted = list(store.read_accepted_events())
    assert len(accepted) == 2  # spot + vix, not futures
    kinds_present = {e.payload["observation"]["value"]["payload"].get("ltp") for e in accepted}
    assert 24345.9 in kinds_present
    assert 13.02 in kinds_present

    assert store.rejection_count() == 0  # No fake RejectedObservation.
    assert tracker.has_open_condition is False  # No fake CaptureEvent.


def test_authentication_error_stops_the_session_via_the_shared_tracker(tmp_path):
    from bujji.broker.errors import AuthenticationError

    gate = _gate()
    store = RawObservationStore(tmp_path, gate, session_id="test-session")
    tracker = CaptureLifecycleTracker(
        store=store, source=capture_script.SOURCE, access_method=capture_script.ACCESS_METHOD,
    )
    broker = FakeBroker(spot=[AuthenticationError("token dead")])

    keep_going = asyncio.run(
        capture_script._run_cycle(broker, gate, store, tracker, EXPIRY_ISO, log_raw=False)
    )

    assert keep_going is False
    assert tracker.has_open_condition is True
    assert tracker.open_reason == taxonomy.REASON_AUTH_FAILURE

    # The AUTH_FAILURE CaptureEvent legitimately shares the accepted store
    # (store.py: "Shares the accepted store... with market observations")
    # -- it is a real, honest fact about the observer, not a fake one.
    # What must NOT happen is a market OBSERVATION being persisted for the
    # instrument that raised.
    events = list(store.read_accepted_events())
    assert len(events) == 1
    assert events[0].event_type == taxonomy.KIND_CAPTURE_EVENT
    assert events[0].payload["reason"] == taxonomy.REASON_AUTH_FAILURE


# --- run()-level: bounded execution, poll interval, single tracker --------
class _TrackerSpy(CaptureLifecycleTracker):
    instances = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _TrackerSpy.instances.append(self)


class _FakeInstrumentMaster:
    def __init__(self, cache_dir, logger):
        pass

    async def resolve_nearest_future(self, underlying):
        return "NSE:NIFTY26AUGFUT", EXPIRY_ISO, 65


def test_run_executes_exactly_the_requested_number_of_cycles(monkeypatch, tmp_path):
    import bujji.broker.fyers as fyers_module
    import bujji.broker.instrument_master as instrument_master_module
    import bujji.core.config as config_module
    import bujji.market_reality.capture_lifecycle as lifecycle_module

    _TrackerSpy.instances.clear()
    fake_broker = FakeBroker(
        spot=[24345.9, 24346.0, 24347.0],
        futures=[
            {"symbol": "NSE:NIFTY26AUGFUT", "ltp": 24429.1, "volume": None, "oi": None},
        ] * 3,
        vix=[{"level": 13.02}, {"level": 13.03}, {"level": 13.04}],
    )

    monkeypatch.setattr(capture_script, "within_market_hours", lambda now: True)
    monkeypatch.setattr(config_module.AppConfig, "load",
                         staticmethod(lambda path: SimpleNamespace(broker=SimpleNamespace(name="fyers"))))
    monkeypatch.setattr(fyers_module, "FyersBroker", lambda cfg, log: fake_broker)
    monkeypatch.setattr(instrument_master_module, "InstrumentMaster", _FakeInstrumentMaster)
    monkeypatch.setattr(lifecycle_module, "CaptureLifecycleTracker", _TrackerSpy)

    sleeps = []
    async def fake_sleep(seconds):
        sleeps.append(seconds)
    monkeypatch.setattr(capture_script.asyncio, "sleep", fake_sleep)

    monkeypatch.setattr(capture_script, "REPO_ROOT", tmp_path)
    (tmp_path / "data_certification").mkdir()
    for name, artifact in {
        "fyers_nifty_spot_certification.json": ("NIFTY_SPOT",),
        "fyers_nifty_future_certification.json": ("NIFTY_FUTURES",),
        "fyers_india_vix_certification.json": ("INDIA_VIX",),
    }.items():
        import json
        (tmp_path / "data_certification" / name).write_text(json.dumps({
            "instrument": artifact[0],
            "access_method": "direct_sdk_fyers_broker_py",
            "validation_result": "CERTIFIED_AVAILABLE",
            "timestamp": NOW,
        }))

    exit_code = asyncio.run(capture_script.run(cycles=3))

    assert exit_code == 0
    assert fake_broker.calls == {"spot": 3, "futures": 3, "vix": 3}
    assert sleeps == [60.0, 60.0, 60.0]
    assert len(_TrackerSpy.instances) == 1  # Exactly one tracker for the whole session.
