"""Market Perception domain models -- Shadow Campaign v2 Phase 1.

Pure, frozen dataclasses describing ONE complete market observation
(a "MarketSnapshot"). This module contains no IO, no broker calls, and
no decision logic -- it is a schema only. Building a MarketSnapshot is
market_data_adapter.py's job; everything downstream (Intelligence,
Trading Brain, Risk Governor, Virtual Portfolio, Decision Journal) is
out of scope for this phase and must never be imported here. No
regime/trend/signal/decision/confidence field exists anywhere on this
object or anything it references -- interpretation belongs to
Intelligence (Phase 2+), never this raw-collection layer.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

SNAPSHOT_VERSION = "1.0"

HEALTH_OK = "OK"
HEALTH_DEGRADED = "DEGRADED"
HEALTH_UNAVAILABLE = "UNAVAILABLE"
ALL_HEALTH_STATUSES = (HEALTH_OK, HEALTH_DEGRADED, HEALTH_UNAVAILABLE)


@dataclass(frozen=True)
class OptionChainConfig:
    """Configurable strike range around ATM -- never hardcoded by callers."""

    strike_range: int = 2000
    strike_step: int = 100

    def __post_init__(self) -> None:
        if self.strike_range <= 0:
            raise ValueError(f"strike_range must be positive, got {self.strike_range!r}")
        if self.strike_step <= 0:
            raise ValueError(f"strike_step must be positive, got {self.strike_step!r}")
        if self.strike_range % self.strike_step != 0:
            raise ValueError(
                f"strike_range ({self.strike_range!r}) must be an exact multiple of "
                f"strike_step ({self.strike_step!r})"
            )


@dataclass(frozen=True)
class OptionLeg:
    """One CE or PE leg at one strike. volume/iv/delta/gamma/theta/vega
    are honestly None this phase -- the current broker capability
    cannot supply volume reliably (see get_quote()'s own docstring),
    and IV/Greeks require calling volatility_brain/greeks_brain, which
    is Phase 2 (Intelligence) work, not this raw-collection layer.
    Never fabricated, never defaulted to 0."""

    symbol: str
    strike: float
    option_type: str  # "CE" or "PE"
    ltp: Optional[float]
    bid: Optional[float]
    ask: Optional[float]
    spread: Optional[float]
    volume: Optional[float]
    open_interest: Optional[float]
    iv: Optional[float]
    delta: Optional[float]
    gamma: Optional[float]
    theta: Optional[float]
    vega: Optional[float]

    def __post_init__(self) -> None:
        if not self.symbol:
            raise ValueError("symbol must not be empty")
        if self.option_type not in ("CE", "PE"):
            raise ValueError(f"option_type must be CE or PE, got {self.option_type!r}")


@dataclass(frozen=True)
class OptionChainSnapshot:
    underlying: str
    expiry: str
    atm_strike: float
    config: OptionChainConfig
    legs: Tuple[OptionLeg, ...]

    def leg(self, strike: float, option_type: str) -> Optional[OptionLeg]:
        for candidate in self.legs:
            if candidate.strike == strike and candidate.option_type == option_type:
                return candidate
        return None


@dataclass(frozen=True)
class FutureSnapshot:
    """basis/premium_discount are computed via the EXISTING
    bujji.futures_observation.engine.compute_basis() -- never
    reimplemented here. premium_discount is the same raw basis number,
    not a classification: compute_basis's own docstring reserves
    PREMIUM/DISCOUNT labeling for MSI's interpretive layer, out of
    scope for this raw-collection phase."""

    symbol: str
    ltp: Optional[float]
    volume: Optional[float]
    open_interest: Optional[float]
    basis: Optional[float]
    premium_discount: Optional[float]


@dataclass(frozen=True)
class SpotSnapshot:
    symbol: str
    ltp: Optional[float]


@dataclass(frozen=True)
class VixSnapshot:
    value: Optional[float]
    prev_close: Optional[float] = None


@dataclass(frozen=True)
class MarketSnapshot:
    """One complete, immutable market observation."""

    snapshot_version: str
    timestamp: str
    source: str
    latency_ms: float
    health_status: str
    missing_fields: Tuple[str, ...]

    spot: SpotSnapshot
    vix: VixSnapshot
    futures: Optional[FutureSnapshot]
    option_chain: Optional[OptionChainSnapshot]

    def __post_init__(self) -> None:
        if not self.timestamp:
            raise ValueError("timestamp must not be empty")
        if not self.source:
            raise ValueError("source must not be empty")
        if self.health_status not in ALL_HEALTH_STATUSES:
            raise ValueError(f"invalid health_status: {self.health_status!r}")
