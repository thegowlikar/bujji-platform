"""A SCALAR observation must answer only for the quantity it measures.

THE DEFECT. `_numeric_field`'s SCALAR branch ignored `field_name` and
returned the bare payload to every caller. A spot PRICE observation
therefore answered "open_interest" and "volume" with the NIFTY level, and
`compare_observations` -- which runs the OI and volume detectors on every
observation -- emitted OI_CHANGED and VOLUME_CHANGED carrying a price as
though it were open interest and traded volume.

Verified on the production bridge before the fix: two real spot ticks
(24601.05 -> 24608.30) emitted OBSERVATION_UPDATED, PRICE_CHANGED,
OI_CHANGED, VOLUME_CHANGED, NEW_SESSION_HIGH, NEW_SESSION_LOW. Two of
those six were invented.

WHY IT MATTERED BEYOND TIDINESS. Those phantom events inflate the episode
and event counts that PSI confidence and the thesis evidence gates are
computed from, so the regime read was scored partly on invented evidence.

These tests build observations through the REAL bridge (`_build`) rather
than by hand, so they exercise the production shape and would fail if the
bridge's own construction changed underneath them.
"""
from __future__ import annotations

import pytest

from bujji.live_market_events.engine import _numeric_field, compare_observations
from bujji.market_observation import taxonomy as moc
from bujji.market_state_builder.observation_bridge import _build


def scalar(observation_type: str, value: float, ts: str = "2026-08-18T09:15:00+05:30"):
    return _build(observation_type, "NSE:NIFTY50-INDEX", "INDEX", ts,
                  moc.VALUE_KIND_SCALAR, float(value))


def event_types(current, previous):
    events, _ = compare_observations(current, previous)
    return [e.event_type for e in events]


class TestScalarAnswersOnlyItsOwnField:
    def test_a_price_is_not_served_as_open_interest_or_volume(self):
        obs = scalar(moc.TYPE_PRICE, 24601.05)
        assert _numeric_field(obs, "price") == 24601.05
        assert _numeric_field(obs, "open_interest") is None
        assert _numeric_field(obs, "volume") is None
        assert _numeric_field(obs, "vix") is None

    def test_price_aliases_still_resolve(self):
        """`_representative_price` tries price/close/last in turn; all three
        must keep working or PRICE_CHANGED and session extremes go blind."""
        obs = scalar(moc.TYPE_PRICE, 24601.05)
        for alias in ("price", "close", "last"):
            assert _numeric_field(obs, alias) == 24601.05

    def test_vix_answers_vix_and_nothing_else(self):
        obs = scalar(moc.TYPE_VOLATILITY_VIX, 11.33)
        assert _numeric_field(obs, "vix") == 11.33
        assert _numeric_field(obs, "price") is None
        assert _numeric_field(obs, "open_interest") is None

    def test_a_scalar_oi_observation_answers_open_interest(self):
        obs = scalar(moc.TYPE_FUTURES_OPEN_INTEREST, 12771460)
        assert _numeric_field(obs, "open_interest") == 12771460
        assert _numeric_field(obs, "price") is None

    def test_an_unmapped_type_answers_nothing(self):
        """Unknown is not a licence to guess: a scalar whose meaning is not
        declared must not be served to any caller."""
        obs = scalar(moc.TYPE_MARKET_BREADTH, 42.0)
        for field in ("price", "close", "open_interest", "volume", "vix"):
            assert _numeric_field(obs, field) is None


class TestNoPhantomEventsFromASpotTick:
    def test_two_real_spot_ticks_emit_no_oi_or_volume_event(self):
        """THE REGRESSION. The payload holds one number -- a price. There is
        no open interest and no volume anywhere in it."""
        a = scalar(moc.TYPE_PRICE, 24601.05, "2026-08-18T09:15:00+05:30")
        b = scalar(moc.TYPE_PRICE, 24608.30, "2026-08-18T09:16:00+05:30")
        emitted = event_types(b, a)
        assert "OI_CHANGED" not in emitted, emitted
        assert "VOLUME_CHANGED" not in emitted, emitted

    def test_the_real_price_events_still_fire(self):
        """The fix must not silence genuine detection."""
        a = scalar(moc.TYPE_PRICE, 24601.05, "2026-08-18T09:15:00+05:30")
        b = scalar(moc.TYPE_PRICE, 24608.30, "2026-08-18T09:16:00+05:30")
        emitted = event_types(b, a)
        assert "PRICE_CHANGED" in emitted
        assert "NEW_SESSION_HIGH" in emitted

    def test_a_vix_tick_emits_no_price_or_oi_event(self):
        a = scalar(moc.TYPE_VOLATILITY_VIX, 11.33, "2026-08-18T09:15:00+05:30")
        b = scalar(moc.TYPE_VOLATILITY_VIX, 11.51, "2026-08-18T09:16:00+05:30")
        emitted = event_types(b, a)
        assert "OI_CHANGED" not in emitted
        assert "VOLUME_CHANGED" not in emitted
        assert "PRICE_CHANGED" not in emitted


class TestMappingPayloadsAreUntouched:
    def test_a_futures_mapping_still_yields_oi_and_volume(self):
        """Futures ride as MAPPING, where field_name was always honoured.
        The fix must not regress the path that was already correct."""
        ts_a, ts_b = "2026-08-18T09:15:00+05:30", "2026-08-18T09:16:00+05:30"
        a = _build(moc.TYPE_FUTURES, "NSE:NIFTY26AUGFUT", "FO", ts_a,
                   moc.VALUE_KIND_MAPPING,
                   {"close": 24370.0, "volume": 2262780, "open_interest": 12771460})
        b = _build(moc.TYPE_FUTURES, "NSE:NIFTY26AUGFUT", "FO", ts_b,
                   moc.VALUE_KIND_MAPPING,
                   {"close": 24395.0, "volume": 2300000, "open_interest": 12800000})
        assert _numeric_field(b, "open_interest") == 12800000
        assert _numeric_field(b, "volume") == 2300000
        emitted = event_types(b, a)
        assert "OI_CHANGED" in emitted, emitted
        assert "VOLUME_CHANGED" in emitted, emitted
