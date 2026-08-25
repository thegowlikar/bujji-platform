"""The evaluation contract. Its job is to REJECT ideas, not to bless them.

A result may not be called promising until every gate here passes. The gates
are deliberately hostile: the default outcome of an experiment is INSUFFICIENT
EVIDENCE, and it takes work to leave that state.

WHY SO HOSTILE. Bailey and Lopez de Prado's central point is that a Sharpe
ratio computed after N trials is not the Sharpe ratio you get next year: with
enough attempts, an impressive backtest is the expected outcome of chance
alone. So the number of trials is a required input here, not an optional one,
and reporting only the best configuration is itself a refusal.

WHAT THIS FILE CANNOT DO. It cannot make a P&L number honest. If the inputs
are LTP-only, no cost model rescues them: a fill is a transaction at a bid or
an ask, and last price is neither.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

VERDICT_INSUFFICIENT = "INSUFFICIENT_EVIDENCE"
VERDICT_REJECTED = "REJECTED"
VERDICT_NOT_REJECTED = "NOT_REJECTED"          # never "profitable"

# Gate codes.
GATE_NO_ACCEPTANCE_CRITERION = "NO_PREDEFINED_ACCEPTANCE_CRITERION"
GATE_INSUFFICIENT_OBSERVATIONS = "INSUFFICIENT_INDEPENDENT_OBSERVATIONS"
GATE_SINGLE_SESSION = "SINGLE_SESSION_EVIDENCE"
GATE_NO_COST_MODEL = "NO_EXECUTION_COST_MODEL"
GATE_LTP_ONLY_PNL = "PNL_FROM_LTP_WITHOUT_QUOTES"
GATE_NO_BASELINE = "NO_BASELINE_COMPARISON"
GATE_LEAKAGE = "LOOK_AHEAD_OR_SAME_DAY_LEAKAGE"
GATE_MULTIPLE_TESTING = "MULTIPLE_TESTING_UNCORRECTED"
GATE_NO_OOS = "NO_OUT_OF_SAMPLE_AFTER_FREEZE"
GATE_UNSTABLE = "UNSTABLE_ACROSS_CONDITIONS"
GATE_NO_TAIL_REPORT = "NO_TAIL_LOSS_REPORT"

# Minimum independent observations before any Sharpe-like statistic is quoted.
# Not a magic number: below this the standard error of a Sharpe estimate is so
# wide that the point estimate carries almost no information, and quoting it
# invites exactly the overconfidence the deflation is meant to remove.
MIN_INDEPENDENT_OBSERVATIONS = 100
MIN_DISTINCT_SESSIONS = 20


@dataclass
class ExperimentSpec:
    """Everything that must be declared BEFORE looking at the result."""
    name: str
    hypothesis: str
    dataset_fingerprint: str
    feature_version: str
    policy_version: str
    acceptance_criterion: Optional[str] = None      # predefined, or refuse
    trials_run: int = 1                             # N, for deflation
    baselines: List[str] = field(default_factory=list)
    cost_model: Optional[Dict[str, Any]] = None
    seed: Optional[int] = None
    notes: str = ""


@dataclass
class ExperimentResult:
    observations: int = 0
    distinct_sessions: int = 0
    sharpe: Optional[float] = None
    skew: float = 0.0
    kurtosis: float = 3.0
    max_drawdown: Optional[float] = None
    worst_observation: Optional[float] = None
    baseline_sharpes: Dict[str, float] = field(default_factory=dict)
    used_quotes_for_fills: bool = False
    used_ltp_for_fills: bool = False
    out_of_sample_after_freeze: bool = False
    leakage_checked: bool = False
    stability: Dict[str, Any] = field(default_factory=dict)


def expected_max_sharpe(trials: int, trial_sharpe_variance: float) -> float:
    """The Sharpe a *worthless* strategy is expected to show as the best of N.

    Two factors multiply, and dropping either one breaks the gate:

      1. The expected maximum of N draws from a standard normal, which grows
         slowly (roughly sqrt(2 ln N)) with the number of configurations tried.
      2. sqrt(V), the standard deviation of the Sharpe estimates ACROSS those
         trials -- the scale those draws actually live on.

    Omitting (2) leaves the threshold denominated in standard-normal units
    while the observed Sharpe is denominated in per-observation units. The
    comparison is then between two different quantities and the gate rejects
    everything, which looks strict and proves nothing.
    """
    trials = max(1, int(trials))
    if trials == 1:
        return 0.0
    euler = 0.5772156649
    z1 = _norm_ppf(1 - 1.0 / trials)
    z2 = _norm_ppf(1 - 1.0 / (trials * math.e))
    return math.sqrt(max(0.0, trial_sharpe_variance)) * (
        (1 - euler) * z1 + euler * z2)


def deflated_sharpe(sr: float, n_obs: int, trials: int,
                    skew: float = 0.0, kurtosis: float = 3.0,
                    trial_sharpe_variance: Optional[float] = None
                    ) -> Optional[float]:
    """Probability that an observed Sharpe exceeds what N trials would produce
    by chance, adjusted for non-normal returns.

    Follows Bailey & Lopez de Prado (2014). The intuition that matters more
    than the algebra: the benchmark a result must beat RISES with the number of
    configurations tried, so a strategy chosen from 200 variants must clear a
    far higher bar than one specified in advance.

    UNITS. `sr` must be a PER-OBSERVATION Sharpe, in the same periodicity as
    `n_obs`. Passing an annualised Sharpe with a daily `n_obs` inflates the
    result by roughly sqrt(252) and turns this gate into a rubber stamp.

    `trial_sharpe_variance` is the variance of the estimated Sharpe ratios
    across the trials actually run. When the caller does not supply it we fall
    back to 1/(n_obs - 1), the asymptotic variance of a Sharpe estimate under
    the null. That fallback assumes the trials were independent; correlated
    variants of one idea have a smaller spread, so the fallback is generous to
    the strategy, and a caller that has the real spread should pass it.
    """
    if n_obs < 2 or sr is None:
        return None
    if trial_sharpe_variance is None:
        trial_sharpe_variance = 1.0 / (n_obs - 1)
    sr0 = expected_max_sharpe(trials, trial_sharpe_variance)
    denom = math.sqrt(max(1e-12,
                          1 - skew * sr + ((kurtosis - 1) / 4.0) * sr ** 2))
    stat = (sr - sr0) * math.sqrt(n_obs - 1) / denom
    return _norm_cdf(stat)


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_ppf(p: float) -> float:
    """Inverse normal CDF, Acklam's rational approximation."""
    if p <= 0.0:
        return -math.inf
    if p >= 1.0:
        return math.inf
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
               ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
                ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q = p - 0.5
    r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / \
           (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


def evaluate(spec: ExperimentSpec, result: ExperimentResult) -> Dict[str, Any]:
    """Apply every gate. The verdict is never 'profitable'."""
    gates: List[Dict[str, str]] = []

    def fail(code, detail):
        gates.append({"code": code, "detail": detail})

    if not spec.acceptance_criterion:
        fail(GATE_NO_ACCEPTANCE_CRITERION,
             "no acceptance criterion was declared before the result was seen. "
             "A threshold chosen afterwards is a description of the result, "
             "not a test of it.")

    if result.observations < MIN_INDEPENDENT_OBSERVATIONS:
        fail(GATE_INSUFFICIENT_OBSERVATIONS,
             f"{result.observations} independent observations; "
             f"{MIN_INDEPENDENT_OBSERVATIONS} is the floor below which a "
             f"Sharpe estimate carries almost no information")

    if result.distinct_sessions < MIN_DISTINCT_SESSIONS:
        fail(GATE_SINGLE_SESSION,
             f"{result.distinct_sessions} distinct session(s); a result from "
             f"one or few days measures those days, not the method")

    if result.used_ltp_for_fills and not result.used_quotes_for_fills:
        fail(GATE_LTP_ONLY_PNL,
             "P&L was formed from last-traded price without bid/ask. A fill "
             "happens at a quote; LTP is neither side of one, and on this "
             "venue the measured median relative spread is ~0.49% with a p95 "
             "of ~9.5% -- large enough to invert a result")

    if not spec.cost_model:
        fail(GATE_NO_COST_MODEL,
             "no execution cost model: fees, spread, slippage, partial fills "
             "and latency are unmodelled")

    if not spec.baselines or not result.baseline_sharpes:
        fail(GATE_NO_BASELINE,
             "no baseline comparison; a number without a null is not a finding")

    if not result.leakage_checked:
        fail(GATE_LEAKAGE, "no look-ahead / same-day leakage check was run")

    if spec.trials_run > 1:
        dsr = deflated_sharpe(result.sharpe or 0.0, result.observations,
                              spec.trials_run, result.skew, result.kurtosis)
        if dsr is None or dsr < 0.95:
            fail(GATE_MULTIPLE_TESTING,
                 f"{spec.trials_run} trials; deflated Sharpe probability "
                 f"{dsr if dsr is None else round(dsr, 4)} does not clear 0.95")

    if not result.out_of_sample_after_freeze:
        fail(GATE_NO_OOS,
             "no out-of-sample evaluation after all policy choices were frozen")

    if result.worst_observation is None and result.max_drawdown is None:
        fail(GATE_NO_TAIL_REPORT,
             "no tail-loss reporting. For short-premium structures the tail is "
             "the strategy: an average is not a risk statement")

    if not result.stability:
        fail(GATE_UNSTABLE,
             "no stability evidence across dates, volatility regimes, expiries "
             "or universe changes")

    verdict = VERDICT_NOT_REJECTED if not gates else VERDICT_INSUFFICIENT
    return {
        "experiment": spec.name,
        "hypothesis": spec.hypothesis,
        "dataset_fingerprint": spec.dataset_fingerprint,
        "feature_version": spec.feature_version,
        "policy_version": spec.policy_version,
        "trials_run": spec.trials_run,
        "seed": spec.seed,
        "verdict": verdict,
        "failed_gates": gates,
        "deflated_sharpe_probability": (
            deflated_sharpe(result.sharpe or 0.0, result.observations,
                            spec.trials_run, result.skew, result.kurtosis)
            if result.sharpe is not None else None),
        "note": ("NOT_REJECTED is the strongest verdict available and means "
                 "only that this evidence failed to refute the hypothesis. It "
                 "is not a profitability claim and authorizes no trading."),
    }
