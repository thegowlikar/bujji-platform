"""Charges Model -- BUJJI Options OS v3, Gate F.2 Part 3.

Isolated, pure, deterministic. Computes the standard components of
Indian equity-derivatives transaction cost from turnover -- brokerage,
STT, GST, stamp duty, SEBI charges, exchange charges. Every rate is a
configuration value with a documented, disclosed default (illustrative
NSE F&O options norms, not this broker's own invention) -- nothing is
hardcoded inside `ChargesCalculator` itself, only in `ChargesConfig`'s
own default field values, which any caller can override wholesale.
No broker-specific assumption is baked into the calculator's logic.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ChargesConfig:
    """All rates are illustrative, disclosed defaults -- not fetched
    from any live source, and deliberately overridable per instrument/
    broker/regime by constructing a different config, never by editing
    this calculator's code."""

    brokerage_per_order: float = 20.0          # Flat fee per executed order (common discount-broker model).
    brokerage_percentage: float = 0.0            # Alternative percentage-of-turnover brokerage (0 = flat-fee-only mode).
    stt_sell_percentage: float = 0.001             # Securities Transaction Tax -- options, on SELL side notional, at exercise-adjusted rate (illustrative).
    exchange_charges_percentage: float = 0.00053     # NSE transaction charges (illustrative F&O options rate).
    gst_percentage: float = 0.18                      # GST on (brokerage + exchange charges).
    sebi_charges_percentage: float = 0.0000010          # SEBI turnover fee (illustrative).
    stamp_duty_percentage: float = 0.00003                # Stamp duty, BUY side only (illustrative).


@dataclass(frozen=True)
class ChargesBreakdown:
    turnover: float
    brokerage: float
    stt: float
    exchange_charges: float
    gst: float
    sebi_charges: float
    stamp_duty: float
    total: float


class IllegalChargesInputError(Exception):
    """Raised on a structurally impossible input -- never silently
    coerced."""


class ChargesCalculator:
    """Stateless. `calculate()` never mutates anything and uses no
    randomness -- identical inputs always produce an identical
    result."""

    @staticmethod
    def calculate(turnover: float, side: str, config: ChargesConfig) -> ChargesBreakdown:
        if turnover < 0:
            raise IllegalChargesInputError(f"turnover must be non-negative, got {turnover!r}")
        if side not in ("BUY", "SELL"):
            raise IllegalChargesInputError(f"side must be 'BUY' or 'SELL', got {side!r}")

        brokerage = config.brokerage_per_order + (turnover * config.brokerage_percentage)
        stt = turnover * config.stt_sell_percentage if side == "SELL" else 0.0
        exchange_charges = turnover * config.exchange_charges_percentage
        gst = (brokerage + exchange_charges) * config.gst_percentage
        sebi_charges = turnover * config.sebi_charges_percentage
        stamp_duty = turnover * config.stamp_duty_percentage if side == "BUY" else 0.0

        total = brokerage + stt + exchange_charges + gst + sebi_charges + stamp_duty

        return ChargesBreakdown(
            turnover=turnover, brokerage=brokerage, stt=stt, exchange_charges=exchange_charges,
            gst=gst, sebi_charges=sebi_charges, stamp_duty=stamp_duty, total=total,
        )
