"""FYERS broker adapter.

Maps the broker-agnostic :class:`Broker` interface onto the real FYERS API
surface. Network calls are isolated behind ``_call`` so the transport can be
swapped without touching mapping logic.

IMPORTANT — transport reality check (read before wiring ``_call``):
MCP tools (the ``mcp__fyers__*`` names referenced in earlier revisions of this
file) are only invokable by an LLM agent through the Claude Code tool-calling
protocol. They are NOT a Python SDK or importable module — a standalone
``python -m bujji.app`` process has no mechanism to call them. "Wire ``_call``
to MCP" is therefore not something a deployed instance of this bot can ever
do. The correct transport for a standalone deployment is FYERS's official
REST API (or the ``fyers-apiv3`` Python SDK), authenticated with
``app_id``/``access_token`` exactly as already threaded through this class.

``_call`` remains a placeholder for that REST/SDK integration. Every mapping
method below it, however, has been corrected against RESPONSE SHAPES verified
live (via an authenticated FYERS session reached through MCP tooling, used
here purely as a verification oracle) — see
``docs/FYERS_TRANSPORT_READINESS.md`` for the full verification record,
per-method status, and unresolved limitations (in particular: ATM option
contract resolution could NOT be verified against live data — see that
method's docstring below).
"""
from __future__ import annotations

import logging
from typing import Any, Optional

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
_FYERS_AUTH_ERROR_CODES: frozenset[int] = frozenset({-8, -15, -16, -17, -300})
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
# real FYERS instrument. Two DIFFERENT verified-live forms exist depending on
# the endpoint, and they were NOT assumed interchangeable — each is exactly
# what was independently confirmed working against that specific endpoint
# before the live session's access token expired mid-verification (see
# docs/FYERS_TRANSPORT_READINESS.md):
#   - LTP endpoint:        "NSE:NIFTY50-INDEX"  (confirmed via fyers_ltp)
#   - Historical endpoint: "NSE:NIFTY 50"       (confirmed via fyers_historical)
# Only NIFTY is mapped/verified for either — anything else falls back to the
# old, unverified "-INDEX" construction and must not be trusted without the
# same live check.
_LTP_INDEX_SYMBOL = {"NIFTY": "NSE:NIFTY50-INDEX"}
_HISTORICAL_INDEX_SYMBOL = {"NIFTY": "NSE:NIFTY 50"}


def _ltp_index_symbol(underlying: str) -> str:
    return _LTP_INDEX_SYMBOL.get(underlying, f"NSE:{underlying}-INDEX")


def _historical_index_symbol(underlying: str) -> str:
    return _HISTORICAL_INDEX_SYMBOL.get(underlying, f"NSE:{underlying}-INDEX")


