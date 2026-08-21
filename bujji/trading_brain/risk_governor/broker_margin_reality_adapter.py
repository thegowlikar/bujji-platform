"""Broker Margin Reality Adapter — BUJJI Options OS v3, Numeric Risk
Governor Gate C.3 (READ-ONLY).

SAFETY BOUNDARY, ENFORCED THREE WAYS, NOT JUST DOCUMENTED:
1. Structural type hint: FyersMarginProvider is typed against
   ReadOnlyMarginBroker, a narrow typing.Protocol exposing ONLY
   get_funds() and get_order_margin() -- the two read-only methods
   already live-certified on Broker/FyersBroker
   (bujji/broker/base.py, bujji/broker/fyers.py). Protocol typing
   alone does not prevent a caller from passing an object with MORE
   methods, so this is backed by two further, actually-enforced
   guarantees:
2. AST-verified: this module's own source is checked (see
   tests/test_broker_margin_reality_adapter.py::
   test_source_never_references_mutating_broker_methods) to never
   reference place_order/cancel_order/modify_order/get_open_positions
   or any other mutating Broker method anywhere, by name, in the
   actual parsed syntax tree -- not just a substring scan.
3. Test-enforced: every test in this module's test suite uses a fake
   broker object that implements get_funds/get_order_margin and
   NOTHING ELSE -- no place_order method exists on it at all, so any
   accidental call would raise AttributeError immediately, never
   silently succeed.

THIS MODULE MAKES ZERO LIVE CALLS ITSELF, AND NEITHER DOES ITS TEST
SUITE. FyersMarginProvider wraps whatever ReadOnlyMarginBroker-shaped
object it is given. If that object happens to be a real, credentialed
FyersBroker instance, calling this adapter's methods WOULD make a
real, already-certified, read-only network call -- get_funds()/
get_order_margin() already exist and are already live-certified
elsewhere in this codebase (bujji/broker/fyers.py, both dated
2026-07-19, see their own docstrings for the full certification
evidence). But constructing and invoking that real, credentialed
instance is exclusively the operator's own action, on their own
schedule, never performed by this module, its tests, or any code path
this session added.

REUSE, NOT DUPLICATION: this module deliberately does NOT re-implement
any HTTP call, span_margin request building, or FYERS-specific
response parsing -- doing so would create a second, unaudited path to
the same real endpoint. FyersMarginProvider is a thin, read-only,
normalizing WRAPPER around the existing, already-certified
get_funds()/get_order_margin() methods, nothing more.

SCOPE LIMITATION, DOCUMENTED RATHER THAN FAKED: get_order_margin()'s
real, certified signature takes exactly one CE+PE pair (a single
straddle), not an arbitrary N-leg whole book -- that is the shape the
underlying, already-certified broker method actually supports. This
adapter therefore compares ONE representative CE/PE pair at a time,
matching this task's own worked example ("NIFTY Short Straddle"), NOT
arbitrary whole-book N-leg comparison. Extending broker-side
comparison to a genuine arbitrary whole book would require either a
new live-certification pass against a different, not-yet-verified
endpoint shape, or per-leg-pair decomposition -- both explicitly out
of scope here.

FAILURE SEMANTICS, INHERITED FROM THE UNDERLYING METHODS AS-IS, NOT
REINVENTED: get_funds() raises AuthenticationError on an auth failure
and returns None for other failures (confirmed by reading its source
directly). get_order_margin() returns None for EVERY failure case
including auth failures -- it does not distinguish them. This adapter
therefore can only ever report AUTH_FAILED when get_funds() itself
raises; a get_order_margin() failure (including a real auth failure at
that call) is reported as the more general UNAVAILABLE, since the
underlying method genuinely does not expose enough information to
distinguish the two cases -- this is an honest limitation inherited
from the certified method's own behavior, not something this adapter
could resolve without changing that already-certified code (explicitly
out of scope: "do not modify existing broker execution code")."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, Optional, Protocol

from bujji.broker.errors import AuthenticationError
from bujji.core.models import OptionContract

Clock = Callable[[], datetime]

BROKER_SNAPSHOT_SOURCE = "FYERS_READ_ONLY"
BROKER_SNAPSHOT_UNAVAILABLE = "UNAVAILABLE"
BROKER_SNAPSHOT_AUTH_FAILED = "AUTH_FAILED"


class ReadOnlyMarginBroker(Protocol):
    """Structural, read-only-only interface. Declares ONLY the two
    methods already live-certified as read-only on Broker/FyersBroker
    -- no place_order, no cancel_order, no position mutation of any
    kind is declared here, and this module's own code never
    references any method beyond these two (AST-verified, see this
    module's docstring)."""

    async def get_funds(self) -> Optional[dict]: ...

    async def get_order_margin(
        self, ce_contract: OptionContract, pe_contract: OptionContract,
    ) -> Optional[dict]: ...


@dataclass(frozen=True)
class BrokerMarginSnapshot:
    """`available` is True ONLY when a genuine, complete, usable
    broker figure was obtained -- never inferred, never defaulted.
    `required_margin=None` (with `available=False`) is how "broker
    unavailable" is represented; it is NEVER reported as 0.0, since a
    caller comparing against a silently-zeroed figure could mistake
    "we don't know" for "no margin required" -- never assume safety."""

    available_margin: Optional[float]
    used_margin: Optional[float]
    required_margin: Optional[float]     # for the specific CE+PE pair queried
    timestamp: datetime
    source: str                           # BROKER_SNAPSHOT_SOURCE | UNAVAILABLE | AUTH_FAILED
    available: bool
    raw_metadata: Optional[Dict[str, Any]] = None


class FyersMarginProvider:
    """Thin, read-only wrapper around an existing ReadOnlyMarginBroker
    -shaped object's already-certified get_funds()/get_order_margin()
    methods. Makes no HTTP call of its own -- see module docstring."""

    def __init__(self, broker: ReadOnlyMarginBroker) -> None:
        self._broker = broker

    async def get_broker_margin_snapshot(
        self, ce_contract: OptionContract, pe_contract: OptionContract, clock: Clock,
    ) -> BrokerMarginSnapshot:
        as_of = clock()

        try:
            funds = await self._broker.get_funds()
        except AuthenticationError:
            return self._unavailable(as_of, BROKER_SNAPSHOT_AUTH_FAILED)
        except Exception:  # noqa: BLE001 -- a failed query must never crash the caller, only fail closed
            return self._unavailable(as_of, BROKER_SNAPSHOT_UNAVAILABLE)

        if funds is None:
            return self._unavailable(as_of, BROKER_SNAPSHOT_UNAVAILABLE)

        try:
            order_margin = await self._broker.get_order_margin(ce_contract, pe_contract)
        except AuthenticationError:
            return self._unavailable(as_of, BROKER_SNAPSHOT_AUTH_FAILED)
        except Exception:  # noqa: BLE001 -- same fail-closed guarantee as above
            return self._unavailable(as_of, BROKER_SNAPSHOT_UNAVAILABLE)

        if order_margin is None:
            # Partial response: funds succeeded but the margin figure did
            # not -- reject the WHOLE snapshot rather than reporting a
            # half-complete one (Step 6: "Partial broker response ->
            # Expected: Reject comparison").
            return self._unavailable(as_of, BROKER_SNAPSHOT_UNAVAILABLE)

        available_margin = self._extract_float(funds, "available_margin")
        used_margin = self._extract_float(funds, "used_margin")
        required_margin = self._extract_float(order_margin, "margin_per_lot")

        if required_margin is None:
            return self._unavailable(as_of, BROKER_SNAPSHOT_UNAVAILABLE)

        return BrokerMarginSnapshot(
            available_margin=available_margin, used_margin=used_margin, required_margin=required_margin,
            timestamp=as_of, source=BROKER_SNAPSHOT_SOURCE, available=True,
            raw_metadata={"funds": funds, "order_margin": order_margin},
        )

    @staticmethod
    def _extract_float(data: dict, key: str) -> Optional[float]:
        value = data.get(key)
        if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return float(value)

    @staticmethod
    def _unavailable(as_of: datetime, source: str) -> BrokerMarginSnapshot:
        return BrokerMarginSnapshot(
            available_margin=None, used_margin=None, required_margin=None,
            timestamp=as_of, source=source, available=False, raw_metadata=None,
        )
