"""Phase 15Q -- Market Timeseries tests: OHLC correctness, window
boundaries, gap honesty, store immutability, restart, and indicator
epistemics."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from bujji.market_timeseries import indicators as ta
from bujji.market_timeseries.aggregator import CandleAggregator, window_bounds
from bujji.market_timeseries.models import Candle, FormingCandle, INTERVAL_FIVE_MINUTE, KIND_OPTION, KIND_SPOT
from bujji.market_timeseries.store import CandleStore, ConflictingCandleError
from bujji.market_timeseries.subscription import (
    atm_strike, build_subscription, option_symbol, strike_band, subscription_symbols,
)

SPOT = "NSE:NIFTY50-INDEX"
FAR_FUTURE = "2099-01-01T00:00:00"  # as_of far past any test candle's window_end.


def ts(minute: int, second: int = 0) -> str:
    return (datetime(2026, 8, 6, 9, 15) + timedelta(minutes=minute, seconds=second)).isoformat()


# --- Window boundaries ----------------------------------------------------
def test_windows_align_to_wall_clock_not_first_tick():
    """A tick at 09:17:33 belongs to the 09:15-09:20 window -- boundaries
    are clock-aligned so a restart reproduces identical windows."""
    start, end = window_bounds("2026-08-06T09:17:33")
    assert start == "2026-08-06T09:15:00"
    assert end == "2026-08-06T09:20:00"


def test_window_bounds_are_deterministic():
    assert window_bounds("2026-08-06T09:17:33") == window_bounds("2026-08-06T09:17:33")


def test_unsupported_interval_rejected():
    with pytest.raises(ValueError):
        window_bounds("2026-08-06T09:17:33", "THREE_MINUTE")


# --- OHLC correctness -----------------------------------------------------
def test_ohlc_is_exact_from_ticks():
    agg = CandleAggregator()
    for i, price in enumerate([100.0, 130.0, 90.0, 110.0]):
        agg.ingest(SPOT, ts(0, i * 10), price)
    candles = agg.flush()
    assert len(candles) == 1
    c = candles[0]
    assert (c.open, c.high, c.low, c.close) == (100.0, 130.0, 90.0, 110.0)
    assert c.tick_count == 4


def test_single_tick_produces_a_valid_flat_candle():
    """One real tick is real evidence -- O=H=L=C is correct here, and
    tick_count=1 tells the consumer how thin it is."""
    agg = CandleAggregator()
    agg.ingest(SPOT, ts(0), 100.0)
    c = agg.flush()[0]
    assert (c.open, c.high, c.low, c.close) == (100.0,) * 4
    assert c.tick_count == 1


def test_rollover_emits_previous_candle():
    agg = CandleAggregator()
    agg.ingest(SPOT, ts(0), 100.0)
    agg.ingest(SPOT, ts(1), 105.0)
    emitted = agg.ingest(SPOT, ts(6), 110.0)  # crosses into the next 5-min window
    assert emitted is not None
    assert emitted.window_start == "2026-08-06T09:15:00"
    assert emitted.close == 105.0


def test_volume_summed_but_none_when_never_reported():
    agg = CandleAggregator()
    agg.ingest(SPOT, ts(0), 100.0, volume=10.0)
    agg.ingest(SPOT, ts(1), 101.0, volume=5.0)
    assert agg.flush()[0].volume == 15.0

    agg2 = CandleAggregator()
    agg2.ingest(SPOT, ts(0), 100.0)
    assert agg2.flush()[0].volume is None  # never coerced to 0.0


# --- Gap honesty (the core epistemic rule) --------------------------------
def test_empty_window_produces_no_candle():
    """A window with zero ticks must yield nothing -- never a synthetic
    flat bar carried from the previous close."""
    agg = CandleAggregator()
    assert agg.flush() == []


def test_gap_in_series_is_absent_not_forward_filled(tmp_path):
    store = CandleStore(tmp_path / "c.db")
    agg = CandleAggregator(on_candle=store.write_candle)
    agg.ingest(SPOT, ts(0), 100.0)
    agg.ingest(SPOT, ts(20), 120.0)   # 09:35 -- three windows skipped entirely
    agg.flush()
    bars = store.recent(SPOT, INTERVAL_FIVE_MINUTE, 100, as_of=FAR_FUTURE, calc_version="")
    assert len(bars) == 2                       # only the two windows that had real ticks
    assert not ta.series_is_contiguous(bars)    # and the discontinuity is visible
    store.close()


def test_contiguous_series_detected():
    collected = []
    agg = CandleAggregator(on_candle=collected.append)
    for w in range(4):
        agg.ingest(SPOT, ts(w * 5), 100.0 + w)
    agg.flush()
    bars = sorted(collected, key=lambda c: c.window_start)
    assert len(bars) == 4          # guard: a 1-bar series would pass contiguity trivially
    assert ta.series_is_contiguous(bars)


# --- Multi-instrument routing --------------------------------------------
def test_instruments_are_isolated():
    agg = CandleAggregator()
    agg.ingest(SPOT, ts(0), 24500.0, kind=KIND_SPOT)
    agg.ingest("NSE:NIFTY2580724500CE", ts(0), 120.0, kind=KIND_OPTION)
    candles = {c.instrument: c for c in agg.flush()}
    assert candles[SPOT].close == 24500.0
    assert candles["NSE:NIFTY2580724500CE"].close == 120.0
    assert candles["NSE:NIFTY2580724500CE"].kind == KIND_OPTION


def test_instruments_share_a_common_time_axis_despite_different_start_times():
    """Two series that begin streaming at different moments inside the
    same window must still land on the SAME window_start -- otherwise
    cross-instrument analysis silently compares misaligned bars."""
    agg = CandleAggregator()
    agg.ingest(SPOT, ts(0, 5), 24500.0)
    agg.ingest("NSE:NIFTY2580724500CE", ts(3, 40), 120.0)
    starts = {c.window_start for c in agg.flush()}
    assert starts == {"2026-08-06T09:15:00"}


# --- Live forming candle --------------------------------------------------
def test_forming_candle_reflects_ticks_so_far():
    agg = CandleAggregator()
    agg.ingest(SPOT, ts(0), 100.0)
    agg.ingest(SPOT, ts(1), 130.0)
    agg.ingest(SPOT, ts(2), 90.0)
    f = agg.forming(SPOT)
    assert (f.open, f.high, f.low, f.last) == (100.0, 130.0, 90.0, 90.0)
    assert f.tick_count == 3
    assert f.is_closed is False


def test_forming_candle_is_a_distinct_type_without_a_close_field():
    """Structural guard against the classic live-vs-replay bug: an
    in-progress bar must not be consumable as a settled Candle."""
    agg = CandleAggregator()
    agg.ingest(SPOT, ts(0), 100.0)
    f = agg.forming(SPOT)
    assert isinstance(f, FormingCandle)
    assert not isinstance(f, Candle)
    assert not hasattr(f, "close")


def test_forming_is_none_before_any_tick():
    assert CandleAggregator().forming(SPOT) is None


# --- Store: immutability, idempotency, restart ---------------------------
def _candle(start="2026-08-06T09:15:00", close=110.0):
    return Candle(instrument=SPOT, kind=KIND_SPOT, interval=INTERVAL_FIVE_MINUTE,
                  window_start=start, window_end="2026-08-06T09:20:00",
                  open=100.0, high=130.0, low=90.0, close=close, volume=None, tick_count=4)


def test_identical_rewrite_is_idempotent(tmp_path):
    store = CandleStore(tmp_path / "c.db")
    assert store.write_candle(_candle()) is True
    assert store.write_candle(_candle()) is False   # no error, no duplicate
    assert store.count() == 1
    store.close()


def test_conflicting_rewrite_is_rejected(tmp_path):
    store = CandleStore(tmp_path / "c.db")
    store.write_candle(_candle(close=110.0))
    with pytest.raises(ConflictingCandleError):
        store.write_candle(_candle(close=999.0))
    assert store.get_candle(SPOT, INTERVAL_FIVE_MINUTE, "2026-08-06T09:15:00").close == 110.0
    store.close()


def test_store_survives_restart(tmp_path):
    path = tmp_path / "c.db"
    s1 = CandleStore(path)
    s1.write_candle(_candle())
    s1.close()
    s2 = CandleStore(path)       # fresh handle, same file -- simulates a process restart
    assert s2.count() == 1
    assert s2.get_candle(SPOT, INTERVAL_FIVE_MINUTE, "2026-08-06T09:15:00").close == 110.0
    s2.close()


def test_recent_returns_oldest_first_and_never_pads(tmp_path):
    store = CandleStore(tmp_path / "c.db")
    agg = CandleAggregator(on_candle=store.write_candle)
    for w in range(3):
        agg.ingest(SPOT, ts(w * 5), 100.0 + w)
    agg.flush()
    bars = store.recent(SPOT, INTERVAL_FIVE_MINUTE, 50, as_of=FAR_FUTURE, calc_version="")
    assert len(bars) == 3                                    # asked for 50, got the real 3
    assert [b.window_start for b in bars] == sorted(b.window_start for b in bars)
    store.close()


def test_range_query_is_half_open(tmp_path):
    store = CandleStore(tmp_path / "c.db")
    agg = CandleAggregator(on_candle=store.write_candle)
    for w in range(4):
        agg.ingest(SPOT, ts(w * 5), 100.0 + w)
    agg.flush()
    bars = store.range(SPOT, INTERVAL_FIVE_MINUTE, "2026-08-06T09:20:00", "2026-08-06T09:30:00",
                       as_of=FAR_FUTURE, calc_version="")
    assert [b.window_start for b in bars] == ["2026-08-06T09:20:00", "2026-08-06T09:25:00"]
    store.close()


# --- Provenance (17F.0/17F.1) ---------------------------------------------
def test_calc_version_joins_the_primary_key_so_versions_coexist(tmp_path):
    """A re-materialization under a CHANGED calc_version must not collide
    with the old one -- two calculation versions disagreeing is expected,
    not an error."""
    store = CandleStore(tmp_path / "c.db")
    base = _candle(close=110.0)
    v2 = Candle(**{**base.__dict__, "close": 111.0, "calc_version": "CV-v2"})
    assert store.write_candle(base) is True
    assert store.write_candle(v2) is True   # different calc_version -- no ConflictingCandleError
    assert store.count() == 2
    store.close()


def test_same_calc_version_disagreeing_with_itself_still_raises(tmp_path):
    """Unlike a version change, the SAME calc_version producing different
    content for the same window is always a genuine defect."""
    store = CandleStore(tmp_path / "c.db")
    store.write_candle(_candle(close=110.0))
    with pytest.raises(ConflictingCandleError):
        store.write_candle(_candle(close=999.0))
    store.close()


def test_get_candle_exact_key_includes_calc_version(tmp_path):
    store = CandleStore(tmp_path / "c.db")
    base = _candle(close=110.0)
    v2 = Candle(**{**base.__dict__, "close": 111.0, "calc_version": "CV-v2"})
    store.write_candle(base)
    store.write_candle(v2)
    assert store.get_candle(SPOT, INTERVAL_FIVE_MINUTE, base.window_start).close == 110.0
    assert store.get_candle(
        SPOT, INTERVAL_FIVE_MINUTE, base.window_start, calc_version="CV-v2"
    ).close == 111.0
    store.close()


def test_recent_never_mixes_calc_versions(tmp_path):
    store = CandleStore(tmp_path / "c.db")
    base = _candle(close=110.0)
    v2 = Candle(**{**base.__dict__, "close": 111.0, "calc_version": "CV-v2"})
    store.write_candle(base)
    store.write_candle(v2)
    default_only = store.recent(SPOT, INTERVAL_FIVE_MINUTE, 50, as_of=FAR_FUTURE, calc_version="")
    v2_only = store.recent(SPOT, INTERVAL_FIVE_MINUTE, 50, as_of=FAR_FUTURE, calc_version="CV-v2")
    assert [b.close for b in default_only] == [110.0]
    assert [b.close for b in v2_only] == [111.0]
    store.close()


def test_calc_versions_lists_every_version_written(tmp_path):
    store = CandleStore(tmp_path / "c.db")
    base = _candle(close=110.0)
    v2 = Candle(**{**base.__dict__, "close": 111.0, "calc_version": "CV-v2"})
    store.write_candle(base)
    store.write_candle(v2)
    assert store.calc_versions(SPOT, INTERVAL_FIVE_MINUTE) == ["", "CV-v2"]
    store.close()


def test_as_of_excludes_a_candle_whose_window_has_not_closed_yet(tmp_path):
    """A candle's data is not knowable until its own window_end -- the
    no-lookahead guarantee applied at the read boundary."""
    store = CandleStore(tmp_path / "c.db")
    store.write_candle(_candle())  # window_end = 2026-08-06T09:20:00
    before_close = store.recent(
        SPOT, INTERVAL_FIVE_MINUTE, 50, as_of="2026-08-06T09:18:00", calc_version=""
    )
    at_close = store.recent(
        SPOT, INTERVAL_FIVE_MINUTE, 50, as_of="2026-08-06T09:20:00", calc_version=""
    )
    assert before_close == []
    assert len(at_close) == 1
    store.close()


def test_provenance_fields_round_trip_through_storage(tmp_path):
    store = CandleStore(tmp_path / "c.db")
    candle = Candle(
        instrument=SPOT, kind=KIND_SPOT, interval=INTERVAL_FIVE_MINUTE,
        window_start="2026-08-06T09:15:00", window_end="2026-08-06T09:20:00",
        open=100.0, high=130.0, low=90.0, close=110.0, volume=None, tick_count=4,
        source_observation_ids=("OBS-a", "OBS-b"), materializer_id="tick_to_candle",
        calc_version="CV-abc123", first_event_time="2026-08-06T09:15:02",
        last_event_time="2026-08-06T09:19:58", knowledge_boundary="2026-08-06T09:20:05",
        capture_event_overlap=("CAP-xyz",),
    )
    store.write_candle(candle)
    restored = store.get_candle(SPOT, INTERVAL_FIVE_MINUTE, candle.window_start, "CV-abc123")
    assert restored.source_observation_ids == ("OBS-a", "OBS-b")
    assert restored.materializer_id == "tick_to_candle"
    assert restored.first_event_time == "2026-08-06T09:15:02"
    assert restored.last_event_time == "2026-08-06T09:19:58"
    assert restored.knowledge_boundary == "2026-08-06T09:20:05"
    assert restored.capture_event_overlap == ("CAP-xyz",)
    store.close()


def test_instruments_listing_filters_by_kind(tmp_path):
    store = CandleStore(tmp_path / "c.db")
    agg = CandleAggregator(on_candle=store.write_candle)
    agg.ingest(SPOT, ts(0), 24500.0, kind=KIND_SPOT)
    agg.ingest("NSE:NIFTY2580724500CE", ts(0), 120.0, kind=KIND_OPTION)
    agg.flush()
    assert store.instruments(kind=KIND_OPTION) == ["NSE:NIFTY2580724500CE"]
    assert len(store.instruments()) == 2
    store.close()


# --- Subscription ---------------------------------------------------------
def test_strike_band_is_symmetric_and_deterministic():
    band = strike_band(24512.0, strikes_each_side=3, step=50)
    assert band == [24350, 24400, 24450, 24500, 24550, 24600, 24650]
    assert band == strike_band(24512.0, 3, 50)


def test_atm_rounds_to_nearest_strike():
    assert atm_strike(24512.0, 50) == 24500
    assert atm_strike(24526.0, 50) == 24550


def test_subscription_contains_spot_vix_and_both_option_types():
    subs = build_subscription(spot=24512.0, expiry_code="25807")
    symbols = [s for s, _ in subs]
    assert "NSE:NIFTY50-INDEX" in symbols
    assert "NSE:INDIAVIX-INDEX" in symbols
    assert len(subs) == 2 + (7 * 2)          # spot + vix + 7 strikes x CE/PE
    assert sum(1 for s in symbols if s.endswith("CE")) == 7
    assert sum(1 for s in symbols if s.endswith("PE")) == 7


def test_option_symbol_rejects_bad_type():
    with pytest.raises(ValueError):
        option_symbol("NIFTY", "25807", 24500, "XX")


def test_subscription_symbols_is_flat_list():
    assert all(isinstance(s, str) for s in subscription_symbols(spot=24512.0, expiry_code="25807"))


# --- Indicator epistemics -------------------------------------------------
def _series(prices):
    """Collects candles emitted on rollover AND at flush -- `ingest`
    returns the previous window's candle when a tick rolls it shut, so
    dropping that return value would silently keep only the last bar."""
    collected = []
    agg = CandleAggregator(on_candle=collected.append)
    for w, p in enumerate(prices):
        agg.ingest(SPOT, ts(w * 5), p)
    agg.flush()
    return sorted(collected, key=lambda c: c.window_start)


def test_indicators_return_none_on_insufficient_history():
    bars = _series([100.0, 101.0, 102.0])
    assert ta.sma(bars, 20) is None
    assert ta.ema(bars, 20) is None
    assert ta.rsi(bars, 14) is None
    assert ta.atr(bars, 14) is None
    assert ta.bollinger(bars, 20) is None
    assert ta.realised_volatility(bars, 20) is None


def test_sma_is_exact():
    bars = _series([float(p) for p in range(1, 11)])
    assert ta.sma(bars, 5) == pytest.approx((6 + 7 + 8 + 9 + 10) / 5)


def test_rsi_all_gains_is_100():
    bars = _series([float(p) for p in range(1, 30)])
    assert ta.rsi(bars, 14) == 100.0


def test_rsi_within_bounds():
    bars = _series([100.0 + ((i * 7) % 13) for i in range(40)])
    value = ta.rsi(bars, 14)
    assert 0.0 <= value <= 100.0


def test_atr_is_positive_and_uses_real_high_low():
    bars = _series([100.0 + ((i * 5) % 9) for i in range(30)])
    assert ta.atr(bars, 14) > 0


def test_bollinger_bands_are_ordered():
    bars = _series([100.0 + ((i * 3) % 7) for i in range(30)])
    lower, middle, upper = ta.bollinger(bars, 20)
    assert lower <= middle <= upper


def test_indicators_reject_nonpositive_period():
    bars = _series([100.0] * 30)
    for fn in (ta.sma, ta.ema, ta.rsi, ta.atr, ta.bollinger, ta.realised_volatility):
        with pytest.raises(ValueError):
            fn(bars, 0)


# --------------------------------------------------------------------- #
# ADX -- Phase 20.1B
# --------------------------------------------------------------------- #
def _hl_series(highs_lows_closes):
    """Candles with real, independent high/low/close (unlike `_series`,
    whose CandleAggregator-driven bars collapse high==low==close) --
    needed for ADX, which is undefined without real intra-bar range."""
    return [
        Candle(
            instrument=SPOT, kind=KIND_SPOT, interval=INTERVAL_FIVE_MINUTE,
            window_start=ts(i * 5), window_end=ts(i * 5 + 5),
            open=c, high=h, low=l, close=c, volume=None, tick_count=1,
        )
        for i, (h, l, c) in enumerate(highs_lows_closes)
    ]


def test_adx_returns_none_below_minimum_bars():
    bars = _hl_series([(101.0, 99.0, 100.0)] * 10)
    assert ta.adx(bars, 14) is None


def test_adx_rejects_nonpositive_period():
    bars = _hl_series([(101.0, 99.0, 100.0)] * 40)
    with pytest.raises(ValueError):
        ta.adx(bars, 0)


def test_adx_higher_for_a_steadily_trending_series_than_a_choppy_one():
    trending = []
    price = 100.0
    for i in range(40):
        price += 0.8
        trending.append((price + 0.2, price - 0.2, price))

    choppy = []
    price = 100.0
    for i in range(40):
        price += 0.8 if i % 2 == 0 else -0.8
        choppy.append((price + 0.2, price - 0.2, price))

    trending_adx = ta.adx(_hl_series(trending), 14)
    choppy_adx = ta.adx(_hl_series(choppy), 14)
    assert trending_adx is not None and choppy_adx is not None
    assert trending_adx > choppy_adx
