"""FYERS broker adapter (skeleton).

Maps the broker-agnostic :class:`Broker` interface onto the FYERS MCP / REST
surface. Network calls are isolated behind ``_call`` so the transport (MCP
tools, official ``fyers_apiv3`` SDK, or raw HTTP) can be swapped without
touching mapping logic. Symbol construction follows the FYERS convention, e.g.
``NSE:NIFTY25JAN22000CE`` — adjust ``_build_option_symbol`` to match the live
expiry-token format returned by the instruments master.

This adapter is intentionally a thin, well-documented shell: fill in ``_call``
with the concrete FYERS MCP invocations at deployment time.
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


class FyersBroker(Broker):
    name = "fyers"

    def __init__(self, config: BrokerConfig, logger: logging.Logger) -> None:
        self._cfg = config
        self._log = logger
        self._connected = False

    async def _call(self, action: str, **params: Any) -> dict:
        """Single choke-point for all FYERS transport.

        Replace the body with the concrete FYERS MCP tool call (e.g.
        ``mcp__fyers__fyers_place_order``) or SDK invocation. Keeping every
        network access here makes retries, logging, and mocking trivial.

        Whatever transport is wired in, its response MUST be passed through
        :meth:`_raise_if_auth_error` before being used (every call site below
        already does this) — do not bypass it for a "quick" new call site.
        """
        raise NotImplementedError(
            f"FYERS transport not wired: action={action} params={params}"
        )

    def _raise_if_auth_error(self, data: dict,
                             http_status: Optional[int] = None) -> None:
        """Classify a FYERS response and raise AuthenticationError if it
        signals an invalid/expired token or invalidated session (E1/E2).

        Never raises for anything else — this is purely a classifier, not a
        general error handler. A response that fails for other reasons (bad
        symbol, insufficient margin, etc.) passes through untouched for the
        normal mapping/handling logic to deal with.
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
        data = await self._call("profile")  # Validates the access token.
        self._raise_if_auth_error(data)
        self._connected = True

    async def get_spot(self, underlying: str) -> float:
        data = await self._call("ltp", symbol=f"NSE:{underlying}-INDEX")
        self._raise_if_auth_error(data)
        return float(data["ltp"])

    async def get_recent_candles(
        self, underlying: str, minutes: int, count: int
    ) -> list[Candle]:
        # NOTE ON VWAP/VOLUME: the FYERS live quote returns volume=0 and atp=0
        # for the index, so there is no broker-provided VWAP to consume. The
        # HISTORICAL endpoint, however, returns genuine per-candle volume (the
        # 6th field). We therefore compute a true volume-weighted VWAP from
        # these candles. Do not swap this for the quote's volume/atp.
        data = await self._call(
            "historical",
            symbol=f"NSE:{underlying}-INDEX",
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
        strike = self.atm_strike(spot, strike_interval)
        opt = self.option_type_for(direction)
        expiry = await self._nearest_weekly_expiry(underlying)
        symbol = self._build_option_symbol(underlying, expiry, strike, opt.value)
        return OptionContract(symbol, underlying, strike, opt, expiry, lot_size)

    async def _nearest_weekly_expiry(self, underlying: str) -> str:
        data = await self._call("instruments", underlying=underlying)
        self._raise_if_auth_error(data)
        # Expected to return sorted expiry tokens; take the nearest.
        return data["expiries"][0]

    def _build_option_symbol(
        self, underlying: str, expiry: str, strike: int, opt: str
    ) -> str:
        # e.g. NSE:NIFTY25JAN22000CE — align with the instruments master.
        return f"NSE:{underlying}{expiry}{strike}{opt}"

    async def get_ltp(self, contract: OptionContract) -> float:
        data = await self._call("ltp", symbol=contract.symbol)
        self._raise_if_auth_error(data)
        return float(data["ltp"])

    async def place_order(self, request: OrderRequest) -> OrderResult:
        # C3 IDEMPOTENCY REQUIREMENT: the ExecutionEngine guarantees at-most-once
        # placement per client_order_id ONLY IF this id is round-trippable — it
        # must be sent as the FYERS `orderTag` here AND be queryable in
        # `get_order` below. Do not drop it. Without it, a place/verify cycle
        # cannot tell whether a timed-out order actually landed.
        data = await self._call(
            "place_order",
            symbol=request.contract.symbol,
            side=1 if request.side is Side.BUY else -1,
            qty=request.quantity,
            order_type=2 if request.limit_price is None else 1,
            limit_price=request.limit_price or 0,
            orderTag=request.client_order_id,  # <-- idempotency key.
        )
        self._raise_if_auth_error(data)
        return self._map_order(request.client_order_id, data)

    async def get_order(self, client_order_id: str) -> OrderResult:
        # Must resolve by the SAME orderTag used at placement so the engine can
        # detect an order that landed despite a lost/failed place response.
        data = await self._call("order_history", order_tag=client_order_id)
        self._raise_if_auth_error(data)
        return self._map_order(client_order_id, data)

    async def cancel_order(self, client_order_id: str) -> OrderResult:
        data = await self._call("cancel_order", client_order_id=client_order_id)
        self._raise_if_auth_error(data)
        return self._map_order(client_order_id, data)

    async def get_open_positions(self) -> list[dict]:
        # Normalize to the shape recovery/reconciliation expects (C1):
        #   {symbol, side: "BUY"|"SELL", qty: >0, avg_price}.
        # VERIFY the raw field names against a live positions payload before go-
        # live; FYERS uses a signed netQty for net positions.
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
        status_map = {
            2: OrderStatus.FILLED,
            1: OrderStatus.PENDING,
            5: OrderStatus.REJECTED,
            6: OrderStatus.CANCELLED,
        }
        status = status_map.get(data.get("status"), OrderStatus.UNKNOWN)
        return OrderResult(
            client_order_id=client_order_id,
            status=status,
            broker_order_id=data.get("id"),
            filled_quantity=int(data.get("filledQty", 0)),
            average_price=data.get("tradedPrice"),
            message=data.get("message", ""),
            raw=data,
        )
