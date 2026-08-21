"""Deterministic Simulated Margin Provider — BUJJI Options OS v3,
Numeric Risk Governor Gate C.1 / C.2.

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

GATE C.2 ADDITION -- MARGIN EXPLANATION: `get_portfolio_margin` answers
"what is the margin number"; `get_portfolio_margin_with_explanation`
additionally answers "why is it this number". Both are computed from
the SAME internal `_compute_book()` pass -- there is deliberately no
second, independent recomputation for the explanation, because two
separate computations of the same figure is exactly how a MarginSnapshot
and its MarginExplanation could silently drift apart. Per-leg margin
contributions are allocated proportionally to each leg's own notional
share of the book-level naked/covered split, so
sum(leg.margin_contribution for leg in contributing_legs) is always
EXACTLY equal to required_margin -- provable by construction, not just
by convention, and asserted directly in this module's own tests.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, List, Optional, Tuple

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

# Margin risk classification -- book-composition-based ONLY. See
# classify_book_risk()'s own docstring for why EXCESSIVE_CAPITAL_USAGE
# is deliberately NOT one of this function's possible outputs.
RISK_LOW_RISK = "LOW_RISK"
RISK_DEFINED_RISK = "DEFINED_RISK"
RISK_HIGH_NAKED_EXPOSURE = "HIGH_NAKED_EXPOSURE"
RISK_EXCESSIVE_CAPITAL_USAGE = "EXCESSIVE_CAPITAL_USAGE"
RISK_INVALID_STATE = "INVALID_STATE"


@dataclass(frozen=True)
class LegMarginContribution:
    """One leg's share of the book-level margin total. `margin_contribution`
    is allocated proportionally to this leg's own notional share of
    whichever pool it belongs to (naked short / covered short / long) --
    NOT independently recomputed, so contributions always sum exactly
    to the book's required_margin."""

    symbol: str
    side: int              # 1 (buy) | -1 (sell), same convention as MarginLegRequest
    qty: int
    notional: float         # qty * limit_price
    margin_contribution: float


@dataclass(frozen=True)
class MarginExplanation:
    """Answers "why is the margin number this high" -- deliberately a
    SEPARATE model from MarginSnapshot (which only answers "what is the
    number"). Always produced from the same underlying computation as
    its paired MarginSnapshot; never a second, independently-derived
    figure."""

    total_required_margin: float
    total_long_exposure: float
    total_short_exposure: float
    covered_exposure: float
    naked_exposure: float
    contributing_legs: Tuple[LegMarginContribution, ...]
    highest_margin_contributor: Optional[LegMarginContribution]
    risk_flags: Tuple[str, ...]
    risk_classification: str
    explanation_source: str   # mirrors the paired MarginSnapshot.margin_source verbatim


@dataclass(frozen=True)
class _BookComputation:
    """Internal. The single shared computation both get_portfolio_margin
    and get_portfolio_margin_with_explanation build their output from."""

    required_margin: float
    long_notional: float
    short_notional: float
    covered_short: float
    naked_short: float
    contributions: Tuple[LegMarginContribution, ...]


def _classify_book(required_margin: Optional[float], naked_short: float, short_notional: float) -> str:
    """The actual classification rule, over primitives -- see
    classify_book_risk() for the public, MarginExplanation-shaped
    entry point tests should use. Kept separate so
    get_portfolio_margin_with_explanation can compute the
    classification BEFORE constructing the (frozen) MarginExplanation,
    without a build-then-replace step."""
    if required_margin is None:
        return RISK_INVALID_STATE
    if required_margin == 0:
        return RISK_LOW_RISK
    if naked_short > 0:
        return RISK_HIGH_NAKED_EXPOSURE
    if short_notional > 0:
        # naked_short == 0 but there WAS short exposure -> fully hedged/covered
        return RISK_DEFINED_RISK
    return RISK_LOW_RISK  # pure long-only book


def classify_book_risk(explanation: MarginExplanation) -> str:
    """Pure, deterministic, book-composition-based ONLY -- this
    function has no access to available capital, so it can NEVER
    return EXCESSIVE_CAPITAL_USAGE (that classification requires
    comparing required_margin against available_capital, which lives
    in capital_utilization.py's assess_capital_utilization() and the
    combined decision layer in explain_trade_decision(), not here).
    Faking that capability here would mean guessing at a threshold
    this function cannot actually see the other side of.

    Rules, in priority order:
      1. INVALID_STATE  -- the explanation itself represents a failed/
         unverified computation (total_required_margin is None).
      2. LOW_RISK        -- zero required margin (empty book), or a
         pure long-only book (bounded loss, no short exposure at all).
      3. HIGH_NAKED_EXPOSURE -- ANY naked (uncovered) short notional
         present, regardless of how small -- matching this whole
         Governor's established philosophy elsewhere (RATIO/SYNTHETIC
         etc. are vetoed entirely rather than partially trusted; there
         is no "mostly hedged, a little naked" tier).
      4. DEFINED_RISK    -- short exposure present but fully covered.

    This is the same rule get_portfolio_margin_with_explanation already
    applies internally (via _classify_book on the same primitives) --
    exposed here so a MarginExplanation built or received independently
    (e.g. in a test, or a future consumer) can be reclassified/verified
    without needing access to a SimulatedMarginProvider instance."""
    return _classify_book(
        required_margin=explanation.total_required_margin,
        naked_short=explanation.naked_exposure,
        short_notional=explanation.total_short_exposure,
    )


