"""Volatility Brain — Market Intelligence Core.

Answers: is implied volatility rich or cheap relative to what the
underlying is actually realizing (the core "does premium-selling have an
edge today" question), and what move does the market's own pricing imply
between now and expiry?

DATA REALITY (stated up front, not discovered later): IV rank and IV
percentile need weeks/months of historical implied volatility. FYERS
serves no historical data for expired option contracts (verified live
during this session's real-data backtesting -- every expired-contract
symbol tested returned "Invalid symbol provided", at every resolution,
including daily). Until either real history accumulates week over week or
an external historical-options vendor is integrated (both already
identified as open items elsewhere in this codebase's research), IV
rank/percentile are structurally unavailable -- this brain reports them as
None, explicitly, rather than fabricating a number from an assumed
"typical" range.

METHOD
------
1. Solve implied volatility from each leg's real market premium via
   Newton-Raphson on the Black-Scholes price (falling back to bisection
   if Newton-Raphson fails to converge -- a single mis-fed premium must
   never crash the brain or silently return a wrong number).
2. Compute realized volatility from real spot candles (same primitives as
   the Regime Brain -- log returns, stdev -- annualized here since IV is
   quoted annualized).
3. Richness = IV / realized vol. This is the textbook volatility risk
   premium framing: options usually price in more movement than actually
   happens, and how much more is exactly what determines whether selling
   premium has a statistical edge today.
4. Expected move (1-sigma, to expiry) = spot * IV * sqrt(time_to_expiry).

CALIBRATION NOTE (same discipline as the Regime Brain): the richness
thresholds below are a documented first pass. There is not yet enough
real history to calibrate them statistically -- treat as a starting
point, not a validated conclusion.
"""
from __future__ import annotations

import math
from typing import Optional

from ..core.enums import OptionType
from ..core.models import Candle
from .context import IntelligenceContext
from .evidence import wrap_evidence
from .models import DataQuality, Richness, VolatilityReading

MIN_CANDLES_FOR_REALIZED_VOL = 6  # Same floor as the Regime Brain.
TRADING_DAYS_PER_YEAR = 252
CANDLES_PER_DAY = 75  # 5-min candles, 09:15-15:30 IST session.

RICHNESS_RICH_THRESHOLD = 1.15   # IV >= 115% of realized vol -> rich.
RICHNESS_CHEAP_THRESHOLD = 0.85  # IV <= 85% of realized vol -> cheap.

_IV_MIN, _IV_MAX = 0.01, 3.00  # 1% to 300% -- sane bounds for the solver.
_MAX_NEWTON_ITER = 50
_TOLERANCE = 1e-6


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _bs_price(spot: float, strike: float, t_years: float, r: float, sigma: float,
             option_type: OptionType) -> float:
    if t_years <= 0 or sigma <= 0:
        intrinsic = (max(0.0, spot - strike) if option_type is OptionType.CE
                    else max(0.0, strike - spot))
        return intrinsic
    d1 = (math.log(spot / strike) + (r + 0.5 * sigma ** 2) * t_years) / (sigma * math.sqrt(t_years))
    d2 = d1 - sigma * math.sqrt(t_years)
    if option_type is OptionType.CE:
        return spot * _norm_cdf(d1) - strike * math.exp(-r * t_years) * _norm_cdf(d2)
    return strike * math.exp(-r * t_years) * _norm_cdf(-d2) - spot * _norm_cdf(-d1)


def _bs_vega(spot: float, strike: float, t_years: float, r: float, sigma: float) -> float:
    if t_years <= 0 or sigma <= 0:
        return 0.0
    d1 = (math.log(spot / strike) + (r + 0.5 * sigma ** 2) * t_years) / (sigma * math.sqrt(t_years))
    return spot * _norm_pdf(d1) * math.sqrt(t_years)


