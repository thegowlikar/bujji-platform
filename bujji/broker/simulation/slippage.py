"""Slippage Model -- BUJJI Options OS v3, Gate F.2 Part 2.

Isolated, pure, deterministic. No slippage logic lives inside
PaperBroker itself -- PaperBroker only ever calls
`SlippageCalculator.compute()` and applies the returned delta.

Slippage is always ADVERSE to the trader (never favorable) -- a BUY
fills at or above the reference price, a SELL fills at or below it.
This is the one deliberate modeling choice this module makes, and it
matches every real execution's actual bias (a market order pays the
spread/impact, it does not receive one).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class SlippageMode(str, Enum):
    ZERO = "ZERO"
    FIXED_TICK = "FIXED_TICK"
    PERCENTAGE = "PERCENTAGE"
    VOLATILITY_ADJUSTED = "VOLATILITY_ADJUSTED"


@dataclass(frozen=True)
class SlippageConfig:
    mode: SlippageMode = SlippageMode.ZERO
    tick_size: float = 0.05           # FIXED_TICK: price grid increment.
    fixed_ticks: int = 1                # FIXED_TICK: number of ticks of adverse slippage.
    percentage: float = 0.0              # PERCENTAGE: fraction of reference_price (e.g. 0.001 = 0.1%).
    volatility_multiplier: float = 0.0    # VOLATILITY_ADJUSTED: fraction of `volatility` applied as slippage.


class IllegalSlippageInputError(Exception):
    """Raised on a structurally impossible input -- never silently
    coerced (e.g. VOLATILITY_ADJUSTED requested with no volatility
    figure supplied)."""


class SlippageCalculator:
    """Stateless. `compute()` never mutates anything and never uses
    randomness -- identical inputs always produce an identical
    result."""

    @staticmethod
    def compute(reference_price: float, side: str, config: SlippageConfig,
                volatility: Optional[float] = None) -> float:
        """Returns the ADVERSE PRICE DELTA (always >= 0) to apply --
        the caller adds this for a BUY, subtracts it for a SELL. Never
        returns a signed value itself, so callers can never
        accidentally apply it in the favorable direction by getting a
        sign wrong."""
        if reference_price < 0:
            raise IllegalSlippageInputError(f"reference_price must be non-negative, got {reference_price!r}")
        if side not in ("BUY", "SELL"):
            raise IllegalSlippageInputError(f"side must be 'BUY' or 'SELL', got {side!r}")

        if config.mode == SlippageMode.ZERO:
            return 0.0

        if config.mode == SlippageMode.FIXED_TICK:
            if config.tick_size < 0 or config.fixed_ticks < 0:
                raise IllegalSlippageInputError("tick_size and fixed_ticks must be non-negative")
            return config.tick_size * config.fixed_ticks

        if config.mode == SlippageMode.PERCENTAGE:
            if config.percentage < 0:
                raise IllegalSlippageInputError("percentage must be non-negative")
            return reference_price * config.percentage

        if config.mode == SlippageMode.VOLATILITY_ADJUSTED:
            if volatility is None:
                raise IllegalSlippageInputError(
                    "VOLATILITY_ADJUSTED mode requires an explicit volatility figure -- never fabricated"
                )
            if volatility < 0 or config.volatility_multiplier < 0:
                raise IllegalSlippageInputError("volatility and volatility_multiplier must be non-negative")
            return volatility * config.volatility_multiplier

        raise IllegalSlippageInputError(f"unrecognized SlippageMode {config.mode!r}")

    @staticmethod
    def apply(reference_price: float, side: str, config: SlippageConfig,
              volatility: Optional[float] = None) -> float:
        """Convenience: returns the final, adverse-adjusted fill price
        directly (reference_price +/- the computed delta)."""
        delta = SlippageCalculator.compute(reference_price, side, config, volatility)
        return reference_price + delta if side == "BUY" else reference_price - delta
