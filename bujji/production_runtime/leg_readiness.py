"""Are these EXACT legs and hedges fit to be ordered? Stage 2 of the rule.

THE OPERATOR'S RULE (2026-08-22): "After it selects a proposed trade but before
it submits an order, the exact legs and hedges must independently pass
freshness and field-completeness checks."

INDEPENDENTLY. This is a second evaluation, not a reuse of the band verdict.
Stage 1 proved the whole candidate set was fresh at the moment the selector
ranged over it; between then and submission the margin call, the risk decision
and the symbol resolution all run, and time passes. A price that was fresh when
the strike was chosen can be stale by the time the order leaves.

FIELD-COMPLETENESS IS NOT FRESHNESS. A tick that arrived one second ago proves
the pipe is live; it says nothing about whether the contract has a two-sided
book to trade against. These are different failures with different causes, so
they are graded separately and reported separately.

WHY THE HEDGES MATTER MORE THAN THE LEGS THEY PROTECT. `_candidates_for_type`
(msi_trade_construction/engine.py:197) requires `delta is not None` -- a solved
premium and a converged IV -- but `_nearest_grid` and `_at_strike`, which pick
EVERY wing, iterate the unfiltered evidence dict. So a wing can be a contract
whose premium was absent or whose IV never solved, while the short it protects
is guaranteed both. The protective side of a defined-risk structure is
systematically the least-validated part of it, and it is also submitted LAST
(engine.py:389-392 emits SHORT, SHORT, WING, WING). This gate is where that
asymmetry is caught.

PURE. No I/O, no clock, no broker, no feed. Handed the legs, their tick ages
and their quotes, it returns a verdict -- so every branch is testable without a
market.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Sequence, Tuple

READY = "READY"
NOT_READY = "NOT_READY"
UNKNOWN = "UNKNOWN"

# Per-leg failures, most severe first when several apply.
LEG_UNRESOLVED = "UNRESOLVED"      # no broker symbol -- cannot be priced or ordered
LEG_SILENT = "SILENT"              # subscribed, never ticked
LEG_STALE = "STALE"                # last tick older than the limit
LEG_NO_QUOTE = "NO_QUOTE"          # no book row at all
LEG_INCOMPLETE = "INCOMPLETE"      # a book row missing the fields an order needs
LEG_READY = "READY"


@dataclass(frozen=True)
class LegQuote:
    """What the book says about one contract. Every field Optional on purpose:
    absent is a real, distinct answer from zero."""

    premium: Optional[float] = None
    bid: Optional[float] = None
    ask: Optional[float] = None

    def missing_fields(self) -> Tuple[str, ...]:
        missing = []
        if not _positive(self.premium):
            missing.append("premium")
        if not _positive(self.bid):
            missing.append("bid")
        if not _positive(self.ask):
            missing.append("ask")
        return tuple(missing)

    def crossed(self) -> bool:
        """A book whose ask is below its bid is not a book. Reported apart from
        a MISSING field because the cause is different -- this is a live feed
        disagreeing with itself, not an absent one."""
        if not (_positive(self.bid) and _positive(self.ask)):
            return False
        return float(self.ask) < float(self.bid)


def _positive(value) -> bool:
    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return False


@dataclass(frozen=True)
class LegState:
    symbol: Optional[str]
    role: str
    state: str
    tick_age_seconds: Optional[float]
    detail: str

    def as_dict(self) -> Dict[str, object]:
        return {
            "symbol": self.symbol, "role": self.role, "state": self.state,
            "tick_age_seconds": self.tick_age_seconds, "detail": self.detail,
        }


@dataclass(frozen=True)
class LegReadiness:
    state: str
    permits_entry: bool
    legs: Tuple[LegState, ...]
    reasons: Tuple[str, ...]

    def as_dict(self) -> Dict[str, object]:
        return {
            "state": self.state,
            "permits_entry": self.permits_entry,
            "reasons": list(self.reasons),
            # A structure is a handful of legs, never hundreds, so every one is
            # reported. This is the payload an operator reads to find out which
            # leg refused the trade.
            "legs": [leg.as_dict() for leg in self.legs],
        }


def evaluate_leg_readiness(
    legs: Sequence,
    *,
    tick_ages: Dict[str, Optional[float]],
    quotes: Dict[str, LegQuote],
    max_age_seconds: float,
) -> LegReadiness:
    """Grade the exact legs about to be ordered. Never raises.

    `legs`      -- objects carrying `symbol` and `role`. A None symbol means
                   the leg could not be resolved to a broker contract.
    `tick_ages` -- symbol -> seconds since its newest tick, None for never.
    `quotes`    -- symbol -> LegQuote. A missing entry is NO_QUOTE, not empty.
    """
    if not legs:
        return LegReadiness(
            state=UNKNOWN, permits_entry=False, legs=(),
            reasons=("a proposal with no legs cannot be graded, and an ungraded "
                     "order is not an order to place",),
        )

    states, reasons = [], []
    for leg in legs:
        symbol = getattr(leg, "symbol", None)
        role = str(getattr(leg, "role", "") or "UNKNOWN_ROLE")

        if not symbol:
            states.append(LegState(None, role, LEG_UNRESOLVED, None,
                                   "no broker symbol -- cannot be priced or ordered"))
            continue

        age = tick_ages.get(symbol)
        if age is None:
            states.append(LegState(symbol, role, LEG_SILENT, None,
                                   "subscribed and never heard from -- silence is not a price"))
            continue
        try:
            age_f = float(age)
        except (TypeError, ValueError):
            states.append(LegState(symbol, role, LEG_SILENT, None,
                                   "tick age unreadable, which is not evidence of freshness"))
            continue
        if age_f > float(max_age_seconds):
            states.append(LegState(symbol, role, LEG_STALE, age_f,
                                   f"newest tick is {age_f:.0f}s old, limit {max_age_seconds:.0f}s"))
            continue

        quote = quotes.get(symbol)
        if quote is None:
            states.append(LegState(symbol, role, LEG_NO_QUOTE, age_f,
                                   "ticking, but the book carries no row for it"))
            continue
        missing = quote.missing_fields()
        if missing:
            states.append(LegState(symbol, role, LEG_INCOMPLETE, age_f,
                                   f"missing {', '.join(missing)} -- an order needs a two-sided "
                                   f"book, not only a last-traded price"))
            continue
        if quote.crossed():
            states.append(LegState(symbol, role, LEG_INCOMPLETE, age_f,
                                   f"crossed book: ask {quote.ask} below bid {quote.bid}"))
            continue

        states.append(LegState(symbol, role, LEG_READY, age_f, "fresh and complete"))

    failed = [s for s in states if s.state != LEG_READY]
    for state in failed:
        reasons.append(f"{state.role} {state.symbol or '<unresolved>'}: "
                       f"{state.state} -- {state.detail}")

    if not failed:
        return LegReadiness(READY, True, tuple(states), ())
    if any(s.state == LEG_READY for s in states):
        return LegReadiness(NOT_READY, False, tuple(states), tuple(reasons))
    # Nothing at all is priceable: not merely not-ready, unknown.
    return LegReadiness(UNKNOWN, False, tuple(states), tuple(reasons))