def solve_implied_volatility(
    market_price: float, spot: float, strike: float, t_years: float, r: float,
    option_type: OptionType,
) -> Optional[float]:
    """Solve for sigma given a real market premium. Returns None (never a
    guess) if the price is below intrinsic value (an arbitrage-violating
    or stale quote), if time-to-expiry is non-positive, or if neither
    Newton-Raphson nor the bisection fallback converges within bounds.
    """
    if t_years <= 0 or market_price <= 0:
        return None
    intrinsic = (max(0.0, spot - strike) if option_type is OptionType.CE
                else max(0.0, strike - spot))
    if market_price < intrinsic:
        return None  # Below intrinsic -- not a solvable/trustworthy quote.

    sigma = 0.20  # Reasonable starting guess for index options.
    for _ in range(_MAX_NEWTON_ITER):
        price = _bs_price(spot, strike, t_years, r, sigma, option_type)
        vega = _bs_vega(spot, strike, t_years, r, sigma)
        diff = price - market_price
        if abs(diff) < _TOLERANCE:
            return sigma if _IV_MIN <= sigma <= _IV_MAX else None
        if vega < 1e-8:
            break  # Vega too flat -- Newton-Raphson unreliable here, fall back.
        sigma -= diff / vega
        if sigma <= 0 or sigma > _IV_MAX * 2:
            break  # Diverged -- fall back to bisection instead of chasing it.

    return _solve_iv_bisection(market_price, spot, strike, t_years, r, option_type)


def compute_expected_move(spot: float, iv: float, t_years: float) -> float:
    """1-sigma expected move (points) to expiry, given a solved IV.
    EXTRACTED (Series 88, additive, zero behavior change to
    VolatilityBrain.analyze(), which keeps its own identical inline
    calculation) so bujji.msi_volatility_structure can reuse this exact
    formula rather than re-deriving it independently."""
    return spot * iv * math.sqrt(max(t_years, 0.0))


def _solve_iv_bisection(market_price, spot, strike, t_years, r, option_type) -> Optional[float]:
    lo, hi = _IV_MIN, _IV_MAX
    price_lo = _bs_price(spot, strike, t_years, r, lo, option_type)
    price_hi = _bs_price(spot, strike, t_years, r, hi, option_type)
    if not (price_lo <= market_price <= price_hi):
        return None  # Market price outside what any sane vol in range can produce.
    for _ in range(100):
        mid = (lo + hi) / 2
        price_mid = _bs_price(spot, strike, t_years, r, mid, option_type)
        if abs(price_mid - market_price) < _TOLERANCE:
            return mid
        if price_mid < market_price:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


