"""What the broker says is held — three answers, never two.

THE RULE THIS ENCODES: **a failed read is not a flat account.**

Every position consumer in this system needs the same fact and used to get it
in a slightly different shape: some filtered the broker's answer through a
local table of symbols this process happened to register, some collapsed an
exception into an empty list, and one asked a hardcoded PaperBroker whose read
cannot fail. Those differences are how two parts of a trading system come to
disagree about what is held.

THREE STATES, AND THE THIRD IS THE POINT:

    CONFIRMED_FLAT   the broker was asked, answered, and holds nothing
    CONFIRMED_OPEN   the broker was asked, answered, and holds these legs
    UNKNOWN          the broker could not be asked, or its answer could not
                     be understood

UNKNOWN IS NEVER DERIVED FROM AN EMPTY LIST. An empty `netPositions` from a
healthy response is CONFIRMED_FLAT. UNKNOWN comes only from a read that failed,
a status that was not ok, or a payload whose shape this adapter no longer
recognises. Collapsing those into "flat" is the single most dangerous
transformation available here: it reads as safe and closes the session.

THE LEGS TRAVEL WITH THE ANSWER. `CONFIRMED_OPEN` carries them because the EOD
path needs per-leg quantities, and a boundary that sent callers back to the raw
broker for detail would recreate the second authority it exists to remove.

SCHEMA VERIFICATION TRAVELS TOO. `schema_verified` is part of the RESULT, not a
comment in an adapter, because a consumer that must remember to check a module
constant will eventually forget. For FYERS it is False and must stay False:
the response's top-level shape was confirmed live against an empty book, but
the per-row field names were never seen with a real position in the account,
and seeing one requires placing a real order.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

STATE_CONFIRMED_FLAT = "CONFIRMED_FLAT"
STATE_CONFIRMED_OPEN = "CONFIRMED_OPEN"
STATE_UNKNOWN = "UNKNOWN"

ALL_STATES = (STATE_CONFIRMED_FLAT, STATE_CONFIRMED_OPEN, STATE_UNKNOWN)


class BrokerTruthUnknownError(Exception):
    """Raised when a caller demanded a known answer and there was not one."""


@dataclass(frozen=True)
class OpenLeg:
    """One leg the broker reports as held. `quantity` is always > 0.

    `raw` is the row exactly as the broker gave it, carried so that callers
    needing broker-native fields -- valuation wants entry price and timestamp,
    not just symbol and quantity -- can have them WITHOUT a second read. Two
    reads is the hazard this avoids: a position closing between them lets the
    two halves of a comparison disagree and manufactures a false divergence.

    This boundary's own decisions never look at `raw`. It is a pass-through,
    not a second opinion.
    """

    symbol: str
    quantity: int
    side: Optional[str] = None
    average_price: Optional[float] = None
    raw: Optional[Dict[str, Any]] = None

    def as_dict(self) -> Dict[str, Any]:
        return {"symbol": self.symbol, "quantity": self.quantity,
                "side": self.side, "average_price": self.average_price}


@dataclass(frozen=True)
class BrokerTruth:
    state: str
    legs: Tuple[OpenLeg, ...]
    detail: str
    source: str
    schema_verified: bool

    def __post_init__(self) -> None:
        if self.state not in ALL_STATES:
            raise ValueError(f"unknown broker-truth state {self.state!r}")
        if self.state == STATE_CONFIRMED_OPEN and not self.legs:
            raise ValueError(
                "CONFIRMED_OPEN with no legs is not a state this boundary can "
                "produce -- either the broker reported holdings, or it did not")
        if self.state != STATE_CONFIRMED_OPEN and self.legs:
            raise ValueError(
                f"{self.state} carrying legs is contradictory")

    # -- the three questions, asked so no caller re-derives them ---------- #
    @property
    def is_flat(self) -> bool:
        return self.state == STATE_CONFIRMED_FLAT

    @property
    def is_open(self) -> bool:
        return self.state == STATE_CONFIRMED_OPEN

    @property
    def is_unknown(self) -> bool:
        return self.state == STATE_UNKNOWN

    @property
    def symbols(self) -> Tuple[str, ...]:
        return tuple(leg.symbol for leg in self.legs)

    def quantity_for(self, symbol: str) -> Optional[int]:
        """Per-leg quantity, or None if this leg is not held.

        None is NOT zero: a caller reducing a position must not treat "I do not
        know about this leg" as "there is nothing to reduce"."""
        for leg in self.legs:
            if leg.symbol == symbol:
                return leg.quantity
        return None

    def require_known(self, what: str) -> "BrokerTruth":
        """Return self, or raise if the answer was UNKNOWN.

        For callers that genuinely cannot proceed without an answer. Most
        should NOT use this -- they should branch on `is_unknown` and refuse
        in their own vocabulary, because an exception crossing a session
        boundary reads as a crash rather than as a safety decision.
        """
        if self.is_unknown:
            raise BrokerTruthUnknownError(
                f"{what}: the broker's position book could not be established "
                f"({self.detail}). UNKNOWN is not FLAT.")
        return self

    def as_dict(self) -> Dict[str, Any]:
        return {
            "state": self.state, "detail": self.detail, "source": self.source,
            "schema_verified": self.schema_verified,
            "legs": [leg.as_dict() for leg in self.legs],
            "leg_count": len(self.legs),
        }


def flat(detail: str, source: str, schema_verified: bool) -> BrokerTruth:
    return BrokerTruth(STATE_CONFIRMED_FLAT, (), detail, source, schema_verified)


def open_with(legs, detail: str, source: str, schema_verified: bool) -> BrokerTruth:
    return BrokerTruth(STATE_CONFIRMED_OPEN, tuple(legs), detail, source, schema_verified)


def unknown(detail: str, source: str, schema_verified: bool = False) -> BrokerTruth:
    """`schema_verified` defaults False here deliberately: an answer we could
    not read tells us nothing about whether we would have understood it."""
    return BrokerTruth(STATE_UNKNOWN, (), detail, source, schema_verified)
