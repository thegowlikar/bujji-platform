"""Options analytics: IV, Greeks and skew, DERIVED from observed prices.

WHY THIS PACKAGE EXISTS. The FYERS option chain returns ltp, bid, ask,
volume and open interest -- and no implied volatility. For a premium seller
that is the central gap: delta-based strike selection, vega exposure and
gamma risk near expiry all had no input at all.

WHAT MAKES THIS LEGITIMATE RATHER THAN FABRICATION. Implied volatility is
not a forecast: it is BY DEFINITION the volatility that reproduces the price
the market actually printed. Inverting an observed price is a transform of
observation, not an invention. And the forward it is inverted against is
itself recovered from the same chain by put-call parity, so no interest rate,
dividend yield or carry model is assumed anywhere.

WHAT IT STILL IMPORTS is a model -- European exercise, lognormal forward,
constant vol -- which for NIFTY's European cash-settled options is a genuine
fit. Every value carries `value_class="DERIVED"` and the model name, so a
consumer can never mistake it for an observation like the LTP beside it.
"""
from .black76 import Greeks, greeks, intrinsic, norm_cdf, norm_pdf, price, time_to_expiry_years
from .engine import (BASIS_LTP, BASIS_MID, VALUE_CLASS_DERIVED, ContractAnalytics,
                     ExpiryAnalytics, SkewSummary, analyse_expiry, build_skew)
from .forward import ForwardEstimate, estimate_forward
from .implied import ImpliedVol, implied_volatility

__all__ = [
    "Greeks", "greeks", "price", "intrinsic", "norm_cdf", "norm_pdf",
    "time_to_expiry_years", "ForwardEstimate", "estimate_forward",
    "ImpliedVol", "implied_volatility", "ContractAnalytics", "ExpiryAnalytics",
    "SkewSummary", "analyse_expiry", "build_skew",
    "BASIS_MID", "BASIS_LTP", "VALUE_CLASS_DERIVED",
]
