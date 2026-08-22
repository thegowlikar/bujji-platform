"""A live book that is never refetched is a replay book wearing a live label.

THE DEFECT. `LiveChainProvider._ensure_loaded` opened with

    if self._chain is not None:
        return

so the option chain AND the spot were fetched once -- at roughly 09:15, during
the pre-market check -- and served unchanged for the rest of the session. In a
continuous session admitting entries until 14:30 that book could be more than
five hours old. `get_spot()` was worse: it went through no load path at all and
simply returned whatever the first fetch had left behind.

Strike selection reads both. A 20-delta strike chosen against a five-hour-old
spot is not a 20-delta strike, and the premium beside it is not a premium
anyone can trade. This is the "a STALE value triggers a real action" harm,
sitting directly on the order path.

The module's own docstring already promised the opposite: a live session "must
refuse to trade rather than fall back to a stale or synthetic chain".

THE INVARIANTS NOW ENFORCED
  * older than `refresh_after` -> refetch
  * refetch fails, still within `max_age` -> serve the previous book, loudly
  * refetch fails, past `max_age`  -> RAISE, and DROP the stale book so
    nothing downstream can read it
  * chain and spot always come from ONE fetch
  * monotonic clock, so an NTP step cannot make a stale book look fresh
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bujji.production_runtime.live_chain_provider import LiveChainProvider  # noqa: E402
from bujji.production_runtime.market_data_provider import (  # noqa: E402
    MarketDataUnavailableError,
)

AS_OF = "2026-08-24"


class _Clock:
    """Injected monotonic clock. Tests move time, never sleep."""

    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def _raw(spot: float, ltp: float):
    """A minimal real-shaped FYERS option-chain payload."""
    return {"data": {"expiryData": [{"date": "25-08-2026", "expiry": "1"}],
                     "optionsChain": [
                         {"strike_price": -1, "symbol": "NSE:NIFTY50-INDEX",
                          "ltp": spot, "option_type": ""},
                         {"strike_price": 24500, "option_type": "CE",
                          "symbol": "NSE:NIFTY2582524500CE", "ltp": ltp,
                          "bid": ltp - 0.5, "ask": ltp + 0.5,
                          "volume": 1000, "oi": 5000, "prev_oi": 4000},
                         {"strike_price": 24500, "option_type": "PE",
                          "symbol": "NSE:NIFTY2582524500PE", "ltp": ltp,
                          "bid": ltp - 0.5, "ask": ltp + 0.5,
                          "volume": 1000, "oi": 5000, "prev_oi": 4000},
                     ]}}


class _Broker:
    def __init__(self, script):
        """`script` is a list of payloads or exceptions, consumed per fetch."""
        self.script = list(script)
        self.calls = 0

    async def get_option_chain_raw(self, underlying, strike_count=20):
        self.calls += 1
        item = self.script.pop(0) if self.script else self.script
        if isinstance(item, Exception):
            raise item
        return item


def _provider(script, clock, **kw):
    return LiveChainProvider(_Broker(script), underlying="NIFTY", clock=clock, **kw)


class TestItRefreshes:
    def test_the_first_read_fetches(self):
        clock = _Clock()
        p = _provider([_raw(24000.0, 100.0)], clock)
        assert p.get_option_chain(AS_OF)
        assert p._broker.calls == 1

    def test_reads_inside_the_refresh_window_reuse_one_fetch(self):
        """This is what keeps the chain and the spot internally consistent."""
        clock = _Clock()
        p = _provider([_raw(24000.0, 100.0)], clock, refresh_after_seconds=30)
        p.get_option_chain(AS_OF)
        clock.advance(10)
        p.get_option_chain(AS_OF)
        p.get_spot()
        assert p._broker.calls == 1

    def test_a_read_past_the_refresh_window_refetches(self):
        """THE regression: this used to return the 09:15 book forever."""
        clock = _Clock()
        p = _provider([_raw(24000.0, 100.0), _raw(24350.0, 61.0)],
                      clock, refresh_after_seconds=30)
        p.get_option_chain(AS_OF)
        assert p.get_spot() == 24000.0
        clock.advance(31)
        p.get_option_chain(AS_OF)
        assert p._broker.calls == 2
        assert p.get_spot() == 24350.0, (
            "spot did not move with the refreshed chain -- strike selection "
            "would still be pricing against the opening book")

    def test_spot_alone_is_also_subject_to_freshness(self):
        """`get_spot()` had no load path at all."""
        clock = _Clock()
        p = _provider([_raw(24000.0, 100.0), _raw(24350.0, 61.0)],
                      clock, refresh_after_seconds=30)
        p.get_option_chain(AS_OF)
        clock.advance(31)
        assert p.get_spot() == 24350.0
        assert p._broker.calls == 2


class TestStaleDataBlocksTheOrderPath:
    def test_a_failed_refresh_within_max_age_keeps_serving(self):
        """One missed poll must not abandon a live position mid-session."""
        clock = _Clock()
        p = _provider([_raw(24000.0, 100.0), RuntimeError("FYERS 502")],
                      clock, refresh_after_seconds=30, max_age_seconds=120)
        p.get_option_chain(AS_OF)
        clock.advance(31)
        assert p.get_option_chain(AS_OF)          # served, with a warning
        assert p.age_seconds() >= 31

    def test_past_max_age_a_failed_refresh_REFUSES(self):
        """The order path must not be reachable with a stale book."""
        clock = _Clock()
        p = _provider([_raw(24000.0, 100.0), RuntimeError("FYERS 502")],
                      clock, refresh_after_seconds=30, max_age_seconds=120)
        p.get_option_chain(AS_OF)
        clock.advance(121)
        with pytest.raises(MarketDataUnavailableError) as exc:
            p.get_option_chain(AS_OF)
        assert "stale book" in str(exc.value)
        assert "121s old" in str(exc.value)

    def test_the_stale_book_is_DROPPED_not_left_readable(self):
        """Raising once is not enough: a cached book left in place would be
        served to the next caller that happens not to trigger a refresh."""
        clock = _Clock()
        p = _provider([_raw(24000.0, 100.0), RuntimeError("502"),
                       RuntimeError("502")],
                      clock, refresh_after_seconds=30, max_age_seconds=120)
        p.get_option_chain(AS_OF)
        clock.advance(121)
        with pytest.raises(MarketDataUnavailableError):
            p.get_option_chain(AS_OF)
        assert p._chain is None and p._spot is None
        assert p.age_seconds() is None
        with pytest.raises(MarketDataUnavailableError):
            p.get_option_chain(AS_OF)

    def test_the_first_load_failing_raises(self):
        clock = _Clock()
        p = _provider([RuntimeError("no network")], clock)
        with pytest.raises(MarketDataUnavailableError):
            p.get_option_chain(AS_OF)


class TestTheSnapshotIsInternallyConsistent:
    def test_chain_and_spot_come_from_one_fetch(self):
        clock = _Clock()
        p = _provider([_raw(24000.0, 100.0), _raw(24350.0, 61.0)],
                      clock, refresh_after_seconds=30)
        chain, spot, age = p.snapshot(AS_OF)
        assert spot == 24000.0 and age == 0.0
        assert p._broker.calls == 1

        clock.advance(31)
        chain2, spot2, age2 = p.snapshot(AS_OF)
        assert spot2 == 24350.0 and age2 == 0.0
        assert p._broker.calls == 2
        assert chain2 is not chain

    def test_age_is_None_before_any_fetch(self):
        assert _provider([_raw(1.0, 1.0)], _Clock()).age_seconds() is None


class TestConfigurationIsRefusedNotAccepted:
    def test_max_age_below_refresh_after_is_rejected(self):
        """Otherwise the book would be refused before a refresh was ever
        attempted -- a config that can only ever fail closed on itself."""
        with pytest.raises(ValueError) as exc:
            LiveChainProvider(_Broker([]), refresh_after_seconds=120,
                              max_age_seconds=30)
        assert "below refresh_after_seconds" in str(exc.value)

    def test_the_shipped_config_is_internally_valid(self):
        import yaml

        cfg = yaml.safe_load(
            (REPO_ROOT / "config" / "options_os_paper_trading.yaml").read_text())
        md = cfg["providers"]["market_data"]
        assert md["chain_max_age_seconds"] > md["chain_refresh_after_seconds"]
        LiveChainProvider(
            _Broker([]),
            refresh_after_seconds=md["chain_refresh_after_seconds"],
            max_age_seconds=md["chain_max_age_seconds"])


class TestTheClockIsMonotonic:
    def test_a_wall_clock_step_cannot_make_a_stale_book_look_fresh(self):
        import time

        p = LiveChainProvider(_Broker([_raw(1.0, 1.0)]))
        assert p._clock is time.monotonic, (
            "a wall-clock source would let an NTP or DST step reset the age of "
            "a book that has not been refetched")
