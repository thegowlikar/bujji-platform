"""Charges Model -- BUJJI Options OS v3, Gate F.2 Part 3.

Isolated, pure, deterministic. Computes the standard components of
Indian equity-derivatives transaction cost from turnover -- brokerage,
STT, GST, stamp duty, SEBI charges, exchange transaction charges,
clearing charges and NSE IPFT. Every rate is a configuration value;
nothing is hardcoded inside `ChargesCalculator` itself, only in
`ChargesConfig`'s own default field values, which any caller can
override wholesale.

RATE-CARD VERIFIED 2026-08-19 against FYERS' own published charges
(fyers.in/charges-list), corroborated line-by-line against
zerodha.com/charges. The previous defaults were self-described as
"illustrative" and three of them were wrong:

  STT (sell, on premium)   0.10%     -> 0.15%        (33% too low)
  NSE transaction charges  0.053%    -> 0.0355299%   (49% too high)
  clearing charges         ABSENT    -> 0.009%       (no field existed)
  GST base                 brokerage + txn
                           -> brokerage + txn + clearing + SEBI + IPFT

The first two errors partly cancel; measured on a real short strangle
(1 lot, sold 120 / bought back 60) the model understated total cost by
Rs 5.47, or 4.4%. Small -- but every error pointed the same way, at
UNDERSTATING cost, and premium selling is high-trade-count by design,
so the bias compounds fastest exactly where this system trades most.

These are FYERS' retail rates for NSE equity/index options. They are
not universal: a different broker, segment or plan needs a different
ChargesConfig, which is why nothing here is hardcoded.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ChargesConfig:
    """FYERS retail rates for NSE equity/index options, verified against
    the published rate card on 2026-08-19 (see this module's docstring).
    Deliberately overridable per instrument/broker/regime by constructing
    a different config, never by editing this calculator's code."""

    brokerage_per_order: float = 20.0            # Flat Rs 20 per executed order (FYERS F&O).
    brokerage_percentage: float = 0.0              # Percentage-of-turnover alternative (0 = flat-fee-only).
    stt_sell_percentage: float = 0.0015              # STT 0.15% on SELL side, on PREMIUM.
    exchange_charges_percentage: float = 0.000355299   # NSE transaction charges 0.0355299% on premium.
    clearing_charges_percentage: float = 0.00009         # Clearing charges 0.009% on premium.
    gst_percentage: float = 0.18                           # GST 18% -- see calculate() for the base.
    sebi_charges_percentage: float = 0.0000010               # SEBI turnover fee, Rs 10/crore.
    ipft_percentage: float = 0.0000001                         # NSE IPFT, Rs 0.01/crore on premium.
    stamp_duty_percentage: float = 0.00003                       # Stamp duty 0.003%, BUY side only.


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
    # Added 2026-08-19 with defaults, so every existing construction site
    # and assertion keeps working unchanged.
    clearing_charges: float = 0.0
    ipft: float = 0.0


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
        clearing_charges = turnover * config.clearing_charges_percentage
        sebi_charges = turnover * config.sebi_charges_percentage
        ipft = turnover * config.ipft_percentage
        # GST base per the published card: brokerage + transaction charges +
        # clearing charges + SEBI turnover fee + IPFT. It is NOT levied on
        # STT or stamp duty, which are themselves taxes.
        gst = (brokerage + exchange_charges + clearing_charges
               + sebi_charges + ipft) * config.gst_percentage
        stamp_duty = turnover * config.stamp_duty_percentage if side == "BUY" else 0.0

        total = (brokerage + stt + exchange_charges + clearing_charges
                 + gst + sebi_charges + ipft + stamp_duty)

        return ChargesBreakdown(
            turnover=turnover, brokerage=brokerage, stt=stt, exchange_charges=exchange_charges,
            gst=gst, sebi_charges=sebi_charges, stamp_duty=stamp_duty, total=total,
            clearing_charges=clearing_charges, ipft=ipft,
        )
