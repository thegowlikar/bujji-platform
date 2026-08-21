"""The intraday tick feed, proven against REAL captured option prices.

Before this, the runner revalued open positions with the prices captured
at FILL, so unrealized P&L was flat by construction: MFE/MAE could only
ever be 0.0 and no stop or target could ever fire. These tests use the
real HistoricalObservationStore on the VPS where it is present, and fall
back to a real-shaped in-memory store elsewhere, so the contract is
pinned either way.
"""
from __future__ import annotations

import os

import pytest

from bujji.core.enums import OptionType
from bujji.core.models import OptionContract
from bujji.production_runtime.intraday_price_provider import (
    HistoricalTickProvider,
    LiveTickProvider,
    _price_from_payload,
    store_identity_for,
)

REAL_DB = "/opt/bujji/app/data/historical_reality/normalized/historical_observations.db"
TICK_DAY = "2026-08-14"
# Verified to carry genuine intraday movement (2351.25 -> 2400).
MOVER = OptionContract("N22000CE", "NIFTY", 22000, OptionType.CE, "2026-08-18", 75)


class TestIdentityMapping:
    def test_identity_is_built_from_real_contract_fields(self):
        assert store_identity_for(MOVER) == "NIFTY|2026-08-18|22000|CE"

    def test_a_contract_missing_any_component_is_never_guessed(self):
        class Partial:
            underlying = "NIFTY"
            expiry = None
            strike = 22000
            option_type = "CE"
        assert store_identity_for(Partial()) is None


class TestPriceExtraction:
    def test_option_payloads_use_ltp(self):
        assert _price_from_payload({"ltp": 123.5}) == 123.5

    def test_ohlc_payloads_use_close(self):
        assert _price_from_payload({"open": 1, "close": 99.0}) == 99.0

    def test_zero_is_a_no_trade_marker_not_a_price(self):
        """A captured 0.0 means nothing traded/quoted. Valuing a leg at
        zero would fabricate a total loss on it."""
        assert _price_from_payload({"ltp": 0}) is None
        assert _price_from_payload({"ltp": 0, "close": 0}) is None

    def test_unusable_payloads_are_none(self):
        assert _price_from_payload(None) is None
        assert _price_from_payload({}) is None
        assert _price_from_payload({"ltp": "n/a"}) is None


class _Row:
    def __init__(self, payload):
        self.payload = payload


class _FakeStore:
    """Real `range(identity, resolution, start, end)` shape."""

    def __init__(self, rows_by_identity):
        self._rows = rows_by_identity

    def range(self, identity, resolution, start, end):
        return self._rows.get(identity, [])


class TestHistoricalTickProviderContract:
    def test_last_real_print_at_or_before_now_is_used(self):
        store = _FakeStore({"NIFTY|2026-08-18|22000|CE": [
            _Row({"ltp": 100.0}), _Row({"ltp": 110.0}), _Row({"ltp": 120.0}),
        ]})
        prices = HistoricalTickProvider(store).get_prices({"N22000CE": MOVER}, f"{TICK_DAY}T11:00:00+05:30")
        assert prices["N22000CE"] == 120.0

    def test_rows_without_a_usable_price_are_skipped_not_zeroed(self):
        store = _FakeStore({"NIFTY|2026-08-18|22000|CE": [
            _Row({"ltp": 100.0}), _Row({"ltp": 0}),
        ]})
        prices = HistoricalTickProvider(store).get_prices({"N22000CE": MOVER}, f"{TICK_DAY}T11:00:00+05:30")
        assert prices["N22000CE"] == 100.0

    def test_no_observation_yields_none_never_a_substitute(self):
        """The whole point: an unpriced leg must stay unpriced. Falling
        back to the entry price here is exactly the bug this replaces."""
        prices = HistoricalTickProvider(_FakeStore({})).get_prices(
            {"N22000CE": MOVER}, f"{TICK_DAY}T11:00:00+05:30")
        assert prices["N22000CE"] is None

    def test_an_unreadable_store_is_unknown_not_a_crash(self):
        class Broken:
            def range(self, *a, **k):
                raise RuntimeError("db gone")
        prices = HistoricalTickProvider(Broken()).get_prices({"N22000CE": MOVER}, f"{TICK_DAY}T11:00:00+05:30")
        assert prices["N22000CE"] is None


class TestLiveTickProvider:
    def test_live_quote_is_used(self):
        provider = LiveTickProvider(broker=type("B", (), {"get_ltp": lambda self, c: 42.0})(),
                                     run_async=lambda x: x)
        assert provider.get_prices({"S": MOVER}, "T")["S"] == 42.0

    def test_a_failed_quote_is_none_never_stale(self):
        class Broker:
            def get_ltp(self, c):
                raise RuntimeError("timeout")
        provider = LiveTickProvider(Broker(), run_async=lambda x: x)
        assert provider.get_prices({"S": MOVER}, "T")["S"] is None

    def test_a_zero_quote_is_none(self):
        provider = LiveTickProvider(type("B", (), {"get_ltp": lambda self, c: 0.0})(), run_async=lambda x: x)
        assert provider.get_prices({"S": MOVER}, "T")["S"] is None


@pytest.mark.skipif(not os.path.exists(REAL_DB), reason="real observation store not present")
class TestAgainstRealCapturedTicks:
    def _store(self):
        from bujji.historical_reality.store import HistoricalObservationStore
        return HistoricalObservationStore(REAL_DB)

    def test_price_moves_across_the_real_session(self):
        """Real captured data, real movement -- the thing that was
        impossible to observe before this feed existed."""
        provider = HistoricalTickProvider(self._store())
        morning = provider.get_prices({"S": MOVER}, f"{TICK_DAY}T09:30:00+05:30")["S"]
        close = provider.get_prices({"S": MOVER}, f"{TICK_DAY}T15:20:00+05:30")["S"]
        assert morning is not None and close is not None
        assert morning != close, "expected genuine intraday movement on a liquid strike"

    def test_price_is_monotonically_available_through_the_day(self):
        provider = HistoricalTickProvider(self._store())
        seen = [provider.get_prices({"S": MOVER}, f"{TICK_DAY}T{hh}:00:00+05:30")["S"]
                for hh in ("10", "11", "12", "13", "14")]
        assert all(p is not None for p in seen)
        assert len(set(seen)) > 1  # not a frozen series
