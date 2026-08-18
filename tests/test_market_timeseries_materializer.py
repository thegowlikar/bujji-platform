"""Phase 17F.1 -- Tick to Candle Materializer.

Covers the properties that make a component a MATERIALIZER under the
Phase 17F design: deterministic, replayable, lineage-preserving, no
look-ahead. Also covers the two provenance behaviours specific to this
materializer: gap-overlap flagging, and that CandleAggregator's own
arithmetic is genuinely untouched (same OHLC/gap-honesty behaviour as
the pre-existing Phase 15Q tests, just now provenance-stamped).
"""
from dataclasses import replace

from bujji.market_reality import taxonomy as rtax
from bujji.market_reality.capture import build_raw_observation
from bujji.market_reality.capture_events import build_capture_event
from bujji.market_reality.certification import StaticCertificationGate
from bujji.market_reality.store import RawObservationStore
from bujji.market_timeseries.materializer import (
    MATERIALIZER_ID,
    default_calc_version,
    materialize_candles,
)
from bujji.market_timeseries.models import INTERVAL_FIVE_MINUTE, KIND_SPOT

CERT = rtax.CERTIFIED_AVAILABLE
SPOT = "NSE:NIFTY50-INDEX"
NOW = "2026-08-06T09:30:00+00:00"
FAR_FUTURE = "2099-01-01T00:00:00+00:00"


def _gate():
    return StaticCertificationGate(CERT)


def _tick(ltp, event_time, kind=rtax.KIND_QUOTE, instrument=SPOT):
    return build_raw_observation(
        kind=kind,
        instrument=instrument,
        instrument_type=rtax.INSTRUMENT_SPOT,
        payload={"ltp": ltp},
        source="fyers",
        access_method="direct_sdk_fyers_broker_py",
        capture_timestamp=event_time,
        event_timestamp=event_time,
        certification_status=CERT,
        identity_fields={},
    )


def _seeded_store(tmp_path, ticks):
    """`now` for each append must be at or after that tick's own
    capture_timestamp, or the validator correctly rejects it as
    CAPTURE_IN_FUTURE -- a fixed, too-early `now` here would be a test
    fixture bug, not a materializer defect."""
    store = RawObservationStore(tmp_path, _gate())
    for ltp, t in ticks:
        store.append(_tick(ltp, t), now=NOW if NOW >= t else t)
    return store


# --- Reuse: CandleAggregator's own arithmetic is untouched -----------------
def test_ohlc_matches_the_existing_aggregator_exactly(tmp_path):
    """Same first/max/min/last rule Phase 15Q already proved -- this
    materializer must not reimplement or subtly alter it."""
    store = _seeded_store(tmp_path, [
        (100.0, "2026-08-06T09:15:00+00:00"),
        (130.0, "2026-08-06T09:16:00+00:00"),
        (90.0, "2026-08-06T09:17:00+00:00"),
        (110.0, "2026-08-06T09:18:00+00:00"),
    ])
    candles = materialize_candles(
        reality_store=store, instrument=SPOT, kind=KIND_SPOT,
        interval=INTERVAL_FIVE_MINUTE, as_of=FAR_FUTURE,
    )
    assert len(candles) == 1
    c = candles[0]
    assert (c.open, c.high, c.low, c.close) == (100.0, 130.0, 90.0, 110.0)
    assert c.tick_count == 4


def test_zero_tick_window_still_produces_no_candle(tmp_path):
    """A window with no foldable observations must yield nothing --
    gap honesty is a property of CandleAggregator, inherited unchanged."""
    store = RawObservationStore(tmp_path, _gate())
    candles = materialize_candles(
        reality_store=store, instrument=SPOT, kind=KIND_SPOT,
        interval=INTERVAL_FIVE_MINUTE, as_of=FAR_FUTURE,
    )
    assert candles == []


def test_gap_between_windows_is_absent_not_forward_filled(tmp_path):
    store = _seeded_store(tmp_path, [
        (100.0, "2026-08-06T09:15:00+00:00"),
        (120.0, "2026-08-06T09:35:00+00:00"),  # several windows skipped
    ])
    candles = materialize_candles(
        reality_store=store, instrument=SPOT, kind=KIND_SPOT,
        interval=INTERVAL_FIVE_MINUTE, as_of=FAR_FUTURE,
    )
    assert len(candles) == 2
    assert candles[0].window_start != candles[1].window_start


# --- Materializer contract: deterministic / replayable ----------------------
def test_materialization_is_deterministic(tmp_path):
    store = _seeded_store(tmp_path, [
        (100.0, "2026-08-06T09:15:00+00:00"),
        (105.0, "2026-08-06T09:16:00+00:00"),
    ])
    first = materialize_candles(reality_store=store, instrument=SPOT, kind=KIND_SPOT,
                                interval=INTERVAL_FIVE_MINUTE, as_of=FAR_FUTURE)
    second = materialize_candles(reality_store=store, instrument=SPOT, kind=KIND_SPOT,
                                 interval=INTERVAL_FIVE_MINUTE, as_of=FAR_FUTURE)
    assert first == second


