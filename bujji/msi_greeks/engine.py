"""Greeks Intelligence -- Phase 15E, engine.

Reuses the SAME permanent, validated Black-Scholes math already
imported (but never actually CALLED -- confirmed by source inspection:
`_bs_delta`/`_bs_gamma`/`_bs_theta`/`_bs_vega` appear in
`msi_volatility_structure/engine.py` only in an import statement and a
docstring, with zero real call sites) into the live MSI Volatility
Structure bridge: `solve_implied_volatility`/`_bs_vega`
(bujji.intelligence.volatility_brain) and `_bs_delta`/`_bs_gamma`/
`_bs_theta` (bujji.intelligence.greeks_brain). Neither function is
modified, forked, or reimplemented here -- same reuse discipline as
`msi_volatility_structure/engine.py`'s own documented precedent.

Each leg is solved INDEPENDENTLY (unlike `derive_iv_and_expected_move`'s
both-or-neither discipline for a single straddle IV average) -- a CE
leg failing to solve must never suppress a PE leg's real, independently
computable Greeks, and vice versa. UNKNOWN never contaminates a leg
that genuinely has enough evidence.
"""
from __future__ import annotations

from typing import Optional

from bujji.core.enums import OptionType
from bujji.intelligence.greeks_brain import _bs_delta, _bs_gamma, _bs_theta
from bujji.intelligence.volatility_brain import _bs_vega, solve_implied_volatility

from .models import GreeksAssessment, GreeksLegAssessment

DEFAULT_RISK_FREE_RATE = 0.065  # Same default as legacy VolatilityBrain/GreeksBrain and msi_volatility_structure.config.


def _assess_leg(
    premium: Optional[float], spot: Optional[float], strike: Optional[float], t_years: Optional[float],
    risk_free_rate: float, option_type: OptionType,
) -> GreeksLegAssessment:
    if premium is None:
        return GreeksLegAssessment(False, "missing_premium", None, None, None, None, None)
    if spot is None or spot <= 0:
        return GreeksLegAssessment(False, "missing_or_invalid_spot", None, None, None, None, None)
    if strike is None or strike <= 0:
        return GreeksLegAssessment(False, "missing_or_invalid_strike", None, None, None, None, None)
    if t_years is None or t_years <= 0:
        return GreeksLegAssessment(False, "missing_or_invalid_time_to_expiry", None, None, None, None, None)

    iv = solve_implied_volatility(premium, spot, strike, t_years, risk_free_rate, option_type)
    if iv is None:
        return GreeksLegAssessment(False, "iv_unsolvable: premium below intrinsic or solver did not converge",
                                    None, None, None, None, None)

    delta = _bs_delta(spot, strike, t_years, risk_free_rate, iv, option_type)
    gamma = _bs_gamma(spot, strike, t_years, risk_free_rate, iv)
    theta = _bs_theta(spot, strike, t_years, risk_free_rate, iv, option_type) / 365.0
    vega = _bs_vega(spot, strike, t_years, risk_free_rate, iv) / 100.0

    return GreeksLegAssessment(
        True, None, round(iv, 4), round(delta, 4), round(gamma, 6), round(theta, 3), round(vega, 3),
    )


def assess_atm_greeks(
    spot: Optional[float], strike: Optional[float], t_years: Optional[float],
    ce_premium: Optional[float], pe_premium: Optional[float], timestamp: str,
    risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
) -> GreeksAssessment:
    """Pure, stateless, no wall-clock read (timestamp is supplied by the
    caller, mirroring assess_volatility_structure's own convention).
    Never raises -- an unsolvable leg degrades to
    GreeksLegAssessment(available=False, ...), never a fabricated value."""
    ce = _assess_leg(ce_premium, spot, strike, t_years, risk_free_rate, OptionType.CE)
    pe = _assess_leg(pe_premium, spot, strike, t_years, risk_free_rate, OptionType.PE)
    return GreeksAssessment(
        timestamp=timestamp, spot=spot, strike=strike, t_years=t_years, risk_free_rate=risk_free_rate,
        ce=ce, pe=pe, provenance="msi_greeks.engine.assess_atm_greeks",
    )