class SimulatedMarginProvider:
    """Deterministic, pure, no network access, no randomness -- the
    same List[MarginLegRequest] always produces the same MarginSnapshot
    (and, via get_portfolio_margin_with_explanation, the same
    MarginExplanation). Implements the same duck-typed
    `get_portfolio_margin(legs, clock)` shape as
    UncertifiedWholeBookMarginProvider/CertifiedWholeBookMarginProvider,
    but does NOT inherit from either (it has no HttpCaller and makes no
    request/response round trip -- inheriting would be structurally
    dishonest about that)."""

    margin_source_label = SIMULATED_MARGIN_SOURCE

    def get_portfolio_margin(self, legs: List[MarginLegRequest], clock: Clock) -> MarginSnapshot:
        as_of = clock()
        invalid_reason = self._validate_legs(legs)
        if invalid_reason is not None:
            return self._invalid_snapshot(as_of, invalid_reason)

        computation = self._compute_book(legs)
        return MarginSnapshot(
            required_margin=computation.required_margin, margin_verified=True,
            margin_source=self.margin_source_label, as_of=as_of, quote=None,
        )

    def get_portfolio_margin_with_explanation(
        self, legs: List[MarginLegRequest], clock: Clock,
    ) -> Tuple[MarginSnapshot, MarginExplanation]:
        """Same validation and same underlying computation as
        get_portfolio_margin -- this method never diverges from that
        one's number, by construction (both call _compute_book() on
        the identical input, and this method never calls the other)."""
        as_of = clock()
        invalid_reason = self._validate_legs(legs)
        if invalid_reason is not None:
            snapshot = self._invalid_snapshot(as_of, invalid_reason)
            explanation = MarginExplanation(
                total_required_margin=None, total_long_exposure=0.0, total_short_exposure=0.0,
                covered_exposure=0.0, naked_exposure=0.0, contributing_legs=(),
                highest_margin_contributor=None, risk_flags=("VALIDATION_FAILED",),
                risk_classification=RISK_INVALID_STATE, explanation_source=snapshot.margin_source,
            )
            return snapshot, explanation

        computation = self._compute_book(legs)
        snapshot = MarginSnapshot(
            required_margin=computation.required_margin, margin_verified=True,
            margin_source=self.margin_source_label, as_of=as_of, quote=None,
        )

        highest_contributor = None
        for contribution in computation.contributions:
            if highest_contributor is None or contribution.margin_contribution > highest_contributor.margin_contribution:
                highest_contributor = contribution

        risk_flags = []
        if computation.naked_short > 0:
            risk_flags.append("NAKED_SHORT_EXPOSURE")
        if computation.long_notional > 0 and computation.short_notional > 0:
            risk_flags.append("HEDGE_PRESENT")

        risk_classification = _classify_book(
            required_margin=computation.required_margin, naked_short=computation.naked_short,
            short_notional=computation.short_notional,
        )
        explanation = MarginExplanation(
            total_required_margin=computation.required_margin,
            total_long_exposure=computation.long_notional,
            total_short_exposure=computation.short_notional,
            covered_exposure=computation.covered_short,
            naked_exposure=computation.naked_short,
            contributing_legs=computation.contributions,
            highest_margin_contributor=highest_contributor,
            risk_flags=tuple(risk_flags),
            risk_classification=risk_classification,
            explanation_source=self.margin_source_label,
        )
        return snapshot, explanation

    def _invalid_snapshot(self, as_of: datetime, reason: str) -> MarginSnapshot:
        return MarginSnapshot(
            required_margin=None, margin_verified=False, margin_source=SIMULATED_VALIDATION_FAILED_SOURCE,
            as_of=as_of,
            quote=WholeBookMarginQuoteResult(
                parsed_successfully=False, parse_error=reason,
                total_margin=None, benefit=None, expo=None, span=None, individual_info=None,
            ),
        )

    def _validate_legs(self, legs: List[MarginLegRequest]) -> Optional[str]:
        for leg in legs:
            reason = self._validate_leg(leg)
            if reason is not None:
                return reason
        return None

    @staticmethod
    def _compute_book(legs: List[MarginLegRequest]) -> _BookComputation:
        if not legs:
            return _BookComputation(
                required_margin=0.0, long_notional=0.0, short_notional=0.0,
                covered_short=0.0, naked_short=0.0, contributions=(),
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

        contributions = []
        for leg in legs:
            notional = leg.qty * leg.limit_price
            if leg.side == -1:
                leg_share = (notional / short_notional) if short_notional > 0 else 0.0
                leg_covered = covered_short * leg_share
                leg_naked = naked_short * leg_share
                margin_contribution = SHORT_MARGIN_RATE * leg_naked + HEDGED_MARGIN_RATE * leg_covered
            else:
                margin_contribution = LONG_MARGIN_RATE * notional
            contributions.append(LegMarginContribution(
                symbol=leg.symbol, side=leg.side, qty=leg.qty, notional=notional,
                margin_contribution=margin_contribution,
            ))

        return _BookComputation(
            required_margin=required_margin, long_notional=long_notional, short_notional=short_notional,
            covered_short=covered_short, naked_short=naked_short, contributions=tuple(contributions),
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
