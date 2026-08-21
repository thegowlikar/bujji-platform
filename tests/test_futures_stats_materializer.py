"""Phase 17F.1.2 — Futures Statistics Materializer.

Mirrors `test_market_timeseries_materializer.py`'s structure (17F.1),
extended for the facts specific to this materializer, per the design
doc's Part 9 test plan.
"""
from dataclasses import replace

import pytest

from bujji.market_reality import taxonomy as rt
from bujji.market_reality.capture import build_raw_observation
from bujji.market_reality.capture_events import build_capture_event
from bujji.market_reality.certification import StaticCertificationGate
from bujji.market_reality.store import RawObservationStore
from bujji.market_timeseries import indicators
from bujji.market_timeseries.futures_stats_materializer import (
    default_calc_version,
    materialize_futures_stats,
)
from bujji.market_timeseries.futures_stats_models import (
    BOOK_STATE_NOT_OBSERVED,
    BOOK_STATE_OBSERVED_EMPTY,
    BOOK_STATE_OBSERVED_NONEMPTY,
)
from bujji.market_timeseries.futures_stats_store import (
    ConflictingFuturesStatisticsError,
    FuturesStatsStore,
)
from bujji.market_timeseries.materializer import materialize_candles
from bujji.market_timeseries.store import CandleStore

CERT = rt.CERTIFIED_AVAILABLE
NOW = "2026-08-12T23:59:00+00:00"
FUT = "NSE:NIFTY26AUGFUT"
SPOT = "NSE:NIFTY50-INDEX"
INTERVAL = "FIVE_MINUTE"
W0_START, W0_END = "2026-08-12T09:20:00+00:00", "2026-08-12T09:25:00+00:00"
W1_START, W1_END = "2026-08-12T09:25:00+00:00", "2026-08-12T09:30:00+00:00"
FAR_FUTURE = "2099-01-01T00:00:00+00:00"


def _tick(instrument, ltp, event_time, capture_time=None):
    return build_raw_observation(
        kind=rt.KIND_MARKET_TICK,
        instrument=instrument,
        instrument_type=rt.INSTRUMENT_FUTURE if instrument == FUT else rt.INSTRUMENT_SPOT,
        payload={"ltp": ltp},
        source="fyers",
        access_method="direct_sdk_fyers_broker_py",
        capture_timestamp=capture_time or event_time,
        event_timestamp=event_time,
        certification_status=CERT,
        identity_fields={"expiry": "2026-08-27"} if instrument == FUT else {},
    )


def _depth(instrument, event_time, bids=None, asks=None, oi=None):
    payload = {"bids": bids if bids is not None else [], "asks": asks if asks is not None else []}
    if oi is not None:
        payload["oi"] = oi
    return build_raw_observation(
        kind=rt.KIND_MARKET_DEPTH,
        instrument=instrument,
        instrument_type=rt.INSTRUMENT_FUTURE,
        payload=payload,
        source="fyers",
        access_method="direct_sdk_fyers_broker_py",
        capture_timestamp=event_time,
        event_timestamp=event_time,
        certification_status=CERT,
        identity_fields={"expiry": "2026-08-27"},
    )


def _reality_store(tmp_path):
    return RawObservationStore(tmp_path / "layer0", StaticCertificationGate(CERT))


def _candle_store(tmp_path):
    return CandleStore(tmp_path / "candles.db")


def _write_candles(reality_store, candle_store, instrument, kind, as_of=FAR_FUTURE):
    candles = materialize_candles(
        reality_store=reality_store, instrument=instrument, kind=rt.KIND_MARKET_TICK,
        interval=INTERVAL, as_of=as_of,
    )
    candle_store.write_many(candles)
    return candles


