"""Read-only market-data facade over captured observations.

WHY THIS EXISTS. The canonical trading runner takes its regime from a
human (`HumanSuppliedRegimeProvider`), because deriving it needs a real
`MarketThesisAssessment`, which needs `CycleEvidence`, which needs
`IntelligenceCycleRecorder.record_cycle(snapshot, broker, ...)` -- and
that needs a BROKER exposing six read-only market-data methods. The
runner has only a chain provider and a `PaperBroker` whose market data is
synthetic. Feeding synthetic spot/VIX/candles into the intelligence stack
would fabricate the very evidence the decision is supposed to rest on.

This serves those six methods from the SAME real captured observations
the tick source and chain provider already read, at a caller-controlled
`as_of` moment. It is the missing data source that lets Bujji derive its
own regime on a replayed day instead of being told one.

STRUCTURALLY INCAPABLE OF EXECUTION. `place_order`/`cancel_order`/
`get_order`/`get_open_positions` raise `ReplayBrokerExecutionError`. This
is a market-data facade being passed into the intelligence layer; if it
ever reaches an execution path that is a wiring bug, and it must fail
loudly rather than silently pretend to trade. Execution belongs to
`PaperBroker` and nothing here duplicates it.

NEVER FABRICATES. Every method returns only what was really captured at
or before `as_of`; a missing observation returns None/empty rather than
an interpolation or a neighbouring day's value. Downstream engines
already degrade honestly on None (UNKNOWN readings, never invented ones),
so an incomplete capture produces a weaker thesis -- not a false one.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import List, Optional

from bujji.broker.base import Broker
from bujji.core.models import Candle

DEFAULT_SPOT_IDENTITY = "NSE:NIFTY50-INDEX"
DEFAULT_VIX_IDENTITY = "NSE:INDIAVIX-INDEX"
DEFAULT_FUTURES_IDENTITY = "NIFTY_FUT_CONTINUOUS"
_PRICE_KEYS = ("close", "ltp", "last_price")


def _as_datetime(value):
    """Store timestamps are ISO strings; Candle needs a real datetime."""
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


class ReplayBrokerExecutionError(RuntimeError):
    """Raised if an execution method is reached. This facade serves market
    data only; reaching an order path means something is mis-wired."""


def _num(payload, keys=_PRICE_KEYS) -> Optional[float]:
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


class ReplayMarketBroker(Broker):
    """Market data as it really stood at `as_of` on a captured day."""

    name = "replay_market"

    def __init__(self, store, *, as_of: str, underlying: str = "NIFTY",
                 resolution: str = "FIVE_MINUTE",
                 spot_identity: str = DEFAULT_SPOT_IDENTITY,
                 vix_identity: str = DEFAULT_VIX_IDENTITY,
                 futures_identity: str = DEFAULT_FUTURES_IDENTITY) -> None:
        self._store = store
        self._as_of = as_of
        self._underlying = underlying
        self._resolution = resolution
        self._spot_identity = spot_identity
        self._vix_identity = vix_identity
        self._futures_identity = futures_identity

    def set_as_of(self, as_of: str) -> None:
        """Advance replay time. The runner calls this per cycle so the
        intelligence stack sees the market evolve rather than one frozen
        instant."""
        self._as_of = as_of

    # -- internals ----------------------------------------------------- #
    def _window(self):
        return f"{self._as_of[:10]}T00:00:00+05:30", self._as_of

    def _rows(self, identity):
        start, end = self._window()
        try:
            return list(self._store.range(identity, self._resolution, start, end))
        except Exception:  # noqa: BLE001 -- unreadable store is unknown, never a fabricated print.
            return []

    def _rows_since(self, identity, lookback_days: int):
        """Rows over a trailing multi-day window ending at `as_of` --
        for series (candles) where history before today is the point."""
        end_day = datetime.fromisoformat(self._as_of[:10])
        start = (end_day - timedelta(days=lookback_days)).date().isoformat()
        try:
            return list(self._store.range(
                identity, self._resolution, f"{start}T00:00:00+05:30", self._as_of))
        except Exception:  # noqa: BLE001
            return []

    def _latest(self, identity):
        rows = self._rows(identity)
        return getattr(rows[-1], "payload", None) if rows else None

    # -- market data (the six the snapshot path needs) ------------------ #
    async def connect(self) -> None:
        return None

    async def get_spot(self, underlying: str) -> Optional[float]:
        return _num(self._latest(self._spot_identity))

    async def get_vix(self) -> Optional[dict]:
        rows = self._rows(self._vix_identity)
        if not rows:
            return None
        level = _num(getattr(rows[-1], "payload", None))
        if level is None:
            return None
        prev = _num(getattr(rows[0], "payload", None))
        return {"level": level, "prev_close": prev}

    async def get_futures_quote(self, underlying: str) -> Optional[dict]:
        payload = self._latest(self._futures_identity)
        ltp = _num(payload)
        if ltp is None:
            return None
        return {"symbol": self._futures_identity, "ltp": ltp,
                "volume": (payload or {}).get("volume"), "oi": (payload or {}).get("open_interest")}

    async def get_recent_candles(self, underlying: str, minutes: int, count: int) -> List[Candle]:
        """Real captured OHLC, most recent `count` bars at or before
        `as_of`. Rows without a usable close are skipped, never
        forward-filled."""
        # Trailing history must CROSS day boundaries. An earlier draft
        # windowed candles to the current day only, so a 09:20 as_of
        # yielded ~1 bar and the volatility engine could not form a
        # regime at all -- the session then refused to trade for lack of
        # evidence that actually existed, just outside the window.
        out: List[Candle] = []
        for row in self._rows_since(self._spot_identity, lookback_days=45):
            payload = getattr(row, "payload", None) or {}
            close = _num(payload)
            if close is None:
                continue
            identity = getattr(getattr(row, "observation", None), "identity", None)
            # Candle.timestamp is a real datetime -- downstream engines
            # call .isoformat() on it. The store holds ISO strings, so
            # parse rather than pass through.
            out.append(Candle(
                timestamp=_as_datetime(getattr(identity, "timestamp", None) or self._as_of),
                open=payload.get("open", close), high=payload.get("high", close),
                low=payload.get("low", close), close=close, volume=payload.get("volume") or 0,
            ))
        return out[-count:] if count else out

    async def get_option_chain(self, underlying: str, spot: float, strike_count: int = 5):
        """`[(strike, ce_oi, pe_oi), ...]` for the strikes nearest spot,
        assembled from the real captured option book at `as_of`."""
        start, end = self._window()
        try:
            rows = self._store.range_by_prefix(f"{underlying}|", self._resolution, start, end)
        except Exception:  # noqa: BLE001
            return []
        latest = {}
        for row in rows:
            identity = getattr(getattr(row, "observation", None), "identity", None)
            key = getattr(identity, "instrument", None) or getattr(row, "instrument_identity", None)
            if key:
                latest[key] = row
        by_strike = {}
        for key, row in latest.items():
            parts = key.split("|")
            if len(parts) != 4 or parts[3] not in ("CE", "PE"):
                continue
            try:
                strike = float(parts[2])
            except ValueError:
                continue
            payload = getattr(row, "payload", None) or {}
            oi = payload.get("open_interest")
            slot = by_strike.setdefault(strike, {"CE": None, "PE": None})
            slot[parts[3]] = oi
        if not by_strike:
            return []
        nearest = sorted(by_strike, key=lambda s: abs(s - (spot or 0)))[: max(1, strike_count) * 2]
        return [(s, by_strike[s]["CE"], by_strike[s]["PE"]) for s in sorted(nearest)]

    async def get_quote(self, contract) -> Optional[dict]:
        identity = "|".join([
            getattr(contract, "underlying", self._underlying), str(getattr(contract, "expiry", "")),
            str(int(getattr(contract, "strike", 0))),
            str(getattr(getattr(contract, "option_type", ""), "value", getattr(contract, "option_type", ""))),
        ])
        payload = self._latest(identity)
        if not payload:
            return None
        bid, ask = payload.get("bid") or None, payload.get("ask") or None
        spread = (ask - bid) if (bid and ask) else None
        return {"bid": bid, "ask": ask, "spread": spread}

    async def get_ltp(self, contract) -> Optional[float]:
        quote = await self.get_quote(contract)
        if quote and quote.get("bid") and quote.get("ask"):
            return (quote["bid"] + quote["ask"]) / 2.0
        identity = "|".join([
            getattr(contract, "underlying", self._underlying), str(getattr(contract, "expiry", "")),
            str(int(getattr(contract, "strike", 0))),
            str(getattr(getattr(contract, "option_type", ""), "value", getattr(contract, "option_type", ""))),
        ])
        return _num(self._latest(identity), ("ltp", "close", "last_price"))

    async def resolve_atm_contract(self, underlying, spot, direction, strike_interval, lot_size):
        raise ReplayBrokerExecutionError(
            "ReplayMarketBroker serves market data only -- contract resolution belongs to "
            "InstrumentMaster / the construction path."
        )

    # -- execution surface: structurally unavailable -------------------- #
    def _refuse(self, what: str):
        raise ReplayBrokerExecutionError(
            f"ReplayMarketBroker cannot {what}: it is a read-only market-data facade over captured "
            f"observations. Reaching this is a wiring bug -- execution belongs to PaperBroker."
        )

    async def place_order(self, request):
        self._refuse("place an order")

    async def get_order(self, client_order_id: str):
        self._refuse("look up an order")

    async def cancel_order(self, client_order_id: str):
        self._refuse("cancel an order")

    async def get_open_positions(self):
        self._refuse("report positions")
