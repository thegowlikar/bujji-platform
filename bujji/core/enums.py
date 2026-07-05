"""Core enumerations shared across all modules.

These are pure value types with no dependencies on any other module, so they
can be imported freely by the Signal Engine, Trade Manager, and Execution
Engine without creating circular dependencies.
"""
from __future__ import annotations

from enum import Enum


class State(str, Enum):
    """Finite state machine states for the trading session.

    The bot moves strictly forward through these states within a single
    trading day. Once ``DONE_FOR_DAY`` is reached no further trading occurs.
    """

    WAITING = "WAITING"          # Before ORB completes / outside window.
    READY = "READY"              # ORB built, watching for a breakout.
    CONFIRMED = "CONFIRMED"      # Signal generated, order being placed.
    IN_POSITION = "IN_POSITION"  # Live position, reassessed every candle.
    EXITING = "EXITING"          # Exit decision made, order being placed.
    DONE_FOR_DAY = "DONE_FOR_DAY"  # Terminal state for the day.


class Direction(str, Enum):
    """Directional bias of the trade thesis."""

    BULLISH = "BULLISH"  # Sell ATM Put.
    BEARISH = "BEARISH"  # Sell ATM Call.


class OptionType(str, Enum):
    """Option right traded."""

    CE = "CE"  # Call.
    PE = "PE"  # Put.


class Side(str, Enum):
    """Order transaction side."""

    BUY = "BUY"
    SELL = "SELL"


class SignalType(str, Enum):
    """Output of the Signal Engine."""

    ENTER_LONG_PREMIUM_SELL = "ENTER_LONG_PREMIUM_SELL"
    NO_TRADE = "NO_TRADE"


class Decision(str, Enum):
    """Output of the Trade Manager reassessment."""

    HOLD = "HOLD"
    EXIT = "EXIT"


class CheckResult(str, Enum):
    """Result of an individual thesis-validation check."""

    PASS = "PASS"
    FAIL = "FAIL"


class OrderStatus(str, Enum):
    """Broker-agnostic order lifecycle status."""

    PENDING = "PENDING"
    FILLED = "FILLED"
    PARTIAL = "PARTIAL"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    UNKNOWN = "UNKNOWN"
