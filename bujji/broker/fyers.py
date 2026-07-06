"""FYERS broker adapter.

Maps the broker-agnostic :class:`Broker` interface onto the real FYERS API,
via the official ``fyers-apiv3`` Python SDK. Network calls are isolated
behind ``_call`` so the transport can be swapped without touching mapping
logic.

TRANSPORT: uses ``fyersModel.FyersModel`` (the official SDK), which is
synchronous — each call is run via ``asyncio.to_thread`` so it doesn't block
the event loop. This was verified end-to-end with a live, authenticated
session (see ``docs/FYERS_TRANSPORT_READINESS.md``): real profile, quote,
history, positions, orderbook, and funds calls all returned live data through
this exact SDK from this codebase's own environment. (An earlier revision of
this file assumed no direct network path existed and left ``_call`` as an
unwired stub — that assumption was wrong and has been corrected; MCP tools
were never the transport, only a verification aid used once.)

Every mapping method has been corrected against REAL response shapes and, for
place_order/cancel_order, the real SDK parameter names (which differ from
what an earlier revision assumed) — see
``docs/FYERS_TRANSPORT_READINESS.md`` for the full verification record and the
one still-unresolved limitation (ATM option contract resolution — see
``resolve_atm_contract``'s docstring, now solved by ``instrument_master.py``).
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Optional

from fyers_apiv3 import fyersModel

from ..core.clock import epoch_to_ist
from ..core.config import BrokerConfig
from ..core.enums import Direction, OrderStatus, Side
from ..core.models import Candle, OptionContract, OrderRequest, OrderResult
from .base import Broker
from .errors import AuthenticationError

# Best-effort FYERS error-code classification for auth/session failures
# (E1/E2). These codes are commonly documented for FYERS API v3 as token/
# authorization related, but MUST be verified against the live FYERS error-code
# reference before go-live — treat this set as a starting point, not gospel.
# The keyword fallback below is the more robust signal and does not depend on
# getting the exact code list right.
# NOTE: -300 was removed after live verification — it is FYERS's "Invalid
# symbol provided" code, NOT an auth failure. Misclassifying it here caused a
# real bug: a bad symbol in get_recent_candles raised AuthenticationError
# instead of surfacing as the (correct) plain error it is.
_FYERS_AUTH_ERROR_CODES: frozenset[int] = frozenset({-8, -15, -16, -17})
_AUTH_KEYWORDS = (
    "token", "auth", "unauthoriz", "unauthenticated", "session expired",
    "login again", "invalid access", "not logged in",
)

# Verified live (see docs/FYERS_TRANSPORT_READINESS.md): FYERS order status
# codes. NOT independently re-derivable from a live order in this pass
# (doing so would require placing a real order, which this codebase will not
# do) — carried over from the prior implementation and flagged here as
# UNVERIFIED. Cross-check against FYERS's official order-status documentation
# before relying on it in full-live mode.
_ORDER_STATUS_MAP = {
    2: OrderStatus.FILLED,
    1: OrderStatus.PENDING,
    5: OrderStatus.REJECTED,
    6: OrderStatus.CANCELLED,
}

# BUG FOUND DURING LIVE VERIFICATION: the index symbol is NOT simply
# "NSE:{underlying}-INDEX" — that produces "NSE:NIFTY-INDEX", which is not a
# real FYERS instrument. The verified-live canonical symbol is
# "NSE:NIFTY50-INDEX", confirmed directly against the real fyers-apiv3 SDK
# for BOTH quotes() and history() (a prior pass believed these needed two
# different forms — "NSE:NIFTY 50" for history — based on a verification
# session mediated through an MCP tool that silently normalizes that alias;
# calling the raw SDK directly proved "NSE:NIFTY 50" is REJECTED by history()
# with "Invalid symbol provided". That earlier conclusion is corrected here.)
# Only NIFTY is mapped/verified — anything else falls back to the old,
# unverified "-INDEX" construction and must not be trusted without the same
# live check.
_INDEX_SYMBOL = {"NIFTY": "NSE:NIFTY50-INDEX"}


def _index_symbol(underlying: str) -> str:
    return _INDEX_SYMBOL.get(underlying, f"NSE:{underlying}-INDEX")


class FyersBroker(Broker):
    name = "fyers"

    def __init__(self, config: BrokerConfig, logger: logging.Logger) -> None:
        self._cfg = config
        self._log = logger
        self._connected = False
        self._client: Optional[fyersModel.FyersModel] = None
        # C3 idempotency bridge: FYERS's order-lookup tools key on FYERS's own
        # order_id, not on an arbitrary client tag (see get_order's docstring
        # for why). This in-memory map lets get_order/cancel_order resolve our
        # client_order_id to the FYERS order_id once place_order has seen it.
        # It does NOT survive a process restart — recovery after a restart
        # relies on get_open_positions()/reconcile (C1), not this cache.
        self._cid_to_order_id: dict[str, str] = {}
        self._instruments = None  # Lazily built InstrumentMaster (Phase C).

    def live_tick_credentials(self) -> Optional[tuple]:
        if not self._cfg.app_id or not self._cfg.access_token:
            return None
        return (self._cfg.app_id, self._cfg.access_token)

    def _get_client(self) -> fyersModel.FyersModel:
        if self._client is None:
            self._client = fyersModel.FyersModel(
                client_id=self._cfg.app_id,
                is_async=False,
                token=self._cfg.access_token,
                log_path="logs",
            )
        return self._client

    # Dispatch table: action name -> (sdk_method_name, takes_data_arg).
    # Verified live against a real authenticated session — see
    # docs/FYERS_TRANSPORT_READINESS.md.
    _NO_ARG_ACTIONS = {
        "profile": "get_profile",
        "positions": "positions",
        "orders": "orderbook",
        "funds": "funds",
        "holdings": "holdings",
    }
    _DATA_ARG_ACTIONS = {
        "ltp": "quotes",
        "historical": "history",
        "place_order": "place_order",
        "cancel_order": "cancel_order",
    }

    async def _call(self, action: str, **params: Any) -> dict:
        """Single choke-point for all FYERS transport.

        Runs the synchronous ``fyers-apiv3`` SDK call in a thread so it
        doesn't block the event loop. Every response MUST be passed through
        :meth:`_raise_if_auth_error` before being used (every call site below
        already does this) — do not bypass it for a "quick" new call site.
        """
        client = self._get_client()
        if action in self._NO_ARG_ACTIONS:
            method = getattr(client, self._NO_ARG_ACTIONS[action])
            return await asyncio.to_thread(method)
        if action in self._DATA_ARG_ACTIONS:
            method = getattr(client, self._DATA_ARG_ACTIONS[action])
            return await asyncio.to_thread(method, params)
        raise ValueError(f"Unknown FYERS action: {action}")

    def _raise_if_auth_error(self, data: dict,
                             http_status: Optional[int] = None) -> None:
        """Classify a FYERS response and raise AuthenticationError if it
        signals an invalid/expired token or invalidated session (E1/E2).

        Never raises for anything else — this is purely a classifier, not a
        general error handler. A response that fails for other reasons (bad
        symbol, insufficient margin, etc.) passes through untouched for the
        normal mapping/handling logic to deal with.

        Verified live: every FYERS response shape observed (profile, ltp,
        historical, positions, orders) carries `s`/`code`/`message` at the
        top level, matching what this classifier inspects.
        """
        if http_status in (401, 403):
            raise AuthenticationError(
                f"FYERS auth failure: HTTP {http_status}: {data}"
            )
        code = data.get("code")
        if isinstance(code, int) and code in _FYERS_AUTH_ERROR_CODES:
            raise AuthenticationError(
                f"FYERS auth failure: code={code} message={data.get('message')}"
            )
        if str(data.get("s", "")).lower() == "error":
            message = str(data.get("message", "")).lower()
            if any(kw in message for kw in _AUTH_KEYWORDS):
                raise AuthenticationError(
                    f"FYERS auth failure (keyword match): {data.get('message')}"
                )

    async def connect(self) -> None:
        if self._connected:
            return
        # Fail fast and unambiguously when credentials are simply absent —
        # do not let this fall through to a transport-level error (which,
        # with `_call` unwired, would otherwise surface as a confusing
        # `NotImplementedError` regardless of whether credentials were ever
        # provided at all).
        if not self._cfg.app_id or not self._cfg.access_token:
            raise AuthenticationError(
                "FYERS credentials missing: FYERS_APP_ID and/or "
                "FYERS_ACCESS_TOKEN are not set. Set both environment "
                "variables and restart."
            )
        # Verified live shape: {"s": "ok", "code": 200, "message": "",
        # "data": {...profile fields...}}.
        data = await self._call("profile")  # Validates the access token.
        self._raise_if_auth_error(data)
        self._connected = True

    async def get_spot(self, underlying: str) -> float:
        symbol = _index_symbol(underlying)
        return await self._quote(symbol)

    async def _quote(self, symbol: str) -> float:
        # Verified live against the REAL fyers-apiv3 SDK (not the MCP tool's
        # own reshaped output, which an earlier pass mistakenly treated as
        # the raw shape): `quotes({"symbols": "A,B"})` -> a LIST under "d",
        # each item `{"n": symbol, "v": {"lp": price, ...}}`.
        data = await self._call("ltp", symbols=symbol)
        self._raise_if_auth_error(data)
        for row in data.get("d", []):
            if row.get("n") == symbol:
                return float(row["v"]["lp"])
        raise KeyError(f"symbol {symbol} not found in quotes response: {data}")

    async def get_recent_candles(
        self, underlying: str, minutes: int, count: int
    ) -> list[Candle]:
        # NOTE ON VWAP/VOLUME: the FYERS live quote returns volume=0 and atp=0
        # for the index, so there is no broker-provided VWAP to consume. The
        # HISTORICAL endpoint, however, returns genuine per-candle volume (the
        # 6th field). We therefore compute a true volume-weighted VWAP from
        # these candles. Do not swap this for the quote's volume/atp.
        #
        # The real SDK's history() has no "give me the last N candles" mode —
        # it takes an explicit date range. Request from a few calendar days
        # back (comfortably covers weekends/holidays for any `count` this
        # codebase actually uses — 1, for the live candle loop) and take the
        # tail. Verified live: rows are [epoch, open, high, low, close,
        # volume] (6 elements) under a top-level "candles" key.
        from datetime import timedelta
        from ..core.clock import now_ist
        today = now_ist().date()
        lookback_days = max(5, (count // 75) + 3)  # ~75 five-min bars/session.
        data = await self._call(
            "historical",
            symbol=_index_symbol(underlying),
            resolution=str(minutes),
            date_format="1",
            range_from=(today - timedelta(days=lookback_days)).isoformat(),
            range_to=today.isoformat(),
            cont_flag="1",
        )
        self._raise_if_auth_error(data)
        candles = [
            Candle(
                # D2: MUST be explicit IST — `datetime.fromtimestamp(row[0])`
                # would interpret the epoch using the host's local timezone,
                # silently wrong the moment the host isn't configured for IST.
                timestamp=epoch_to_ist(row[0]),
                open=row[1],
                high=row[2],
                low=row[3],
                close=row[4],
                volume=row[5] if len(row) > 5 else 0.0,  # Real index volume.
            )
            for row in data.get("candles", [])
        ]
        return candles[-count:] if count else candles

    async def resolve_atm_contract(
        self, underlying, spot, direction, strike_interval, lot_size
    ) -> OptionContract:
        """Resolve the nearest-expiry ATM option contract.

        Backed by :class:`InstrumentMaster` — FYERS's real, public NFO
        symbol-master CSV (downloaded, cached 24h, parsed locally). No
        hardcoded symbols. A prior pass could not verify this at all: the
        MCP-tool-mediated instrument search only covered the cash-market
        segment (no F&O), and manual symbol guesses failed. Downloading the
        real NFO file directly (verified live — see
        docs/FYERS_TRANSPORT_READINESS.md) resolved it properly.
        """
        opt = self.option_type_for(direction)
        return await self._instrument_master().resolve_atm(
            underlying, spot, opt, strike_interval, lot_size
        )

    def _instrument_master(self):
        if self._instruments is None:
            from .instrument_master import InstrumentMaster
            self._instruments = InstrumentMaster(Path("data/instrument_master"), self._log)
        return self._instruments

    async def get_ltp(self, contract: OptionContract) -> float:
        return await self._quote(contract.symbol)

    async def place_order(self, request: OrderRequest) -> OrderResult:
        # C3 IDEMPOTENCY REQUIREMENT: the ExecutionEngine guarantees at-most-once
        # placement per client_order_id ONLY IF this id is round-trippable —
        # it is sent as `orderTag` here and matched back in get_order via the
        # `_cid_to_order_id` cache / tag-scan fallback below.
        #
        # Verified against the real fyers-apiv3 SDK's place_order() signature
        # (introspected directly, not guessed): combined `symbol` string,
        # `side` as int (1=Buy, -1=Sell), `type` as int (2=Market, 1=Limit),
        # `productType` string ("INTRADAY", not "MIS" — the SDK's own name).
        # `orderTag`: NOT in the SDK's documented param list; passed anyway
        # since it's a documented FYERS v3 REST field the SDK passes through
        # verbatim. UNVERIFIED whether it's echoed back in orderbook() entries
        # — cannot confirm without placing a real order (deliberately not
        # done here); flagged in the readiness report.
        data = await self._call(
            "place_order",
            symbol=request.contract.symbol,
            side=1 if request.side is Side.BUY else -1,
            qty=request.quantity,
            type=2 if request.limit_price is None else 1,
            limitPrice=request.limit_price or 0,
            productType="INTRADAY",
            validity="DAY",
            orderTag=request.client_order_id,  # <-- idempotency key.
        )
        self._raise_if_auth_error(data)
        result = self._map_order(request.client_order_id, data)
        if result.broker_order_id:
            self._cid_to_order_id[request.client_order_id] = result.broker_order_id
        return result

    async def get_order(self, client_order_id: str) -> OrderResult:
        """Look up an order by OUR client_order_id.

        The real SDK has no separate order-history-by-id method (confirmed
        by introspection: `fyersModel.FyersModel` defines no such method) —
        only `orderbook()` (today's full order book, no filter). So: if we
        already know the FYERS order id for this client_order_id (cached from
        a prior successful `place_order` in this process), filter locally by
        id. Otherwise — the exact scenario C3 exists for, e.g. a crash/
        timeout right after placing — filter by `orderTag` instead.

        ⚠ UNVERIFIED: whether `orderbook()` entries actually expose the
        `orderTag` we set at placement (and under what response field name)
        could not be confirmed without placing a real order, which this
        codebase deliberately does not do. Flagged in the readiness report.
        """
        order_id = self._cid_to_order_id.get(client_order_id)
        data = await self._call("orders")  # orderbook() — today's orders.
        self._raise_if_auth_error(data)
        for order in data.get("orderBook", []):
            matched = (order.get("id") == order_id if order_id
                      else order.get("orderTag") == client_order_id)
            if matched:
                self._cid_to_order_id[client_order_id] = order.get("id", "")
                return self._map_order(client_order_id, order)
        return OrderResult(client_order_id, OrderStatus.UNKNOWN,
                           message="not_found_in_todays_order_book")

    async def cancel_order(self, client_order_id: str) -> OrderResult:
        # FYERS's cancel takes its own order `id`, not our client tag.
        order_id = self._cid_to_order_id.get(client_order_id)
        if not order_id:
            lookup = await self.get_order(client_order_id)
            order_id = self._cid_to_order_id.get(client_order_id)
            if not order_id:
                return lookup  # Nothing to cancel; report what we found.
        data = await self._call("cancel_order", id=order_id)
        self._raise_if_auth_error(data)
        return self._map_order(client_order_id, data)

    async def get_open_positions(self) -> list[dict]:
        # Normalize to the shape recovery/reconciliation expects (C1):
        #   {symbol, side: "BUY"|"SELL", qty: >0, avg_price}.
        # Verified live: top-level "netPositions" list + "overall" summary
        # (confirmed with an empty position book). Per-row field names
        # (netQty/netAvg) are carried over from the prior implementation and
        # are UNVERIFIED — no live position existed to inspect actual row
        # contents without placing a real order.
        data = await self._call("positions")
        self._raise_if_auth_error(data)
        normalized: list[dict] = []
        for p in data.get("netPositions", []):
            net = int(p.get("netQty", 0))
            if net == 0:
                continue  # Flat legs are not open positions.
            normalized.append({
                "symbol": p.get("symbol"),
                "side": Side.BUY.value if net > 0 else Side.SELL.value,
                "qty": abs(net),
                "avg_price": p.get("netAvg", p.get("avgPrice", 0.0)),
            })
        return normalized

    def _map_order(self, client_order_id: str, data: dict) -> OrderResult:
        status = _ORDER_STATUS_MAP.get(data.get("status"), OrderStatus.UNKNOWN)
        return OrderResult(
            client_order_id=client_order_id,
            status=status,
            broker_order_id=data.get("id"),
            filled_quantity=int(data.get("filledQty", 0)),
            average_price=data.get("tradedPrice"),
            message=data.get("message", ""),
            raw=data,
        )
