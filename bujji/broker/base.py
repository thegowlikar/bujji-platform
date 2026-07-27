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

    async def get_option_candles(
        self, contract: "OptionContract", minutes: int, count: int
    ) -> list[Candle]:
        """Return the most recent ``count`` completed candles (OHLC +
        volume) for a specific OPTION contract -- distinct from
        get_recent_candles, which is the underlying INDEX's candles.

        This exists specifically to support a genuine volume-weighted
        Premium VWAP: get_ltp() only ever returns a single last-traded-price
        snapshot with no volume attached, which cannot support real
        volume-weighting. A broker that hasn't implemented this returns an
        empty list (default) -- callers must treat that as "volume data
        unavailable" and fall back to equal-weight, never fabricate volume.
        """
        return []

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

    async def get_funds(self) -> Optional[dict]:
        """Account funds/margin snapshot for the Capital Management Engine.

        Returns a dict with any subset of these keys (a missing key means
        that particular figure could not be verified — never fabricate a
        value under it): account_equity, available_funds, available_margin,
        cash_balance, collateral, used_margin, available_exposure,
        peak_margin. Returns None if funds cannot be obtained at all.

        Default: None (not implemented) — a broker that hasn't wired this up
        yet reports itself as unable to verify capital, which the Capital
        Management Engine correctly treats as a reason to refuse a trade,
        never as a reason to guess.
        """
        return None

    async def get_order_margin(self, ce_contract: OptionContract,
                               pe_contract: OptionContract) -> Optional[dict]:
        """Broker-quoted margin required for ONE lot of this exact CE+PE
        short straddle, for the Capital Management Engine.

        Returns {"margin_per_lot": float, "verified": bool, "source": str}
        or None if no figure is available. "verified": True must mean a
        real broker margin-calculator response backs the number — never set
        it True for an estimate.

        Default: None (not implemented).
        """

    async def get_quote(self, contract: OptionContract) -> Optional[dict]:
        """Real-time top-of-book bid/ask/spread for a specific option
        contract, for the Liquidity Brain
        (bujji/intelligence/liquidity_brain.py -- see
        docs/MARKET_INTELLIGENCE_CORE.md's Liquidity Brain section for the
        live verification this is based on).

        Returns {"bid": float, "ask": float, "spread": float} or None if
        unavailable. Default: None (not implemented) -- a broker without a
        real quote source simply has no live liquidity data; never a
        fabricated bid/ask.
        """
        return None

    async def get_option_chain(
        self, underlying: str, spot: float, strike_count: int = 5
    ) -> Optional[list[tuple[float, float, float]]]:
        """Real per-strike (strike, ce_oi, pe_oi) open interest around the
        current spot, for the Structure Brain
        (bujji/intelligence/structure_brain.py -- see
        docs/MARKET_INTELLIGENCE_CORE.md's Structure Brain section for the
        live verification this is based on).

        Returns a list of (strike, ce_oi, pe_oi) tuples or None if
        unavailable. Default: None (not implemented) -- never a fabricated
        chain.
        """
        return None

    async def get_vix(self) -> Optional[dict]:
        """Real India VIX level and prior close, for the Event Brain's
        VIX half (bujji/intelligence/event_brain.py -- see
        docs/MARKET_INTELLIGENCE_CORE.md's Event Brain section for the
        live verification this is based on).

        Returns {"level": float, "prev_close": float} or None if
        unavailable. Default: None (not implemented) -- never a fabricated
        VIX level.
        """
        return None

    @staticmethod
    def atm_strike(spot: float, interval: int) -> int:
        """Nearest ATM strike to spot on the given interval grid.

        Deliberately NOT ``round(spot / interval) * interval`` — Python's
        ``round()`` is round-half-to-even (banker's rounding), which resolves
        the two possible exact-midpoint spots on a 50-point grid
        *inconsistently*: spot=25225 rounds DOWN to 25200 (504 is even) while
        spot=25275 rounds UP to 25300 (506 is even), purely as an artifact of
        which candidate integer happens to be even. Both are equally valid
        nearest-strike answers at an exact midpoint, but the choice must be
        deterministic and the same at every midpoint, not accidental. This
        always rounds an exact midpoint UP to the higher strike.
        """
        import math
        return int(math.floor(spot / interval + 0.5) * interval)

    @staticmethod
    def option_type_for(direction: Direction) -> OptionType:
        # Bullish -> sell PUT; Bearish -> sell CALL.
        return OptionType.PE if direction is Direction.BULLISH else OptionType.CE
