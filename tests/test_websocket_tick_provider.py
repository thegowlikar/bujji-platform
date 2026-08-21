"""The websocket prices positions; REST covers what it cannot; None survives.

WHY THIS LAYER EXISTS (2026-08-21). The management pass evaluates the
stop-loss once per interval, so tick freshness bounds the window in which an
unbounded loss runs unchecked -- measured worst case ~Rs 39,764 inside one
60-second REST interval on a real straddle credit. FyersTickFeed was
certified live at ~2.4 ticks/s and referenced by the trading runner only
inside a comment. These tests pin the wiring that changed that.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from bujji.broker.fyers_ws import TickSilenceWatchdog, WATCHDOG_RECONNECTING
from bujji.production_runtime.intraday_price_provider import WebsocketTickProvider


class _Feed:
    """Scriptable feed double, mirroring FyersTickFeed's read API."""

    def __init__(self, ticks=None, ages=None, connected=True):
        self._ticks = dict(ticks or {})
        self._ages = dict(ages or {})
        self.is_connected = connected
        self.subscribed = []
        self.reconnects = []
        self.raise_on = set()

    def subscribe(self, symbols):
        if "subscribe" in self.raise_on:
            raise RuntimeError("subscribe failed")
        self.subscribed.append(list(symbols))

    def latest(self, symbol):
        if "latest" in self.raise_on:
            raise RuntimeError("feed unreadable")
        return self._ticks.get(symbol)

    def tick_age_seconds(self, symbol):
        return self._ages.get(symbol)

    def force_reconnect(self, reason):
        self.reconnects.append(reason)


class _Rest:
    """Fallback double. Records exactly which symbols cost a REST call."""

    def __init__(self, prices=None, fail=False):
        self._prices = dict(prices or {})
        self._fail = fail
        self.asked = []

    def get_prices(self, contracts_by_symbol, as_of):
        self.asked.append(sorted(contracts_by_symbol))
        if self._fail:
            return {s: None for s in contracts_by_symbol}
        return {s: self._prices.get(s) for s in contracts_by_symbol}


CONTRACTS = {"NSE:NIFTY26AUG24400CE": object(), "NSE:NIFTY26AUG24400PE": object()}
CE, PE = sorted(CONTRACTS)


def _provider(feed, rest, **kw):
    kw.setdefault("market_hours_fn", lambda: True)
    kw.setdefault("monotonic", time.monotonic)
    return WebsocketTickProvider(feed, None, rest, **kw)


class TestFreshTicksWin:
    def test_fresh_ticks_price_without_a_single_rest_call(self):
        feed = _Feed(ticks={CE: 120.5, PE: 98.2}, ages={CE: 1.2, PE: 0.8})
        rest = _Rest()
        prices = _provider(feed, rest).get_prices(CONTRACTS, "T")
        assert prices == {CE: 120.5, PE: 98.2}
        assert rest.asked == [], "fresh ticks must not cost REST budget"

    def test_only_the_unpriced_leg_costs_a_rest_call(self):
        """Fallback is per symbol, not either/or."""
        feed = _Feed(ticks={CE: 120.5}, ages={CE: 1.2})
        rest = _Rest(prices={PE: 98.2})
        prices = _provider(feed, rest).get_prices(CONTRACTS, "T")
        assert prices == {CE: 120.5, PE: 98.2}
        assert rest.asked == [[PE]]


class TestStalenessIsHonoured:
    def test_a_stale_tick_is_no_tick(self):
        """Pricing a moving position off a stopped clock is the defect."""
        feed = _Feed(ticks={CE: 120.5, PE: 98.2}, ages={CE: 200.0, PE: 1.0})
        rest = _Rest(prices={CE: 121.0})
        prices = _provider(feed, rest, max_tick_age_seconds=90.0).get_prices(CONTRACTS, "T")
        assert prices[CE] == 121.0, "the stale symbol must come from REST"
        assert prices[PE] == 98.2

    def test_a_tick_with_no_age_is_not_trusted(self):
        feed = _Feed(ticks={CE: 120.5}, ages={})
        rest = _Rest(prices={CE: 121.0, PE: 98.0})
        assert _provider(feed, rest).get_prices(CONTRACTS, "T")[CE] == 121.0

    def test_a_zero_or_negative_tick_is_not_a_price(self):
        feed = _Feed(ticks={CE: 0.0, PE: -1.0}, ages={CE: 1.0, PE: 1.0})
        rest = _Rest(prices={CE: 121.0, PE: 98.0})
        prices = _provider(feed, rest).get_prices(CONTRACTS, "T")
        assert prices == {CE: 121.0, PE: 98.0}


