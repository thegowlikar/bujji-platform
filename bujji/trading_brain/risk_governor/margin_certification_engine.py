"""Margin Certification Harness — BUJJI Options OS v3, Numeric Risk
Governor Gate C.4.

PURPOSE, AND WHAT THIS PHASE'S OUTPUT IS NOT: this module proves
whether Bujji's internal margin model is sufficiently aligned with
real broker margin behavior, across a batch of comparison cases. Its
output is CERTIFIED / WARNING / FAILED / INSUFFICIENT_DATA -- a
REPORT, not a trading permission. This module has no reference to
CapitalCheckInput, never imports capital_check.py, and
MarginCertificationResult is never consumed by anything that gates a
trade. capital_check.assess_capital() remains the sole ALLOW/VETO
authority, completely untouched by anything here, exactly as it has
been by every prior Gate C phase.

PURE AGGREGATION, NO DUPLICATED COMPARISON LOGIC: this module performs
zero margin math and zero broker comparison of its own. Every
MarginCertificationCase carries a `comparison_report` already built by
the EXISTING, already-tested margin_comparison_engine.compare_margin()
-- this module only aggregates and classifies collections of those
already-computed reports. build_certification_case() is provided as a
convenience that calls compare_margin() internally (never
reimplementing it) so a caller doesn't have to import both modules
separately.

THE ONE NON-OBVIOUS DESIGN DECISION, MADE EXPLICIT: how do UNAVAILABLE
/STALE comparison cases (broker data missing or too old) factor into
certification? They are EXCLUDED from pass/warning/fail counts and
deviation statistics -- a case where the broker simply didn't respond
tells us NOTHING about whether the MODEL is accurate, and counting it
as a "fail" would conflate "we don't know" with "the model is wrong."
They ARE always counted in `total_cases` (for report transparency) and
always surfaced in `failure_reasons` (never silently dropped). If ALL
cases (or too few of them, per the configurable `min_comparable_cases`)
are unavailable/stale, the overall status is INSUFFICIENT_DATA rather
than a falsely optimistic CERTIFIED built on zero real comparisons.
This directly answers this phase's own edge case #5 ("missing broker
snapshot -> FAIL or INSUFFICIENT_DATA, choose and document"): chosen
answer is INSUFFICIENT_DATA (when it drives the comparable count to
zero) or exclusion-with-transparency (when mixed with usable cases),
never FAIL, since a missing observation is not evidence of a
misaligned model.

CERTIFICATION RULE, TRACED DIRECTLY AGAINST BOTH WORKED EXAMPLES IN
THIS PHASE'S OWN SPEC:
  1. comparable_cases == 0, or below the configurable min_comparable_cases
     -> INSUFFICIENT_DATA (cannot certify confidence from too little data)
  2. ANY individual comparable case has comparison status FAIL
     -> FAILED, REGARDLESS of how good the average deviation looks.
     This is deliberate and directly satisfies this phase's own
     adversarial audit requirement ("strategy segmentation cannot hide
     failures behind aggregate averages") -- one genuinely bad outlier
     disqualifies certification even if buried among many good cases.
  3. average_deviation > fail_threshold -> FAILED
  4. average_deviation >= pass_threshold -> WARNING (mirrors compare_margin's
     own strict-less-than PASS boundary: exactly at the threshold is not PASS)
  5. otherwise -> CERTIFIED

Verified against the spec's own two worked examples: "95 passed, 5
warnings, avg 3.1%, max 11%" has zero FAIL-tier cases and avg < 5%,
producing CERTIFIED (rule 5) -- note the presence of WARNING-tier
cases and an 11% MAXIMUM deviation do NOT by themselves downgrade the
overall status; only the AVERAGE and the presence of any FAIL-tier
case do, matching the example exactly. "avg deviation 18%" produces
FAILED via rule 3."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from bujji.trading_brain.risk_governor.broker_margin_reality_adapter import BrokerMarginSnapshot
from bujji.trading_brain.risk_governor.margin_comparison_engine import (
    COMPARISON_FAIL,
    COMPARISON_PASS,
    COMPARISON_STALE,
    COMPARISON_UNAVAILABLE,
    COMPARISON_WARNING,
    DEFAULT_PASS_THRESHOLD,
    DEFAULT_WARNING_THRESHOLD,
    MarginComparisonReport,
    compare_margin,
)
from bujji.trading_brain.risk_governor.whole_book_margin_provider import MarginSnapshot

Clock = Callable[[], datetime]

CERTIFICATION_CERTIFIED = "CERTIFIED"
CERTIFICATION_WARNING = "WARNING"
CERTIFICATION_FAILED = "FAILED"
CERTIFICATION_INSUFFICIENT_DATA = "INSUFFICIENT_DATA"

_UNCOMPARABLE_STATUSES = (COMPARISON_UNAVAILABLE, COMPARISON_STALE)

DEFAULT_MIN_COMPARABLE_CASES = 1


@dataclass(frozen=True)
class MarginCertificationCase:
    case_id: str
    strategy_type: str
    simulated_margin_snapshot: MarginSnapshot
    broker_margin_snapshot: Optional[BrokerMarginSnapshot]
    comparison_report: MarginComparisonReport
    timestamp: datetime
    metadata: Dict[str, Any] = field(default_factory=dict)


def build_certification_case(
    case_id: str,
    strategy_type: str,
    simulated_snapshot: MarginSnapshot,
    broker_snapshot: Optional[BrokerMarginSnapshot],
    clock: Clock,
    pass_threshold: float = DEFAULT_PASS_THRESHOLD,
    warning_threshold: float = DEFAULT_WARNING_THRESHOLD,
    metadata: Optional[Dict[str, Any]] = None,
) -> MarginCertificationCase:
    """Convenience constructor -- calls the EXISTING compare_margin()
    internally, never reimplementing comparison logic."""
    comparison_report = compare_margin(
        simulated_snapshot, broker_snapshot, clock,
        pass_threshold=pass_threshold, warning_threshold=warning_threshold,
    )
    return MarginCertificationCase(
        case_id=case_id, strategy_type=strategy_type,
        simulated_margin_snapshot=simulated_snapshot, broker_margin_snapshot=broker_snapshot,
        comparison_report=comparison_report, timestamp=clock(), metadata=metadata or {},
    )


@dataclass(frozen=True)
class StrategyCertificationSummary:
    strategy_type: str
    total_cases: int
    comparable_cases: int
    passed_cases: int
    warning_cases: int
    failed_cases: int
    excluded_cases: int              # unavailable/stale -- see module docstring
    average_deviation: Optional[float]
    maximum_deviation: Optional[float]
    status: str


@dataclass(frozen=True)
class MarginCertificationResult:
    total_cases: int
    comparable_cases: int
    passed_cases: int
    warning_cases: int
    failed_cases: int
    excluded_cases: int
    average_deviation: Optional[float]
    maximum_deviation: Optional[float]
    certification_status: str
    strategy_breakdown: Tuple[StrategyCertificationSummary, ...]
    failure_reasons: Tuple[str, ...]


def _classify(
    cases: Tuple[MarginCertificationCase, ...],
    pass_threshold: float, fail_threshold: float, min_comparable_cases: int,
) -> Tuple[int, int, int, int, int, int, Optional[float], Optional[float], str, Tuple[str, ...]]:
    """Shared aggregation logic, used identically for the overall
    result and every per-strategy breakdown -- one rule, no
    duplication."""
    total = len(cases)
    passed = warned = failed = excluded = 0
    deviations: List[float] = []
    reasons: List[str] = []

    for case in cases:
        report = case.comparison_report
        if report.status in _UNCOMPARABLE_STATUSES:
            excluded += 1
            reasons.append(
                f"case {case.case_id!r} ({case.strategy_type}) excluded from statistics: "
                f"{report.status} -- {report.reason or 'no reason given'}"
            )
            continue
        if report.deviation_fraction is not None:
            deviations.append(report.deviation_fraction)
        if report.status == COMPARISON_PASS:
            passed += 1
        elif report.status == COMPARISON_WARNING:
            warned += 1
        elif report.status == COMPARISON_FAIL:
            failed += 1
            reasons.append(
                f"case {case.case_id!r} ({case.strategy_type}) FAILED with "
                f"{report.deviation_fraction:.1%} deviation"
                if report.deviation_fraction is not None else
                f"case {case.case_id!r} ({case.strategy_type}) FAILED"
            )

    comparable = passed + warned + failed
    average_deviation = sum(deviations) / len(deviations) if deviations else None
    maximum_deviation = max(deviations) if deviations else None

    if comparable == 0 or comparable < min_comparable_cases:
        status = CERTIFICATION_INSUFFICIENT_DATA
        if comparable == 0 and total > 0:
            reasons.append("all cases were excluded (unavailable/stale broker data) -- zero comparable cases")
        elif total == 0:
            reasons.append("no certification cases were supplied")
    elif failed > 0:
        status = CERTIFICATION_FAILED
    elif average_deviation is not None and average_deviation > fail_threshold:
        status = CERTIFICATION_FAILED
    elif average_deviation is not None and average_deviation >= pass_threshold:
        # >= (not >) to mirror margin_comparison_engine.compare_margin's own
        # boundary convention exactly: PASS is deviation_fraction < pass_threshold
        # (strict), so exactly-at-the-threshold belongs to the next tier up,
        # both per-case and in this aggregate rule -- verified via
        # test_exactly_five_percent_average_is_warning_not_certified.
        status = CERTIFICATION_WARNING
    else:
        status = CERTIFICATION_CERTIFIED

    return total, comparable, passed, warned, failed, excluded, average_deviation, maximum_deviation, status, tuple(reasons)


class MarginCertificationEngine:
    """Pure aggregation only -- no broker calls, no order imports, no
    capital_check reference anywhere. See module docstring for the
    full certification rule and its rationale."""

    def __init__(
        self, pass_threshold: float = DEFAULT_PASS_THRESHOLD, fail_threshold: float = DEFAULT_WARNING_THRESHOLD,
        min_comparable_cases: int = DEFAULT_MIN_COMPARABLE_CASES,
    ) -> None:
        if not (0.0 <= pass_threshold <= fail_threshold):
            raise ValueError(
                "thresholds must satisfy 0 <= pass_threshold <= fail_threshold, "
                f"got pass={pass_threshold!r} fail={fail_threshold!r}"
            )
        if min_comparable_cases < 0:
            raise ValueError(f"min_comparable_cases must be non-negative, got {min_comparable_cases!r}")
        self._pass_threshold = pass_threshold
        self._fail_threshold = fail_threshold
        self._min_comparable_cases = min_comparable_cases

    def certify(self, cases: List[MarginCertificationCase]) -> MarginCertificationResult:
        cases_tuple = tuple(cases)

        (total, comparable, passed, warned, failed, excluded,
         average_deviation, maximum_deviation, status, reasons) = _classify(
            cases_tuple, self._pass_threshold, self._fail_threshold, self._min_comparable_cases,
        )

        strategy_types = sorted({case.strategy_type for case in cases_tuple})
        breakdown = []
        for strategy_type in strategy_types:
            strategy_cases = tuple(c for c in cases_tuple if c.strategy_type == strategy_type)
            (s_total, s_comparable, s_passed, s_warned, s_failed, s_excluded,
             s_avg, s_max, s_status, _s_reasons) = _classify(
                strategy_cases, self._pass_threshold, self._fail_threshold, self._min_comparable_cases,
            )
            breakdown.append(StrategyCertificationSummary(
                strategy_type=strategy_type, total_cases=s_total, comparable_cases=s_comparable,
                passed_cases=s_passed, warning_cases=s_warned, failed_cases=s_failed, excluded_cases=s_excluded,
                average_deviation=s_avg, maximum_deviation=s_max, status=s_status,
            ))

        return MarginCertificationResult(
            total_cases=total, comparable_cases=comparable, passed_cases=passed, warning_cases=warned,
            failed_cases=failed, excluded_cases=excluded, average_deviation=average_deviation,
            maximum_deviation=maximum_deviation, certification_status=status,
            strategy_breakdown=tuple(breakdown), failure_reasons=reasons,
        )


def format_certification_report(result: MarginCertificationResult, period: Optional[str] = None) -> str:
    """Pure formatting only -- no new computation, just renders an
    already-built MarginCertificationResult as human-readable text,
    matching this phase's own requested report shape."""
    lines = ["Bujji Margin Certification Report"]
    if period:
        lines.append(f"Period: {period}")
    lines.append(f"Cases: {result.total_cases}")
    lines.append(f"Overall Status: {result.certification_status}")
    lines.append("Deviation:")
    avg = f"{result.average_deviation:.1%}" if result.average_deviation is not None else "N/A"
    mx = f"{result.maximum_deviation:.1%}" if result.maximum_deviation is not None else "N/A"
    lines.append(f"  Average: {avg}")
    lines.append(f"  Maximum: {mx}")
    lines.append("Strategy Results:")
    for summary in result.strategy_breakdown:
        lines.append(f"  {summary.strategy_type}: {summary.status}")
    lines.append("Failure Reasons:")
    if result.failure_reasons:
        for reason in result.failure_reasons:
            lines.append(f"  - {reason}")
    else:
        lines.append("  None")
    return "\n".join(lines)