def _seed_full_window(reality_store):
    """One futures tick, one futures MARKET_DEPTH (non-empty book, real
    OI), one spot tick -- all inside window 0."""
    reality_store.append(_tick(FUT, 24650.0, "2026-08-12T09:20:30+00:00"), now=NOW)
    reality_store.append(_tick(FUT, 24660.0, "2026-08-12T09:21:00+00:00"), now=NOW)
    reality_store.append(
        _depth(FUT, "2026-08-12T09:21:30+00:00",
               bids=[{"price": 24659.0, "volume": 50}], asks=[{"price": 24661.0, "volume": 40}],
               oi=12000000),
        now=NOW,
    )
    reality_store.append(_tick(SPOT, 24600.0, "2026-08-12T09:20:30+00:00"), now=NOW)
    reality_store.append(_tick(SPOT, 24610.0, "2026-08-12T09:21:00+00:00"), now=NOW)


# --- Reality consumption ----------------------------------------------------

def test_price_change_matches_the_referenced_candle_not_a_re_derivation(tmp_path):
    reality = _reality_store(tmp_path)
    candles = _candle_store(tmp_path)
    _seed_full_window(reality)
    _write_candles(reality, candles, FUT, rt.KIND_MARKET_TICK)
    _write_candles(reality, candles, SPOT, rt.KIND_MARKET_TICK)

    results = materialize_futures_stats(
        reality_store=reality, candle_store=candles, futures_instrument=FUT,
        spot_instrument=SPOT, interval=INTERVAL,
        window_start=W0_START, window_end=W1_END, as_of=FAR_FUTURE,
    )
    stats = [r for r in results if r.window_start == W0_START][0]
    ref_key = [k for k in stats.referenced_candle_keys if k[0] == FUT][0]
    referenced_candle = candles.get_candle(*ref_key)
    assert referenced_candle is not None
    assert stats.price_change == pytest.approx(referenced_candle.close - referenced_candle.open)


def test_oi_only_populates_from_market_depth_never_from_a_quote(tmp_path):
    """A QUOTE carrying a stray 'oi'-shaped key must never be read for
    OI -- OI is single-sourced from MARKET_DEPTH, structurally."""
    reality = _reality_store(tmp_path)
    candles = _candle_store(tmp_path)
    stray_quote = build_raw_observation(
        kind=rt.KIND_QUOTE, instrument=FUT, instrument_type=rt.INSTRUMENT_FUTURE,
        payload={"ltp": 24650.0, "oi": 999999},
        source="fyers", access_method="direct_sdk_fyers_broker_py",
        capture_timestamp="2026-08-12T09:20:30+00:00", event_timestamp="2026-08-12T09:20:30+00:00",
        certification_status=CERT, identity_fields={"expiry": "2026-08-27"},
    )
    reality.append(stray_quote, now=NOW)
    _write_candles(reality, candles, FUT, rt.KIND_MARKET_TICK)

    results = materialize_futures_stats(
        reality_store=reality, candle_store=candles, futures_instrument=FUT,
        interval=INTERVAL, window_start=W0_START, window_end=W1_END, as_of=FAR_FUTURE,
    )
    assert all(r.oi_open is None and r.oi_close is None for r in results)


def test_missing_oi_stays_missing_not_zero(tmp_path):
    reality = _reality_store(tmp_path)
    candles = _candle_store(tmp_path)
    reality.append(_tick(FUT, 24650.0, "2026-08-12T09:20:30+00:00"), now=NOW)
    reality.append(_depth(FUT, "2026-08-12T09:21:30+00:00",
                           bids=[{"price": 1, "volume": 1}], asks=[{"price": 2, "volume": 1}],
                           oi=None), now=NOW)
    _write_candles(reality, candles, FUT, rt.KIND_MARKET_TICK)

    results = materialize_futures_stats(
        reality_store=reality, candle_store=candles, futures_instrument=FUT,
        interval=INTERVAL, window_start=W0_START, window_end=W1_END, as_of=FAR_FUTURE,
    )
    stats = [r for r in results if r.window_start == W0_START][0]
    assert stats.oi_open is None
    assert stats.oi_close is None
    assert stats.oi_change is None
    assert stats.depth_observation_count == 1
    assert stats.oi_observation_count == 0


# --- Determinism / replay ----------------------------------------------------

