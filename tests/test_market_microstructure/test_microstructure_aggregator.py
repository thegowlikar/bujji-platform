"""Phase 19.20.2 -- MicrostructureAggregator tests.

Synthetic tick fixtures only -- no broker connection, no FYERS, no
systemd. Covers the 5 required categories: normal aggregation, zero-tick
gap behaviour, out-of-order ticks, option premium metadata, and quality
scoring.
"""
from __future__ import annotations

from bujji.market_microstructure.microstructure_aggregator import (
    MIN_HEALTHY_TICKS,
    SILENCE_TOLERANCE_SECONDS,
    MicrostructureAggregator,
)
from bujji.market_microstructure.models import KIND_OPTION, KIND_SPOT, OPTION_TYPE_CE

DAY = "2026-08-17"


def _ts(hms: str) -> str:
    """Shorthand 'HH:MM:SS' -> a real ISO 8601 timestamp on DAY, IST."""
    return f"{DAY}T{hms}+05:30"


# --------------------------------------------------------------------- #
# A) Normal minute aggregation
# --------------------------------------------------------------------- #
def test_normal_minute_aggregation_produces_correct_ohlc_and_microstructure():
    agg = MicrostructureAggregator()
    agg.ingest("NSE:NIFTY50-INDEX", _ts("09:15:01"), 24500, kind=KIND_SPOT)
    agg.ingest("NSE:NIFTY50-INDEX", _ts("09:15:10"), 24502, kind=KIND_SPOT)
    agg.ingest("NSE:NIFTY50-INDEX", _ts("09:15:40"), 24498, kind=KIND_SPOT)
    emitted = agg.ingest("NSE:NIFTY50-INDEX", _ts("09:15:59"), 24501, kind=KIND_SPOT)
    assert emitted is None  # no rollover yet -- all four ticks are in the same minute

    results = agg.flush()
    obs = results["NSE:NIFTY50-INDEX"]
    assert obs is not None
    assert obs.open == 24500
    assert obs.high == 24502
    assert obs.low == 24498
    assert obs.close == 24501
    assert obs.tick_count == 4
    assert obs.max_tick_silence_seconds == 30.0     # gap between 09:15:10 and 09:15:40
    assert obs.max_price_move == 4.0                # 24502 -> 24498
    assert obs.session_date == DAY
    assert obs.window_start.startswith(f"{DAY}T09:15:00")
    assert obs.first_tick_timestamp == _ts("09:15:01")
    assert obs.last_tick_timestamp == _ts("09:15:59")
    assert obs.rejected_tick_count == 0


def test_rollover_via_next_minute_tick_emits_prior_minute_without_flush():
    agg = MicrostructureAggregator()
    agg.ingest("NSE:NIFTY50-INDEX", _ts("09:15:05"), 24500, kind=KIND_SPOT)
    agg.ingest("NSE:NIFTY50-INDEX", _ts("09:15:45"), 24505, kind=KIND_SPOT)
    emitted = agg.ingest("NSE:NIFTY50-INDEX", _ts("09:16:05"), 24510, kind=KIND_SPOT)
    assert emitted is not None
    assert emitted.tick_count == 2
    assert emitted.close == 24505
    assert emitted.window_start.startswith(f"{DAY}T09:15:00")

    # the 09:16 tick opened a new window, not yet closed
    still_open = agg.flush()
    assert still_open["NSE:NIFTY50-INDEX"].tick_count == 1
    assert still_open["NSE:NIFTY50-INDEX"].window_start.startswith(f"{DAY}T09:16:00")


# --------------------------------------------------------------------- #
# B) Zero tick minute
# --------------------------------------------------------------------- #
def test_zero_tick_minute_produces_no_observation_never_a_synthetic_bar():
    agg = MicrostructureAggregator()
    # No ticks ingested at all for this instrument.
    results = agg.flush()
    assert results == {}  # nothing was ever tracked -- no fabricated entry either


