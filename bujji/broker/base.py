"""Broker-agnostic interface.

Every execution path in the system is expressed against this abstract base.
Swapping FYERS for another broker, a paper simulator, or a backtest feed
requires only a new subclass — no change to the Signal Engine, Trade Manager,
or Execution Engine.
"""
from __future__ import annotations

import abc
from datetime import datetime
from typing import Optional

from ..core.enums import Direction, OptionType
from ..core.models import Candle, OptionContract, OrderRequest, OrderResult


class Broker(abc.ABC):
    """Minimal surface every broker adapter must implement."""

    name: str = "base"

    @abc.abstractmethod
    async def connect(self) -> None:
        """Establish/verify the session. Idempotent.

        Implementations MUST raise :class:`~bujji.broker.errors.AuthenticationError`
        distinctly (never a generic ``Exception``) when the failure is due to
        an invalid/expired token or a revoked session (E1/E2) — this lets the
        Execution Engine short-circuit retries instead of wasting the backoff
        schedule on a call that can never succeed with the same credentials.
        """

    @abc.abstractmethod
    async def get_spot(self, underlying: str) -> float:
        """Return the latest spot price for the underlying."""

    @abc.abstractmethod
    async def get_recent_candles(
        self, underlying: str, minutes: int, count: int
    ) -> list[Candle]:
        """Return the most recent ``count`` completed candles."""

    @abc.abstractmethod
    async def resolve_atm_contract(
        self,
        underlying: str,
        spot: float,
        direction: Direction,
        strike_interval: int,
        lot_size: int,
    ) -> OptionContract:
        """Resolve the nearest ATM contract to sell for the given direction.

        Bullish sells the ATM Put; Bearish sells the ATM Call.
        """

    @abc.abstractmethod
    async def get_ltp(self, contract: OptionContract) -> float:
        """Latest traded price (premium) for a contract."""

    @abc.abstractmethod
    async def place_order(self, request: OrderRequest) -> OrderResult:
        """Submit an order. Must be safe to retry with the same client id."""

    @abc.abstractmethod
    async def get_order(self, client_order_id: str) -> OrderResult:
        """Poll the current status of a previously placed order."""

    @abc.abstractmethod
    async def cancel_order(self, client_order_id: str) -> OrderResult:
        ...

    @abc.abstractmethod
    async def get_open_positions(self) -> list[dict]:
        """Return open positions for reconciliation/recovery (C1).

        Each entry MUST be normalized to::

            {"symbol": str, "side": "BUY"|"SELL", "qty": int > 0, "avg_price": float}

        Flat legs (net zero) must be omitted. Recovery relies on this shape to
        detect and adopt/flatten live positions after a restart.
        """

    def live_tick_credentials(self) -> Optional[tuple[str, str]]:
        """(app_id, access_token) for a real FYERS WebSocket feed, or None.

        Optional capability, not part of the required trading interface —
        default is None (no live tick feed available). Only a broker with a
        genuine live data leg overrides this (FyersBroker directly;
        HybridPaperBroker delegates to its live leg). PaperBroker and any
        other broker without live data simply never offers tick monitoring —
        this is what "add the WebSocket only where it has a concrete runtime
        purpose" means at the broker layer: no fake/synthetic tick feed is
        ever fabricated for a broker that has no real one.
        """
        return None

    @staticmethod
    def atm_strike(spot: float, interval: int) -> int:
        """Nearest ATM strike to spot on the given interval grid."""
        return int(round(spot / interval) * interval)

    @staticmethod
    def option_type_for(direction: Direction) -> OptionType:
        # Bullish -> sell PUT; Bearish -> sell CALL.
        return OptionType.PE if direction is Direction.BULLISH else OptionType.CE
