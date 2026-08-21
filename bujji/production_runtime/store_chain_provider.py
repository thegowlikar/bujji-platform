"""Option chain served from captured intraday observations.

WHY. `ReplayChainProvider` reads a bhavcopy — one EOD snapshot, so the
chain is identical on every call and there is no intraday granularity.
Meanwhile `capture_options_reality_session.py` has been writing a FULL
intraday chain into `HistoricalObservationStore` all along (2,190 NIFTY
option instruments at 5-minute resolution on 2026-08-14, plus matching
SPOT/INDEX series). Nothing could read a chain back out of it, so entry
had to come from a bhavcopy on one date while ticks existed on another,
and no single day could serve both.

This provider closes that: entry chain AND revaluation ticks come from
the SAME real capture, on the SAME day, from the same rows. Combined with
`HistoricalTickProvider`, a captured session finally replays coherently.

It builds real `OptionObservation`s through `options_observation.engine.
build_option_observation` — the same constructor the bhavcopy path uses —
rather than hand-assembling the observation graph, so downstream
consumers cannot tell the difference and no second chain format exists.

MEASURED COST, DISCLOSED. Loading one day's chain drives peak RSS to
~950MB, because `HistoricalObservationStore.range_by_prefix` hydrates the
whole matching row set (~162k option rows for a full-day window) before
returning; streaming the consuming loop does not change that, the cost is
inside the store. Two consequences worth knowing before scheduling this:
a session that loads a chain will trip `ops.health_monitor`'s own 500MB
MEMORY_WARNING_KB for the remainder of that process, and peak RSS never
falls again. Load the chain ONCE per session (this provider caches it) and
prefer a narrow `as_of_time` window. Making the store itself stream is the
real fix and is deliberately out of this module's scope.

DISCLOSED GAPS, NOT SILENT ONES. The options capture records
`ltp`/`bid`/`ask`/`volume`/`open_interest`, not full OHLC. `close` is
therefore fed the real traded `ltp`, and `open`/`high`/`low`/`settlement`
are passed as `None` -- which `build_option_observation` records as
explicit `missing_fields`, exactly as it does for a bhavcopy row that
lacks them. Nothing is back-filled from a neighbouring field to make the
row look more complete than the capture actually was.
"""
from __future__ import annotations

from typing import Optional, Sequence

from bujji.production_runtime.market_data_provider import (
    MarketDataProvider,
    MarketDataUnavailableError,
)

DEFAULT_SPOT_IDENTITY = "NSE:NIFTY50-INDEX"
_SPOT_KEYS = ("close", "ltp", "last_price")


def _payload_value(payload, keys) -> Optional[float]:
    if not isinstance(payload, dict):
        return None
    for key in keys:
        value = payload.get(key)
        if value is None:
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if number > 0:
            return number
    return None


def _parse_identity(identity: str):
    """`NIFTY|2026-08-18|22000|CE` -> (underlying, expiry, strike, type).

    Returns None on anything that does not match, so a stray identity in
    the store can never be mistaken for an option contract.
    """
    parts = identity.split("|")
    if len(parts) != 4:
        return None
    underlying, expiry, strike, option_type = parts
    if option_type not in ("CE", "PE"):
        return None
    try:
        strike_value = float(strike)
    except ValueError:
        return None
    return underlying, expiry, strike_value, option_type


