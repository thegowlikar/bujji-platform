"""Realized volatility estimation and the HAR-RV forecaster.

WHY HAR AND NOT GARCH. Corsi's Heterogeneous AutoRegressive model regresses
tomorrow's realized volatility on its own daily, weekly and monthly averages.
The economic story is that traders act on different horizons -- a market maker,
a swing trader and a pension fund respond to different windows -- so volatility
carries components at each. The empirical story is blunter: across indices and
horizons the published comparisons put HAR-RV ahead of ARCH-family models,
with reported forecast-error reductions in the 35-40% range against
GARCH(1,1), largely because it consumes high-frequency data instead of
squeezing daily returns.

WHY IT MATTERS TO BUJJI SPECIFICALLY. The documented cause of Indian retail
option losses is not exotic. It is paying more implied volatility than the
realised volatility that follows. You cannot notice you are overpaying without
a defensible forecast of what realised volatility will actually be. That is
this file's entire job -- and note it is a FORECAST, so it is an input to a
comparison, never a signal by itself.

WHAT THIS FILE REFUSES TO DO. HAR needs a history of daily realized
volatilities. Bujji currently has one session. So `fit` refuses below a stated
minimum rather than returning coefficients from a handful of points, because a
regression on five observations produces numbers that look exactly like a
regression on five hundred.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

# Andersen & Bollerslev's practical compromise. Finer sampling measures the
# bid-ask bounce; coarser throws away information.
RV_SAMPLING_SECONDS = 300

# HAR's three horizons, in trading days.
HORIZON_DAILY = 1
HORIZON_WEEKLY = 5
HORIZON_MONTHLY = 22

# Below this the coefficients are noise wearing a lab coat. HAR has four
# parameters; fitting them on a few dozen points yields an R-squared that
# flatters itself and forecasts that do not survive contact with next month.
MIN_OBSERVATIONS_TO_FIT = 60

REFUSE_INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
REFUSE_SINGULAR = "SINGULAR_DESIGN_MATRIX"
REFUSE_NON_POSITIVE = "NON_POSITIVE_VOLATILITY"


def realized_variance(prices: Sequence[float]) -> Optional[float]:
    """Sum of squared log returns. Variance, not volatility -- take the root
    only when you mean to, and never add volatilities."""
    if len(prices) < 2:
        return None
    total = 0.0
    n = 0
    for a, b in zip(prices, prices[1:]):
        if a is None or b is None or a <= 0 or b <= 0:
            continue
        r = math.log(b / a)
        total += r * r
        n += 1
    return total if n else None


def bipower_variation(prices: Sequence[float]) -> Optional[float]:
    """Barndorff-Nielsen & Shephard's jump-robust variance estimator.

    Products of ADJACENT absolute returns rather than squares. A single large
    jump inflates one term instead of dominating the sum, so comparing this
    with realized variance separates continuous diffusion from jumps. The
    distinction matters for options: a jump and a grind to the same place are
    the same realized variance and very different things to be short.
    """
    rets = []
    for a, b in zip(prices, prices[1:]):
        if a is None or b is None or a <= 0 or b <= 0:
            continue
        rets.append(abs(math.log(b / a)))
    if len(rets) < 2:
        return None
    mu1 = math.sqrt(2.0 / math.pi)
    scale = 1.0 / (mu1 * mu1)
    return scale * sum(x * y for x, y in zip(rets, rets[1:]))


def jump_component(prices: Sequence[float]) -> Dict[str, Any]:
    """Realized variance minus bipower variation, floored at zero.

    Floored because the difference is an estimator and can go negative on
    noise; a negative jump component is a measurement artefact, not a market
    that jumped backwards.
    """
    rv = realized_variance(prices)
    bv = bipower_variation(prices)
    if rv is None or bv is None:
        return {"realized_variance": rv, "bipower_variation": bv,
                "jump": None, "status": "INSUFFICIENT_DATA"}
    jump = max(0.0, rv - bv)
    return {
        "realized_variance": rv,
        "bipower_variation": bv,
        "jump": jump,
        "jump_share": (jump / rv) if rv > 0 else None,
        "note": ("jump floored at zero: the estimator difference can be "
                 "negative on noise, which is an artefact rather than a fact"),
    }


def _solve(matrix: List[List[float]], rhs: List[float]) -> Optional[List[float]]:
    """Gaussian elimination with partial pivoting. Pure Python: numpy is not
    installed on this host and a four-parameter solve does not justify a
    dependency in an offline research path."""
    n = len(matrix)
    a = [row[:] + [rhs[i]] for i, row in enumerate(matrix)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(a[r][col]))
        if abs(a[pivot][col]) < 1e-12:
            return None
        a[col], a[pivot] = a[pivot], a[col]
        for r in range(n):
            if r == col:
                continue
            f = a[r][col] / a[col][col]
            for c in range(col, n + 1):
                a[r][c] -= f * a[col][c]
    return [a[i][n] / a[i][i] for i in range(n)]


@dataclass
class HARModel:
    intercept: float
    beta_daily: float
    beta_weekly: float
    beta_monthly: float
    observations: int
    r_squared: float
    in_sample_only: bool = True

    def forecast(self, rv_daily: float, rv_weekly: float,
                 rv_monthly: float) -> Dict[str, Any]:
        value = (self.intercept + self.beta_daily * rv_daily
                 + self.beta_weekly * rv_weekly
                 + self.beta_monthly * rv_monthly)
        return {
            "forecast_rv": max(0.0, value),
            "clipped_at_zero": value < 0,
            "inputs": {"daily": rv_daily, "weekly": rv_weekly,
                       "monthly": rv_monthly},
            "observations_fitted": self.observations,
            "r_squared_in_sample": self.r_squared,
            "limit": ("in-sample R-squared describes the fit, not forecast "
                      "skill. Only out-of-sample error does, and this model "
                      "has not been evaluated out of sample here."),
        }


def fit_har(daily_rv: Sequence[float]) -> Dict[str, Any]:
    """Fit HAR-RV on a series of daily realized volatilities.

    Refuses below MIN_OBSERVATIONS_TO_FIT. Bujji has one session, so this
    refuses today -- which is the correct output, not a gap to work around.
    """
    series = [x for x in daily_rv if x is not None and x > 0]
    if len(series) < MIN_OBSERVATIONS_TO_FIT + HORIZON_MONTHLY:
        return {
            "model": None,
            "refused": REFUSE_INSUFFICIENT_HISTORY,
            "detail": (
                f"{len(series)} usable daily observations; HAR needs at least "
                f"{MIN_OBSERVATIONS_TO_FIT} fitting rows after a "
                f"{HORIZON_MONTHLY}-day warmup. Fitting four parameters on "
                f"less produces coefficients indistinguishable in appearance "
                f"from good ones."),
            "have": len(series),
            "need": MIN_OBSERVATIONS_TO_FIT + HORIZON_MONTHLY,
        }

    rows, targets = [], []
    for t in range(HORIZON_MONTHLY, len(series) - 1):
        d = series[t]
        w = sum(series[t - HORIZON_WEEKLY + 1:t + 1]) / HORIZON_WEEKLY
        m = sum(series[t - HORIZON_MONTHLY + 1:t + 1]) / HORIZON_MONTHLY
        rows.append([1.0, d, w, m])
        targets.append(series[t + 1])

    k = 4
    xtx = [[sum(r[i] * r[j] for r in rows) for j in range(k)] for i in range(k)]
    xty = [sum(r[i] * y for r, y in zip(rows, targets)) for i in range(k)]
    beta = _solve(xtx, xty)
    if beta is None:
        return {"model": None, "refused": REFUSE_SINGULAR,
                "detail": "design matrix is singular; horizons are collinear "
                          "on this sample and the coefficients are not identified"}

    mean_y = sum(targets) / len(targets)
    ss_tot = sum((y - mean_y) ** 2 for y in targets)
    ss_res = sum((y - sum(b * x for b, x in zip(beta, r))) ** 2
                 for r, y in zip(rows, targets))
    r2 = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0.0

    return {
        "model": HARModel(intercept=beta[0], beta_daily=beta[1],
                          beta_weekly=beta[2], beta_monthly=beta[3],
                          observations=len(rows), r_squared=r2),
        "refused": None,
        "basis": ("Corsi's HAR-RV: tomorrow's realized volatility on its own "
                  "daily, weekly and monthly averages"),
    }


def variance_risk_premium(implied_vol: Optional[float],
                          realized_vol: Optional[float],
                          *, implied_ts: Optional[float] = None,
                          realized_window_end_ts: Optional[float] = None
                          ) -> Dict[str, Any]:
    """Implied variance minus realized variance, with both timestamps kept.

    THE DIRECTION OF TIME IS THE WHOLE POINT. A premium exists only if the
    implied figure was observed BEFORE the realized window it is compared
    against. Comparing today's implied with volatility already realised is not
    a premium, it is a look-ahead dressed as one -- so the timestamps are
    required inputs and a backwards pair is named as such.
    """
    if implied_vol is None or realized_vol is None:
        return {"vrp": None, "status": "INSUFFICIENT_DATA",
                "detail": "both an implied and a realized figure are required"}
    if implied_vol <= 0 or realized_vol < 0:
        return {"vrp": None, "status": REFUSE_NON_POSITIVE}

    ordering = "UNKNOWN"
    if implied_ts is not None and realized_window_end_ts is not None:
        ordering = ("IMPLIED_PRECEDES_REALIZED"
                    if implied_ts <= realized_window_end_ts
                    else "BACKWARDS_NOT_A_PREMIUM")

    return {
        "vrp": implied_vol ** 2 - realized_vol ** 2,
        "implied_vol": implied_vol,
        "realized_vol": realized_vol,
        "ordering": ordering,
        "usable_as_premium": ordering == "IMPLIED_PRECEDES_REALIZED",
        "limit": ("one pair is an observation, not a premium. A premium is a "
                  "distribution over many non-overlapping windows, and its "
                  "sign in Indian index options is not established by this "
                  "codebase or by any paper it has read."),
    }
