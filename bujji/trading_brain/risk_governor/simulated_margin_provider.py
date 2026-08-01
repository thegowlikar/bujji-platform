"""Deterministic Simulated Margin Provider — BUJJI Options OS v3,
Numeric Risk Governor Gate C.1.

PURPOSE, AND THE ONE THING THIS MODULE MUST NEVER BE MISTAKEN FOR:
this is a PIPELINE VALIDATION ENGINE, not a broker replica and not a
certification substitute. `SimulatedMarginProvider` is capable of
reporting `margin_verified=True` on its own MarginSnapshot output --
the ONLY reason that is safe is that this class has zero relationship
to any real broker, makes zero network calls, and its
`margin_source_label` ("SIMULATED_WHOLE_BOOK") is structurally
distinct from both `WHOLE_BOOK_UNCERTIFIED` and `WHOLE_BOOK_CERTIFIED`
(whole_book_margin_provider.py) -- a caller can always tell a simulated
figure apart from a real (or even a real-but-uncertified) one by
inspecting that label. This class must NEVER be wired into
msi_entry_bridge.py, production_runtime, or any other real construction
path -- it exists only for tests and standalone pipeline-shape
validation, exactly the "test fixture" carve-out capital_check.py's own
module docstring already describes ("every real call here that
supplies margin_verified=True is necessarily a test fixture, never a
live figure").

MODEL, AND WHY IT IS DELIBERATELY THIS SIMPLE: real SPAN/exposure
margin calculation requires strike-level netting (recognizing that a
short call is "covered" specifically by a long call at a nearby strike
in the SAME underlying/expiry) -- information this module deliberately
does NOT attempt to parse out of `MarginLegRequest.symbol`, since doing
so would mean guessing at a broker-specific symbol format, exactly the
kind of unverified assumption this whole Governor has avoided
everywhere else. Instead, this model reasons only over the fields
`MarginLegRequest` actually carries structurally (`qty`, `side`,
`limit_price`) via a single explainable netting rule:

    short_notional = sum(qty * limit_price) over all SELL legs
    long_notional  = sum(qty * limit_price) over all BUY legs
    covered_short  = min(short_notional, long_notional)
    naked_short    = short_notional - covered_short

    required_margin = SHORT_MARGIN_RATE   * naked_short
                     + HEDGED_MARGIN_RATE * covered_short
                     + LONG_MARGIN_RATE   * long_notional

Long premium in the book is treated as available to "cover" short
notional up to its own value, never more (covered_short is capped by
min(), so long premium can reduce but never eliminate or invert a
short's margin contribution) -- naked short exposure is charged at the
highest rate, a covered/hedged short at a much lower rate, and pure
long premium at the lowest rate (a long option's loss is bounded by
the premium paid, matching Gate B's own LONG_OPTION_PREMIUM_PAID
formula's reasoning). This does NOT distinguish which specific long
leg hedges which specific short leg, or verify they share an
underlying/strike relationship that would make a real broker recognize
the hedge -- it is a book-wide notional netting heuristic, sufficient
to prove the PIPELINE behaves correctly in the expected qualitative
direction (spreads cost less than naked shorts, larger size costs
proportionally more, hedged books cost less than unhedged ones), not
to predict a real margin figure.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, List, Optional

from bujji.trading_brain.risk_governor.whole_book_margin_provider import (
    MarginLegRequest,
    MarginSnapshot,
    WholeBookMarginQuoteResult,
)

Clock = Callable[[], datetime]

SIMULATED_MARGIN_SOURCE = "SIMULATED_WHOLE_BOOK"
SIMULATED_VALIDATION_FAILED_SOURCE = "SIMULATED_VALIDATION_FAILED"

# Illustrative, not calibrated to any real broker's SPAN/exposure tables.
SHORT_MARGIN_RATE = 15.0     # naked (uncovered) short notional
HEDGED_MARGIN_RATE = 3.0     # short notional covered by offsetting long premium
LONG_MARGIN_RATE = 1.0       # long premium -- loss is already bounded by what was paid


class SimulatedMarginProvider:
    """Deterministic, pure, no network access, no randomness -- the
    same List[MarginLegRequest] always produces the same MarginSnapshot.
    Implements the same duck-typed `get_portfolio_margin(legs, clock)`
    shape as UncertifiedWholeBookMarginProvider/
    CertifiedWholeBookMarginProvider, but does NOT inherit from either
    (it has no HttpCaller and makes no request/response round trip --
    inheriting would be structurally dishonest about that)."""

    margin_source_label = SIMULATED_MARGIN_SOURCE

    def get_portfolio_margin(self, legs: List[MarginLegRequest], clock: Clock) -> MarginSnapshot:
        as_of = clock()

        if not legs:
            return MarginSnapshot(
                required_margin=0.0, margin_verified=True, margin_source=self.margin_source_label,
                as_of=as_of, quote=None,
            )

        for leg in legs:
            invalid_reason = self._validate_leg(leg)
            if invalid_reason is not None:
                return MarginSnapshot(
                    required_margin=None, margin_verified=False, margin_source=SIMULATED_VALIDATION_FAILED_SOURCE,
                    as_of=as_of,
                    quote=WholeBookMarginQuoteResult(
                        parsed_successfully=False, parse_error=invalid_reason,
                        total_margin=None, benefit=None, expo=None, span=None, individual_info=None,
                    ),
                )

        short_notional = sum(leg.qty * leg.limit_price for leg in legs if leg.side == -1)
        long_notional = sum(leg.qty * leg.limit_price for leg in legs if leg.side == 1)
        covered_short = min(short_notional, long_notional)
        naked_short = short_notional - covered_short

        required_margin = (
            SHORT_MARGIN_RATE * naked_short
            + HEDGED_MARGIN_RATE * covered_short
            + LONG_MARGIN_RATE * long_notional
        )

        return MarginSnapshot(
            required_margin=required_margin, margin_verified=True, margin_source=self.margin_source_label,
            as_of=as_of, quote=None,
        )

    @staticmethod
    def _validate_leg(leg: MarginLegRequest) -> Optional[str]:
        if not leg.symbol or not leg.symbol.strip():
            return "leg has an empty, missing, or whitespace-only symbol"
        if leg.side not in (1, -1):
            return f"leg {leg.symbol!r} has invalid side {leg.side!r} -- expected 1 (buy) or -1 (sell)"
        if leg.qty <= 0:
            return f"leg {leg.symbol!r} has non-positive qty {leg.qty!r}"
        if leg.limit_price is None:
            return f"leg {leg.symbol!r} is missing a reference/limit price"
        if leg.limit_price < 0:
            return f"leg {leg.symbol!r} has a negative limit_price {leg.limit_price!r}"
        return None
