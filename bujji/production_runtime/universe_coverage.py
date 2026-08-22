"""Is the market-data universe actually covered, or do we merely believe it is?

THE RULE THIS ENCODES: **subscribed is not covered.**

A subscription request that was sent, accepted, and then delivered nothing is
indistinguishable — from the subscriber's side — from a symbol that is simply
not trading. Both look like silence. Only a TICK proves the pipe is carrying
data for that symbol, so freshness, not acknowledgement, is what this gate
counts.

WHY IT EXISTS. Until now the trading runner subscribed to nothing until an
entry had already filled: `WebsocketTickProvider.get_prices()` subscribes
`list(contracts_by_symbol)`, and that dict is populated only after the entry
orders fill. So the feed was a consequence of trading, never a precondition
for it — the session could select strikes, size them and place them without a
single live price having arrived for anything.

It also means a blind feed could not be detected before it mattered. On
2026-08-21 the feed delivered zero ticks all day; the first evidence was a
BLIND CYCLE warning logged *after* a naked short strangle was already open.

FAIL-CLOSED BY CONSTRUCTION. Every uncertain state resolves to "do not enter":
no universe, a symbol never requested, a symbol requested but silent, an
unreadable tick age. None of them is treated as coverage. UNKNOWN is not OK.

PURE. No I/O, no clock, no broker, no feed. It is handed what is intended,
what was requested, and how old each symbol's newest tick is; it returns a
verdict. That makes every branch testable without a market.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, Optional, Sequence, Tuple

# Verdict states. Only COMPLETE permits new risk.
COVERAGE_COMPLETE = "COMPLETE"
COVERAGE_INCOMPLETE = "INCOMPLETE"
COVERAGE_UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class SymbolCoverage:
    """One intended symbol's state. `fresh` is the only thing that counts."""

    symbol: str
    requested: bool
    tick_age_seconds: Optional[float]
    fresh: bool

    def as_dict(self) -> Dict[str, object]:
        return {
            "symbol": self.symbol,
            "requested": self.requested,
            "tick_age_seconds": self.tick_age_seconds,
            "fresh": self.fresh,
        }


@dataclass(frozen=True)
class CoverageVerdict:
    state: str
    permits_entry: bool
    reasons: Tuple[str, ...]
    intended: int
    requested: int
    fresh: int
    never_requested: Tuple[str, ...]
    silent: Tuple[str, ...]
    stale: Tuple[str, ...]

    def as_dict(self) -> Dict[str, object]:
        return {
            "state": self.state,
            "permits_entry": self.permits_entry,
            "reasons": list(self.reasons),
            "intended": self.intended,
            "requested": self.requested,
            "fresh": self.fresh,
            # Bounded: a 242-symbol universe that is wholly silent must not
            # write 242 names into every session summary.
            "never_requested_sample": list(self.never_requested[:10]),
            "never_requested_count": len(self.never_requested),
            "silent_sample": list(self.silent[:10]),
            "silent_count": len(self.silent),
            "stale_sample": list(self.stale[:10]),
            "stale_count": len(self.stale),
        }


def evaluate_coverage(
    intended: Sequence[str],
    requested: Iterable[str],
    tick_ages: Dict[str, Optional[float]],
    max_age_seconds: float,
) -> CoverageVerdict:
    """Grade the universe's live coverage. Never raises.

    `intended`     -- every symbol the session universe says must be covered.
    `requested`    -- symbols a subscribe call was actually issued for.
    `tick_ages`    -- symbol -> seconds since its newest tick, or None for
                      "never ticked / unreadable". None is NOT zero.
    `max_age`      -- how old a symbol's newest tick may be and still count.

    A symbol is covered only when it was requested AND has a tick no older
    than `max_age_seconds`. Everything else is a reason to refuse.
    """
    intended_list = [s for s in dict.fromkeys(intended) if s]
    if not intended_list:
        return CoverageVerdict(
            state=COVERAGE_UNKNOWN, permits_entry=False,
            reasons=("no session universe was constructed, so there is nothing "
                     "to prove coverage of -- an empty universe is UNKNOWN, "
                     "never 'nothing required'",),
            intended=0, requested=0, fresh=0,
            never_requested=(), silent=(), stale=(),
        )

    requested_set = {s for s in requested if s}
    never_requested, silent, stale, fresh = [], [], [], []

    for symbol in intended_list:
        if symbol not in requested_set:
            never_requested.append(symbol)
            continue
        age = tick_ages.get(symbol)
        if age is None:
            # Requested, acknowledged perhaps, and never heard from. This is
            # the exact shape of the 2026-08-21 feed: connected, silent.
            silent.append(symbol)
            continue
        try:
            age_f = float(age)
        except (TypeError, ValueError):
            silent.append(symbol)
            continue
        if age_f > float(max_age_seconds):
            stale.append(symbol)
            continue
        fresh.append(symbol)

    reasons = []
    if never_requested:
        reasons.append(
            f"{len(never_requested)} of {len(intended_list)} intended symbols were "
            f"never requested (e.g. {', '.join(never_requested[:5])}) -- the "
            f"universe was not fully subscribed")
    if silent:
        reasons.append(
            f"{len(silent)} of {len(intended_list)} subscribed symbols have NEVER "
            f"delivered a tick (e.g. {', '.join(silent[:5])}) -- subscribed is "
            f"not covered")
    if stale:
        reasons.append(
            f"{len(stale)} of {len(intended_list)} symbols have no tick within "
            f"{max_age_seconds:.0f}s (e.g. {', '.join(stale[:5])}) -- a stale "
            f"price is not a price")

    if not reasons:
        state, permits = COVERAGE_COMPLETE, True
    elif fresh:
        state, permits = COVERAGE_INCOMPLETE, False
    else:
        # Nothing at all is arriving: not merely incomplete, unknown.
        state, permits = COVERAGE_UNKNOWN, False

    return CoverageVerdict(
        state=state, permits_entry=permits, reasons=tuple(reasons),
        intended=len(intended_list), requested=len(requested_set & set(intended_list)),
        fresh=len(fresh),
        never_requested=tuple(never_requested), silent=tuple(silent), stale=tuple(stale),
    )
