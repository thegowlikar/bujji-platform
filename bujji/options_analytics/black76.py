"""Black-76 on the forward: pricing and Greeks for European index options.

WHY BLACK-76 AND NOT BLACK-SCHOLES. NIFTY options are European and cash
settled, so the model assumption is a real fit rather than a convenience.
Black-76 prices off the FORWARD, which matters here for an epistemic reason:
the forward can be recovered from the option market itself (see forward.py),
so this module needs no interest-rate assumption, no dividend yield, and no
carry model. Every input is either observed or derived from observations.

WHAT THIS MODULE IS NOT. It is not a source of market data. Everything it
returns is a MODEL OUTPUT computed from observed prices, and the caller is
responsible for keeping that distinction visible downstream -- see
models.OptionAnalytics, where derived values live under their own status and
carry the inputs that produced them.

CONVENTIONS, stated because they are choices:
  - sigma is annualised, expressed as a decimal (0.12 == 12%).
  - T is in years.
  - Greeks are with respect to the FORWARD, not spot. For an index option
    whose forward is recovered from the same chain, forward Greeks are the
    honest quantity: a spot delta would require the carry assumption this
    module exists to avoid. Named `forward_delta` so nobody mistakes it.
  - vega is per 1.00 of vol (a 100-point move); vega_per_pct divides by 100.
  - theta is per calendar day, and is the pure time-decay term. With df=1.0
    (the default -- undiscounted) there is no rate component to include, and
    rho is therefore not modelled at all rather than being reported as zero.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

_SQRT_2PI = math.sqrt(2.0 * math.pi)
DAYS_PER_YEAR = 365.0


def norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / _SQRT_2PI


def norm_cdf(x: float) -> float:
    """Standard normal CDF via erf -- exact to double precision, no table."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


@dataclass(frozen=True)
class Greeks:
    """Model outputs. Every one is derived; none is observed."""

    price: float
    forward_delta: float
    gamma: float
    vega: float                 # per 1.00 of vol
    theta_per_day: float        # per calendar day
    d1: float
    d2: float

    @property
    def vega_per_pct(self) -> float:
        return self.vega / 100.0

    def to_dict(self) -> dict:
        return {
            "price": self.price, "forward_delta": self.forward_delta,
            "gamma": self.gamma, "vega": self.vega,
            "vega_per_pct": self.vega_per_pct,
            "theta_per_day": self.theta_per_day, "d1": self.d1, "d2": self.d2,
        }


def _d1_d2(forward: float, strike: float, sigma: float, t_years: float):
    vol_sqrt_t = sigma * math.sqrt(t_years)
    d1 = (math.log(forward / strike) + 0.5 * sigma * sigma * t_years) / vol_sqrt_t
    return d1, d1 - vol_sqrt_t


def price(forward: float, strike: float, sigma: float, t_years: float,
          is_call: bool, df: float = 1.0) -> float:
    """Black-76 price. Raises on inputs where the model is undefined --
    never returns a number for a question that has none."""
    if forward <= 0 or strike <= 0:
        raise ValueError(f"forward and strike must be positive: F={forward} K={strike}")
    if t_years <= 0:
        raise ValueError(f"time to expiry must be positive: T={t_years}")
    if sigma <= 0:
        raise ValueError(f"sigma must be positive: {sigma}")
    d1, d2 = _d1_d2(forward, strike, sigma, t_years)
    if is_call:
        return df * (forward * norm_cdf(d1) - strike * norm_cdf(d2))
    return df * (strike * norm_cdf(-d2) - forward * norm_cdf(-d1))


def greeks(forward: float, strike: float, sigma: float, t_years: float,
           is_call: bool, df: float = 1.0) -> Greeks:
    """Price and the standard first/second-order sensitivities."""
    d1, d2 = _d1_d2(forward, strike, sigma, t_years)
    sqrt_t = math.sqrt(t_years)
    pdf_d1 = norm_pdf(d1)

    value = price(forward, strike, sigma, t_years, is_call, df)
    delta = df * (norm_cdf(d1) if is_call else -norm_cdf(-d1))
    gamma = df * pdf_d1 / (forward * sigma * sqrt_t)
    vega = df * forward * pdf_d1 * sqrt_t
    # Pure time decay of the option's forward value. Same magnitude for a
    # call and a put at the same strike, which is the correct behaviour under
    # this convention and is asserted by a test.
    theta_year = -df * forward * pdf_d1 * sigma / (2.0 * sqrt_t)
    return Greeks(price=value, forward_delta=delta, gamma=gamma, vega=vega,
                  theta_per_day=theta_year / DAYS_PER_YEAR, d1=d1, d2=d2)


def intrinsic(forward: float, strike: float, is_call: bool, df: float = 1.0) -> float:
    """The no-arbitrage floor on the forward. A quoted price below this is
    not a price this model can invert -- it is stale, crossed, or wrong, and
    the IV solver refuses it rather than returning a boundary value."""
    return df * max(0.0, (forward - strike) if is_call else (strike - forward))


def time_to_expiry_years(now_iso: str, expiry_iso: str,
                         expiry_time: str = "15:30:00") -> Optional[float]:
    """Calendar-time year fraction to the expiry INSTANT.

    Calendar time, not trading time, and said so: a trading-day convention
    would be defensible too and would give different Greeks. What matters is
    that one convention is used consistently and is recorded with the output.
    Returns None once expiry has passed -- an expired option has no implied
    vol, and returning a tiny positive T would manufacture one.
    """
    import datetime

    try:
        now = datetime.datetime.fromisoformat(now_iso)
        parts = [int(p) for p in expiry_time.split(":")]
        while len(parts) < 3:
            parts.append(0)
        expiry_date = datetime.date.fromisoformat(expiry_iso)
        expiry = datetime.datetime.combine(
            expiry_date, datetime.time(*parts[:3]), tzinfo=now.tzinfo)
    except (TypeError, ValueError):
        return None
    seconds = (expiry - now).total_seconds()
    if seconds <= 0:
        return None
    return seconds / (DAYS_PER_YEAR * 24.0 * 3600.0)
