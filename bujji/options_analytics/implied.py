"""Implied volatility: inverting an observed price, not estimating one.

IV IS NOT A FORECAST AND NOT A GUESS. It is, by definition, the volatility
that makes the model reproduce the price the market actually printed. Given a
real traded price, a forward recovered from that same chain, a strike, and a
time to expiry, solving for sigma is a deterministic transform of observed
data. That is what makes this legitimate where "estimating IV" would not be.

What it DOES import is the model: European exercise, lognormal forward,
constant vol over the life. For NIFTY -- European, cash settled -- that is a
genuine fit rather than a convenience. It is still an assumption, and every
result carries the model name so nobody downstream forgets.

THE SOLVER REFUSES MORE THAN IT SOLVES, ON PURPOSE:
  - a price below intrinsic cannot be inverted at any vol -- it is stale,
    crossed or wrong, and returning the boundary would invent a 0% or a
    floor value that looks like a reading;
  - a price at or above the forward (for a call) has no finite solution;
  - non-convergence returns nothing rather than the last iterate.

Bisection on a bracket, not Newton. Vega collapses to nearly zero for deep
out-of-the-money options, exactly where a chain has its widest, stalest
quotes, and Newton diverges spectacularly there. Bisection is slower and
always right, and a chain is a few hundred contracts, not a few million.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .black76 import intrinsic, price

# The bracket. 1% and 500% annualised comfortably contain anything a listed
# index option has ever implied; a solution outside it means the inputs are
# wrong, not that the market is extraordinary.
MIN_SIGMA = 0.01
MAX_SIGMA = 5.00

MAX_ITERATIONS = 100
PRICE_TOLERANCE = 1e-6          # in price units
SIGMA_TOLERANCE = 1e-7          # in vol units

STATUS_OK = "SOLVED"
STATUS_BELOW_INTRINSIC = "PRICE_BELOW_INTRINSIC"
STATUS_ABOVE_MAX = "PRICE_ABOVE_MODEL_MAXIMUM"
STATUS_NO_BRACKET = "NO_SOLUTION_IN_BRACKET"
STATUS_NO_CONVERGENCE = "NO_CONVERGENCE"
STATUS_BAD_INPUT = "BAD_INPUT"


@dataclass(frozen=True)
class ImpliedVol:
    status: str
    sigma: Optional[float] = None
    iterations: int = 0
    price_error: Optional[float] = None
    reason: Optional[str] = None

    @property
    def is_solved(self) -> bool:
        return self.status == STATUS_OK and self.sigma is not None

    def to_dict(self) -> dict:
        return {"status": self.status, "sigma": self.sigma,
                "iterations": self.iterations, "price_error": self.price_error,
                "reason": self.reason, "method": "black76_bisection"}


def implied_volatility(
    observed_price: float, forward: float, strike: float, t_years: float,
    is_call: bool, df: float = 1.0,
) -> ImpliedVol:
    """Solve for the sigma that reproduces `observed_price`."""
    if observed_price is None or observed_price <= 0:
        return ImpliedVol(STATUS_BAD_INPUT, reason="no positive observed price")
    if forward is None or forward <= 0 or strike <= 0:
        return ImpliedVol(STATUS_BAD_INPUT, reason=f"F={forward} K={strike}")
    if t_years is None or t_years <= 0:
        return ImpliedVol(STATUS_BAD_INPUT, reason="option has expired or T <= 0")

    floor_price = intrinsic(forward, strike, is_call, df)
    if observed_price < floor_price - PRICE_TOLERANCE:
        return ImpliedVol(
            STATUS_BELOW_INTRINSIC, reason=(
                f"observed {observed_price:.4f} is below intrinsic {floor_price:.4f}; "
                f"no volatility reproduces it -- the quote is stale, crossed or wrong"))

    low_price = price(forward, strike, MIN_SIGMA, t_years, is_call, df)
    high_price = price(forward, strike, MAX_SIGMA, t_years, is_call, df)

    if observed_price > high_price + PRICE_TOLERANCE:
        return ImpliedVol(
            STATUS_ABOVE_MAX, reason=(
                f"observed {observed_price:.4f} exceeds the price at {MAX_SIGMA:.0%} vol "
                f"({high_price:.4f})"))
    if observed_price < low_price - PRICE_TOLERANCE:
        return ImpliedVol(
            STATUS_NO_BRACKET, reason=(
                f"observed {observed_price:.4f} is below the price at {MIN_SIGMA:.0%} vol "
                f"({low_price:.4f}) -- implied vol would be under the bracket floor"))

    lo, hi = MIN_SIGMA, MAX_SIGMA
    for iteration in range(1, MAX_ITERATIONS + 1):
        mid = 0.5 * (lo + hi)
        value = price(forward, strike, mid, t_years, is_call, df)
        error = value - observed_price
        if abs(error) < PRICE_TOLERANCE or (hi - lo) < SIGMA_TOLERANCE:
            return ImpliedVol(STATUS_OK, sigma=mid, iterations=iteration, price_error=error)
        # Price is monotonically increasing in sigma, which is what makes a
        # plain bisection valid here at all.
        if error > 0:
            hi = mid
        else:
            lo = mid

    return ImpliedVol(
        STATUS_NO_CONVERGENCE, iterations=MAX_ITERATIONS,
        reason=f"no convergence in {MAX_ITERATIONS} bisection steps")
