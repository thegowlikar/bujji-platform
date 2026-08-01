"""Capital Safety Governor — BUJJI Options OS v3, Numeric Risk Governor
Gate D.1.

PURPOSE: capital_check.assess_capital() (Gate B) answers "does THIS
trade's own margin fit within configured capital" -- a single-trade,
margin-only question. This module sits ABOVE that and answers a
different, account-level question: "even if this trade's own margin
checks out, is taking it safe for the WHOLE ACCOUNT right now" --
accounting for daily loss, drawdown, and account-wide risk
concentration, none of which capital_check.py or portfolio_limits.py
(exposure/position-count only, verified by reading both directly
before writing this module) currently consider.

capital_check.assess_capital() REMAINS THE SOLE ALLOW/VETO AUTHORITY
for margin-fit decisions -- this module is NOT a replacement, does not
import capital_check.py, and its own CapitalSafetyDecision is a
SEPARATE, ADDITIONAL gate a caller would consult alongside (not
instead of) the existing one. Nothing here executes a trade, places an
order, or connects to any broker/execution path -- every input is a
caller-supplied observed value or a pure projection over it.

BROKER-DISCONNECTED BY DESIGN: this module has no HttpCaller, no
Broker reference, no live data fetch of any kind. total_capital,
available_capital, used_margin, open_risk, reserved_risk, daily_pnl,
and consecutive_losses are all caller-OBSERVED values (the caller is
responsible for sourcing them from wherever they actually live --
Gate C's SimulatedMarginProvider/BrokerMarginRealityAdapter for
margin, the caller's own PnL/drawdown tracking for the rest, none of
which exists as a live-connected system in this codebase yet). This
module performs pure measurement and classification over whatever it
is given -- it invents no field it cannot be handed.

FIELDS DELIBERATELY NOT INVENTED: no volatility-adjusted risk, no VaR,
no ML-derived risk score, no correlation matrix. Every metric here is
a simple ratio of two caller-supplied or trivially-derived numbers,
matching this whole Governor's established "explainable, not a broker
replica, not a prediction engine" discipline from every prior Gate B/C
phase.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Callable, Optional, Tuple

Clock = Callable[[], datetime]

# --------------------------------------------------------------------- #
# Part 3 -- classification statuses
# --------------------------------------------------------------------- #

METRIC_NORMAL = "NORMAL"
METRIC_WARNING = "WARNING"
METRIC_BREACH = "BREACH"
METRIC_UNAVAILABLE = "UNAVAILABLE"

SAFETY_SAFE = "SAFE"
SAFETY_CAUTION = "CAUTION"
SAFETY_RESTRICTED = "RESTRICTED"
SAFETY_BLOCKED = "BLOCKED"


# --------------------------------------------------------------------- #
# Part 1 -- Capital Safety State Model
# --------------------------------------------------------------------- #

@dataclass(frozen=True)
class CapitalSafetySnapshot:
    """OBSERVED values only -- every field is either a real,
    caller-supplied figure or explicitly None (unavailable). This
    class performs NO derivation itself; derive_capital_metrics()
    below computes free_margin/drawdown_from_peak/etc. from it, kept
    deliberately separate so "what was observed" and "what was
    computed from it" can never be confused with each other.

    `open_risk` = sum of defined-risk max_loss across already-OPEN/
    PARTIALLY_OPEN positions (Gate B's DefinedRiskAssessment.max_loss,
    summed by the caller across the whole book -- this module does not
    re-derive it). `reserved_risk` = the same, for CONSTRUCTED
    (not-yet-filled) positions, matching portfolio_limits.py's own
    established "CONSTRUCTED counts toward its own limits" precedent
    (verified by reading that module directly before writing this
    one)."""

    total_capital: Optional[float]
    available_capital: Optional[float]
    used_margin: Optional[float]
    open_risk: Optional[float]
    reserved_risk: Optional[float]
    daily_pnl: Optional[float]
    daily_loss_limit: Optional[float]
    peak_capital: Optional[float]
    max_allowed_drawdown: Optional[float]     # fraction, e.g. 0.20 for 20%
    consecutive_losses: Optional[int]
    timestamp: datetime


@dataclass(frozen=True)
class DerivedCapitalValues:
    """Pure derivations from a CapitalSafetySnapshot -- never accepted
    as direct caller input, to prevent an observed field and its own
    derivation from silently disagreeing."""

    free_margin: Optional[float]              # available_capital - used_margin
    maximum_loss_exposure: Optional[float]     # open_risk + reserved_risk
    drawdown_from_peak: Optional[float]         # (peak_capital - total_capital) / peak_capital
    daily_loss: Optional[float]                 # max(0, -daily_pnl) -- a profitable day has zero "loss"


def derive_capital_values(snapshot: CapitalSafetySnapshot) -> DerivedCapitalValues:
    free_margin = (
        snapshot.available_capital - snapshot.used_margin
        if snapshot.available_capital is not None and snapshot.used_margin is not None else None
    )
    maximum_loss_exposure = (
        snapshot.open_risk + snapshot.reserved_risk
        if snapshot.open_risk is not None and snapshot.reserved_risk is not None else None
    )
    drawdown_from_peak = (
        (snapshot.peak_capital - snapshot.total_capital) / snapshot.peak_capital
        if snapshot.peak_capital is not None and snapshot.total_capital is not None and snapshot.peak_capital > 0
        else None
    )
    daily_loss = max(0.0, -snapshot.daily_pnl) if snapshot.daily_pnl is not None else None
    return DerivedCapitalValues(
        free_margin=free_margin, maximum_loss_exposure=maximum_loss_exposure,
        drawdown_from_peak=drawdown_from_peak, daily_loss=daily_loss,
    )


# --------------------------------------------------------------------- #
# Safety invariants (Part 6) -- checked BEFORE any metric is computed.
# A violation here forces BLOCKED unconditionally, before threshold
# logic even runs, since a negative/nonsensical observed value means
# the inputs themselves cannot be trusted, not just that a limit was
# exceeded.
# --------------------------------------------------------------------- #

def check_snapshot_invariants(snapshot: CapitalSafetySnapshot) -> Optional[str]:
    """Returns a violation reason string, or None if the snapshot's
    observed values are internally sane. Missing (None) values are NOT
    flagged here -- that's METRIC_UNAVAILABLE's job in
    compute_capital_metrics(), which also forces BLOCKED, just with a
    more specific per-metric reason. This function only catches
    values that ARE present but invalid (negative capital, negative
    margin, etc.), since a negative number passing straight through
    threshold comparisons could otherwise silently produce a
    nonsensical utilization ratio."""
    numeric_fields = (
        ("total_capital", snapshot.total_capital),
        ("available_capital", snapshot.available_capital),
        ("used_margin", snapshot.used_margin),
        ("open_risk", snapshot.open_risk),
        ("reserved_risk", snapshot.reserved_risk),
        ("daily_loss_limit", snapshot.daily_loss_limit),
        ("peak_capital", snapshot.peak_capital),
        ("max_allowed_drawdown", snapshot.max_allowed_drawdown),
    )
    for name, value in numeric_fields:
        if value is not None and value < 0:
            return f"SAFETY_INVARIANT_VIOLATION_NEGATIVE_{name.upper()}"
    if snapshot.consecutive_losses is not None and snapshot.consecutive_losses < 0:
        return "SAFETY_INVARIANT_VIOLATION_NEGATIVE_CONSECUTIVE_LOSSES"
    return None


# --------------------------------------------------------------------- #
# Part 2 -- Account Risk Utilization Engine
# --------------------------------------------------------------------- #

@dataclass(frozen=True)
class MetricThresholds:
    warning: float
    breach: float


@dataclass(frozen=True)
class CapitalSafetyThresholds:
    """All illustrative defaults, not calibrated to any real account --
    every value here is a plain constructor argument, matching this
    whole Governor's established configurable-thresholds discipline
    from every prior Gate B/C phase. `breach` for daily_loss/drawdown
    defaults to 1.0 (100% of the configured limit) since the limit
    itself already represents the account's own hard stop; margin/risk
    utilization default to a BREACH point below 100%, a deliberately
    more conservative account-level safety margin than waiting for an
    actual broker margin call."""

    margin_utilization: MetricThresholds = MetricThresholds(warning=0.70, breach=0.90)
    risk_utilization: MetricThresholds = MetricThresholds(warning=0.70, breach=0.90)
    daily_loss_utilization: MetricThresholds = MetricThresholds(warning=0.70, breach=1.0)
    drawdown_utilization: MetricThresholds = MetricThresholds(warning=0.70, breach=1.0)


@dataclass(frozen=True)
class CapitalMetric:
    name: str
    value: Optional[float]        # the utilization fraction itself, e.g. 0.42 for 42% -- None if unavailable
    threshold: float               # the configured BREACH threshold for this metric
    status: str                    # NORMAL | WARNING | BREACH | UNAVAILABLE


def _classify(value: Optional[float], thresholds: MetricThresholds) -> str:
    if value is None:
        return METRIC_UNAVAILABLE
    if value >= thresholds.breach:
        return METRIC_BREACH
    if value >= thresholds.warning:
        return METRIC_WARNING
    return METRIC_NORMAL


def compute_capital_metrics(
    snapshot: CapitalSafetySnapshot, thresholds: CapitalSafetyThresholds,
) -> Tuple[CapitalMetric, ...]:
    """Pure measurement only -- no trading decision is made here. A
    metric whose denominator is missing or exactly zero (never
    silently treated as an all-clear 0.0) is UNAVAILABLE, never
    NORMAL."""
    derived = derive_capital_values(snapshot)

    margin_value = (
        snapshot.used_margin / snapshot.total_capital
        if snapshot.used_margin is not None and snapshot.total_capital not in (None, 0)
        else None
    )
    risk_value = (
        derived.maximum_loss_exposure / snapshot.total_capital
        if derived.maximum_loss_exposure is not None and snapshot.total_capital not in (None, 0)
        else None
    )
    daily_loss_value = (
        derived.daily_loss / snapshot.daily_loss_limit
        if derived.daily_loss is not None and snapshot.daily_loss_limit not in (None, 0)
        else None
    )
    drawdown_value = (
        derived.drawdown_from_peak / snapshot.max_allowed_drawdown
        if derived.drawdown_from_peak is not None and snapshot.max_allowed_drawdown not in (None, 0)
        else None
    )

    return (
        CapitalMetric("margin_utilization", margin_value, thresholds.margin_utilization.breach,
                       _classify(margin_value, thresholds.margin_utilization)),
        CapitalMetric("risk_utilization", risk_value, thresholds.risk_utilization.breach,
                       _classify(risk_value, thresholds.risk_utilization)),
        CapitalMetric("daily_loss_utilization", daily_loss_value, thresholds.daily_loss_utilization.breach,
                       _classify(daily_loss_value, thresholds.daily_loss_utilization)),
        CapitalMetric("drawdown_utilization", drawdown_value, thresholds.drawdown_utilization.breach,
                       _classify(drawdown_value, thresholds.drawdown_utilization)),
    )


# --------------------------------------------------------------------- #
# Part 3 -- Capital Safety Classification
# --------------------------------------------------------------------- #

def classify_capital_safety(
    snapshot: CapitalSafetySnapshot, metrics: Tuple[CapitalMetric, ...],
) -> Tuple[str, Tuple[str, ...]]:
    """Returns (status, reasons). Rules, in priority order (highest
    severity first -- a single BLOCKED-tier condition anywhere forces
    the whole result to BLOCKED, regardless of how healthy other
    metrics look, the same "no averaging away a real failure"
    discipline established in Gate C.4's certification rule):

      1. BLOCKED  -- an explicit safety invariant violation (negative/
         nonsensical observed value), OR any metric is UNAVAILABLE
         (insufficient capital data), OR daily_loss_utilization is
         BREACH, OR drawdown_utilization is BREACH. Daily loss and
         drawdown are singled out as BLOCKED-tier (not merely
         RESTRICTED) because breaching either represents an already-
         realized loss event, not just elevated exposure.
      2. RESTRICTED -- margin_utilization or risk_utilization is
         BREACH (capital usage or risk concentration too high, but no
         loss has actually been realized yet).
      3. CAUTION -- any metric is WARNING (and none are BLOCKED/
         RESTRICTED-tier).
      4. SAFE -- every metric is NORMAL."""
    invariant_violation = check_snapshot_invariants(snapshot)
    reasons = []

    if invariant_violation is not None:
        return SAFETY_BLOCKED, (invariant_violation,)

    unavailable = [m for m in metrics if m.status == METRIC_UNAVAILABLE]
    if unavailable:
        return SAFETY_BLOCKED, tuple(f"INSUFFICIENT_CAPITAL_DATA:{m.name}" for m in unavailable)

    by_name = {m.name: m for m in metrics}
    if by_name["daily_loss_utilization"].status == METRIC_BREACH:
        reasons.append("DAILY_LOSS_LIMIT_BREACHED")
    if by_name["drawdown_utilization"].status == METRIC_BREACH:
        reasons.append("MAXIMUM_DRAWDOWN_BREACHED")
    if reasons:
        return SAFETY_BLOCKED, tuple(reasons)

    if by_name["margin_utilization"].status == METRIC_BREACH:
        reasons.append("MARGIN_UTILIZATION_TOO_HIGH")
    if by_name["risk_utilization"].status == METRIC_BREACH:
        reasons.append("RISK_CONCENTRATION_TOO_HIGH")
    if reasons:
        return SAFETY_RESTRICTED, tuple(reasons)

    warnings = [m.name for m in metrics if m.status == METRIC_WARNING]
    if warnings:
        return SAFETY_CAUTION, tuple(f"{name}_ELEVATED" for name in warnings)

    return SAFETY_SAFE, ()


# --------------------------------------------------------------------- #
# Part 4 -- Trade Admission Layer
# --------------------------------------------------------------------- #

@dataclass(frozen=True)
class ProposedTradeEffect:
    """The hypothetical impact of a proposed trade on the account,
    expressed purely as deltas -- SCENARIO PROJECTION ONLY, matching
    Gate C.2.5's MarginScenarioEngine's own established design. The
    trade itself never executes; this is arithmetic over a copy of the
    snapshot, never a mutation of any real state."""

    additional_margin: float
    additional_max_loss: float     # this trade's own defined-risk contribution to open_risk

    def increases_risk(self) -> bool:
        return self.additional_margin > 0 or self.additional_max_loss > 0


def project_snapshot(snapshot: CapitalSafetySnapshot, effect: ProposedTradeEffect) -> CapitalSafetySnapshot:
    """Pure. Returns a NEW snapshot reflecting the hypothetical trade's
    effect -- never mutates the original (frozen dataclass, built via
    dataclasses.replace). If used_margin or open_risk is unavailable in
    the original, the projection stays unavailable too (an unknown
    starting point plus a known delta is still unknown) rather than
    silently treating a missing observed value as zero."""
    new_used_margin = (
        snapshot.used_margin + effect.additional_margin if snapshot.used_margin is not None else None
    )
    new_open_risk = (
        snapshot.open_risk + effect.additional_max_loss if snapshot.open_risk is not None else None
    )
    return replace(snapshot, used_margin=new_used_margin, open_risk=new_open_risk)


@dataclass(frozen=True)
class CapitalSafetyDecision:
    allowed: bool
    status: str                          # the AFTER status
    blocking_reasons: Tuple[str, ...]
    warnings: Tuple[str, ...]
    before_metrics: Tuple[CapitalMetric, ...]
    after_metrics: Tuple[CapitalMetric, ...]
    explanation: str
    evaluated_at: datetime


def evaluate_trade_capital_safety(
    current_state: CapitalSafetySnapshot,
    proposed_trade_effect: ProposedTradeEffect,
    thresholds: Optional[CapitalSafetyThresholds],
    clock: Clock,
) -> CapitalSafetyDecision:
    """The account-level admission decision. Safety invariants (Part 6),
    enforced unconditionally, with NO caller-supplied override of any
    kind -- there is no "force approve" parameter anywhere in this
    function's signature, deliberately:

      - If the BEFORE state is already BLOCKED, the decision is BLOCKED
        regardless of what the proposed trade would do -- a blocked
        account cannot be unblocked by a hypothetical future trade's
        own merits, it requires the underlying condition (missing data,
        an invariant violation, a breached limit) to be resolved first.
      - If the AFTER state would be BLOCKED, the decision is BLOCKED.
      - If the AFTER state would be RESTRICTED, the decision is allowed
        ONLY if the trade does not increase risk (ProposedTradeEffect.
        increases_risk() is False) -- matching this phase's own
        "RESTRICTED blocks risky additions" framing: a de-risking or
        risk-neutral trade (closing/hedging) can still proceed, a
        risk-increasing one cannot.
      - Otherwise (AFTER is CAUTION or SAFE), the trade is allowed,
        with any WARNING-tier metrics surfaced as warnings."""
    active_thresholds = thresholds or CapitalSafetyThresholds()
    as_of = clock()

    before_metrics = compute_capital_metrics(current_state, active_thresholds)
    before_status, before_reasons = classify_capital_safety(current_state, before_metrics)

    projected_state = project_snapshot(current_state, proposed_trade_effect)
    after_metrics = compute_capital_metrics(projected_state, active_thresholds)
    after_status, after_reasons = classify_capital_safety(projected_state, after_metrics)

    if before_status == SAFETY_BLOCKED:
        blocking_reasons = tuple(f"ACCOUNT_ALREADY_BLOCKED:{r}" for r in before_reasons)
        decision = CapitalSafetyDecision(
            allowed=False, status=SAFETY_BLOCKED, blocking_reasons=blocking_reasons, warnings=(),
            before_metrics=before_metrics, after_metrics=after_metrics,
            explanation=_explain(False, SAFETY_BLOCKED, blocking_reasons, (), before_metrics, after_metrics),
            evaluated_at=as_of,
        )
        return decision

    if after_status == SAFETY_BLOCKED:
        decision = CapitalSafetyDecision(
            allowed=False, status=SAFETY_BLOCKED, blocking_reasons=after_reasons, warnings=(),
            before_metrics=before_metrics, after_metrics=after_metrics,
            explanation=_explain(False, SAFETY_BLOCKED, after_reasons, (), before_metrics, after_metrics),
            evaluated_at=as_of,
        )
        return decision

    if after_status == SAFETY_RESTRICTED:
        allowed = not proposed_trade_effect.increases_risk()
        blocking_reasons = () if allowed else tuple(f"RESTRICTED_BLOCKS_RISK_INCREASE:{r}" for r in after_reasons)
        decision = CapitalSafetyDecision(
            allowed=allowed, status=SAFETY_RESTRICTED, blocking_reasons=blocking_reasons, warnings=(),
            before_metrics=before_metrics, after_metrics=after_metrics,
            explanation=_explain(allowed, SAFETY_RESTRICTED, blocking_reasons, (), before_metrics, after_metrics),
            evaluated_at=as_of,
        )
        return decision

    warnings = after_reasons if after_status == SAFETY_CAUTION else ()
    decision = CapitalSafetyDecision(
        allowed=True, status=after_status, blocking_reasons=(), warnings=warnings,
        before_metrics=before_metrics, after_metrics=after_metrics,
        explanation=_explain(True, after_status, (), warnings, before_metrics, after_metrics),
        evaluated_at=as_of,
    )
    return decision


# --------------------------------------------------------------------- #
# Part 7 -- Explanation Layer. Built from the SAME before/after_metrics
# already computed above -- no separate calculation of any kind.
# --------------------------------------------------------------------- #

def _format_pct(value: Optional[float]) -> str:
    return f"{value:.0%}" if value is not None else "unavailable"


def _explain(
    allowed: bool, status: str, blocking_reasons: Tuple[str, ...], warnings: Tuple[str, ...],
    before_metrics: Tuple[CapitalMetric, ...], after_metrics: Tuple[CapitalMetric, ...],
) -> str:
    after_by_name = {m.name: m for m in after_metrics}
    lines = []
    if allowed:
        lines.append("Trade allowed.")
    else:
        lines.append("Trade rejected.")
    lines.append(f"Projected margin utilization: {_format_pct(after_by_name['margin_utilization'].value)}.")
    lines.append(f"Projected risk utilization: {_format_pct(after_by_name['risk_utilization'].value)}.")
    lines.append(f"Daily loss usage: {_format_pct(after_by_name['daily_loss_utilization'].value)}.")
    lines.append(f"Drawdown usage: {_format_pct(after_by_name['drawdown_utilization'].value)}.")
    if blocking_reasons:
        lines.append("Blocking reasons: " + ", ".join(blocking_reasons) + ".")
    if warnings:
        lines.append("Warnings: " + ", ".join(warnings) + ".")
    if not blocking_reasons and not warnings:
        lines.append("No safety limits breached.")
    return " ".join(lines)