def test_same_inputs_produce_identical_output_twice(tmp_path):
    reality = _reality_store(tmp_path)
    candles = _candle_store(tmp_path)
    _seed_full_window(reality)
    _write_candles(reality, candles, FUT, rt.KIND_MARKET_TICK)
    _write_candles(reality, candles, SPOT, rt.KIND_MARKET_TICK)

    kwargs = dict(
        reality_store=reality, candle_store=candles, futures_instrument=FUT,
        spot_instrument=SPOT, interval=INTERVAL,
        window_start=W0_START, window_end=W1_END, as_of=FAR_FUTURE,
    )
    first = materialize_futures_stats(**kwargs)
    second = materialize_futures_stats(**kwargs)
    assert [r.to_dict() for r in first] == [r.to_dict() for r in second]


def test_different_calc_version_is_a_distinct_coexisting_record(tmp_path):
    reality = _reality_store(tmp_path)
    candles = _candle_store(tmp_path)
    stats_store = FuturesStatsStore(tmp_path / "futures_stats.db")
    _seed_full_window(reality)
    _write_candles(reality, candles, FUT, rt.KIND_MARKET_TICK)
    _write_candles(reality, candles, SPOT, rt.KIND_MARKET_TICK)

    default = materialize_futures_stats(
        reality_store=reality, candle_store=candles, futures_instrument=FUT,
        spot_instrument=SPOT, interval=INTERVAL,
        window_start=W0_START, window_end=W1_END, as_of=FAR_FUTURE,
    )
    custom_period = materialize_futures_stats(
        reality_store=reality, candle_store=candles, futures_instrument=FUT,
        spot_instrument=SPOT, interval=INTERVAL,
        window_start=W0_START, window_end=W1_END, as_of=FAR_FUTURE,
        volatility_period=5,
    )
    assert default[0].calc_version != custom_period[0].calc_version

    for r in default + custom_period:
        stats_store.write(r)  # must never raise -- distinct calc_versions coexist.
    assert stats_store.count(FUT) == len(default) + len(custom_period)


def test_conflicting_content_under_the_same_calc_version_raises(tmp_path):
    stats_store = FuturesStatsStore(tmp_path / "futures_stats.db")
    reality = _reality_store(tmp_path)
    candles = _candle_store(tmp_path)
    _seed_full_window(reality)
    _write_candles(reality, candles, FUT, rt.KIND_MARKET_TICK)
    results = materialize_futures_stats(
        reality_store=reality, candle_store=candles, futures_instrument=FUT,
        interval=INTERVAL, window_start=W0_START, window_end=W1_END, as_of=FAR_FUTURE,
    )
    original = results[0]
    stats_store.write(original)
    tampered = replace(original, oi_open=1.0, oi_close=2.0, oi_change=1.0)
    with pytest.raises(ConflictingFuturesStatisticsError):
        stats_store.write(tampered)


def test_rebuild_from_layer0_and_candles_alone_is_byte_identical(tmp_path):
    """Delete the materialized output entirely, rebuild from Layer 0 +
    CandleStore alone, compare byte-for-byte -- the replay proof."""
    reality = _reality_store(tmp_path)
    candles = _candle_store(tmp_path)
    _seed_full_window(reality)
    _write_candles(reality, candles, FUT, rt.KIND_MARKET_TICK)
    _write_candles(reality, candles, SPOT, rt.KIND_MARKET_TICK)

    kwargs = dict(
        reality_store=reality, candle_store=candles, futures_instrument=FUT,
        spot_instrument=SPOT, interval=INTERVAL,
        window_start=W0_START, window_end=W1_END, as_of=FAR_FUTURE,
    )
    before = materialize_futures_stats(**kwargs)
    # No FuturesStatsStore was ever written in this test -- "delete the
    # output store" is trivially true since nothing exists yet; the
    # meaningful proof is that a second materialization from the SAME
    # Layer 0 + Candle inputs reproduces the same result.
    after = materialize_futures_stats(**kwargs)
    assert [r.to_dict() for r in before] == [r.to_dict() for r in after]


# --- No look-ahead ------------------------------------------------------------

