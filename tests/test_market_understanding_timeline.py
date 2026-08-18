"""Phase 17J.4 — Episode Timeline comparison tests. Mechanics-only, see
docs/PHASE_17J4_SWEEP_VALIDATION_RESULT.md for the real discrimination
validation result."""
import sys
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from bujji.historical_reality.capture import build_historical_observation
from bujji.historical_reality.store import HistoricalObservationStore
from bujji.market_observation import taxonomy as moc_taxonomy
from bujji.market_understanding.similarity import SituationFeatureVector
from bujji.market_understanding.structure import IntradayStructureCatalog
from bujji.market_understanding.timeline import (
    EpisodeTimeline, _checkpoint_times, build_episode_timeline, compare_timelines,
)
from bujji.reality_memory.catalog import RealityMemoryCatalog


def _write_full_session(store, instrument_identity, date):
    # 09:15 through 15:40, every 5 minutes -- enough for every 30-min
    # checkpoint to have real trailing data.
    import datetime
    cursor = datetime.time(9, 15)
    ts_list = []
    h, m = 9, 15
    while (h, m) <= (15, 40):
        ts_list.append(f"{date}T{h:02d}:{m:02d}:00+05:30")
        m += 5
        if m >= 60:
            m -= 60
            h += 1
    for i, ts in enumerate(ts_list):
        store.write(build_historical_observation(
            instrument_identity=instrument_identity, instrument_type="SPOT",
            resolution=moc_taxonomy.RESOLUTION_FIVE_MINUTE, timestamp=ts,
            payload={"open": 100.0 + i * 0.1, "high": 100.0 + i * 0.1, "low": 100.0 + i * 0.1,
                     "close": 100.0 + i * 0.1, "volume": None},
            source="fyers_historical", access_method="direct_sdk_fyers_historical_intraday_rest",
            source_epoch=i, source_symbol=instrument_identity,
            raw_artifact_ref="x", ingestion_run_id="RUN-x", retrieved_at=ts,
            certification_status="CERTIFIED_AVAILABLE", certification_ref="ref",
        ))


# --- Checkpoint schedule --------------------------------------------------------
def test_checkpoint_times_covers_open_to_close_every_30_min():
    times = _checkpoint_times("2026-08-01", 30)
    assert times[0] == "2026-08-01T09:15:00+05:30"
    assert times[-1] == "2026-08-01T15:40:00+05:30"
    assert len(times) == 14  # 09:15..15:15 every 30min (13) + explicit close (15:40)


def test_checkpoint_times_respects_custom_interval():
    times = _checkpoint_times("2026-08-01", 60)
    assert len(times) == 8  # 09:15,10:15,...,15:15 (7) + close


# --- build_episode_timeline -------------------------------------------------------
def test_build_episode_timeline_populates_all_checkpoints_for_a_full_session():
    with tempfile.TemporaryDirectory() as d:
        store = HistoricalObservationStore(str(Path(d) / "hist.db"))
        _write_full_session(store, "NSE:NIFTY50-INDEX", "2026-08-01")
        sc = IntradayStructureCatalog(historical_store=store)
        mc = RealityMemoryCatalog(historical_store=store)
        tl = build_episode_timeline("NSE:NIFTY50-INDEX", "2026-08-01", structure_catalog=sc, memory_catalog=mc)
        assert tl is not None
        assert len(tl.checkpoints) == len(tl.checkpoint_times) == 14
        # The very first checkpoint (09:15, market open) has no PRIOR bar to
        # pair with -- honestly None, matching detect_price_change()'s own
        # "previous is None -> no event" contract. Every later checkpoint
        # (which has real trailing history) must be populated.
        assert tl.checkpoints[0] is None
        assert all(c is not None for c in tl.checkpoints[1:])


def test_build_episode_timeline_returns_none_for_a_date_with_no_data():
    with tempfile.TemporaryDirectory() as d:
        store = HistoricalObservationStore(str(Path(d) / "hist.db"))
        sc = IntradayStructureCatalog(historical_store=store)
        mc = RealityMemoryCatalog(historical_store=store)
        tl = build_episode_timeline("NSE:NIFTY50-INDEX", "2026-08-01", structure_catalog=sc, memory_catalog=mc)
        assert tl is None


# --- compare_timelines: alignment + missing-position handling --------------------
def _vector(**overrides):
    base = dict(
        instrument_identity="NSE:NIFTY50-INDEX", date="2026-08-01", as_of="2026-08-01T10:15:00+05:30",
        trend_state="TREND_NONE", swing_state="CONFIRMED", compression_state="NOT_DETECTED",
        expansion_state="EARLY", balance_state="IN_BALANCE", structure_state="BALANCE",
        structure_integrity="COHERENT", support_state="ESTABLISHED", resistance_state="WEAK",
        breakout_state="FAILED", breakdown_state="DEVELOPING", retest_state="ACTIVE",
        rejection_state="STRONG", structural_balance="UNBOUNDED", structure_location="AT_RETEST",
        vix_close=15.0, vix_band="NORMAL", source_observation_ids=("OBS-a",),
    )
    base.update(overrides)
    return SituationFeatureVector(**base)


def test_compare_timelines_identical_timelines_is_1():
    checkpoints = tuple(_vector() for _ in range(4))
    times = tuple(f"t{i}" for i in range(4))
    a = EpisodeTimeline("NSE:NIFTY50-INDEX", "2026-08-01", 30, times, checkpoints)
    b = EpisodeTimeline("NSE:NIFTY50-INDEX", "2026-08-02", 30, times, checkpoints)
    assert compare_timelines(a, b) == 1.0


def test_compare_timelines_skips_none_positions_without_misaligning():
    times = tuple(f"t{i}" for i in range(3))
    checkpoints_a = (_vector(), None, _vector(trend_state="TREND_ESTABLISHED"))
    checkpoints_b = (_vector(), _vector(vix_band="EXTREME"), _vector(trend_state="TREND_ESTABLISHED"))
    a = EpisodeTimeline("NSE:NIFTY50-INDEX", "2026-08-01", 30, times, checkpoints_a)
    b = EpisodeTimeline("NSE:NIFTY50-INDEX", "2026-08-02", 30, times, checkpoints_b)
    # Position 0: identical match. Position 1: skipped (a is None). Position 2: identical match.
    # Only positions 0 and 2 are comparable, both perfect matches -> 1.0.
    assert compare_timelines(a, b) == 1.0


def test_compare_timelines_returns_none_for_mismatched_schedule_length():
    times_a = tuple(f"t{i}" for i in range(3))
    times_b = tuple(f"t{i}" for i in range(4))
    a = EpisodeTimeline("NSE:NIFTY50-INDEX", "2026-08-01", 30, times_a, tuple(_vector() for _ in range(3)))
    b = EpisodeTimeline("NSE:NIFTY50-INDEX", "2026-08-02", 30, times_b, tuple(_vector() for _ in range(4)))
    assert compare_timelines(a, b) is None


def test_compare_timelines_returns_none_when_nothing_is_comparable():
    times = tuple(f"t{i}" for i in range(2))
    a = EpisodeTimeline("NSE:NIFTY50-INDEX", "2026-08-01", 30, times, (None, None))
    b = EpisodeTimeline("NSE:NIFTY50-INDEX", "2026-08-02", 30, times, (_vector(), _vector()))
    assert compare_timelines(a, b) is None
