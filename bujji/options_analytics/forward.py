"""The forward, recovered from the option market itself.

THIS IS THE KEY MOVE OF THE WHOLE PHASE. To imply a volatility you need a
forward and a discount factor. The obvious route is to assume a risk-free
rate and a dividend yield and build the forward from spot -- and every
number downstream would then inherit two assumptions nobody measured, while
looking exactly as solid as the observed prices beside them.

Put-call parity gives them away for free. For a European option:

    C - K_disc  =  P + F_disc          =>     C - P  =  DF * (F - K)

which, across the strikes of ONE expiry, is a straight line in K:

    (C - P)  =  (DF * F)  -  DF * K

Least squares on that line yields DF from the slope and F from the intercept.
Both come out of observed option prices. No rate assumption, no dividend
assumption, no carry model -- and the fit quality (R^2, strike count, the
residual) is itself evidence about whether the chain is internally coherent
at that moment.

WHEN THE FIT IS BAD, THAT IS INFORMATION. A stale, crossed or thin chain will
not lie on a line. This module REFUSES rather than returning a best-effort
forward, because a wrong forward silently poisons every implied vol built on
it -- and an IV that is quietly wrong is far more dangerous than an IV that
is honestly missing.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

# A parity line needs enough points to be a line rather than a coincidence.
MIN_STRIKES_FOR_FIT = 4

# Discount factors for listed index expiries live very close to 1. Anything
# outside this band means the fit found something other than the forward.
MIN_PLAUSIBLE_DF = 0.90
MAX_PLAUSIBLE_DF = 1.01

# Below this the points are not on a line and the fit is not a forward.
MIN_R_SQUARED = 0.99

STATUS_OK = "DERIVED_FROM_PARITY"
STATUS_TOO_FEW_STRIKES = "TOO_FEW_PAIRED_STRIKES"
STATUS_POOR_FIT = "PARITY_FIT_POOR"
STATUS_IMPLAUSIBLE_DF = "IMPLAUSIBLE_DISCOUNT_FACTOR"
STATUS_DEGENERATE = "DEGENERATE_STRIKES"


@dataclass(frozen=True)
class ForwardEstimate:
    """A forward and discount factor, with the evidence that produced them."""

    status: str
    forward: Optional[float] = None
    discount_factor: Optional[float] = None
    strikes_used: int = 0
    r_squared: Optional[float] = None
    max_abs_residual: Optional[float] = None
    expiry: str = ""
    reason: Optional[str] = None

    @property
    def is_usable(self) -> bool:
        return self.status == STATUS_OK and self.forward is not None

    def to_dict(self) -> dict:
        return {
            "status": self.status, "forward": self.forward,
            "discount_factor": self.discount_factor, "strikes_used": self.strikes_used,
            "r_squared": self.r_squared, "max_abs_residual": self.max_abs_residual,
            "expiry": self.expiry, "reason": self.reason,
            "method": "put_call_parity_least_squares",
        }


def estimate_forward(
    call_prices: Dict[float, float], put_prices: Dict[float, float], expiry: str = "",
) -> ForwardEstimate:
    """Fit (C - P) against K across every strike where BOTH legs are priced.

    Both legs required, deliberately: parity is a statement about a pair, and
    filling a missing leg from a model would make the recovered forward
    depend on the very thing it is supposed to give us.
    """
    paired: List[Tuple[float, float]] = []
    for strike, call in call_prices.items():
        put = put_prices.get(strike)
        if put is None or call is None or strike <= 0:
            continue
        if call <= 0 or put <= 0:
            continue    # a zero quote is an absence, not a price of zero
        paired.append((float(strike), float(call) - float(put)))

    n = len(paired)
    if n < MIN_STRIKES_FOR_FIT:
        return ForwardEstimate(
            status=STATUS_TOO_FEW_STRIKES, strikes_used=n, expiry=expiry,
            reason=f"{n} strike(s) with both legs priced; need >= {MIN_STRIKES_FOR_FIT}")

    xs = [k for k, _ in paired]
    ys = [d for _, d in paired]
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    sxx = sum((x - mean_x) ** 2 for x in xs)
    if sxx <= 0:
        return ForwardEstimate(status=STATUS_DEGENERATE, strikes_used=n, expiry=expiry,
                               reason="all strikes identical -- no line to fit")
    sxy = sum((x - mean_x) * (y - mean_y) for x, y in paired)
    slope = sxy / sxx
    intercept = mean_y - slope * mean_x

    residuals = [y - (intercept + slope * x) for x, y in paired]
    max_abs_residual = max(abs(r) for r in residuals)
    syy = sum((y - mean_y) ** 2 for y in ys)
    r_squared = 1.0 - (sum(r * r for r in residuals) / syy) if syy > 0 else None

    discount_factor = -slope
    if not (MIN_PLAUSIBLE_DF <= discount_factor <= MAX_PLAUSIBLE_DF):
        return ForwardEstimate(
            status=STATUS_IMPLAUSIBLE_DF, strikes_used=n, r_squared=r_squared,
            max_abs_residual=max_abs_residual, expiry=expiry,
            reason=(f"implied discount factor {discount_factor:.4f} outside "
                    f"[{MIN_PLAUSIBLE_DF}, {MAX_PLAUSIBLE_DF}] -- the fit did not find a forward"))

    if r_squared is None or r_squared < MIN_R_SQUARED:
        return ForwardEstimate(
            status=STATUS_POOR_FIT, strikes_used=n, r_squared=r_squared,
            max_abs_residual=max_abs_residual, expiry=expiry,
            reason=(f"parity R^2 {r_squared} below {MIN_R_SQUARED} -- the chain is not "
                    f"internally coherent here (stale, crossed or thin), so no forward "
                    f"is published"))

    forward = intercept / discount_factor
    if forward <= 0:
        return ForwardEstimate(
            status=STATUS_DEGENERATE, strikes_used=n, r_squared=r_squared,
            max_abs_residual=max_abs_residual, expiry=expiry,
            reason=f"fitted forward {forward} is not positive")

    return ForwardEstimate(
        status=STATUS_OK, forward=forward, discount_factor=discount_factor,
        strikes_used=n, r_squared=r_squared, max_abs_residual=max_abs_residual,
        expiry=expiry)