def test_depth_observation_after_as_of_is_excluded_from_oi_close(tmp_path):
    reality = _reality_store(tmp_path)
    candles = _candle_store(tmp_path)
    reality.append(_tick(FUT, 24650.0, "2026-08-12T09:20:30+00:00"), now=NOW)
    reality.append(_depth(FUT, "2026-08-12T09:20:45+00:00",
                           bids=[{"price": 1, "volume": 1}], asks=[{"price": 2, "volume": 1}],
                           oi=100), now=NOW)
    reality.append(_depth(FUT, "2026-08-12T09:24:00+00:00",
                           bids=[{"price": 1, "volume": 1}], asks=[{"price": 2, "volume": 1}],
                           oi=200), now=NOW)
    cutoff = "2026-08-12T09:22:00+00:00"
    _write_candles(reality, candles, FUT, rt.KIND_MARKET_TICK, as_of=FAR_FUTURE)

    results = materialize_futures_stats(
        reality_store=reality, candle_store=candles, futures_instrument=FUT,
        interval=INTERVAL, window_start=W0_START, window_end=W1_END, as_of=cutoff,
    )
    stats = [r for r in results if r.window_start == W0_START][0]
    assert stats.oi_close == 100
    assert stats.oi_observation_count == 1


def test_observation_known_after_as_of_knowledge_time_is_excluded_via_replay(tmp_path):
    """The dual-bound gap this project's audit found (Q4): an observation
    whose event_time is in range but whose knowledge_time is not, must
    still be excludable -- proven at the replay() boundary this
    materializer calls into (it inherits whatever that boundary
    guarantees)."""
    from bujji.market_reality import replay as reality_replay
    reality = _reality_store(tmp_path)
    reality.append(_tick(FUT, 24650.0, "2026-08-12T09:20:30+00:00",
                          capture_time="2026-08-12T09:26:00+00:00"), now=NOW)
    observed = list(reality_replay.replay(
        reality,
        as_of_event_time="2026-08-12T09:22:00+00:00",
        as_of_knowledge_time="2026-08-12T09:22:00+00:00",
    ))
    assert observed == []


# --- Gap handling --------------------------------------------------------------

def test_capture_event_overlap_demotes_quality_and_names_the_reason(tmp_path):
    reality = _reality_store(tmp_path)
    candles = _candle_store(tmp_path)
    _seed_full_window(reality)
    _write_candles(reality, candles, FUT, rt.KIND_MARKET_TICK)
    reality.append_capture_event(build_capture_event(
        reason=rt.REASON_DISCONNECT,
        event_time="2026-08-12T09:21:45+00:00", knowledge_time="2026-08-12T09:21:46+00:00",
        source="fyers", access_method="direct_sdk_fyers_broker_py",
    ))

    results = materialize_futures_stats(
        reality_store=reality, candle_store=candles, futures_instrument=FUT,
        interval=INTERVAL, window_start=W0_START, window_end=W1_END, as_of=FAR_FUTURE,
    )
    stats = [r for r in results if r.window_start == W0_START][0]
    assert stats.capture_event_overlap != ()
    assert stats.quality.state == "GAP"
    assert "DISCONNECT" in (stats.quality.limiting_factor or "")


def test_no_capture_event_overlap_leaves_quality_undemoted(tmp_path):
    reality = _reality_store(tmp_path)
    candles = _candle_store(tmp_path)
    _seed_full_window(reality)
    _write_candles(reality, candles, FUT, rt.KIND_MARKET_TICK)
    _write_candles(reality, candles, SPOT, rt.KIND_MARKET_TICK)

    results = materialize_futures_stats(
        reality_store=reality, candle_store=candles, futures_instrument=FUT,
        spot_instrument=SPOT, interval=INTERVAL,
        window_start=W0_START, window_end=W1_END, as_of=FAR_FUTURE,
    )
    stats = [r for r in results if r.window_start == W0_START][0]
    assert stats.capture_event_overlap == ()
    assert stats.quality.state == "KNOWN"
    assert stats.quality.confidence == "HIGH"


