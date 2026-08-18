"""Chain served from the captured day -- so entry and revaluation finally
come from one coherent session.

`ReplayChainProvider` reads a bhavcopy (one EOD snapshot, no intraday
granularity) while ticks live in the observation store on a different
date, so no single day could supply both. The capture had been writing a
full intraday chain all along; nothing could read it back.
"""
from __future__ import annotations

import os

import pytest

from bujji.production_runtime.market_data_provider import MarketDataUnavailableError
from bujji.production_runtime.store_chain_provider import (
    StoreChainProvider,
    _parse_identity,
    _payload_value,
)

REAL_DB = "/opt/bujji/app/data/historical_reality/normalized/historical_observations.db"
DAY = "2026-08-14"

# Loading a real chain costs ~950MB peak RSS (see StoreChainProvider's own
# docstring -- the cost is inside the store's row hydration, not this
# module). Peak RSS never falls, so running these inside the shared suite
# process permanently trips `ops.health_monitor`'s 500MB warning and makes
# an unrelated health-snapshot test report AMBER instead of GREEN. That
# test is not wrong: it is asserting a real process-global property that a
# memory-heavy neighbour genuinely violates.
#
# So these run on request, not by default:
#     BUJJI_REAL_CAPTURE_TESTS=1 pytest tests/test_store_chain_provider.py
# Everything above this line is pure and always runs.
_REAL_CAPTURE_ENABLED = os.environ.get("BUJJI_REAL_CAPTURE_TESTS") == "1"
_REAL_CAPTURE_REASON = (
    "set BUJJI_REAL_CAPTURE_TESTS=1 to run (loads a real chain, ~950MB peak RSS)"
)


class TestIdentityParsing:
    def test_a_real_option_identity_parses(self):
        assert _parse_identity("NIFTY|2026-08-18|22000|CE") == ("NIFTY", "2026-08-18", 22000.0, "CE")

    def test_non_option_identities_are_rejected_not_coerced(self):
        assert _parse_identity("NSE:NIFTY50-INDEX") is None
        assert _parse_identity("NIFTY|2026-08-18|22000|XX") is None
        assert _parse_identity("NIFTY|2026-08-18|notastrike|CE") is None
        assert _parse_identity("NIFTY|2026-08-18|22000") is None


class TestPayloadValue:
    def test_prefers_first_usable_key_in_order(self):
        assert _payload_value({"ltp": 10.0, "close": 20.0}, ("ltp", "close")) == 10.0
        assert _payload_value({"close": 20.0}, ("ltp", "close")) == 20.0

    def test_zero_and_junk_are_not_prices(self):
        assert _payload_value({"ltp": 0}, ("ltp",)) is None
        assert _payload_value({"ltp": "n/a"}, ("ltp",)) is None
        assert _payload_value(None, ("ltp",)) is None


class _Row:
    def __init__(self, payload):
        self.payload = payload


class _EmptyStore:
    def range(self, *a, **k):
        return []

    def range_by_prefix(self, *a, **k):
        return []


class _SpotOnlyStore(_EmptyStore):
    def range(self, identity, resolution, start, end):
        return [_Row({"close": 24000.0})]


class TestFailsClosed:
    def test_no_chain_rows_raises_rather_than_serving_an_empty_book(self):
        provider = StoreChainProvider(_SpotOnlyStore())
        with pytest.raises(MarketDataUnavailableError, match="zero usable option-chain rows"):
            provider.get_option_chain(DAY)

    def test_no_spot_raises_rather_than_guessing_one(self):
        provider = StoreChainProvider(_EmptyStore())
        with pytest.raises(MarketDataUnavailableError):
            provider.get_option_chain(DAY)


# Loading a chain scans a 670k-row store, so the real-capture cases share
# one load per as-of time rather than re-scanning per test. Beyond being
# wasteful, the repeated scans made this module slow enough (~10s) to trip
# a time-sensitive threshold in an unrelated health-snapshot test when the
# suite ran in one process -- a real ordering hazard this module created.
@pytest.fixture(scope="module")
def _chains():
    if not (_REAL_CAPTURE_ENABLED and os.path.exists(REAL_DB)):
        pytest.skip(_REAL_CAPTURE_REASON)
    from bujji.historical_reality.store import HistoricalObservationStore

    store = HistoricalObservationStore(REAL_DB)
    loaded = {}
    for at in ("T09:20:00+05:30", "T15:20:00+05:30"):
        provider = StoreChainProvider(store, as_of_time=at)
        loaded[at] = (provider, provider.get_option_chain(DAY))
    return loaded


@pytest.mark.skipif(not (_REAL_CAPTURE_ENABLED and os.path.exists(REAL_DB)),
                    reason=_REAL_CAPTURE_REASON)
class TestAgainstTheRealCapture:

    def test_a_real_chain_is_served_from_captured_observations(self, _chains):
        provider, chain = _chains["T09:20:00+05:30"]
        assert len(chain) > 500, "expected a full captured chain"
        assert {r.option_type for r in chain} == {"CE", "PE"}
        assert provider.get_spot() > 0

    def test_chain_rows_carry_real_traded_prices_and_open_interest(self, _chains):
        _, chain = _chains["T09:20:00+05:30"]
        assert all(r.close is not None and r.close > 0 for r in chain)
        assert any((r.open_interest or 0) > 0 for r in chain), "expected real OI on some strikes"

    def test_uncaptured_fields_are_disclosed_gaps_not_invented(self, _chains):
        """The options capture records ltp/bid/ask/OI, not OHLC. Those
        must surface as honest gaps rather than being back-filled from
        the traded price to look complete."""
        _, chain = _chains["T09:20:00+05:30"]
        row = chain[0]
        assert row.open is None and row.high is None and row.low is None

    def test_the_book_is_point_in_time_not_the_whole_day(self, _chains):
        """Asking for 09:20 must not return the 15:20 book -- that is the
        entire difference from a bhavcopy snapshot."""
        early = _chains["T09:20:00+05:30"][1]
        late = _chains["T15:20:00+05:30"][1]
        early_px = {(r.strike, r.option_type, r.expiry): r.close for r in early}
        late_px = {(r.strike, r.option_type, r.expiry): r.close for r in late}
        shared = set(early_px) & set(late_px)
        assert shared, "expected overlapping contracts"
        assert any(early_px[k] != late_px[k] for k in shared), \
            "expected at least one contract to have moved between 09:20 and 15:20"

    def test_chain_and_ticks_agree_on_the_same_contract(self, _chains):
        """The point of the whole exercise: one day, one capture, both
        the entry book and the revaluation series."""
        from bujji.core.enums import OptionType
        from bujji.core.models import OptionContract
        from bujji.historical_reality.store import HistoricalObservationStore
        from bujji.production_runtime.intraday_price_provider import HistoricalTickProvider

        provider, chain = _chains["T09:20:00+05:30"]
        spot = provider.get_spot()
        atm = sorted(chain, key=lambda r: abs(r.strike - spot))[0]

        contract = OptionContract(
            "X", "NIFTY", int(atm.strike),
            OptionType.CE if atm.option_type == "CE" else OptionType.PE, atm.expiry, 75,
        )
        ticks = HistoricalTickProvider(HistoricalObservationStore(REAL_DB))
        priced = ticks.get_prices({"X": contract}, f"{DAY}T14:00:00+05:30")["X"]
        assert priced is not None, "a contract in the captured chain must be priceable from ticks"