def test_a_window_that_never_receives_a_single_valid_tick_emits_nothing():
    agg = MicrostructureAggregator()
    # Only an invalid tick arrives (price <= 0) -- must not become a
    # fabricated flat candle at some previous close, and must not open
    # a trackable window at all.
    rejected = agg.ingest("NSE:NIFTY50-INDEX", _ts("09:20:00"), -1, kind=KIND_SPOT)
    assert rejected is None
    assert "NSE:NIFTY50-INDEX" not in agg.tracked_instruments
    results = agg.flush()
    assert results == {}
    assert agg.rejected_count("NSE:NIFTY50-INDEX") == 1


# --------------------------------------------------------------------- #
# C) Tick ordering
# --------------------------------------------------------------------- #
def test_out_of_order_ticks_are_handled_deterministically_no_corruption():
    agg = MicrostructureAggregator()
    # Arrival order is scrambled relative to timestamp order.
    agg.ingest("NSE:NIFTY50-INDEX", _ts("09:15:40"), 24498, kind=KIND_SPOT)
    agg.ingest("NSE:NIFTY50-INDEX", _ts("09:15:01"), 24500, kind=KIND_SPOT)
    agg.ingest("NSE:NIFTY50-INDEX", _ts("09:15:59"), 24501, kind=KIND_SPOT)
    agg.ingest("NSE:NIFTY50-INDEX", _ts("09:15:10"), 24502, kind=KIND_SPOT)

    obs = agg.flush()["NSE:NIFTY50-INDEX"]
    # Open/close must reflect chronological order, not arrival order.
    assert obs.open == 24500     # earliest timestamp (09:15:01), not first-arrived (09:15:40)
    assert obs.close == 24501    # latest timestamp (09:15:59), not last-arrived (09:15:10)
    assert obs.high == 24502
    assert obs.low == 24498
    assert obs.tick_count == 4
    assert obs.first_tick_timestamp == _ts("09:15:01")
    assert obs.last_tick_timestamp == _ts("09:15:59")


def test_out_of_order_ticks_produce_identical_result_regardless_of_arrival_order():
    """Determinism proof: two aggregators fed the same ticks in
    different arrival orders must produce byte-identical observations."""
    ticks = [
        (_ts("09:15:40"), 24498), (_ts("09:15:01"), 24500),
        (_ts("09:15:59"), 24501), (_ts("09:15:10"), 24502),
    ]
    agg1 = MicrostructureAggregator()
    for ts, price in ticks:
        agg1.ingest("NSE:NIFTY50-INDEX", ts, price, kind=KIND_SPOT)
    obs1 = agg1.flush()["NSE:NIFTY50-INDEX"]

    agg2 = MicrostructureAggregator()
    for ts, price in reversed(ticks):
        agg2.ingest("NSE:NIFTY50-INDEX", ts, price, kind=KIND_SPOT)
    obs2 = agg2.flush()["NSE:NIFTY50-INDEX"]

    assert obs1.to_dict() == obs2.to_dict()


# --------------------------------------------------------------------- #
# D) Option premium metadata
# --------------------------------------------------------------------- #
def test_option_observation_calculates_premium_movement_and_preserves_open_interest():
    agg = MicrostructureAggregator()
    symbol = "NSE:NIFTY2582624500CE"
    agg.ingest(symbol, _ts("09:15:01"), 120.5, kind=KIND_OPTION,
               open_interest=45000, strike=24500, option_type=OPTION_TYPE_CE)
    agg.ingest(symbol, _ts("09:15:20"), 125.0, kind=KIND_OPTION,
               open_interest=45500, strike=24500, option_type=OPTION_TYPE_CE)
    agg.ingest(symbol, _ts("09:15:50"), 118.0, kind=KIND_OPTION,
               open_interest=46000, strike=24500, option_type=OPTION_TYPE_CE)

    obs = agg.flush()[symbol]
    assert obs.kind == KIND_OPTION
    assert obs.option_type == OPTION_TYPE_CE
    assert obs.strike == 24500
    assert obs.max_premium_move == 7.0            # |118.0 - 125.0|
    assert obs.max_price_move == 7.0               # same computation, generic field
    assert obs.open_interest == 46000               # latest value wins, same convention as CandleAggregator