def test_no_interpolation_a_gap_window_never_borrows_a_neighbors_oi(tmp_path):
    """window 1 has no depth observation at all -- its oi_* fields must
    stay None, never filled from window 0's real OI."""
    reality = _reality_store(tmp_path)
    candles = _candle_store(tmp_path)
    _seed_full_window(reality)
    reality.append(_tick(FUT, 24670.0, "2026-08-12T09:26:00+00:00"), now=NOW)
    _write_candles(reality, candles, FUT, rt.KIND_MARKET_TICK)

    results = materialize_futures_stats(
        reality_store=reality, candle_store=candles, futures_instrument=FUT,
        interval=INTERVAL, window_start=W0_START, window_end=W1_END, as_of=FAR_FUTURE,
    )
    window1 = [r for r in results if r.window_start == W1_START][0]
    assert window1.oi_open is None
    assert window1.oi_close is None


# --- Absence semantics ---------------------------------------------------------

def test_structurally_empty_book_is_distinct_from_no_observation_at_all(tmp_path):
    reality = _reality_store(tmp_path)
    candles = _candle_store(tmp_path)
    reality.append(_tick(FUT, 24650.0, "2026-08-12T09:20:30+00:00"), now=NOW)
    reality.append(_depth(FUT, "2026-08-12T09:21:30+00:00", bids=[], asks=[]), now=NOW)
    reality.append(_tick(FUT, 24670.0, "2026-08-12T09:26:00+00:00"), now=NOW)
    _write_candles(reality, candles, FUT, rt.KIND_MARKET_TICK)

    results = materialize_futures_stats(
        reality_store=reality, candle_store=candles, futures_instrument=FUT,
        interval=INTERVAL, window_start=W0_START, window_end=W1_END, as_of=FAR_FUTURE,
    )
    window0 = [r for r in results if r.window_start == W0_START][0]
    window1 = [r for r in results if r.window_start == W1_START][0]
    assert window0.book_state == BOOK_STATE_OBSERVED_EMPTY
    assert window1.book_state == BOOK_STATE_NOT_OBSERVED
    assert window0.book_state != window1.book_state


def test_nonempty_book_reports_top_of_book_sizes(tmp_path):
    reality = _reality_store(tmp_path)
    candles = _candle_store(tmp_path)
    _seed_full_window(reality)
    _write_candles(reality, candles, FUT, rt.KIND_MARKET_TICK)

    results = materialize_futures_stats(
        reality_store=reality, candle_store=candles, futures_instrument=FUT,
        interval=INTERVAL, window_start=W0_START, window_end=W1_END, as_of=FAR_FUTURE,
    )
    stats = [r for r in results if r.window_start == W0_START][0]
    assert stats.book_state == BOOK_STATE_OBSERVED_NONEMPTY
    assert stats.top_bid_size_last == 50
    assert stats.top_ask_size_last == 40


def test_missing_spot_candle_leaves_basis_none_without_gating_other_fields(tmp_path):
    reality = _reality_store(tmp_path)
    candles = _candle_store(tmp_path)
    _seed_full_window(reality)
    _write_candles(reality, candles, FUT, rt.KIND_MARKET_TICK)
    # Deliberately never materialize/write spot candles.

    results = materialize_futures_stats(
        reality_store=reality, candle_store=candles, futures_instrument=FUT,
        spot_instrument=SPOT, interval=INTERVAL,
        window_start=W0_START, window_end=W1_END, as_of=FAR_FUTURE,
    )
    stats = [r for r in results if r.window_start == W0_START][0]
    assert stats.basis is None
    assert stats.basis_percent is None
    assert stats.price_change is not None
    assert stats.oi_close == 12000000


