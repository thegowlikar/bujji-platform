"""Live FYERS option chain, as a MarketDataProvider.

THE LAST MISSING SOURCE. `ReplayChainProvider` reads an EOD bhavcopy;
`StoreChainProvider` reads a captured day. Neither can serve a session
happening right now, so the trading runner could not take a live entry.

WHY THIS COULD NOT BE WRITTEN EARLIER, AND WHAT UNBLOCKED IT.
`FyersBroker.get_option_chain()` deliberately extracts only
strike/option_type/oi and drops everything else, so it cannot build an
`OptionObservation` -- there is no premium in it. Its sibling
`get_option_chain_raw()` exists precisely because that narrow extraction
"has never been proof that OTHER fields (LTP, bid, ask, volume) are
absent from the real response -- only that this codebase has never
looked", and it instructs that a caller needing the full row shape "must
not guess field names" but read them "from a real, dated capture".

That capture exists: `data_certification/fyers_option_chain_discovery_20260813.json`
(2026-08-13 09:19 IST, real NIFTY). Every field name below was read from
it. A real strike row:

    {"strike_price": 24100, "option_type": "CE", "ltp": 319.8,
     "bid": 319.2, "ask": 320.1, "volume": 137475,
     "oi": 344630, "oich": 23595, "prev_oi": 321035,
     "symbol": "NSE:NIFTY2681824100CE"}

DISCLOSED GAPS. The response carries a traded price but not OHLC, so
`close` is fed the real `ltp` and `open`/`high`/`low`/`settlement` are
None -- recorded by `build_option_observation` as explicit
`missing_fields`, exactly as for a bhavcopy row lacking them. Nothing is
back-filled from a neighbouring field.

FAILS CLOSED. No usable rows, no spot, or an unreadable response raises
`MarketDataUnavailableError`. A live session that cannot see the book
must refuse to trade rather than fall back to a stale or synthetic chain
-- the whole point of taking a live entry is that it rests on what the
market is actually doing.
"""
from __future__ import annotations

import asyncio
from typing import Optional, Sequence

from bujji.production_runtime.market_data_provider import (
    MarketDataProvider,
    MarketDataUnavailableError,
)

# The underlying/index row is marked by a sentinel strike, not by absence.
_UNDERLYING_STRIKE_SENTINEL = -1


def _to_iso_expiry(ddmmyyyy: str) -> Optional[str]:
    """`expiryData` dates are DD-MM-YYYY; the rest of the system uses ISO."""
    parts = (ddmmyyyy or "").split("-")
    if len(parts) != 3:
        return None
    day, month, year = parts
    if len(year) != 4:
        return None
    return f"{year}-{month}-{day}"


def _positive(value) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


