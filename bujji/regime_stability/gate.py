"""Is this regime a reading of the market, or an artefact of how we sampled it?

THE PROBLEM THIS SOLVES. A warm-up feeds N spaced spot observations through
one MarketStateBuilder so the price-structure domains have enough real
price deltas to form an opinion. But an adversarial replay of 478 real
captured snapshots (SHADOW-OBSERVATORY-2026-08-06-run2) found the verdict
moves with the SPACING and nothing else:

    30 / 45 / 60 / 90 / 120 s  ->  psi.structure_state = TRENDING
    180 s                       ->  CORRECTING
    300 s                       ->  different again

Same tape, same start instant, same code. Only the sampling interval
changed. A configurable `spacing_seconds` would therefore have let an
arbitrary number in a YAML file decide the day's regime, while looking
like a market judgement. That is worse than the starvation it replaces:
NO_TRADE from thin evidence is honest, a regime picked by a knob is not.

THE GATE. Poll once at the FINEST interval, then derive the regime from
several subsamples of that one series -- every poll, every 2nd, every 4th.
Subsampling is exact, not approximate: the price at t=0,120,240 is the same
number whether or not you also looked at t=30. So this costs no extra API
calls and no extra waiting, and it asks the only question that matters --

    does this regime survive being looked at differently?

If every stride agrees, the reading is a property of the market and is
returned with the agreeing strides recorded as evidence. If they disagree,
that IS the finding: the structure is not resolvable at this timescale, and
the honest answer is NO_TRADE with the disagreement stated, not a coin-flip
dressed as a decision.

WHAT THIS IS NOT. It is not a confidence score, a vote, or a tie-break.
There is no "best of three" -- unanimity or nothing. A majority rule would
reintroduce exactly the arbitrariness it exists to remove.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, Optional, Sequence, Tuple

# Strides over the polled series. 1 = every poll, 2 = every second, and so
# on. Chosen to span the range where the real capture showed the verdict
# flipping; a regime that holds across a 4x change in sampling interval is
# not an artefact of one of them.
DEFAULT_STRIDES: Tuple[int, ...] = (1, 2, 4)

# The gate needs at least this many observations AFTER subsampling, or the
# widest stride is judging on fewer price deltas than the narrowest and the
# comparison is not like-for-like. 4 observations = 3 deltas, which is the
# measured threshold for a real MDI opinion.
MIN_OBSERVATIONS_PER_STRIDE = 4

UNSTABLE = "UNSTABLE_ACROSS_SAMPLING"
INSUFFICIENT = "INSUFFICIENT_OBSERVATIONS"


@dataclass(frozen=True)
class StabilityVerdict:
    """The regime, plus what it survived. `regime` is None when the gate
    refuses -- there is no partial answer and no fallback value."""
    regime: Optional[str]
    is_stable: bool
    reason: str
    by_stride: Dict[int, Optional[str]]
    observations_used: Dict[int, int]

    @property
    def disagreement(self) -> Tuple[str, ...]:
        return tuple(sorted({r for r in self.by_stride.values() if r is not None}))


def subsample(observations: Sequence, stride: int) -> Tuple:
    """Every `stride`-th observation, anchored at the FIRST.

    Anchoring matters: starting at index 0 for every stride means all
    subsamples share a start instant, so a difference between them is a
    difference of sampling RATE and not of when the window opened.
    """
    if stride < 1:
        raise ValueError(f"stride must be >= 1, got {stride}")
    return tuple(observations[::stride])


def assess_stability(
    observations: Sequence,
    derive_regime: Callable[[Sequence], Optional[str]],
    strides: Sequence[int] = DEFAULT_STRIDES,
    min_observations: int = MIN_OBSERVATIONS_PER_STRIDE,
) -> StabilityVerdict:
    """Derive the regime at each stride and demand unanimity.

    `derive_regime` takes a sequence of observations and returns a regime
    string, or None when it honestly cannot form one. This is injected
    rather than imported so the gate carries no dependency on the thesis
    stack and can be tested against it directly.

    A stride that cannot be evaluated -- too few observations after
    subsampling, or a None regime -- does NOT get skipped. It makes the
    verdict unstable. Dropping the strides that failed to answer and
    agreeing among the rest is precisely how a "stability check" becomes a
    rubber stamp.
    """
    by_stride: Dict[int, Optional[str]] = {}
    used: Dict[int, int] = {}

    for stride in strides:
        window = subsample(observations, stride)
        used[stride] = len(window)
        if len(window) < min_observations:
            by_stride[stride] = None
            continue
        by_stride[stride] = derive_regime(window)

    thin = [s for s, n in used.items() if n < min_observations]
    if thin:
        return StabilityVerdict(
            None, False,
            f"{INSUFFICIENT}: stride(s) {sorted(thin)} had fewer than "
            f"{min_observations} observations after subsampling "
            f"({ {s: used[s] for s in thin} }). Poll longer or reduce the widest stride.",
            by_stride, used,
        )

    unanswered = [s for s, r in by_stride.items() if r is None]
    if unanswered:
        return StabilityVerdict(
            None, False,
            f"{UNSTABLE}: stride(s) {sorted(unanswered)} formed no regime at all while "
            "others did -- the structure is visible at some sampling rates and invisible "
            "at others, which is itself instability.",
            by_stride, used,
        )

    distinct = {r for r in by_stride.values()}
    if len(distinct) > 1:
        detail = ", ".join(f"stride {s} -> {by_stride[s]}" for s in sorted(by_stride))
        return StabilityVerdict(
            None, False,
            f"{UNSTABLE}: the regime changes with the sampling interval ({detail}). "
            "Same tape, same start, different answer -- so no answer.",
            by_stride, used,
        )

    agreed = distinct.pop()
    return StabilityVerdict(
        agreed, True,
        f"stable: every stride {sorted(by_stride)} independently derived {agreed}.",
        by_stride, used,
    )