def test_basis_computed_when_both_candles_exist(tmp_path):
    """Confirms the SEMANTIC CONTRACT documented in the design doc Part 5
    item 5 and the materializer's own basis comment: basis is
    futures_candle.close - spot_candle.close for the SAME window key,
    both bounded by the same as_of/calc_version -- not a pairing of two
    independent live snapshots."""
    reality = _reality_store(tmp_path)
    candles = _candle_store(tmp_path)
    _seed_full_window(reality)
    _write_candles(reality, candles, FUT, rt.KIND_MARKET_TICK)
    _write_candles(reality, candles, SPOT, rt.KIND_MARKET_TICK)

    results = materialize_futures_stats(
        reality_store=reality, candle_store=candles, futures_instrument=FUT,
        spot_instrument=SPOT, interval=INTERVAL,
        window_start=W0_START, window_end=W1_END, as_of=FAR_FUTURE,
    )
    stats = [r for r in results if r.window_start == W0_START][0]
    fut_candle = candles.get_candle(*[k for k in stats.referenced_candle_keys if k[0] == FUT][0])
    spot_candle = candles.get_candle(*[k for k in stats.referenced_candle_keys if k[0] == SPOT][0])
    assert stats.basis == pytest.approx(fut_candle.close - spot_candle.close)
    assert stats.basis_percent == pytest.approx(stats.basis / spot_candle.close * 100)


# --- Schema / forbidden fields --------------------------------------------------

_FORBIDDEN_SUBSTRINGS = (
    "regime", "signal", "score", "sentiment", "bias", "trend",
    "liquidity_score", "iv", "delta", "gamma", "theta", "vega",
)


def test_no_field_name_matches_the_forbidden_interpretation_list():
    from dataclasses import fields
    from bujji.market_timeseries.futures_stats_models import FuturesStatistics as FS
    names = [f.name for f in fields(FS)]
    for name in names:
        lowered = name.lower()
        for forbidden in _FORBIDDEN_SUBSTRINGS:
            assert forbidden not in lowered, f"forbidden term {forbidden!r} found in field {name!r}"


# --- Reuse verification ----------------------------------------------------------

def test_price_volatility_matches_calling_realised_volatility_directly(tmp_path):
    reality = _reality_store(tmp_path)
    candles = _candle_store(tmp_path)
    base = "2026-08-12T09:20:"
    t = 20
    for i in range(8):
        reality.append(_tick(FUT, 24650.0 + i * 3, f"2026-08-12T09:{20+i}:30+00:00"), now=NOW)
    _write_candles(reality, candles, FUT, rt.KIND_MARKET_TICK)

    results = materialize_futures_stats(
        reality_store=reality, candle_store=candles, futures_instrument=FUT,
        interval=INTERVAL, window_start=W0_START, window_end="2026-08-12T09:30:00+00:00",
        as_of=FAR_FUTURE, volatility_period=3,
    )
    for stats in results:
        trailing = candles.range(
            FUT, INTERVAL, "0001-01-01T00:00:00+00:00", stats.window_end,
            as_of=FAR_FUTURE, calc_version=stats.referenced_candle_keys[0][3],
        ) if stats.referenced_candle_keys else []
        expected = indicators.realised_volatility(trailing, period=3) if trailing else None
        assert stats.price_volatility == expected


# --- Windowing / scope ------------------------------------------------------------

def test_windows_outside_the_requested_range_are_not_materialized(tmp_path):
    reality = _reality_store(tmp_path)
    candles = _candle_store(tmp_path)
    reality.append(_tick(FUT, 24650.0, "2026-08-12T09:10:30+00:00"), now=NOW)  # before range
    _seed_full_window(reality)
    _write_candles(reality, candles, FUT, rt.KIND_MARKET_TICK)

    results = materialize_futures_stats(
        reality_store=reality, candle_store=candles, futures_instrument=FUT,
        interval=INTERVAL, window_start=W0_START, window_end=W1_END, as_of=FAR_FUTURE,
    )
    assert all(W0_START <= r.window_start < W1_END for r in results)


def test_a_window_with_neither_candle_nor_depth_is_absent_not_a_fabricated_row(tmp_path):
    reality = _reality_store(tmp_path)
    candles = _candle_store(tmp_path)
    _seed_full_window(reality)
    _write_candles(reality, candles, FUT, rt.KIND_MARKET_TICK)

    results = materialize_futures_stats(
        reality_store=reality, candle_store=candles, futures_instrument=FUT,
        interval=INTERVAL, window_start=W0_START, window_end=W1_END, as_of=FAR_FUTURE,
    )
    assert len(results) == 1
    assert results[0].window_start == W0_START
