"""Fill Simulator -- BUJJI Options OS v3, Gate F.2 Part 1.

MarketSnapshot -> FillSimulator -> SimulatedFill. Pure composition of
SlippageCalculator (Part 2) and the caller-supplied latency/rejection/
partial-fill configuration -- this module invents no market behavior
of its own; every number it produces traces back to an explicit config
value or the caller-supplied MarketSnapshot.

Deterministic and injectable: the ONLY source of randomness accepted
is an `random.Random` instance the CALLER constructs and passes in
(PaperBroker passes its own existing `self._rng`, never a second,
independently-seeded source) -- with `random_range` latency disabled
(the default), this class is fully deterministic with no rng calls at
all.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from bujji.core.enums import OrderStatus

from .market_snapshot import MarketSnapshot
from .order_lifecycle import ExecutionStage
from .slippage import SlippageCalculator, SlippageConfig


class LatencyMode(str, Enum):
    ZERO = "ZERO"
    FIXED = "FIXED"
    RANDOM_RANGE = "RANDOM_RANGE"


@dataclass(frozen=True)
class LatencyConfig:
    mode: LatencyMode = LatencyMode.ZERO
    fixed_ms: float = 0.0
    min_ms: float = 0.0
    max_ms: float = 0.0


@dataclass(frozen=True)
class RejectionConfig:
    reject_on_insufficient_liquidity: bool = False
    min_liquidity_score: float = 0.0
    force_reject: bool = False
    force_reject_reason: str = "SIMULATED_REJECTION"


@dataclass(frozen=True)
class PartialFillConfig:
    fill_ratio: Optional[float] = None       # None = always attempt a full fill.


def _reference_price_for_side(snapshot, side: str) -> float:
    """The price this order actually crosses to: the ASK for a BUY, the
    BID for a SELL. Returns `last_price` when the relevant side is not
    quoted -- an absent quote is never approximated from the other side,
    which would invent a spread that was not observed.

    A crossed or inverted book (bid > ask) is left exactly as supplied:
    this function reports what the caller observed, and inventing a
    "corrected" mid here would hide a real data-quality problem behind a
    plausible number.
    """
    if side == "BUY" and snapshot.ask is not None:
        return snapshot.ask
    if side == "SELL" and snapshot.bid is not None:
        return snapshot.bid
    return snapshot.last_price


def _depth_impact_multiplier(fill_quantity: int, available_depth) -> float:
    """How much worse the fill gets for consuming more than top-of-book.

    Returns 1.0 (no change) when depth is unknown or the order fits
    inside it -- an unknown book is never penalised on a guess. Beyond
    that, impact scales linearly with how many times over the available
    depth the order is: 2x the depth pays 2x the slippage.

    MODELLED, NOT MEASURED -- deliberately the simplest defensible shape,
    in the same disclosed spirit as execution_profiles' own
    CALIBRATION_PENDING figures. Its purpose is to stop a strategy that
    is unfillable at real size from scoring identically to one that is
    genuinely liquid; it is not a claim about true market impact.
    """
    if not available_depth or available_depth <= 0 or fill_quantity <= available_depth:
        return 1.0
    return fill_quantity / float(available_depth)


class IllegalFillSimulationInputError(Exception):
    """Raised on a structurally impossible input -- never silently
    coerced."""


@dataclass(frozen=True)
class SimulatedFill:
    fill_price: Optional[float]           # None only when status == REJECTED
    fill_quantity: int
    status: str                            # bujji.core.enums.OrderStatus value
    stage: ExecutionStage
    latency_ms: float
    slippage: float
    rejection_reason: Optional[str]


class FillSimulator:
    """Stateless except for the caller-supplied `rng` passed into each
    call -- never holds its own random state."""

    @staticmethod
    def simulate(
        requested_qty: int, side: str, snapshot: MarketSnapshot,
        slippage_config: SlippageConfig, latency_config: LatencyConfig,
        rejection_config: RejectionConfig, partial_fill_config: PartialFillConfig,
        rng: random.Random,
    ) -> SimulatedFill:
        if requested_qty <= 0:
            raise IllegalFillSimulationInputError(f"requested_qty must be positive, got {requested_qty!r}")

        latency_ms = FillSimulator._compute_latency(latency_config, rng)

        if rejection_config.force_reject:
            return SimulatedFill(
                fill_price=None, fill_quantity=0, status=OrderStatus.REJECTED.value,
                stage=ExecutionStage.REJECTED, latency_ms=latency_ms, slippage=0.0,
                rejection_reason=rejection_config.force_reject_reason,
            )

        if (
            rejection_config.reject_on_insufficient_liquidity
            and snapshot.liquidity_score is not None
            and snapshot.liquidity_score < rejection_config.min_liquidity_score
        ):
            return SimulatedFill(
                fill_price=None, fill_quantity=0, status=OrderStatus.REJECTED.value,
                stage=ExecutionStage.REJECTED, latency_ms=latency_ms, slippage=0.0,
                rejection_reason="INSUFFICIENT_LIQUIDITY",
            )

        fill_quantity = requested_qty
        if partial_fill_config.fill_ratio is not None:
            if not (0.0 <= partial_fill_config.fill_ratio <= 1.0):
                raise IllegalFillSimulationInputError(
                    f"fill_ratio must be within [0.0, 1.0], got {partial_fill_config.fill_ratio!r}"
                )
            fill_quantity = math.floor(requested_qty * partial_fill_config.fill_ratio)

        if fill_quantity <= 0:
            return SimulatedFill(
                fill_price=None, fill_quantity=0, status=OrderStatus.REJECTED.value,
                stage=ExecutionStage.REJECTED, latency_ms=latency_ms, slippage=0.0,
                rejection_reason="ZERO_FILL_QUANTITY",
            )

        # Fill against the side of the book the order actually crosses:
        # a BUY lifts the ASK, a SELL hits the BID. Previously both sides
        # filled at the same `last_price`, so a round trip cost nothing --
        # the spread, which is the dominant real execution cost on the
        # illiquid OTM wings of an IRON_CONDOR/IRON_FLY, was invisible.
        # `MarketSnapshot.bid`/`.ask` already existed and were read by
        # nothing at all.
        #
        # Falls back to `last_price` when a real quote is absent, so any
        # caller that supplies no bid/ask keeps byte-identical behaviour.
        # Slippage is then applied ON TOP of the correct side -- the two
        # model different costs (crossing the spread vs. adverse impact)
        # and must not substitute for one another.
        reference_price = _reference_price_for_side(snapshot, side)
        base_delta = SlippageCalculator.compute(reference_price, side, slippage_config, snapshot.volatility)
        # Size matters. `available_depth` already existed on MarketSnapshot
        # and was read by nothing, so a 1-lot and a 500-lot order on the
        # same thin OTM wing filled at the identical price. For a campaign
        # whose purpose is collecting strategy evidence that is actively
        # misleading: a structure that is unfillable at real size looks
        # exactly as good as one that is not.
        slippage_delta = base_delta * _depth_impact_multiplier(fill_quantity, snapshot.available_depth)
        # Same formula SlippageCalculator.apply() uses -- applied here
        # rather than via apply() so the depth-scaled delta is the one
        # that reaches the price (apply() would recompute the base).
        fill_price = (reference_price + slippage_delta if side == "BUY"
                      else reference_price - slippage_delta)

        if fill_quantity >= requested_qty:
            status, stage = OrderStatus.FILLED.value, ExecutionStage.FILLED
        else:
            status, stage = OrderStatus.PARTIAL.value, ExecutionStage.PARTIALLY_FILLED

        return SimulatedFill(
            fill_price=fill_price, fill_quantity=fill_quantity, status=status, stage=stage,
            latency_ms=latency_ms, slippage=slippage_delta, rejection_reason=None,
        )

    @staticmethod
    def _compute_latency(config: LatencyConfig, rng: random.Random) -> float:
        if config.mode == LatencyMode.ZERO:
            return 0.0
        if config.mode == LatencyMode.FIXED:
            if config.fixed_ms < 0:
                raise IllegalFillSimulationInputError("fixed_ms must be non-negative")
            return config.fixed_ms
        if config.mode == LatencyMode.RANDOM_RANGE:
            if config.min_ms < 0 or config.max_ms < config.min_ms:
                raise IllegalFillSimulationInputError("min_ms/max_ms must satisfy 0 <= min_ms <= max_ms")
            return rng.uniform(config.min_ms, config.max_ms)
        raise IllegalFillSimulationInputError(f"unrecognized LatencyMode {config.mode!r}")