def test_rebuild_from_layer0_alone_is_byte_identical(tmp_path):
    """The REPLAY_VERIFIED proof's precondition: delete all derived
    output, re-materialize from Layer 0 alone, compare."""
    store = _seeded_store(tmp_path, [
        (100.0, "2026-08-06T09:15:00+00:00"),
        (130.0, "2026-08-06T09:16:00+00:00"),
        (24310.0, "2026-08-06T09:21:00+00:00"),
    ])
    original = materialize_candles(reality_store=store, instrument=SPOT, kind=KIND_SPOT,
                                   interval=INTERVAL_FIVE_MINUTE, as_of=FAR_FUTURE)
    # "Deletion" here is simply discarding the Python objects and
    # re-running -- materialize_candles never persisted anything, so
    # there is nothing else to tear down.
    rebuilt = materialize_candles(reality_store=store, instrument=SPOT, kind=KIND_SPOT,
                                  interval=INTERVAL_FIVE_MINUTE, as_of=FAR_FUTURE)
    assert original == rebuilt
    assert len(original) == 2


def test_materializer_is_read_only_on_layer_0(tmp_path):
    store = _seeded_store(tmp_path, [(100.0, "2026-08-06T09:15:00+00:00")])
    before = len(list(store.read_accepted_events()))
    materialize_candles(reality_store=store, instrument=SPOT, kind=KIND_SPOT,
                        interval=INTERVAL_FIVE_MINUTE, as_of=FAR_FUTURE)
    after = len(list(store.read_accepted_events()))
    assert before == after


# --- Lineage-preserving ------------------------------------------------------
def test_every_candle_traces_back_to_its_source_observation_ids(tmp_path):
    store = _seeded_store(tmp_path, [
        (100.0, "2026-08-06T09:15:00+00:00"),
        (110.0, "2026-08-06T09:16:00+00:00"),
    ])
    candles = materialize_candles(reality_store=store, instrument=SPOT, kind=KIND_SPOT,
                                  interval=INTERVAL_FIVE_MINUTE, as_of=FAR_FUTURE)
    assert len(candles[0].source_observation_ids) == 2
    # Every id must resolve to a real, still-present Layer 0 record.
    accepted_ids = {e.event_id for e in store.read_accepted_events()}
    assert set(candles[0].source_observation_ids) <= accepted_ids


def test_materializer_id_and_calc_version_are_stamped(tmp_path):
    store = _seeded_store(tmp_path, [(100.0, "2026-08-06T09:15:00+00:00")])
    candle = materialize_candles(reality_store=store, instrument=SPOT, kind=KIND_SPOT,
                                 interval=INTERVAL_FIVE_MINUTE, as_of=FAR_FUTURE)[0]
    assert candle.materializer_id == MATERIALIZER_ID
    assert candle.calc_version == default_calc_version(INTERVAL_FIVE_MINUTE)
    assert candle.calc_version  # non-empty


def test_calc_version_changes_if_the_definition_changes():
    """A hand-maintained version could drift silently. This one cannot:
    a different definition source hashes to a different version."""
    from bujji.epistemics.lineage import calc_version_for
    v1 = calc_version_for("definition A", {"interval": "FIVE_MINUTE"})
    v2 = calc_version_for("definition B", {"interval": "FIVE_MINUTE"})
    assert v1 != v2


def test_first_and_last_event_time_are_the_real_observed_span(tmp_path):
    """Distinct from the nominal window boundary -- a bar built from
    ticks spanning 09:15:02-09:15:58 is different evidence from one
    spanning the full window, even with an identical window_start/end."""
    store = _seeded_store(tmp_path, [
        (100.0, "2026-08-06T09:15:02+00:00"),
        (105.0, "2026-08-06T09:15:58+00:00"),
    ])
    candle = materialize_candles(reality_store=store, instrument=SPOT, kind=KIND_SPOT,
                                 interval=INTERVAL_FIVE_MINUTE, as_of=FAR_FUTURE)[0]
    assert candle.first_event_time == "2026-08-06T09:15:02+00:00"
    assert candle.last_event_time == "2026-08-06T09:15:58+00:00"
    assert candle.window_start != candle.first_event_time  # nominal vs. real span differ


def test_knowledge_boundary_is_stamped_with_the_query_as_of(tmp_path):
    store = _seeded_store(tmp_path, [(100.0, "2026-08-06T09:15:00+00:00")])
    candle = materialize_candles(reality_store=store, instrument=SPOT, kind=KIND_SPOT,
                                 interval=INTERVAL_FIVE_MINUTE, as_of=FAR_FUTURE)[0]
    assert candle.knowledge_boundary == FAR_FUTURE


