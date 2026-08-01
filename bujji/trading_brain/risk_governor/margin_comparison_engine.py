"""Margin Comparison Engine — BUJJI Options OS v3, Numeric Risk
Governor Gate C.3.

Compares Bujji's internal SimulatedMarginProvider figure against a
real, read-only BrokerMarginSnapshot -- STRICTLY DIAGNOSTIC. This
module cannot influence, gate, or participate in any ALLOW/VETO
decision in any way: it has no reference to CapitalCheckInput, never
imports capital_check.py, and MarginComparisonReport is never
consumed by anything that feeds a trading decision. capital_check.
assess_capital() remains the sole ALLOW/VETO authority, completely
untouched by this module, exactly as it was by Gate C.1/C.2/C.2.5.

This is a calibration tool -- "does Bujji's model agree with reality"
-- not an integration point. Per this phase's own framing: only a
LATER, separately-approved milestone (Gate C.4) may ever connect
broker-verified figures to live trade approval; nothing here does
that, or is wired to anything that could."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional

from bujji.trading_brain.risk_governor.broker_margin_reality_adapter import BrokerMarginSnapshot
from bujji.trading_brain.risk_governor.whole_book_margin_provider import MarginSnapshot

Clock = Callable[[], datetime]

COMPARISON_PASS = "PASS"
COMPARISON_WARNING = "WARNING"
COMPARISON_FAIL = "FAIL"
COMPARISON_UNAVAILABLE = "UNAVAILABLE"
COMPARISON_STALE = "STALE"

DEFAULT_PASS_THRESHOLD = 0.05        # <5% deviation
DEFAULT_WARNING_THRESHOLD = 0.15     # 5-15% deviation; >15% is FAIL
DEFAULT_STALENESS_SECONDS = 300.0    # 5 minutes


@dataclass(frozen=True)
class MarginComparisonReport:
    simulated_margin: Optional[float]
    broker_margin: Optional[float]
    difference: Optional[float]           # broker - simulated; None if not comparable
    deviation_fraction: Optional[float]    # abs(difference) / abs(broker_margin); None if not comparable
    status: str                            # PASS | WARNING | FAIL | UNAVAILABLE | STALE
    reason: Optional[str]                   # populated for UNAVAILABLE/STALE, None otherwise


def compare_margin(
    simulated_snapshot: MarginSnapshot,
    broker_snapshot: Optional[BrokerMarginSnapshot],
    clock: Clock,
    pass_threshold: float = DEFAULT_PASS_THRESHOLD,
    warning_threshold: float = DEFAULT_WARNING_THRESHOLD,
    staleness_seconds: float = DEFAULT_STALENESS_SECONDS,
) -> MarginComparisonReport:
    """Pure (given its clock/thresholds as explicit inputs). Never
    raises on a missing/unavailable broker snapshot -- returns
    UNAVAILABLE/STALE instead, so a broker outage never silently
    reads as "PASS" or "margin=0"."""
    if not (0.0 <= pass_threshold <= warning_threshold):
        raise ValueError(
            "thresholds must satisfy 0 <= pass_threshold <= warning_threshold, "
            f"got pass={pass_threshold!r} warning={warning_threshold!r}"
        )

    if broker_snapshot is None:
        return MarginComparisonReport(
            simulated_margin=simulated_snapshot.required_margin, broker_margin=None,
            difference=None, deviation_fraction=None, status=COMPARISON_UNAVAILABLE,
            reason="no broker margin snapshot was supplied",
        )
    if not broker_snapshot.available:
        return MarginComparisonReport(
            simulated_margin=simulated_snapshot.required_margin, broker_margin=None,
            difference=None, deviation_fraction=None, status=COMPARISON_UNAVAILABLE,
            reason=f"broker margin snapshot unavailable (source={broker_snapshot.source})",
        )

    if not simulated_snapshot.margin_verified or simulated_snapshot.required_margin is None:
        return MarginComparisonReport(
            simulated_margin=None, broker_margin=broker_snapshot.required_margin,
            difference=None, deviation_fraction=None, status=COMPARISON_UNAVAILABLE,
            reason="simulated margin snapshot is unverified or missing a required_margin figure",
        )

    now = clock()
    age_seconds = (now - broker_snapshot.timestamp).total_seconds()
    if age_seconds > staleness_seconds:
        return MarginComparisonReport(
            simulated_margin=simulated_snapshot.required_margin, broker_margin=broker_snapshot.required_margin,
            difference=None, deviation_fraction=None, status=COMPARISON_STALE,
            reason=f"broker snapshot is {age_seconds:.0f}s old, exceeding the {staleness_seconds:.0f}s staleness threshold",
        )

    if broker_snapshot.required_margin is None:
        return MarginComparisonReport(
            simulated_margin=simulated_snapshot.required_margin, broker_margin=None,
            difference=None, deviation_fraction=None, status=COMPARISON_UNAVAILABLE,
            reason="broker margin snapshot has no required_margin figure",
        )

    difference = broker_snapshot.required_margin - simulated_snapshot.required_margin
    if broker_snapshot.required_margin == 0:
        deviation_fraction = 0.0 if simulated_snapshot.required_margin == 0 else float("inf")
    else:
        deviation_fraction = abs(difference) / abs(broker_snapshot.required_margin)

    if deviation_fraction < pass_threshold:
        status = COMPARISON_PASS
    elif deviation_fraction <= warning_threshold:
        status = COMPARISON_WARNING
    else:
        status = COMPARISON_FAIL

    return MarginComparisonReport(
        simulated_margin=simulated_snapshot.required_margin, broker_margin=broker_snapshot.required_margin,
        difference=difference, deviation_fraction=deviation_fraction, status=status, reason=None,
    )


class MarginComparisonEngine:
    """Thin class wrapper around compare_margin(), matching the
    requested interface name. All actual logic lives in the standalone
    pure function above, independently testable on its own."""

    def __init__(
        self, pass_threshold: float = DEFAULT_PASS_THRESHOLD, warning_threshold: float = DEFAULT_WARNING_THRESHOLD,
        staleness_seconds: float = DEFAULT_STALENESS_SECONDS,
    ) -> None:
        self._pass_threshold = pass_threshold
        self._warning_threshold = warning_threshold
        self._staleness_seconds = staleness_seconds

    def compare(
        self, simulated_snapshot: MarginSnapshot, broker_snapshot: Optional[BrokerMarginSnapshot], clock: Clock,
    ) -> MarginComparisonReport:
        return compare_margin(
            simulated_snapshot, broker_snapshot, clock,
            pass_threshold=self._pass_threshold, warning_threshold=self._warning_threshold,
            staleness_seconds=self._staleness_seconds,
        )
