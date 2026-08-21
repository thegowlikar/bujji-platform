"""Observation quality is MEASURED, and says so honestly when it cannot be.

WHAT THIS REPLACES. `build_raw_observation()` stamped `completeness=1.0`,
`missing_fields=()` and `validation_status=UNKNOWN` on every observation
unconditionally -- a record that admitted it had never been validated while
claiming to be 100% complete. The value could not change, so it carried no
information, yet read to every consumer exactly like a measurement.

The tests below exist to make that regression impossible to reintroduce
quietly: several of them fail if completeness ever becomes constant again.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from bujji.market_observation import taxonomy as moc_taxonomy
from bujji.market_reality import taxonomy as T
from bujji.market_reality.capture import build_raw_observation
from bujji.market_reality.observation_quality import (
    ANOMALY_CROSSED_BOOK,
    ANOMALY_IMPOSSIBLE_BAR,
    REQUIRED_FIELDS_BY_KIND,
    assess_observation_quality,
    compute_acquisition_latency_seconds,
    detect_anomalies,
)

TS = "2026-08-20T09:15:00.866135+05:30"
CHAIN_ROW = {"ltp": 120.5, "bid": 120.0, "ask": 121.0, "open_interest": 8144, "volume": 237}


def _q(kind, payload, **kw):
    return assess_observation_quality(kind=kind, payload=payload, capture_timestamp=TS, **kw)


class TestCompletenessIsAMeasurementNotAConstant:
    def test_it_takes_more_than_one_value(self):
        """The single assertion that would have caught the original defect."""
        values = {
            _q(T.KIND_QUOTE, {"ltp": 1.0}).completeness,
            _q(T.KIND_QUOTE, {}).completeness,
            _q(T.KIND_OPTION_CHAIN, {"ltp": 1.0, "ask": 2.0, "open_interest": 3}).completeness,
        }
        assert len(values) > 1, "completeness is constant again -- it is not measuring"

    @pytest.mark.parametrize("payload,expected", [
        (CHAIN_ROW, 1.0),
        ({"ltp": 120.5, "ask": 121.0, "open_interest": 8144}, 0.6),
        ({"ltp": 120.5}, 0.2),
        ({}, 0.0),
    ])
    def test_it_is_the_real_present_fraction(self, payload, expected):
        assert _q(T.KIND_OPTION_CHAIN, payload).completeness == pytest.approx(expected)

    def test_missing_fields_names_what_is_missing(self):
        q = _q(T.KIND_OPTION_CHAIN, {"ltp": 120.5, "ask": 121.0, "open_interest": 8144})
        assert set(q.missing_fields) == {"bid", "volume"}

    def test_an_explicit_none_counts_as_missing_not_present(self):
        """A key present with value None is absence wearing a key."""
        q = _q(T.KIND_QUOTE, {"ltp": None})
        assert q.missing_fields == ("ltp",) and q.completeness == 0.0


class TestValidationStatusReflectsReality:
    def test_complete_and_sane_is_valid(self):
        assert _q(T.KIND_OPTION_CHAIN, CHAIN_ROW).validation_status == moc_taxonomy.VALIDATION_VALID

    def test_missing_fields_is_incomplete(self):
        assert _q(T.KIND_OPTION_CHAIN, {"ltp": 1.0}).validation_status == moc_taxonomy.VALIDATION_INCOMPLETE

    def test_an_anomaly_is_invalid_even_when_complete(self):
        """Corruption outranks completeness -- all five fields present does
        not redeem a book that cannot exist."""
        bad = dict(CHAIN_ROW, bid=130.0, ask=121.0)
        q = _q(T.KIND_OPTION_CHAIN, bad)
        assert q.completeness == 1.0
        assert q.validation_status == moc_taxonomy.VALIDATION_INVALID

    def test_an_unknown_kind_admits_ignorance_instead_of_claiming_completeness(self):
        q = _q("SOME_KIND_NOBODY_DEFINED", {"anything": 1})
        assert q.completeness == 0.0
        assert q.validation_status == moc_taxonomy.VALIDATION_UNKNOWN


class TestAnomaliesAreImpossibilitiesNotOpinions:
    def test_crossed_book_is_flagged(self):
        assert ANOMALY_CROSSED_BOOK in detect_anomalies({"bid": 130.0, "ask": 121.0})

    def test_a_locked_market_is_legal(self):
        """bid == ask is a real, legal market state."""
        assert detect_anomalies({"bid": 121.0, "ask": 121.0}) == ()

    def test_a_one_sided_book_is_absence_not_a_crossed_book(self):
        """A zero on one side means no quote there, not an inverted market."""
        assert detect_anomalies({"bid": 0.0, "ask": 121.0}) == ()
        assert detect_anomalies({"bid": 130.0, "ask": 0.0}) == ()

    def test_the_real_wide_spread_zero_volume_row_is_not_corruption(self):
        """From the real 2026-08-20 capture: a 587-point spread on a
        zero-volume deep-ITM call. Unusable for TRADING, perhaps -- that is
        a liquidity policy's call. It is not corrupt DATA, and labelling it
        so would put an integrity verdict on a trading judgement."""
        real = {"ltp": 1419.2, "bid": 1144.85, "ask": 1732.1, "open_interest": 11050, "volume": 0}
        q = _q(T.KIND_OPTION_CHAIN, real)
        assert q.anomalies == ()
        assert q.validation_status == moc_taxonomy.VALIDATION_VALID

    @pytest.mark.parametrize("field", ["ltp", "bid", "volume", "open_interest"])
    def test_negative_values_are_flagged(self, field):
        assert any("NEGATIVE_VALUE" in a for a in detect_anomalies({field: -1.0}))

    def test_zero_is_not_negative(self):
        assert detect_anomalies({"volume": 0, "open_interest": 0}) == ()

    def test_high_below_low_is_impossible(self):
        assert ANOMALY_IMPOSSIBLE_BAR in detect_anomalies({"high": 10.0, "low": 99.0})

    def test_a_bool_is_not_a_price(self):
        """float(True) is 1.0 -- a silent coercion that would let a boolean
        masquerade as a valid price."""
        assert any("NON_NUMERIC" in a for a in detect_anomalies({"ltp": True}))

    def test_a_string_price_is_non_numeric(self):
        assert any("NON_NUMERIC" in a for a in detect_anomalies({"ltp": "n/a"}))


class TestLatencyIsMeasuredOnlyWhereItExists:
    def test_the_real_depth_row_latency(self):
        """Real captured pair: exchange 09:15:12, receipt 09:15:13.560706."""
        assert compute_acquisition_latency_seconds(
            event_timestamp="2026-08-14T09:15:12+05:30",
            capture_timestamp="2026-08-14T09:15:13.560706+05:30") == pytest.approx(1.560706)

    def test_no_exchange_clock_yields_none_never_zero(self):
        """0.0 is a MEASUREMENT of instantaneous receipt. Absence of an
        exchange clock is not -- collapsing them would let every
        unmeasurable path read as a perfectly fast one."""
        assert compute_acquisition_latency_seconds(
            event_timestamp=None, capture_timestamp=TS) is None

    def test_clock_skew_is_reported_not_hidden(self):
        """A receipt earlier than the exchange's own event time is real
        evidence of skew. max(0, x) would destroy the only signal it exists."""
        v = compute_acquisition_latency_seconds(
            event_timestamp="2026-08-14T09:15:13+05:30",
            capture_timestamp="2026-08-14T09:15:12+05:30")
        assert v is not None and v < 0

    def test_a_naive_timestamp_refuses_rather_than_assuming_a_zone(self):
        assert compute_acquisition_latency_seconds(
            event_timestamp="2026-08-14T09:15:12",
            capture_timestamp="2026-08-14T09:15:13.560706+05:30") is None

    def test_garbage_timestamps_do_not_raise(self):
        assert compute_acquisition_latency_seconds(
            event_timestamp="not-a-time", capture_timestamp=TS) is None


class TestTheRequiredSetsMatchTheRealCorpus:
    def test_quote_requires_only_ltp(self):
        """volume/oi/prev_close appear on 886 of 2657 real QUOTE rows
        (futures only). Requiring them would mark every spot and VIX
        observation permanently incomplete -- manufacturing incompleteness,
        the same class of error as manufacturing completeness."""
        assert REQUIRED_FIELDS_BY_KIND[T.KIND_QUOTE] == ("ltp",)
        assert _q(T.KIND_QUOTE, {"ltp": 24416.2}).completeness == 1.0

    def test_every_defined_kind_has_a_required_set(self):
        for kind in T.ALL_OBSERVATION_KINDS:
            assert kind in REQUIRED_FIELDS_BY_KIND, f"{kind} has no grounded required-field set"


class TestItNeverCostsTheObservation:
    @pytest.mark.parametrize("payload", [None, [], "string", 42, object()])
    def test_a_non_mapping_payload_is_assessed_without_raising(self, payload):
        q = _q(T.KIND_QUOTE, payload)
        assert q.validation_status == moc_taxonomy.VALIDATION_UNKNOWN
        assert q.completeness == 0.0

    def test_capture_still_produces_a_record_for_a_broken_payload(self):
        o = build_raw_observation(
            kind=T.KIND_QUOTE, payload=None, instrument="NSE:NIFTY50-INDEX",
            instrument_type="INDEX", source="fyers", access_method="rest",
            capture_timestamp=TS)
        assert o.observation_id
        assert o.observation.quality.validation_status == moc_taxonomy.VALIDATION_UNKNOWN


class TestItReachesThePersistedRecord:
    def test_the_capture_path_carries_measured_values(self):
        o = build_raw_observation(
            kind=T.KIND_OPTION_CHAIN,
            payload={"ltp": 120.5, "ask": 121.0, "open_interest": 8144},
            instrument="NIFTY|2026-08-25|24400|CE", instrument_type="OPTION",
            source="fyers", access_method="rest", capture_timestamp=TS)
        assert o.observation.quality.completeness == pytest.approx(0.6)
        assert set(o.observation.quality.missing_fields) == {"bid", "volume"}

    def test_quality_detail_round_trips(self):
        from bujji.market_reality.models import RawObservation
        o = build_raw_observation(
            kind=T.KIND_MARKET_DEPTH,
            payload={"total_buy_quantity": 209235, "total_sell_quantity": 169455,
                     "last_price": 24415.3},
            event_timestamp="2026-08-14T09:15:12+05:30",
            instrument="NSE:NIFTY26AUGFUT", instrument_type="FUTURE", source="fyers",
            access_method="rest", capture_timestamp="2026-08-14T09:15:13.560706+05:30")
        back = RawObservation.from_dict(o.to_dict())
        assert back.quality_detail["acquisition_latency_seconds"] == pytest.approx(1.560706)

    def test_an_older_record_without_quality_detail_still_loads(self):
        """Every already-persisted Layer 0 record predates this field."""
        from bujji.market_reality.models import RawObservation
        o = build_raw_observation(
            kind=T.KIND_QUOTE, payload={"ltp": 1.0}, instrument="X",
            instrument_type="INDEX", source="fyers", access_method="rest",
            capture_timestamp=TS)
        d = o.to_dict()
        del d["quality_detail"]
        assert RawObservation.from_dict(d).quality_detail == {}
