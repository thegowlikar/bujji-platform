"""The market-data facade that lets Bujji derive its own regime.

Deriving a regime needs a real MarketThesisAssessment <- CycleEvidence <-
record_cycle(snapshot, broker, ...) <- a broker exposing six read-only
market-data methods. The runner had only a chain provider and a
PaperBroker whose market data is synthetic; feeding synthetic spot/VIX/
candles into the intelligence stack would fabricate the very evidence the
decision rests on. This serves those six from real captured observations.
"""
from __future__ import annotations

import asyncio
import os

import pytest

from bujji.production_runtime.replay_market_broker import (
    ReplayBrokerExecutionError,
    ReplayMarketBroker,
    _as_datetime,
)

REAL_DB = "/opt/bujji/app/data/historical_reality/normalized/historical_observations.db"
DAY = "2026-08-14"
AS_OF = f"{DAY}T09:20:00+05:30"
_REAL = os.environ.get("BUJJI_REAL_CAPTURE_TESTS") == "1" and os.path.exists(REAL_DB)
_REASON = "set BUJJI_REAL_CAPTURE_TESTS=1 to run (reads the real observation store)"


class _EmptyStore:
    def range(self, *a, **k):
        return []

    def range_by_prefix(self, *a, **k):
        return []


class TestExecutionIsStructurallyUnavailable:
    """This facade is handed to the intelligence layer. If it ever reaches
    an order path that is a wiring bug, and it must fail loudly rather
    than silently pretend to trade."""

    @pytest.mark.parametrize("call", [
        lambda b: b.place_order(object()),
        lambda b: b.get_order("X"),
        lambda b: b.cancel_order("X"),
        lambda b: b.get_open_positions(),
    ])
    def test_execution_methods_refuse(self, call):
        broker = ReplayMarketBroker(_EmptyStore(), as_of=AS_OF)
        with pytest.raises(ReplayBrokerExecutionError):
            asyncio.run(call(broker))

    def test_contract_resolution_is_refused_not_faked(self):
        broker = ReplayMarketBroker(_EmptyStore(), as_of=AS_OF)
        with pytest.raises(ReplayBrokerExecutionError):
            asyncio.run(broker.resolve_atm_contract("NIFTY", 24000, None, 50, 75))


class TestNeverFabricates:
    def test_an_empty_store_yields_nothing_not_defaults(self):
        broker = ReplayMarketBroker(_EmptyStore(), as_of=AS_OF)
        assert asyncio.run(broker.get_spot("NIFTY")) is None
        assert asyncio.run(broker.get_vix()) is None
        assert asyncio.run(broker.get_futures_quote("NIFTY")) is None
        assert asyncio.run(broker.get_recent_candles("NIFTY", 5, 10)) == []
        assert asyncio.run(broker.get_option_chain("NIFTY", 24000)) == []

    def test_timestamps_are_parsed_to_real_datetimes(self):
        """Candle.timestamp must be a datetime -- downstream engines call
        .isoformat() on it. The store holds ISO strings."""
        parsed = _as_datetime("2026-08-14T09:20:00+05:30")
        assert parsed is not None and hasattr(parsed, "isoformat")
        assert _as_datetime("not-a-timestamp") is None


@pytest.mark.skipif(not _REAL, reason=_REASON)
class TestAgainstRealCapture:
    def _broker(self, as_of=AS_OF):
        from bujji.historical_reality.store import HistoricalObservationStore
        return ReplayMarketBroker(HistoricalObservationStore(REAL_DB), as_of=as_of)

    def test_real_spot_vix_and_futures(self):
        b = self._broker()
        assert asyncio.run(b.get_spot("NIFTY")) > 0
        assert asyncio.run(b.get_vix())["level"] > 0
        assert asyncio.run(b.get_futures_quote("NIFTY"))["ltp"] > 0

    def test_candles_cross_day_boundaries(self):
        """Trailing history is the point. An earlier draft windowed to the
        current day only, so a 09:20 as_of yielded ~1 bar and the
        volatility engine could not form a regime -- the session refused
        to trade for lack of evidence that existed just outside the
        window."""
        candles = asyncio.run(self._broker().get_recent_candles("NIFTY", 5, 75))
        assert len(candles) > 10, "expected real trailing history, not just today's bars"
        assert len({c.timestamp.date() for c in candles}) > 1, "history must span more than one day"

    def test_chain_is_centred_on_spot(self):
        b = self._broker()
        spot = asyncio.run(b.get_spot("NIFTY"))
        chain = asyncio.run(b.get_option_chain("NIFTY", spot, strike_count=5))
        assert chain, "expected a real captured chain"
        strikes = [s for s, _, _ in chain]
        assert min(strikes) <= spot <= max(strikes), "chain should straddle spot"

    def test_time_advances_the_view(self):
        b = self._broker(f"{DAY}T09:20:00+05:30")
        early = asyncio.run(b.get_spot("NIFTY"))
        b.set_as_of(f"{DAY}T15:20:00+05:30")
        late = asyncio.run(b.get_spot("NIFTY"))
        assert early is not None and late is not None
        assert early != late, "advancing replay time must change what the broker sees"
