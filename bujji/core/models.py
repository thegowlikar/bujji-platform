"""Immutable domain models passed between modules.

These dataclasses form the *contract* between the three modules. The Signal
Engine emits :class:`Signal`, the Trade Manager consumes market data and emits
:class:`TradeDecision`, and the Execution Engine turns :class:`OrderRequest`
into :class:`OrderResult`. Keeping these types free of behaviour keeps the
module boundaries clean.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:  # Avoid circular imports; used only for annotations.
    from .decision_trace import DecisionTrace
    from .thesis import TradeThesis

from .enums import (
    CheckResult,
    Decision,
    Direction,
    OptionType,
    OrderStatus,
    Side,
    SignalType,
)


@dataclass(frozen=True)
class Candle:
    """A single OHLCV bar for the underlying spot."""

    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    @property
    def body(self) -> float:
        """Absolute size of the candle body."""
        return abs(self.close - self.open)

    @property
    def range(self) -> float:
        """Total high-low range of the candle."""
        return self.high - self.low

    @property
    def body_ratio(self) -> float:
        """Body as a fraction of the total range (0.0 when range is zero)."""
        if self.range <= 0:
            return 0.0
        return self.body / self.range

    @property
    def is_bullish(self) -> bool:
        return self.close > self.open

    @property
    def is_bearish(self) -> bool:
        return self.close < self.open


@dataclass(frozen=True)
class OpeningRange:
    """The opening range built during the ORB window."""

    high: float
    low: float
    start: datetime
    end: datetime


@dataclass(frozen=True)
class Signal:
    """Output of the Signal Engine — never carries broker details."""

    type: SignalType
    timestamp: datetime
    direction: Optional[Direction] = None
    spot: Optional[float] = None
    vwap: Optional[float] = None
    orb: Optional[OpeningRange] = None
    reason: str = ""
    thesis: Optional["TradeThesis"] = None
    trace: Optional["DecisionTrace"] = None

    @property
    def is_trade(self) -> bool:
        return self.type is SignalType.ENTER_LONG_PREMIUM_SELL


@dataclass(frozen=True)
class OptionContract:
    """A resolved tradable option contract."""

    symbol: str          # Broker-agnostic symbol string.
    underlying: str
    strike: int
    option_type: OptionType
    expiry: str          # ISO date or broker expiry token.
    lot_size: int


@dataclass(frozen=True)
class OrderRequest:
    """Instruction handed to the Execution Engine.

    Trade logic lives entirely outside this object; it is a pure directive.
    """

    contract: OptionContract
    side: Side
    quantity: int          # Total quantity (lots * lot_size).
    client_order_id: str    # Idempotency key for retries/reconciliation.
    limit_price: Optional[float] = None  # None => market order.
    tag: str = ""


@dataclass(frozen=True)
class OrderResult:
    """Result returned by the Execution Engine after an order attempt."""

    client_order_id: str
    status: OrderStatus
    broker_order_id: Optional[str] = None
    filled_quantity: int = 0
    average_price: Optional[float] = None
    message: str = ""
    raw: dict = field(default_factory=dict)

    @property
    def is_filled(self) -> bool:
        return self.status is OrderStatus.FILLED


@dataclass
class Position:
    """A live position owned by the Trade Manager."""

    contract: OptionContract
    direction: Direction
    entry_side: Side
    quantity: int
    entry_price: float
    entry_spot: float
    entry_time: datetime
    orb: OpeningRange
    # Excursion tracking (in rupees of premium P&L for the whole position).
    max_favourable_excursion: float = 0.0
    max_adverse_excursion: float = 0.0
    max_profit_seen: float = 0.0
    max_loss_seen: float = 0.0
    candles_held: int = 0
    thesis: Optional["TradeThesis"] = None

    def mtm(self, current_premium: float) -> float:
        """Mark-to-market P&L in rupees.

        We are always the seller (short premium), so profit accrues as the
        premium *falls* below the entry premium.
        """
        return (self.entry_price - current_premium) * self.quantity

    def update_excursion(self, current_premium: float) -> None:
        pnl = self.mtm(current_premium)
        self.max_profit_seen = max(self.max_profit_seen, pnl)
        self.max_loss_seen = min(self.max_loss_seen, pnl)
        self.max_favourable_excursion = self.max_profit_seen
        self.max_adverse_excursion = self.max_loss_seen


@dataclass(frozen=True)
class CheckOutcome:
    """A single named thesis-validation check result."""

    name: str
    result: CheckResult
    detail: str = ""

    @property
    def passed(self) -> bool:
        return self.result is CheckResult.PASS


@dataclass(frozen=True)
class TradeDecision:
    """Output of the Trade Manager reassessment cycle."""

    decision: Decision
    timestamp: datetime
    checks: tuple[CheckOutcome, ...] = ()
    reason: str = ""
    trace: Optional["DecisionTrace"] = None

    @property
    def should_exit(self) -> bool:
        return self.decision is Decision.EXIT
