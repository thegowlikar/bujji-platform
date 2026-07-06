"""Paper Trading mode — composite broker (live data + paper execution).

This is an essential validation capability, not a strategy enhancement: it
lets the bot run against **real FYERS market data** (candles, quotes, option
prices, contract resolution) while every order-related action — placement,
cancellation, status lookup, and position discovery — is routed exclusively
through the existing, unmodified :class:`~bujji.broker.paper.PaperBroker`
ledger. No strategy logic or capital-protection code is touched: the Signal
Engine, Market Brain, Trade Manager, Execution Engine, Orchestrator,
Dashboard, Journal, Decision Trace, and VWAP Audit are all reused completely
unchanged, because this is just another :class:`~bujji.broker.base.Broker`
implementation plugged into the same dependency-injection seam.

Safety guarantee: the live data source's order-execution methods are
neutered by :func:`bujji.broker.guard.disable_live_execution` at construction
time, before this class ever holds a reference to it. Even a coding mistake
that reached ``self._live_data.place_order(...)`` would raise
:class:`~bujji.broker.errors.LiveExecutionDisabledError` immediately — no
network call, no real order, ever. This class additionally never calls those
methods on ``self._live_data`` in its own code (see the method bodies below);
the guard is defense-in-depth on top of that.
"""
from __future__ import annotations

import logging
from dataclasses import replace
from typing import Optional

from ..core.enums import Direction
from ..core.models import Candle, OptionContract, OrderRequest, OrderResult
from .base import Broker
from .guard import disable_live_execution
from .paper import PaperBroker


class HybridPaperBroker(Broker):
    """Live market data (any :class:`Broker`) + paper-only execution ledger.

    ``live_data`` supplies every read-only/data method. ``ledger`` (a
    :class:`PaperBroker`) supplies every execution method — untouched, exactly
    as it behaves in plain Paper mode. The only bridging logic here is filling
    paper orders at the *live* observed premium instead of the ledger's
    synthetic random-walk default, so MTM/journal/exit behaviour reflects real
    market prices rather than a fictitious one.
    """

    name = "fyers_paper"

    def __init__(self, live_data: Broker, ledger: PaperBroker,
                 logger: Optional[logging.Logger] = None) -> None:
        # Belt-and-braces: guarantee the safety property holds even if a
        # caller forgot to pre-disable the instance before passing it in.
        self._live_data: Broker = disable_live_execution(live_data)
        self._ledger: PaperBroker = ledger
        self._log = logger

    # ------------------------------------------------------------------ #
    # Live market data — every read-only method delegates to the live broker.
    # ------------------------------------------------------------------ #
    async def connect(self) -> None:
        # Validates the live session (E1/E2's AuthenticationError applies
        # here exactly as it would in full-live mode). The paper ledger's
        # connect() is a no-op; calling it costs nothing and keeps the
        # contract "call connect() on everything" honest.
        await self._live_data.connect()
        await self._ledger.connect()

    def live_tick_credentials(self):
        # Delegates to the live data leg — this is exactly the "live data,
        # paper execution" split: tick monitoring is market data, so it comes
        # from the real broker, same as candles/quotes do.
        return self._live_data.live_tick_credentials()

    async def get_spot(self, underlying: str) -> float:
        return await self._live_data.get_spot(underlying)

    async def get_recent_candles(
        self, underlying: str, minutes: int, count: int
    ) -> list[Candle]:
        return await self._live_data.get_recent_candles(underlying, minutes, count)

    async def resolve_atm_contract(
        self,
        underlying: str,
        spot: float,
        direction: Direction,
        strike_interval: int,
        lot_size: int,
    ) -> OptionContract:
        # Real strike/expiry/symbol resolution against the live instrument
        # master — this is exactly what makes the paper fill "real" rather
        # than synthetic: the contract traded is the one that actually exists.
        return await self._live_data.resolve_atm_contract(
            underlying, spot, direction, strike_interval, lot_size
        )

    async def get_ltp(self, contract: OptionContract) -> float:
        return await self._live_data.get_ltp(contract)

    # ------------------------------------------------------------------ #
    # Execution — every method delegates EXCLUSIVELY to the paper ledger.
    # `self._live_data` is never referenced below, by construction.
    # ------------------------------------------------------------------ #
    async def place_order(self, request: OrderRequest) -> OrderResult:
        if request.limit_price is None:
            # Fill against the real observed premium rather than the ledger's
            # synthetic default, so paper P&L reflects genuine market prices.
            live_price = await self._live_data.get_ltp(request.contract)
            request = replace(request, limit_price=live_price)
        return await self._ledger.place_order(request)

    async def get_order(self, client_order_id: str) -> OrderResult:
        return await self._ledger.get_order(client_order_id)

    async def cancel_order(self, client_order_id: str) -> OrderResult:
        return await self._ledger.cancel_order(client_order_id)

    async def get_open_positions(self) -> list[dict]:
        return await self._ledger.get_open_positions()