def test_non_option_instrument_never_populates_premium_move():
    agg = MicrostructureAggregator()
    agg.ingest("NSE:NIFTY50-INDEX", _ts("09:15:01"), 24500, kind=KIND_SPOT)
    agg.ingest("NSE:NIFTY50-INDEX", _ts("09:15:30"), 24510, kind=KIND_SPOT)
    obs = agg.flush()["NSE:NIFTY50-INDEX"]
    assert obs.max_premium_move is None
    assert obs.strike is None
    assert obs.option_type is None


# --------------------------------------------------------------------- #
# E) Quality score
# --------------------------------------------------------------------- #
def test_healthy_minute_scores_100():
    agg = MicrostructureAggregator()
    # 6 ticks, 10s apart, well within silence tolerance -> full density, no penalty.
    for i in range(MIN_HEALTHY_TICKS):
        agg.ingest("NSE:NIFTY50-INDEX", _ts(f"09:15:{i * 10:02d}"), 24500 + i, kind=KIND_SPOT)
    obs = agg.flush()["NSE:NIFTY50-INDEX"]
    assert obs.observation_quality_score == 100.0


def test_degraded_minute_scores_lower_than_healthy():
    agg = MicrostructureAggregator()
    # Only 3 ticks (half of MIN_HEALTHY_TICKS), gaps within tolerance -> density penalty only.
    agg.ingest("NSE:NIFTY50-INDEX", _ts("09:15:00"), 24500, kind=KIND_SPOT)
    agg.ingest("NSE:NIFTY50-INDEX", _ts("09:15:15"), 24501, kind=KIND_SPOT)
    agg.ingest("NSE:NIFTY50-INDEX", _ts("09:15:30"), 24502, kind=KIND_SPOT)
    obs = agg.flush()["NSE:NIFTY50-INDEX"]
    assert 0.0 < obs.observation_quality_score < 100.0
    assert obs.observation_quality_score == round(100.0 * 3 / MIN_HEALTHY_TICKS, 2)


def test_silent_minute_with_a_large_gap_is_penalised_below_density_alone():
    agg = MicrostructureAggregator()
    agg.ingest("NSE:NIFTY50-INDEX", _ts("09:15:00"), 24500, kind=KIND_SPOT)
    # Silence far beyond SILENCE_TOLERANCE_SECONDS.
    agg.ingest("NSE:NIFTY50-INDEX", _ts("09:15:59"), 24501, kind=KIND_SPOT)
    obs = agg.flush()["NSE:NIFTY50-INDEX"]
    assert obs.max_tick_silence_seconds == 59.0
    assert obs.max_tick_silence_seconds > SILENCE_TOLERANCE_SECONDS
    assert obs.observation_quality_score < round(100.0 * 2 / MIN_HEALTHY_TICKS, 2)


def test_invalid_ticks_are_rejected_and_never_silently_included():
    agg = MicrostructureAggregator()
    agg.ingest("NSE:NIFTY50-INDEX", _ts("09:15:00"), 24500, kind=KIND_SPOT)
    rejected = agg.ingest("NSE:NIFTY50-INDEX", _ts("09:15:10"), -50, kind=KIND_SPOT)   # invalid: non-positive price
    assert rejected is None
    agg.ingest("NSE:NIFTY50-INDEX", _ts("09:15:20"), 24505, kind=KIND_SPOT)

    obs = agg.flush()["NSE:NIFTY50-INDEX"]
    assert obs.tick_count == 2                     # the invalid tick never entered the window
    assert obs.rejected_tick_count == 1
    assert obs.high == 24505 and obs.low == 24500   # -50 never pollutes OHLC
    assert agg.rejected_count("NSE:NIFTY50-INDEX") == 1


def test_malformed_timestamp_is_rejected_not_silently_coerced():
    agg = MicrostructureAggregator()
    rejected = agg.ingest("NSE:NIFTY50-INDEX", "not-a-timestamp", 24500, kind=KIND_SPOT)
    assert rejected is None
    assert agg.rejected_count("NSE:NIFTY50-INDEX") == 1
    assert "NSE:NIFTY50-INDEX" not in agg.tracked_instruments
