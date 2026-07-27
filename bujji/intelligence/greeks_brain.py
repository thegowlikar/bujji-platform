"""Greeks Brain — Market Intelligence Core.

Answers: right now, how sensitive is this SHORT straddle position to a
move in spot (delta), a change in that sensitivity itself (gamma), the
passage of time (theta), and a change in implied volatility (vega)?

METHOD
------
Standard closed-form Black-Scholes Greeks (delta, gamma, theta, vega),
computed per leg (CE, PE) for a LONG position in that leg -- textbook
convention -- then combined into POSITION Greeks for the actual SHORT
straddle BUJJI holds: position_x = -(leg_x_ce + leg_x_pe).

Reuses `_norm_cdf`, `_norm_pdf`, `_bs_price`, and `_bs_vega` from the
Volatility Brain (already validated there via a solver round-trip check)
-- only delta, gamma, and theta are new formulas here, added following
the exact same textbook Black-Scholes derivation, and sanity-checked
against known ATM values (delta ~ +-0.5, positive gamma, negative
per-leg theta) before trusting them on real data.

Theta is reported PER CALENDAR DAY (raw annualized theta / 365) and vega
PER 1% IV CHANGE (raw annualized vega / 100) -- the units a trader
actually reasons in, not the raw per-year/per-100%-vol-point outputs of
the formulas.

DATA REALITY: requires a known IV (from the Volatility Brain or another
reliable source) and a positive time-to-expiry. Without either, there is
no legitimate Greeks calculation to report -- this brain refuses to
guess and returns UNKNOWN/INSUFFICIENT instead.
"""
from __future__ import annotations

import math
from typing import Optional

from ..core.clock import now_ist
from ..core.enums import OptionType
from .models import DataQuality, GreeksExposure, GreeksReading
from .volatility_brain import _bs_vega, _norm_cdf, _norm_pdf

EXPOSURE_NEUTRAL_THRESHOLD = 0.15  # |position_delta| below this -> DELTA_NEUTRAL.


def _bs_delta(spot: float, strike: float, t_years: float, r: float, sigma: float,
              option_type: OptionType) -> float:
    d1 = (math.log(spot / strike) + (r + 0.5 * sigma ** 2) * t_years) / (sigma * math.sqrt(t_years))
    return _norm_cdf(d1) if option_type is OptionType.CE else _norm_cdf(d1) - 1.0


def _bs_gamma(spot: float, strike: float, t_years: float, r: float, sigma: float) -> float:
    d1 = (math.log(spot / strike) + (r + 0.5 * sigma ** 2) * t_years) / (sigma * math.sqrt(t_years))
    return _norm_pdf(d1) / (spot * sigma * math.sqrt(t_years))


def _bs_theta(spot: float, strike: float, t_years: float, r: float, sigma: float,
              option_type: OptionType) -> float:
    d1 = (math.log(spot / strike) + (r + 0.5 * sigma ** 2) * t_years) / (sigma * math.sqrt(t_years))
    d2 = d1 - sigma * math.sqrt(t_years)
    term1 = -(spot * _norm_pdf(d1) * sigma) / (2 * math.sqrt(t_years))
    if option_type is OptionType.CE:
        term2 = -r * strike * math.exp(-r * t_years) * _norm_cdf(d2)
    else:
        term2 = r * strike * math.exp(-r * t_years) * _norm_cdf(-d2)
    return term1 + term2  # Annualized (per year).


class GreeksBrain:
    """Stateless: call `analyze(...)` with real spot, strike, a known IV
    (per leg or a shared straddle IV), and time-to-expiry. Never mutates
    anything, never talks to a broker, never decides whether to trade."""

    def analyze(
        self,
        spot: float,
        strike: float,
        t_years: float,
        iv_ce: Optional[float],
        iv_pe: Optional[float],
        risk_free_rate: float = 0.065,
    ) -> GreeksReading:
        as_of = now_ist()

        if t_years <= 0:
            return self._unknown(as_of, "invalid_time: t_years must be positive")
        if iv_ce is None or iv_pe is None:
            return self._unknown(as_of, "missing_iv: both iv_ce and iv_pe are required")
        if iv_ce <= 0 or iv_pe <= 0:
            return self._unknown(as_of, "invalid_iv: iv must be positive")
        if spot <= 0 or strike <= 0:
            return self._unknown(as_of, "invalid_inputs: spot and strike must be positive")

        delta_ce = _bs_delta(spot, strike, t_years, risk_free_rate, iv_ce, OptionType.CE)
        delta_pe = _bs_delta(spot, strike, t_years, risk_free_rate, iv_pe, OptionType.PE)
        gamma_ce = _bs_gamma(spot, strike, t_years, risk_free_rate, iv_ce)
        gamma_pe = _bs_gamma(spot, strike, t_years, risk_free_rate, iv_pe)
        theta_ce = _bs_theta(spot, strike, t_years, risk_free_rate, iv_ce, OptionType.CE) / 365.0
        theta_pe = _bs_theta(spot, strike, t_years, risk_free_rate, iv_pe, OptionType.PE) / 365.0
        vega_ce = _bs_vega(spot, strike, t_years, risk_free_rate, iv_ce) / 100.0
        vega_pe = _bs_vega(spot, strike, t_years, risk_free_rate, iv_pe) / 100.0

        position_delta = -(delta_ce + delta_pe)
        position_gamma = -(gamma_ce + gamma_pe)
        position_theta = -(theta_ce + theta_pe)
        position_vega = -(vega_ce + vega_pe)

        exposure = self._classify_exposure(position_delta)

        evidence = {
            "spot": spot, "strike": strike, "t_years": round(t_years, 5),
            "iv_ce": round(iv_ce, 4), "iv_pe": round(iv_pe, 4),
        }

        return GreeksReading(
            delta_ce=round(delta_ce, 4), delta_pe=round(delta_pe, 4),
            gamma_ce=round(gamma_ce, 6), gamma_pe=round(gamma_pe, 6),
            theta_ce_per_day=round(theta_ce, 3), theta_pe_per_day=round(theta_pe, 3),
            vega_ce_per_pct=round(vega_ce, 3), vega_pe_per_pct=round(vega_pe, 3),
            position_delta=round(position_delta, 4), position_gamma=round(position_gamma, 6),
            position_theta_per_day=round(position_theta, 3),
            position_vega_per_pct=round(position_vega, 3),
            exposure=exposure, confidence=1.0, data_quality=DataQuality.SUFFICIENT,
            evidence=evidence,
            reason=f"position_delta {position_delta:.4f} -> {exposure.value}",
            as_of=as_of,
        )

    @staticmethod
    def _classify_exposure(position_delta: float) -> GreeksExposure:
        if abs(position_delta) < EXPOSURE_NEUTRAL_THRESHOLD:
            return GreeksExposure.DELTA_NEUTRAL
        return GreeksExposure.NET_LONG_EXPOSURE if position_delta > 0 else GreeksExposure.NET_SHORT_EXPOSURE

    @staticmethod
    def _unknown(as_of, reason: str) -> GreeksReading:
        return GreeksReading(
            delta_ce=None, delta_pe=None, gamma_ce=None, gamma_pe=None,
            theta_ce_per_day=None, theta_pe_per_day=None,
            vega_ce_per_pct=None, vega_pe_per_pct=None,
            position_delta=None, position_gamma=None,
            position_theta_per_day=None, position_vega_per_pct=None,
            exposure=GreeksExposure.UNKNOWN, confidence=0.0,
            data_quality=DataQuality.INSUFFICIENT, reason=reason, as_of=as_of,
        )