class LiveChainProvider(MarketDataProvider):
    """The option chain as FYERS is reporting it right now.

    `strike_count` is the number of strikes EACH SIDE of ATM that FYERS
    returns; the default is deliberately wider than the 5 used for OI
    reconciliation, because trade construction needs enough strikes to
    place both short legs and both wings of a condor.
    """

    # A live chain that is never refetched is a replay chain wearing a live
    # label. These bound how old the book may be before it stops being
    # evidence about the market that exists right now.
    DEFAULT_REFRESH_AFTER_SECONDS = 30.0
    DEFAULT_MAX_AGE_SECONDS = 120.0

    def __init__(self, broker, underlying: str = "NIFTY", strike_count: int = 20,
                 logger=None, refresh_after_seconds: float | None = None,
                 max_age_seconds: float | None = None, clock=None) -> None:
        self._broker = broker
        self._underlying = underlying
        self._strike_count = strike_count
        self._refresh_after = float(
            self.DEFAULT_REFRESH_AFTER_SECONDS if refresh_after_seconds is None
            else refresh_after_seconds)
        self._max_age = float(
            self.DEFAULT_MAX_AGE_SECONDS if max_age_seconds is None else max_age_seconds)
        if self._max_age < self._refresh_after:
            raise ValueError(
                f"max_age_seconds ({self._max_age}) is below refresh_after_seconds "
                f"({self._refresh_after}) -- the book would be refused before a "
                f"refresh was ever attempted")
        import time as _time

        # Monotonic: a wall-clock step (NTP, DST) must not make a stale book
        # look fresh. Injectable so freshness is testable without sleeping.
        self._clock = clock or _time.monotonic
        self._fetched_at: Optional[float] = None
        self._last_as_of: Optional[str] = None
        # A component that cannot explain itself fails silently -- the exact
        # defect that cost three blind cycles on 2026-08-21, when
        # WebsocketTickProvider's diagnostics went to a logger the session
        # never configured. Callers pass the session logger; the module
        # default exists only so this class is usable in isolation.
        import logging as _logging

        self._logger = logger or _logging.getLogger("bujji.live_chain_provider")
        self._chain: Optional[Sequence] = None
        self._spot: Optional[float] = None

    # -- MarketDataProvider ------------------------------------------- #
    def get_option_chain(self, as_of_date: str) -> Sequence:
        self._ensure_fresh(as_of_date)
        return self._chain

    def get_spot(self) -> Optional[float]:
        # Spot went through NO freshness path at all -- it returned whatever
        # the first fetch of the day had put there. It now travels with the
        # chain it was read from and is subject to the same limits.
        if self._last_as_of is not None:
            self._ensure_fresh(self._last_as_of)
        return self._spot

    # -- freshness ----------------------------------------------------- #
    def snapshot(self, as_of_date: str):
        """`(chain, spot, age_seconds)` from ONE fetch.

        `get_option_chain()` followed by `get_spot()` is two calls, and a
        refresh landing between them would pair a chain from fetch N with a
        spot from fetch N+1 -- an internally inconsistent book, which is
        exactly what strike selection must never run on. This returns the
        triple atomically.
        """
        self._ensure_fresh(as_of_date)
        return self._chain, self._spot, self.age_seconds()

    def age_seconds(self) -> Optional[float]:
        """How old the served book is, or None if nothing has been fetched."""
        if self._fetched_at is None:
            return None
        return max(0.0, self._clock() - self._fetched_at)

    # -- internals ----------------------------------------------------- #
    def _ensure_fresh(self, as_of_date: str) -> None:
        """Refetch when the book is older than the refresh interval, and
        REFUSE when it is older than the hard limit.

        THE DEFECT THIS REPLACES. `_ensure_loaded` opened with
        `if self._chain is not None: return` -- loaded once at ~09:15 and never
        again. Every entry for the rest of the session selected its strikes and
        read its premiums from that one snapshot; in a continuous session
        admitting entries until 14:30 the book could be more than five hours
        old. A 20-delta strike chosen against a five-hour-old spot is not a
        20-delta strike, and the module docstring already promised the
        opposite: a live session "must refuse to trade rather than fall back to
        a stale or synthetic chain".

        Fails CLOSED on both edges: the first load raises if it cannot fetch,
        and a refresh failure raises once the cached book passes `max_age`.
        Between `refresh_after` and `max_age` a failed refresh keeps serving
        the previous book and says so -- one missed poll is not a reason to
        abandon a position mid-session, but an unbounded run of them is.
        """
        self._last_as_of = as_of_date
        now = self._clock()

        if self._chain is None or self._fetched_at is None:
            chain, spot = self._fetch(as_of_date)
            self._chain, self._spot, self._fetched_at = chain, spot, self._clock()
            return

        age = now - self._fetched_at
        if age < self._refresh_after:
            return

        try:
            chain, spot = self._fetch(as_of_date)
        except MarketDataUnavailableError as exc:
            age = self._clock() - self._fetched_at
            if age > self._max_age:
                # Drop the stale book so nothing downstream can read it.
                self._chain = None
                self._spot = None
                self._fetched_at = None
                raise MarketDataUnavailableError(
                    f"live option chain for {self._underlying!r} is {age:.0f}s old "
                    f"(limit {self._max_age:.0f}s) and the refresh failed ({exc}) -- "
                    f"refusing to serve a stale book to strike selection"
                ) from exc
            self._logger.warning(
                "live chain refresh failed (%s); serving the previous book, now "
                "%.0fs old (hard limit %.0fs).", exc, age, self._max_age)
            return

        self._chain, self._spot, self._fetched_at = chain, spot, self._clock()

    def _fetch(self, as_of_date: str):
        """One raw call -> (chain, spot). Raises rather than returning partial."""
        try:
            raw = asyncio.run(
                self._broker.get_option_chain_raw(self._underlying, strike_count=self._strike_count))
        except Exception as exc:  # noqa: BLE001 -- surface as a data failure, not a raw traceback.
            raise MarketDataUnavailableError(
                f"live option chain request failed for {self._underlying!r}: {exc}") from exc
        if not raw:
            raise MarketDataUnavailableError(
                f"live option chain returned no data for {self._underlying!r} -- refusing to "
                "trade on an absent book."
            )
        chain, spot = self._build(raw, as_of_date)
        if not chain:
            raise MarketDataUnavailableError(
                f"live option chain produced zero usable rows for {self._underlying!r}"
            )
        if spot is None:
            raise MarketDataUnavailableError(
                f"live option chain carried no usable underlying price for {self._underlying!r}"
            )
        return tuple(chain), spot

    def _build(self, raw: dict, as_of_date: str):
        from bujji.options_observation import taxonomy as opt_taxonomy
        from bujji.options_observation.engine import build_option_observation

        data = raw.get("data") or {}
        rows = data.get("optionsChain") or []

        # The chain returned for an unspecified timestamp is the NEAREST
        # expiry, which is `expiryData[0]`. Reading it from the payload
        # rather than parsing the option symbol avoids depending on
        # FYERS's symbol-encoding scheme.
        expiry_entries = data.get("expiryData") or []
        expiry = _to_iso_expiry(expiry_entries[0].get("date")) if expiry_entries else None

        spot = None
        for row in rows:
            if row.get("strike_price") == _UNDERLYING_STRIKE_SENTINEL:
                spot = _positive(row.get("ltp"))
                break

        chain = []
        dropped_no_symbol = []
        for row in rows:
            option_type = row.get("option_type")
            strike = row.get("strike_price")
            if option_type not in ("CE", "PE") or strike is None or strike < 0:
                continue  # underlying/VIX rows carry strike_price=-1 and option_type="".
            close = _positive(row.get("ltp"))
            if close is None:
                continue  # never traded -- not a usable chain row.
            row_symbol = row.get("symbol")
            if not row_symbol:
                # NO FABRICATED SYMBOL (2026-08-21).
                #
                # This read `row.get("symbol") or f"{underlying}{strike}{type}"`.
                # That `or` was a silent downgrade: when FYERS omits `symbol`
                # on a row it manufactured a THIRD symbol format carrying no
                # expiry and no NSE: prefix -- and wrote it into the exact
                # field Gate B (trading_brain_runtime.py:274-280) trusts as
                # broker-real. A fabricated value in a field whose whole
                # purpose is to be the broker's own string is the most
                # expensive kind of default.
                #
                # Dropped and COUNTED, not `continue`d silently: this builder
                # already drops rows above when `ltp` is missing, so a second
                # uncounted drop would be invisible -- the chain would just be
                # quietly shorter.
                dropped_no_symbol.append(f"{option_type}{int(strike)}")
                continue
            chain.append(build_option_observation(
                underlying=self._underlying,
                instrument_symbol=row_symbol,
                strike=float(strike), expiry=expiry or as_of_date, option_type=option_type,
                exchange="NSE", segment="FO", timestamp=as_of_date, resolution="SNAPSHOT",
                # A live quote carries a traded price, not OHLC bars.
                open_=None, high=None, low=None, close=close, settlement=None,
                volume=row.get("volume"),
                open_interest=row.get("oi"),
                change_in_open_interest=row.get("oich"),
                underlying_price=spot,
                origin="fyers_optionchain_live",
                acquisition_timestamp=as_of_date, normalization_timestamp=as_of_date,
                # FYERS's optionchain response returned `row_symbol` itself --
                # this is the execution venue's own string, passed through
                # verbatim above, and rows lacking it were already dropped and
                # counted rather than filled in. Nothing else in this codebase
                # may claim BROKER_AUTHORITATIVE.
                symbol_provenance=opt_taxonomy.SYMBOL_PROVENANCE_BROKER_AUTHORITATIVE,
                bid=_positive(row.get("bid")), ask=_positive(row.get("ask")),
            ))
        if dropped_no_symbol:
            # Loud, and specific about WHICH strikes: a caller that later
            # cannot resolve one of these needs to know the row existed and
            # was refused, not guess that the chain was short.
            self._logger.warning(
                "option chain: dropped %d row(s) carrying no broker symbol -- %s. "
                "These strikes are NOT selectable this cycle.",
                len(dropped_no_symbol), sorted(dropped_no_symbol))

        return chain, spot