class VolatilityBrain:
    """Stateless: call `analyze(...)` with today's real spot candles and
    the resolved straddle's real CE/PE premiums. Never mutates anything,
    never talks to a broker, never decides whether to trade."""

    def analyze(
        self,
        spot_candles: list[Candle],
        spot: float,
        strike: float,
        t_years: float,
        ce_premium: float,
        pe_premium: float,
        risk_free_rate: float = 0.065,
        *,
        context: IntelligenceContext,
    ) -> VolatilityReading:
        now = context.as_of_time
        n = len(spot_candles)

        if n < MIN_CANDLES_FOR_REALIZED_VOL:
            return VolatilityReading(
                iv_ce=None, iv_pe=None, iv_average=None, realized_vol=None,
                richness=Richness.UNKNOWN, richness_ratio=None,
                expected_move_points=None, expected_move_pct=None,
                confidence=0.0, data_quality=DataQuality.INSUFFICIENT,
                reason=f"insufficient_data: {n} candle(s), need >= {MIN_CANDLES_FOR_REALIZED_VOL}",
                as_of=now,
            )

        iv_ce = solve_implied_volatility(ce_premium, spot, strike, t_years,
                                         risk_free_rate, OptionType.CE)
        iv_pe = solve_implied_volatility(pe_premium, spot, strike, t_years,
                                         risk_free_rate, OptionType.PE)
        # For a STRADDLE, an average of just one solved leg is not a
        # trustworthy representation of the position's implied volatility
        # -- require BOTH legs to solve, or report iv_average as None
        # (never silently average whatever happened to solve). This is
        # the same both-or-neither discipline already enforced on
        # ce_contract/pe_contract in position_codec.py earlier this
        # session -- caught here by a test that deliberately fed one
        # unsolvable leg.
        iv_average = (iv_ce + iv_pe) / 2 if iv_ce is not None and iv_pe is not None else None

        realized_vol = self._annualized_realized_vol(spot_candles)

        evidence = {
            "iv_ce_raw": round(iv_ce, 4) if iv_ce is not None else None,
            "iv_pe_raw": round(iv_pe, 4) if iv_pe is not None else None,
            "ce_premium": ce_premium,
            "pe_premium": pe_premium,
            "spot": spot,
            "strike": strike,
            "t_years": round(t_years, 5),
            "candles_used_for_rv": n,
        }

        if iv_average is None or realized_vol is None or realized_vol <= 0:
            return VolatilityReading(
                iv_ce=iv_ce, iv_pe=iv_pe, iv_average=iv_average,
                realized_vol=realized_vol, richness=Richness.UNKNOWN,
                richness_ratio=None, expected_move_points=None, expected_move_pct=None,
                confidence=0.0, data_quality=DataQuality.INSUFFICIENT,
                evidence=evidence, evidence_lineage=wrap_evidence(evidence, context=context),
                reason="iv_or_realized_vol_unsolvable: cannot compute richness without both",
                as_of=now,
            )

        richness_ratio = iv_average / realized_vol
        richness, reason, confidence = self._classify_richness(richness_ratio)

        expected_move_points = spot * iv_average * math.sqrt(max(t_years, 0.0))
        expected_move_pct = (expected_move_points / spot * 100.0) if spot > 0 else None

        evidence["richness_ratio"] = round(richness_ratio, 4)

        return VolatilityReading(
            iv_ce=iv_ce, iv_pe=iv_pe, iv_average=round(iv_average, 4),
            realized_vol=round(realized_vol, 4),
            richness=richness, richness_ratio=round(richness_ratio, 4),
            expected_move_points=round(expected_move_points, 2),
            expected_move_pct=round(expected_move_pct, 3) if expected_move_pct else None,
            confidence=confidence, data_quality=DataQuality.SUFFICIENT,
            evidence=evidence, evidence_lineage=wrap_evidence(evidence, context=context),
            reason=reason, as_of=now,
        )

    @staticmethod
    def _annualized_realized_vol(candles: list[Candle]) -> Optional[float]:
        candles = sorted(candles, key=lambda c: c.timestamp)
        closes = [c.close for c in candles]
        returns = []
        for i in range(1, len(closes)):
            if closes[i - 1] > 0 and closes[i] > 0:
                returns.append(math.log(closes[i] / closes[i - 1]))
        if len(returns) < 2:
            return None
        mean = sum(returns) / len(returns)
        var = sum((v - mean) ** 2 for v in returns) / (len(returns) - 1)
        per_candle_vol = math.sqrt(var)
        # Annualize: per-candle vol * sqrt(candles per year).
        candles_per_year = CANDLES_PER_DAY * TRADING_DAYS_PER_YEAR
        return per_candle_vol * math.sqrt(candles_per_year)

    @staticmethod
    def _classify_richness(ratio: float) -> tuple[Richness, str, float]:
        if ratio >= RICHNESS_RICH_THRESHOLD:
            confidence = min(1.0, 0.5 + 0.5 * min(1.0, (ratio - RICHNESS_RICH_THRESHOLD) / RICHNESS_RICH_THRESHOLD))
            return (Richness.IV_RICH,
                    f"IV/RV ratio {ratio:.3f} >= threshold {RICHNESS_RICH_THRESHOLD}",
                    round(confidence, 4))
        if ratio <= RICHNESS_CHEAP_THRESHOLD:
            confidence = min(1.0, 0.5 + 0.5 * min(1.0, (RICHNESS_CHEAP_THRESHOLD - ratio) / RICHNESS_CHEAP_THRESHOLD))
            return (Richness.IV_CHEAP,
                    f"IV/RV ratio {ratio:.3f} <= threshold {RICHNESS_CHEAP_THRESHOLD}",
                    round(confidence, 4))
        return (Richness.IV_FAIR,
                f"IV/RV ratio {ratio:.3f} between {RICHNESS_CHEAP_THRESHOLD} and {RICHNESS_RICH_THRESHOLD}",
                0.5)