class TestNonePropagates:
    def test_both_sources_dry_yields_none_never_a_substitute(self):
        """None must reach revalue(), which already refuses a half-priced
        group. Substituting anything here would silently reintroduce the
        flat-P&L defect this provider family exists to remove."""
        prices = _provider(_Feed(), _Rest(fail=True)).get_prices(CONTRACTS, "T")
        assert prices == {CE: None, PE: None}

    def test_an_unreadable_feed_falls_back_rather_than_raising(self):
        feed = _Feed(ticks={CE: 120.5}, ages={CE: 1.0})
        feed.raise_on.add("latest")
        rest = _Rest(prices={CE: 121.0, PE: 98.0})
        prices = _provider(feed, rest).get_prices(CONTRACTS, "T")
        assert prices == {CE: 121.0, PE: 98.0}

    def test_a_failed_subscribe_still_prices_via_rest(self):
        feed = _Feed()
        feed.raise_on.add("subscribe")
        rest = _Rest(prices={CE: 121.0, PE: 98.0})
        prices = _provider(feed, rest).get_prices(CONTRACTS, "T")
        assert prices == {CE: 121.0, PE: 98.0}


class TestSubscriptionDiscipline:
    def test_symbols_are_subscribed_once_not_per_cycle(self):
        feed = _Feed(ticks={CE: 1.0, PE: 1.0}, ages={CE: 1.0, PE: 1.0})
        p = _provider(feed, _Rest())
        p.get_prices(CONTRACTS, "T1")
        p.get_prices(CONTRACTS, "T2")
        p.get_prices(CONTRACTS, "T3")
        assert len(feed.subscribed) == 1

    def test_a_new_leg_mid_session_is_subscribed(self):
        feed = _Feed(ticks={CE: 1.0}, ages={CE: 1.0})
        p = _provider(feed, _Rest(prices={"NEW": 5.0}))
        p.get_prices({CE: object()}, "T1")
        p.get_prices({CE: object(), "NEW": object()}, "T2")
        assert ["NEW"] in feed.subscribed


class TestTheWatchdogIsDriven:
    def test_silence_escalates_to_a_real_reconnect(self):
        """The management loop is the watchdog's heartbeat: two silent
        checks (detect, then act) must reach force_reconnect."""
        feed = _Feed(ticks={}, ages={CE: 500.0, PE: 500.0})
        clock = iter(range(0, 10_000, 61))
        wd = TickSilenceWatchdog(silence_threshold_seconds=120.0)
        p = WebsocketTickProvider(feed, wd, _Rest(fail=True),
                                  market_hours_fn=lambda: True,
                                  monotonic=lambda: float(next(clock)))
        p.get_prices(CONTRACTS, "T1")   # HEALTHY -> TICK_SILENCE
        p.get_prices(CONTRACTS, "T2")   # TICK_SILENCE -> reconnect issued
        assert feed.reconnects, "persistent silence must force a reconnect"
        assert wd.watchdog_state == WATCHDOG_RECONNECTING

    def test_an_unticked_feed_at_open_is_not_silence(self):
        """Before the first tick ever arrives, tick_age is None everywhere.
        Declaring that 'silence' would reconnect-storm every session open."""
        feed = _Feed()
        wd = TickSilenceWatchdog(silence_threshold_seconds=120.0)
        p = WebsocketTickProvider(feed, wd, _Rest(fail=True),
                                  market_hours_fn=lambda: True,
                                  monotonic=time.monotonic)
        p.get_prices(CONTRACTS, "T1")
        assert feed.reconnects == []

    def test_a_watchdog_failure_never_costs_the_valuation(self):
        class _BadWd:
            def check(self, **kw): raise RuntimeError("wd broken")
        feed = _Feed(ticks={CE: 120.5, PE: 98.2}, ages={CE: 1.0, PE: 1.0})
        p = WebsocketTickProvider(feed, _BadWd(), _Rest(),
                                  market_hours_fn=lambda: True,
                                  monotonic=time.monotonic)
        assert p.get_prices(CONTRACTS, "T")[CE] == 120.5


class TestRunnerWiring:
    def test_websocket_without_the_live_broker_refuses_to_start(self):
        """The identical fail-closed guard type=broker carries: a websocket
        whose fallback is the synthetic PaperBroker would price real
        management off a random walk whenever the socket went quiet."""
        src = (REPO_ROOT / "bujji_options_os_runner.py").read_text()
        i = src.index('tick_type == "websocket"')
        block = src[i:i + 2000]
        assert 'regime_type != "market_thesis_live"' in block
        assert "ConfigurationError" in block

    def test_the_shutdown_path_stops_the_feed(self):
        src = (REPO_ROOT / "bujji_options_os_runner.py").read_text()
        i = src.index("def _shutdown")
        assert "feed.stop()" in src[i:i + 1500]

    def test_production_config_uses_the_websocket(self):
        cfg = (REPO_ROOT / "config" / "options_os_paper_trading.yaml").read_text()
        assert "type: websocket" in cfg
