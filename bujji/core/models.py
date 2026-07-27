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
        return self.type in (SignalType.ENTER_LONG_PREMIUM_SELL, SignalType.ENTER_STRADDLE)


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
    orb: Optional[OpeningRange]
    # Straddle legs (None for single-leg positions)
    ce_contract: Optional[OptionContract] = None
    pe_contract: Optional[OptionContract] = None
    # Excursion tracking (in rupees of premium P&L for the whole position).
    max_favourable_excursion: float = 0.0
    max_adverse_excursion: float = 0.0
    max_profit_seen: float = 0.0
    max_loss_seen: float = 0.0
    candles_held: int = 0
    thesis: Optional["TradeThesis"] = None
    # Capital Management Engine's entry-time sizing decision, kept on the
    # position purely for the permanent journal/audit trail (see
    # Orchestrator._journal_trade) -- never re-consulted for anything else.
    capital_decision: Optional[dict] = None
    # Decision Lineage (Sprint 2): same decision_id as this position's
    # originating TradeIntention/DecisionSnapshot/ExecutionPlan -- carried
    # through to the journal row at exit. Never re-consulted for anything
    # else, same discipline as capital_decision above.
    decision_id: str = ""

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


@dataclass(frozen=True)
class TradeIntention:
    """Strategy Layer's ONLY output (Production Pipeline Entry 11, Layer 3).

    Deliberately thin and broker-free: direction and thesis only, never a
    strike, quantity, order, or margin figure. This is a pure, lossless
    reframing of the Signal Engine's existing broker-free `Signal` output
    into the Production Pipeline's formal boundary object -- it introduces
    no new decision, no new field the strategy didn't already produce.
    """

    direction: Optional[Direction]
    strategy_type: str          # e.g. "PREMIUM_VWAP_STRADDLE" -- descriptive only.
    thesis: str                 # Narrative from the existing TradeThesis, if any.
    evidence_refs: dict         # Pointers to the evidence this was built from (spot, vwap, orb) -- not the evidence itself.
    as_of: datetime
    # Decision Lineage (Sprint 2): ties this intention to the decision_id
    # every downstream artifact (DecisionSnapshot, ExecutionPlan, Position,
    # TradeJournal row) will also carry. Defaulted for backward
    # compatibility with any existing construction that predates lineage.
    decision_id: str = ""


@dataclass(frozen=True)
class DecisionSnapshot:
    """Immutable record of decision state BEFORE the first broker order is
    sent (Production Pipeline Entry 11, item 4). NOT a journal entry --
    the journal records what happened; this records what was believed and
    planned at the moment of acting, for later replay/audit comparison.
    Captured, logged, and never read back into any live decision -- purely
    additive, cannot change trading behaviour by construction.
    """

    decision_id: str
    as_of: datetime
    strategy_version: str
    market_observations: dict       # candle OHLCV at decision time.
    intelligence_snapshot: dict     # MIC's run_intelligence() output at this instant.
    intention: TradeIntention
    planned_contracts: dict         # {"ce": symbol, "pe": symbol, "strike": int}.
    planned_structure: str          # e.g. "ATM_STRADDLE".
    broker_session: str             # broker.name -- which broker/mode executed this.
    replay_reference: str           # candle timestamp isoformat, the replay-session anchor.


@dataclass(frozen=True)
class ExecutionPlan:
    """Order Planning Layer's final output (Production Pipeline Entry 11,
    between Layer 6 and Layer 7). Represents exactly what will be executed
    -- and nothing that happened yet. Contains NO broker response, no
    fill price, no order status. The Broker Layer receives only this,
    never a TradeIntention.
    """

    execution_id: str
    decision_id: str          # Ties this plan back to its TradeIntention/DecisionSnapshot.
    strategy_type: str
    structure_type: str       # e.g. "ATM_STRADDLE".
    contracts: dict           # {"ce": OptionContract, "pe": OptionContract}.
    side_per_leg: dict        # {"ce": Side, "pe": Side}.
    quantities: dict          # {"ce": int, "pe": int}.
    execution_sequence: list  # Order legs are submitted in, e.g. ["ce", "pe"].
    idempotency_keys: dict    # {"ce": client_order_id, "pe": client_order_id}.
    broker_account: str       # broker.name -- which broker/mode will execute this.
    execution_constraints: dict = field(default_factory=dict)
    as_of: Optional[datetime] = None