class FyersBroker(Broker):
    name = "fyers"

    def __init__(self, config: BrokerConfig, logger: logging.Logger) -> None:
        self._cfg = config
        self._log = logger
        self._connected = False
        # C3 idempotency bridge: FYERS's order-lookup tools key on FYERS's own
        # order_id, not on an arbitrary client tag (see get_order's docstring
        # for why). This in-memory map lets get_order/cancel_order resolve our
        # client_order_id to the FYERS order_id once place_order has seen it.
        # It does NOT survive a process restart — recovery after a restart
        # relies on get_open_positions()/reconcile (C1), not this cache.
        self._cid_to_order_id: dict[str, str] = {}

    async def _call(self, action: str, **params: Any) -> dict:
        """Single choke-point for all FYERS transport.

        Still unwired: this requires a real network client (FYERS REST API
        over HTTPS, or the official ``fyers-apiv3`` SDK) that this sandboxed
        session cannot construct and verify end-to-end (no direct network
        egress to FYERS's REST endpoints is available here — only the
        MCP-mediated tool calls used to verify the mappings below). Wiring
        this is a discrete follow-up task; see
        docs/FYERS_TRANSPORT_READINESS.md for exactly what is and isn't
        blocked on it.

        Whatever transport is wired in, its response MUST be passed through
        :meth:`_raise_if_auth_error` before being used (every call site below
        already does this) — do not bypass it for a "quick" new call site.
        """
        raise NotImplementedError(
            f"FYERS transport not wired: action={action} params={params}. "
            f"See docs/FYERS_TRANSPORT_READINESS.md."
        )

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
        symbol = _ltp_index_symbol(underlying)
        # Verified live shape: LTP takes a LIST of instruments and returns a
        # dict KEYED BY SYMBOL, e.g. {"NSE:NIFTY50-INDEX": {"last_price": ..}}
        # — NOT the flat {"ltp": ...} this method previously assumed.
        data = await self._call("ltp", instruments=[symbol])
        self._raise_if_auth_error(data)
        return float(data[symbol]["last_price"])

    async def get_recent_candles(
        self, underlying: str, minutes: int, count: int
    ) -> list[Candle]:
        # NOTE ON VWAP/VOLUME: the FYERS live quote returns volume=0 and atp=0
        # for the index, so there is no broker-provided VWAP to consume. The
        # HISTORICAL endpoint, however, returns genuine per-candle volume (the
        # 6th field). We therefore compute a true volume-weighted VWAP from
        # these candles. Do not swap this for the quote's volume/atp.
        #
        # Verified live: rows are exactly [epoch, open, high, low, close,
        # volume] (6 elements) under a top-level "candles" key, alongside
        # "s"/"code"/"message" — matches the parsing below unchanged.
        data = await self._call(
            "historical",
            symbol=_historical_index_symbol(underlying),
            resolution=str(minutes),
            count=count,
        )
        self._raise_if_auth_error(data)
        return [
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

    async def resolve_atm_contract(
        self, underlying, spot, direction, strike_interval, lot_size
    ) -> OptionContract:
        """Resolve the nearest ATM option contract.

        ⚠ UNVERIFIED AGAINST LIVE DATA — this is the one capability this
        pass could NOT confirm works, and no workaround was invented in its
        place (per explicit instruction). Two things were tried and both
        failed:

        1. The available FYERS instrument-search tool only covers the NSE/
           BSE/MCX *cash-market* symbol master (equities/indices/ETFs) — a
           live search for "NIFTY" returned zero option contracts. It has no
           F&O/derivatives segment at all.
        2. A direct LTP query against a manually-constructed guess at the
           FYERS weekly-option symbol format (two plausible variants tried)
           returned `last_price: null` for both — i.e., neither guess
           resolved to a real, quotable instrument.

        The symbol construction below is therefore an UNCHANGED, UNVERIFIED
        best-effort guess (`_build_option_symbol`) carried over from the
        prior implementation. Before any live or `fyers_paper` run that
        depends on entering a position, this MUST be replaced with a
        correctly-verified format — e.g. by downloading FYERS's published
        NFO symbol-master CSV directly (outside the tool surface available
        in this session) and confirming the exact weekly-expiry token
        convention against it.
        """
        strike = self.atm_strike(spot, strike_interval)
        opt = self.option_type_for(direction)
        expiry = await self._nearest_weekly_expiry(underlying)
        symbol = self._build_option_symbol(underlying, expiry, strike, opt.value)
        return OptionContract(symbol, underlying, strike, opt, expiry, lot_size)

    async def _nearest_weekly_expiry(self, underlying: str) -> str:
        # ⚠ Same limitation as resolve_atm_contract: no verified FYERS tool
        # returns option expiry tokens for an underlying. This action name
        # has no confirmed real-world mapping; left as-is, unwired, rather
        # than invented.
        data = await self._call("instruments", underlying=underlying)
        self._raise_if_auth_error(data)
        return data["expiries"][0]

    def _build_option_symbol(
        self, underlying: str, expiry: str, strike: int, opt: str
    ) -> str:
        # e.g. NSE:NIFTY25JAN22000CE — UNVERIFIED, see resolve_atm_contract.
        return f"NSE:{underlying}{expiry}{strike}{opt}"

    async def get_ltp(self, contract: OptionContract) -> float:
        # Same verified shape correction as get_spot: list in, symbol-keyed
        # dict out.
        data = await self._call("ltp", instruments=[contract.symbol])
        self._raise_if_auth_error(data)
        return float(data[contract.symbol]["last_price"])

    async def place_order(self, request: OrderRequest) -> OrderResult:
        # C3 IDEMPOTENCY REQUIREMENT: the ExecutionEngine guarantees at-most-once
        # placement per client_order_id ONLY IF this id is round-trippable —
        # it is sent as the order `tag` here and matched back in get_order via
        # the `_cid_to_order_id` cache / tag-scan fallback below.
        #
        # Verified live: the real place-order tool takes `tradingsymbol` and
        # `exchange` as SEPARATE fields (not a combined "NSE:SYMBOL" string),
        # `transaction_type` as "BUY"/"SELL" (not a numeric side code), and
        # `order_type` as "MARKET"/"LIMIT" (not a numeric code). A `product`
        # field (CNC/MIS) is required and has no prior equivalent in this
        # codebase — MIS (intraday) is used, matching this strategy's
        # same-day-square-off design; this must be reviewed before go-live.
        exchange, tradingsymbol = request.contract.symbol.split(":", 1)
        data = await self._call(
            "place_order",
            tradingsymbol=tradingsymbol,
            exchange=exchange,
            transaction_type=request.side.value,  # "BUY" | "SELL" — matches Side enum.
            quantity=request.quantity,
            order_type="MARKET" if request.limit_price is None else "LIMIT",
            price=request.limit_price or 0,
            product="MIS",  # Intraday — see docstring note above.
            tag=request.client_order_id,  # <-- idempotency key.
        )
        self._raise_if_auth_error(data)
        result = self._map_order(request.client_order_id, data)
        if result.broker_order_id:
            self._cid_to_order_id[request.client_order_id] = result.broker_order_id
        return result

    async def get_order(self, client_order_id: str) -> OrderResult:
        """Look up an order by OUR client_order_id.

        FYERS has no tag-based order lookup tool — only `order_history`
        (by FYERS's own order_id) and `orders` (all of today's orders, no
        filter). So: if we already know the FYERS order_id for this
        client_order_id (cached from a prior successful `place_order` in
        this process), look it up directly. Otherwise — the exact scenario
        C3 exists for, e.g. a crash/timeout right after placing — fetch
        today's full order book and find the entry whose `tag` matches ours.

        ⚠ UNVERIFIED: whether `orders`' returned entries actually expose the
        tag we set at placement (and under what field name) could not be
        confirmed without placing a real order, which this codebase
        deliberately does not do. Flagged in the readiness report.
        """
        order_id = self._cid_to_order_id.get(client_order_id)
        if order_id:
            data = await self._call("order_history", order_id=order_id)
            self._raise_if_auth_error(data)
            return self._map_order(client_order_id, data)

        data = await self._call("orders")  # All of today's orders.
        self._raise_if_auth_error(data)
        for order in data.get("orderBook", []):
            if order.get("tag") == client_order_id:
                self._cid_to_order_id[client_order_id] = order.get("id", "")
                return self._map_order(client_order_id, order)
        return OrderResult(client_order_id, OrderStatus.UNKNOWN,
                           message="not_found_in_todays_order_book")

    async def cancel_order(self, client_order_id: str) -> OrderResult:
        # FYERS's cancel tool takes ITS OWN order_id, not our client tag.
        order_id = self._cid_to_order_id.get(client_order_id)
        if not order_id:
            # We don't know the FYERS order id yet — resolve it first via
            # the same tag-scan get_order() falls back to.
            lookup = await self.get_order(client_order_id)
            order_id = self._cid_to_order_id.get(client_order_id)
            if not order_id:
                return lookup  # Nothing to cancel; report what we found.
        data = await self._call("cancel_order", order_id=order_id)
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
