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
    """Answers: what was each of these instruments worth at this moment?"""

    @abstractmethod
    def get_prices(self, contracts_by_symbol: Dict[str, object],
                   as_of: str) -> Dict[str, Optional[float]]:
        """`{broker_symbol: price_or_None}` -- keyed by the SAME symbol the
        caller uses, so the result drops straight into `revalue_all`."""


class HistoricalTickProvider(IntradayPriceProvider):
    """Replays real captured 5-minute observations."""

    def __init__(self, store, resolution: str = "FIVE_MINUTE",
                 session_start: str = "T00:00:00+05:30") -> None:
        self._store = store
        self._resolution = resolution
        self._session_start_suffix = session_start

    def get_prices(self, contracts_by_symbol, as_of):
        day = as_of[:10]
        start = f"{day}{self._session_start_suffix}"
        prices: Dict[str, Optional[float]] = {}
        for symbol, contract in contracts_by_symbol.items():
            prices[symbol] = self._last_price_at_or_before(contract, start, as_of)
        return prices

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


class LiveTickProvider(IntradayPriceProvider):
    """Reads live prices from a broker's own read-only quote call."""

    def __init__(self, broker, run_async) -> None:
        self._broker = broker
        self._run_async = run_async

    def get_prices(self, contracts_by_symbol, as_of):
        prices: Dict[str, Optional[float]] = {}
        for symbol, contract in contracts_by_symbol.items():
            try:
                price = self._run_async(self._broker.get_ltp(contract))
            except Exception:  # noqa: BLE001 -- a failed quote is unknown, never stale.
                price = None
            prices[symbol] = price if (price or 0) > 0 else None
        return prices