# --- No look-ahead ------------------------------------------------------------
def test_as_of_excludes_observations_after_the_boundary(tmp_path):
    """A materializer run pinned to T must not see anything with
    event_time > T -- the no-lookahead guarantee applied at the source."""
    store = _seeded_store(tmp_path, [
        (100.0, "2026-08-06T09:15:00+00:00"),
        (999.0, "2026-08-06T09:15:30+00:00"),  # later in the SAME window
    ])
    partial = materialize_candles(
        reality_store=store, instrument=SPOT, kind=KIND_SPOT,
        interval=INTERVAL_FIVE_MINUTE, as_of="2026-08-06T09:15:10+00:00",
    )
    full = materialize_candles(
        reality_store=store, instrument=SPOT, kind=KIND_SPOT,
        interval=INTERVAL_FIVE_MINUTE, as_of=FAR_FUTURE,
    )
    assert partial[0].close == 100.0   # the 999.0 tick had not happened yet
    assert full[0].close == 999.0
    assert partial[0].tick_count == 1
    assert full[0].tick_count == 2


def test_materializing_at_an_earlier_as_of_is_a_strict_prefix(tmp_path):
    """The defining bitemporal property: what was knowable earlier must
    be a strict subset of what's knowable later -- never different,
    never more."""
    store = _seeded_store(tmp_path, [
        (100.0, "2026-08-06T09:15:00+00:00"),
        (110.0, "2026-08-06T09:21:00+00:00"),
    ])
    early = materialize_candles(reality_store=store, instrument=SPOT, kind=KIND_SPOT,
                                interval=INTERVAL_FIVE_MINUTE, as_of="2026-08-06T09:18:00+00:00")
    late = materialize_candles(reality_store=store, instrument=SPOT, kind=KIND_SPOT,
                               interval=INTERVAL_FIVE_MINUTE, as_of=FAR_FUTURE)
    assert len(early) == 1
    assert len(late) == 2
    # Identical data (OHLC, tick_count, lineage, ids) -- knowledge_boundary
    # is EXPECTED to differ, since it is deliberately stamped with the
    # query's own as_of. Normalize just that one field before comparing,
    # so this assertion still proves "same facts," not "same query."
    assert early[0] == replace(late[0], knowledge_boundary=early[0].knowledge_boundary)


# --- Capture events: overlap flagging, never folded into OHLC ---------------
def test_capture_event_overlapping_a_window_is_flagged(tmp_path):
    store = _seeded_store(tmp_path, [
        (100.0, "2026-08-06T09:15:00+00:00"),
        (105.0, "2026-08-06T09:18:00+00:00"),
    ])
    store.append_capture_event(build_capture_event(
        reason=rtax.REASON_DISCONNECT,
        event_time="2026-08-06T09:16:30+00:00",  # inside the same 09:15-09:20 window
        knowledge_time="2026-08-06T09:16:31+00:00",
        source="fyers", access_method="fyers_websocket",
    ))
    candle = materialize_candles(reality_store=store, instrument=SPOT, kind=KIND_SPOT,
                                 interval=INTERVAL_FIVE_MINUTE, as_of=FAR_FUTURE)[0]
    assert len(candle.capture_event_overlap) == 1


def test_capture_event_outside_a_window_is_not_flagged(tmp_path):
    store = _seeded_store(tmp_path, [(100.0, "2026-08-06T09:15:00+00:00")])
    store.append_capture_event(build_capture_event(
        reason=rtax.REASON_DISCONNECT,
        event_time="2026-08-06T11:00:00+00:00",  # far outside the 09:15 window
        knowledge_time="2026-08-06T11:00:01+00:00",
        source="fyers", access_method="fyers_websocket",
    ))
    candle = materialize_candles(reality_store=store, instrument=SPOT, kind=KIND_SPOT,
                                 interval=INTERVAL_FIVE_MINUTE, as_of=FAR_FUTURE)[0]
    assert candle.capture_event_overlap == ()


def test_capture_event_never_contributes_to_ohlc(tmp_path):
    """A materializer must never fold a capture event into a candle --
    it describes the observer, not the market."""
    store = _seeded_store(tmp_path, [(100.0, "2026-08-06T09:15:00+00:00")])
    store.append_capture_event(build_capture_event(
        reason=rtax.REASON_DISCONNECT,
        event_time="2026-08-06T09:16:00+00:00",
        knowledge_time="2026-08-06T09:16:01+00:00",
        source="fyers", access_method="fyers_websocket",
    ))
    candle = materialize_candles(reality_store=store, instrument=SPOT, kind=KIND_SPOT,
                                 interval=INTERVAL_FIVE_MINUTE, as_of=FAR_FUTURE)[0]
    assert candle.tick_count == 1  # only the real tick, never the capture event
    assert (candle.open, candle.high, candle.low, candle.close) == (100.0, 100.0, 100.0, 100.0)


# --- Instrument isolation -----------------------------------------------------
def test_other_instruments_are_not_folded_in(tmp_path):
    store = RawObservationStore(tmp_path, _gate())
    store.append(_tick(100.0, "2026-08-06T09:15:00+00:00", instrument=SPOT), now=NOW)
    store.append(_tick(999.0, "2026-08-06T09:15:00+00:00", instrument="NSE:BANKNIFTY-INDEX"), now=NOW)
    candles = materialize_candles(reality_store=store, instrument=SPOT, kind=KIND_SPOT,
                                  interval=INTERVAL_FIVE_MINUTE, as_of=FAR_FUTURE)
    assert len(candles) == 1
    assert candles[0].close == 100.0