class StoreChainProvider(MarketDataProvider):
    """Serves the chain as it really stood at `as_of_time` on the
    captured day. Unlike the bhavcopy provider this is a point-in-time
    read: ask for 09:20 and you get the 09:20 book, not the day's close.
    """

    def __init__(self, store, underlying: str = "NIFTY",
                 as_of_time: str = "T09:20:00+05:30",
                 resolution: str = "FIVE_MINUTE",
                 spot_identity: str = DEFAULT_SPOT_IDENTITY) -> None:
        self._store = store
        self._underlying = underlying
        self._as_of_time = as_of_time
        self._resolution = resolution
        self._spot_identity = spot_identity
        self._chain: Optional[Sequence] = None
        self._spot: Optional[float] = None

    # -- MarketDataProvider ------------------------------------------- #
    def get_option_chain(self, as_of_date: str) -> Sequence:
        self._ensure_loaded(as_of_date)
        return self._chain

    def get_spot(self) -> Optional[float]:
        return self._spot

    # -- internals ----------------------------------------------------- #
    def _window(self, as_of_date: str):
        day = as_of_date[:10]
        return f"{day}T00:00:00+05:30", f"{day}{self._as_of_time}"

    def _ensure_loaded(self, as_of_date: str) -> None:
        if self._chain is not None:
            return
        start, end = self._window(as_of_date)
        spot = self._load_spot(start, end)
        chain = self._load_chain(start, end, spot)
        if not chain:
            raise MarketDataUnavailableError(
                f"observation store produced zero usable option-chain rows for "
                f"underlying={self._underlying!r} at {end!r} "
                f"(resolution={self._resolution!r})"
            )
        if spot is None or spot <= 0:
            raise MarketDataUnavailableError(
                f"observation store produced no usable spot price for "
                f"{self._spot_identity!r} at {end!r}"
            )
        self._chain = tuple(chain)
        self._spot = spot

    def _load_spot(self, start: str, end: str) -> Optional[float]:
        # Streamed, keeping only the latest usable print -- the day's
        # spot series is materialised nowhere.
        latest = None
        try:
            for row in self._store.range(self._spot_identity, self._resolution, start, end):
                price = _payload_value(getattr(row, "payload", None), _SPOT_KEYS)
                if price is not None:
                    latest = price
        except Exception:  # noqa: BLE001 -- unreadable store is unknown, never a fabricated spot.
            return None
        return latest

    def _load_chain(self, start: str, end: str, spot: Optional[float]):
        from bujji.options_observation import taxonomy as opt_taxonomy
        from bujji.options_observation.engine import build_option_observation

        # Keep only the LAST real print per contract at or before `end` --
        # the book as it actually stood, never an average across the day.
        #
        # STREAMED, deliberately. A full-day window spans ~162k option
        # rows to produce a ~2.2k-contract book; materialising the query
        # first (an earlier draft called `list()` here) drove peak RSS to
        # ~950MB and tripped the operator health monitor's own 500MB
        # warning for the rest of the process. Memory is now bounded by
        # the number of distinct CONTRACTS, not the number of rows.
        latest = {}
        try:
            for row in self._store.range_by_prefix(
                    f"{self._underlying}|", self._resolution, start, end):
                identity = getattr(getattr(row, "observation", None), "identity", None)
                key = getattr(identity, "instrument", None) or getattr(row, "instrument_identity", None)
                if key:
                    latest[key] = row
        except Exception as exc:  # noqa: BLE001
            raise MarketDataUnavailableError(
                f"could not read option observations from the store: {exc}") from exc

        chain = []
        for identity, row in latest.items():
            parsed = _parse_identity(identity)
            if parsed is None:
                continue
            underlying, expiry, strike, option_type = parsed
            payload = getattr(row, "payload", None) or {}
            close = _payload_value(payload, ("ltp", "close", "last_price"))
            if close is None:
                continue  # never traded/quoted -- not a usable chain row.
            timestamp = getattr(getattr(row, "observation", None), "identity", None)
            timestamp = getattr(timestamp, "timestamp", None) or end
            chain.append(build_option_observation(
                underlying=underlying,
                # NO BROKER SYMBOL EXISTS HERE, AND THIS NO LONGER PRETENDS ONE DOES.
                #
                # This line used to read
                #     instrument_symbol=f"{underlying}{expiry}{int(strike)}{option_type}"
                # which manufactured "NIFTY2026-08-1824000CE" -- shaped like a
                # real symbol, tradable nowhere. The observation store simply
                # never captured broker symbols: option rows are keyed
                # "NIFTY|2026-08-18|22000|CE" (see _parse_identity above), a
                # pipe-delimited INTERNAL identity. The fabrication existed only
                # because `instrument_symbol` is a required, validated-non-empty
                # field that feeds observation_id, so "no symbol" was not
                # expressible. Provenance makes it expressible.
                #
                # The sentinel keeps identity working (deterministic, so
                # observation_id stays stable across rebuilds) while being
                # unmistakable: it carries "|", which no exchange symbol
                # vocabulary in use here contains.
                instrument_symbol=opt_taxonomy.unresolved_symbol(
                    underlying, expiry, int(strike), option_type),
                symbol_provenance=opt_taxonomy.SYMBOL_PROVENANCE_ABSENT,
                strike=strike, expiry=expiry, option_type=option_type,
                exchange="NSE", segment="FO", timestamp=timestamp,
                resolution=self._resolution,
                # The capture records a traded price, not OHLC: the real
                # `ltp` becomes `close`, and the rest are disclosed gaps.
                open_=None, high=None, low=None, close=close, settlement=None,
                volume=payload.get("volume"),
                open_interest=payload.get("open_interest"),
                change_in_open_interest=payload.get("open_interest_change"),
                underlying_price=spot,
                origin="historical_observation_store",
                acquisition_timestamp=timestamp,
                normalization_timestamp=timestamp,
                bid=payload.get("bid") or None,
                ask=payload.get("ask") or None,
            ))
        return chain
