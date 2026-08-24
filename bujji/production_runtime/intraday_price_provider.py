"""Intraday per-leg prices -- the tick feed the runner never had.

WHY THIS EXISTS. `bujji_options_os_runner` revalued open positions by
passing `self._entry_prices` (the prices captured at FILL) back in as
though they were current prices. Unrealized P&L was therefore flat by
construction, for the whole session, every session: MFE and MAE could
only ever be 0.0, profit-target and max-loss hard limits could never
fire, and 30 sessions of "evidence" would contain no information about
how any trade behaved between entry and exit. The runner's own docstring
disclosed this and called a tick feed "a genuinely different, larger
piece of work".

This module is that feed, as a provider boundary rather than a rewrite --
the same shape `MarketDataProvider`/`RegimeProvider` already establish,
so the runner swaps an implementation instead of growing a data path.

TWO REAL IMPLEMENTATIONS, ONE HONEST ABSENCE:

  `HistoricalTickProvider` replays REAL captured 5-minute option
  observations out of `HistoricalObservationStore` -- the same store
  `capture_options_reality_session.py` already writes. It answers "the
  last price actually observed at or before T", never an interpolation
  between observations, because a price that was never printed is not
  evidence.

  `LiveTickProvider` wraps a broker's own `get_ltp` for real sessions.
  It is a read-only market-data call; this module never imports an order
  path and never places, modifies, or cancels anything.

  A symbol with no observation at or before T returns `None` -- NOT the
  entry price, and NOT the last price from some other day. `None`
  propagates into `revalue()` (which already refuses to value a group
  whose legs are not all priced) and into `compute_mfe_mae` (which drops
  unknown cycles). An unpriced leg must stay unpriced: substituting the
  entry price is exactly the bug this module exists to remove, and it
  would silently reintroduce it while looking like a tick feed.

DATA COVERAGE, MEASURED NOT ASSUMED (2026-08-17): the store holds
162,150 five-minute OPTION rows, all on 2026-08-14, across 2,190
instruments -- of which 740 show genuine intraday movement and 1,450 are
flat all day (illiquid strikes that never traded; flatness there is real
market behaviour, not missing data). No bhavcopy exists for that date,
so the REPLAY path cannot yet enter and revalue on one coherent day.
That is a data-collection gap, not an architectural one, and it does not
affect live sessions, where the broker supplies chain and ticks together.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from bujji.market_perception.quote import (
    Quote, SOURCE_REST, SOURCE_TICK, from_rest_price, unavailable)
from typing import Dict, Optional, Sequence

# Payload keys that carry a traded price, in preference order. Options
# are captured as `ltp`; index/spot/future series are captured as OHLC.
_PRICE_KEYS = ("ltp", "close", "last_price")


def store_identity_for(contract) -> Optional[str]:
    """The `HistoricalObservationStore` instrument identity for an
    `OptionContract`, e.g. `NIFTY|2026-08-18|21900|CE`.

    Built from the contract's own real fields rather than parsed out of
    its broker symbol, whose format differs per broker. Returns None when
    any component is missing -- an identity is never partially guessed,
    because a wrong identity silently prices the wrong instrument.
    """
    underlying = getattr(contract, "underlying", None)
    expiry = getattr(contract, "expiry", None)
    strike = getattr(contract, "strike", None)
    option_type = getattr(contract, "option_type", None)
    if not underlying or not expiry or strike is None or option_type is None:
        return None
    opt = getattr(option_type, "value", option_type)
    return f"{underlying}|{expiry}|{int(strike)}|{opt}"


def _price_from_payload(payload) -> Optional[float]:
    if not isinstance(payload, dict):
        return None
    for key in _PRICE_KEYS:
        value = payload.get(key)
        if value is not None:
            try:
                price = float(value)
            except (TypeError, ValueError):
                continue
            # A captured 0.0 is a real "no trade / no quote" marker in
            # this store, not a real price of zero -- valuing a leg at
            # zero would fabricate a total loss on it.
            if price > 0:
                return price
    return None


class IntradayPriceProvider(ABC):
    """Answers: what was each of these instruments worth at this moment?

    TWO CONTRACTS, ONE AUTHORITY.

    `get_quotes()` is the real one. It returns typed quotes that carry the
    fields the source actually supplied, where each came from, and when --
    so a decision can say what it was made on.

    `get_prices()` is the LEGACY float projection, retained only while its
    callers migrate. It is derived FROM `get_quotes()` and is therefore
    incapable of disagreeing with it; it is not a second source. It discards
    bid, ask, sizes, volume, open interest, provenance and freshness, which
    is why nothing new may be built on it.
    """

    @abstractmethod
    def get_quotes(self, contracts_by_symbol: Dict[str, object],
                   as_of: str) -> Dict[str, "Quote"]:
        """`{broker_symbol: Quote}`. A symbol with no usable data is present
        with an UNAVAILABLE quote rather than absent, so a caller can tell
        'asked and got nothing' from 'never asked'."""

    def get_prices(self, contracts_by_symbol: Dict[str, object],
                   as_of: str) -> Dict[str, Optional[float]]:
        """DEPRECATED one-way adapter over `get_quotes`.

        Kept so unmigrated callers keep working during Phase 1. It is derived,
        never independent: there is no path by which this can return a price
        the typed contract does not also hold. Remove it once
        tests/test_quote_path_is_authoritative.py shows no enabled-runtime
        caller remains.
        """
        return {sym: (q.ltp if q is not None else None)
                for sym, q in self.get_quotes(contracts_by_symbol, as_of).items()}


class HistoricalTickProvider(IntradayPriceProvider):
    """Replays real captured 5-minute observations."""

    def __init__(self, store, resolution: str = "FIVE_MINUTE",
                 session_start: str = "T00:00:00+05:30") -> None:
        self._store = store
        self._resolution = resolution
        self._session_start_suffix = session_start

    def get_quotes(self, contracts_by_symbol, as_of):
        """Replayed observations carry a price and nothing else, and say so:
        source REPLAY, every other field UNAVAILABLE. A replayed bar is not a
        book, and a decision that needed a spread must not be able to take one
        from here."""
        from bujji.market_perception.quote import SOURCE_REPLAY
        day = as_of[:10]
        start = f"{day}{self._session_start_suffix}"
        out: Dict[str, Quote] = {}
        for symbol, contract in contracts_by_symbol.items():
            price = self._last_price_at_or_before(contract, start, as_of)
            q = from_rest_price(symbol, price)
            out[symbol] = Quote(symbol=symbol, fields=q.fields,
                                source=SOURCE_REPLAY)
        return out

    def _last_price_at_or_before(self, contract, start: str, as_of: str) -> Optional[float]:
        identity = store_identity_for(contract)
        if identity is None:
            return None
        try:
            rows = self._store.range(identity, self._resolution, start, as_of)
        except Exception:  # noqa: BLE001 -- an unreadable store is unknown, never a price.
            return None
        # `range` is ordered by timestamp; the LAST row at or before
        # `as_of` is the most recent real print. Rows whose payload has no
        # usable price are skipped rather than treated as zero.
        for row in reversed(list(rows)):
            price = _price_from_payload(getattr(row, "payload", None))
            if price is not None:
                return price
        return None


class WebsocketTickProvider(IntradayPriceProvider):
    """Live per-leg prices from the FYERS websocket, REST as fallback.

    WHY THIS EXISTS (the largest uncontrolled-loss path, Layer 1/2 audit,
    2026-08-21). The stop-loss, the daily loss limit and the emergency brake
    are evaluated once per management pass, so the polling interval IS the
    width of the window in which an unbounded loss runs unchecked. Measured
    over 168,194 real 5-minute NIFTY bars, the worst single bar ranged 611.8
    points -- ~Rs 39,764 against a real 240.95-point straddle credit, 2.5x
    the stop, inside ONE 60-second REST interval. FyersTickFeed (certified
    live at ~2.4 ticks/s, with its own silence watchdog and generation-safe
    reconnect) existed the whole time and was referenced by the trading
    runner only inside a comment. This wires it.

    DESIGN. The feed is PRIMARY; the broker's REST get_ltp is a PER-SYMBOL
    fallback, not an either/or switch. Each call:

      1. subscribes any not-yet-subscribed leg symbols (FyersTickFeed's own
         `_pending_symbols` makes this idempotent, and its on_connect closure
         deterministically restores subscriptions across reconnects -- the
         installed SDK does not, which that class documents and works around);
      2. drives one TickSilenceWatchdog check off this call's own cadence --
         the management loop is the heartbeat, no extra thread is added;
      3. answers each symbol from the freshest source available:
         a live tick no older than `max_tick_age_seconds`, else REST for
         THAT symbol only, else None.

    None propagates -- never the entry price, never a stale tick relabelled
    fresh. `revalue()` already refuses a partially priced group, and
    substituting anything here would silently reintroduce the exact
    flat-P&L defect this provider family exists to remove.

    STALENESS BOUND. `max_tick_age_seconds` defaults to 90: three missed
    ~30s index broadcast intervals, and tighter than the watchdog's own
    120s silence threshold so a symbol goes to REST fallback BEFORE the
    feed as a whole is declared silent. A stale tick is treated as no tick
    -- age is measured per symbol from the feed's own receipt clock.

    READ-ONLY. Ticks and quotes only; this class never imports an order
    path and never places, modifies, or cancels anything.
    """

    def __init__(self, feed, watchdog, fallback: "LiveTickProvider",
                 *, max_tick_age_seconds: float = 90.0,
                 market_hours_fn=None, monotonic=None, logger=None) -> None:
        import logging as _logging
        import time as _time

        self._feed = feed
        self._watchdog = watchdog
        self._fallback = fallback
        self._max_tick_age_seconds = max_tick_age_seconds
        # Injected for tests; real callers take the defaults.
        self._market_hours_fn = market_hours_fn or (lambda: True)
        self._monotonic = monotonic or _time.monotonic
        self._log = logger or _logging.getLogger("bujji.websocket_tick_provider")
        self._subscribed: set = set()

    def get_quotes(self, contracts_by_symbol, as_of):
        """Typed quotes, per symbol, with the source recorded on each.

        MIXED SOURCES STAY VISIBLE. A pass can legitimately price some legs
        from a live tick and the rest from REST. Previously both arrived as
        bare floats and the difference vanished; now each quote declares
        LIVE_TICK or REST_FALLBACK, so a decision made on a mixture can say so.
        """
        symbols = list(contracts_by_symbol)
        self._ensure_subscribed(symbols)
        self._drive_watchdog(symbols)

        out: Dict[str, Quote] = {}
        rest_needed = {}
        now_mono = self._monotonic()
        for symbol, contract in contracts_by_symbol.items():
            q = self._fresh_quote(symbol, now_mono)
            if q is not None:
                out[symbol] = q
            else:
                rest_needed[symbol] = contract

        if rest_needed:
            # Fallback is per symbol: legs with a fresh tick keep it, and
            # only the unpriced remainder costs REST calls against the
            # shared host-wide FYERS budget.
            self._log.info(
                "websocket priced %d/%d legs; falling back to REST for %s",
                len(out), len(symbols), sorted(rest_needed))
            out.update(self._fallback_quotes(rest_needed, as_of))
        return out

    def _fallback_quotes(self, needed, as_of):
        """Typed quotes from the fallback, whichever contract it implements.

        A fallback that predates `get_quotes` still answers in floats, and
        those are wrapped as REST_FALLBACK quotes carrying LTP alone. The
        wrapping is what keeps provenance honest: a float from REST becomes a
        quote that SAYS it came from REST, rather than one that looks like a
        tick because it arrived through the tick provider.
        """
        import time as _t
        getter = getattr(self._fallback, "get_quotes", None)
        if getter is not None:
            try:
                return dict(getter(needed, as_of) or {})
            except Exception:  # noqa: BLE001 -- a failed fallback is unknown, never a price
                return {}
        try:
            prices = self._fallback.get_prices(needed, as_of) or {}
        except Exception:  # noqa: BLE001
            return {}
        now_w, now_m = _t.time(), _t.monotonic()
        return {sym: from_rest_price(sym, price, observed_wall=now_w,
                                     observed_mono=now_m)
                for sym, price in prices.items()}

    def _fresh_quote(self, symbol, now_mono):
        """A typed quote if the feed has one and it is FRESH; else None.

        Freshness is monotonic and per symbol. A quote with no monotonic
        stamp is not fresh -- unknown age is not youth.
        """
        # PREFER THE TYPED QUOTE; fall back to the float interface.
        #
        # Not every feed implementation offers `latest_quote` yet -- the
        # migration is deliberately incremental, and a provider that hard-
        # required the new method would break every caller holding an older
        # feed before those callers had anywhere to move to. When only the
        # float interface exists the quote is SYNTHESISED from it and declared
        # LIVE_TICK with ltp alone available: honest about carrying no book,
        # rather than inventing one.
        getter = getattr(self._feed, "latest_quote", None)
        if getter is not None:
            try:
                q = getter(symbol)
            except Exception:  # noqa: BLE001 -- unreadable feed is unknown, never a price
                return None
            if q is not None:
                if not q.is_fresh(now_mono, self._max_tick_age_seconds):
                    return None
                if not q.get("ltp").is_available or not (q.ltp or 0) > 0:
                    # Acked-and-silent, or malformed. Evidence, not a price.
                    return None
                return q

        price = self._fresh_tick(symbol)
        if price is None:
            return None
        return Quote(
            symbol=symbol, source=SOURCE_TICK, recv_mono=now_mono,
            fields={"ltp": __import__(
                "bujji.market_perception.quote", fromlist=["FieldValue"]
            ).FieldValue(float(price), "AVAILABLE", SOURCE_TICK,
                         None, now_mono, "feed.latest")})

    # ------------------------------------------------------------------ #
    def _ensure_subscribed(self, symbols) -> None:
        fresh = [s for s in symbols if s not in self._subscribed]
        if not fresh:
            return
        try:
            self._feed.subscribe(fresh)
            self._subscribed.update(fresh)
        except Exception as exc:  # noqa: BLE001 -- a failed subscribe leaves REST fallback intact
            self._log.warning("websocket subscribe failed (%s); REST fallback covers "
                              "these legs this cycle", exc)

    def _drive_watchdog(self, symbols) -> None:
        """One watchdog check per get_prices call -- the management loop's
        own cadence is the heartbeat. tick_age is the age of the FRESHEST
        subscribed symbol: the watchdog guards the FEED (connected but
        wholly silent), while per-symbol staleness is `_fresh_tick`'s job.
        A wholly-unticked feed yields tick_age None, which the watchdog
        treats as not-silent -- correct at session start, before the first
        tick has ever arrived; the REST fallback prices every leg then.
        """
        if self._watchdog is None:
            return
        try:
            ages = [age for age in (self._feed.tick_age_seconds(s) for s in symbols)
                    if age is not None]
            self._watchdog.check(
                tick_age=min(ages) if ages else None,
                is_connected=self._feed.is_connected,
                market_hours=self._market_hours_fn(),
                now_monotonic=self._monotonic(),
                force_reconnect_fn=self._feed.force_reconnect,
            )
        except Exception as exc:  # noqa: BLE001 -- the watchdog must never cost a valuation
            self._log.warning("tick watchdog check failed: %s", exc)

    def _fresh_tick(self, symbol) -> Optional[float]:
        try:
            price = self._feed.latest(symbol)
            if price is None or not price > 0:
                return None
            age = self._feed.tick_age_seconds(symbol)
            if age is None or age > self._max_tick_age_seconds:
                # A stale tick is no tick. Relabelling it fresh would price
                # a moving position off a stopped clock.
                return None
            return float(price)
        except Exception:  # noqa: BLE001 -- an unreadable feed is unknown, never a price
            return None


class LiveTickProvider(IntradayPriceProvider):
    """Reads live prices from a broker's own read-only quote call."""

    def __init__(self, broker, run_async) -> None:
        self._broker = broker
        self._run_async = run_async

    def get_quotes(self, contracts_by_symbol, as_of):
        """REST reads, each declared REST_FALLBACK with one field.

        Everything except LTP is UNAVAILABLE, because a REST price says
        nothing about the book. Filling those in would be fabrication.
        """
        import time as _t
        out: Dict[str, Quote] = {}
        for symbol, contract in contracts_by_symbol.items():
            try:
                price = self._run_async(self._broker.get_ltp(contract))
            except Exception:  # noqa: BLE001 -- a failed quote is unknown, never stale.
                price = None
            price = price if (price or 0) > 0 else None
            out[symbol] = from_rest_price(symbol, price,
                                          observed_wall=_t.time(),
                                          observed_mono=_t.monotonic())
        return out
