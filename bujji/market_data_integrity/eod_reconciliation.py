"""bujji.market_data_integrity.eod_reconciliation — Phase 19.20.4.

READ ONLY. May read `MicrostructureStore`, Phase 19.19's
`HistoricalObservationStore`, and NSE bhavcopy CSV files — never writes
to any of them. No function in this module calls `.write()`,
`.write_many()`, or any mutating method on either store; proven by
`tests/test_market_data_integrity/test_anti_fabrication.py`, not just
asserted here.

Comparison A (1-minute vs 5-minute) reuses `bujji.market_timeseries.
aggregator.window_bounds` verbatim for the 5-minute bucket boundary
arithmetic — this module performs a group-by/fold over already-closed
`MinuteObservation`s, not a new rollover state machine (there is no
streaming state here at all, unlike `CandleAggregator`/
`MicrostructureAggregator`).

Comparison B (bhavcopy) never guesses which bhavcopy row corresponds to
a given instrument — `find_bhavcopy_row` requires the caller to supply
exact match criteria. Guessing a TckrSymb/FinInstrmTp/expiry
convention without a confirmed specification would itself be a
fabrication risk this project's own discipline forbids.
"""
from __future__ import annotations

import csv
from typing import Dict, List, Optional, Sequence

from bujji.market_timeseries.aggregator import window_bounds
from bujji.market_timeseries.models import INTERVAL_FIVE_MINUTE

from . import intraday_checks
from .models import (
    CATEGORY_BHAVCOPY_MISMATCH,
    CATEGORY_OHLC_MISMATCH,
    SEVERITY_ADVISORY,
    SEVERITY_FAILED,
    SEVERITY_WARNING,
    STATUS_FAILED,
    STATUS_GREEN,
    STATUS_WARNING,
    DailyIntegrityReport,
    IntegrityIssue,
)

DEFAULT_FIVE_MINUTE_TOLERANCE = 0.05     # absolute price points
DEFAULT_BHAVCOPY_ROUNDING_TOLERANCE = 0.05
DEFAULT_BHAVCOPY_MISMATCH_TOLERANCE = 1.0


# --------------------------------------------------------------------- #
# Comparison A: 1-minute vs 5-minute
# --------------------------------------------------------------------- #
def aggregate_one_minute_to_five_minute(minute_observations: Sequence) -> Dict[str, dict]:
    """Fold already-closed 1-minute `MinuteObservation`s into 5-minute
    OHLC buckets, keyed by the 5-minute window's own `window_start`.
    Pure grouping + fold (open=earliest, close=latest, high=max,
    low=min) over data that is ALREADY a settled fact — never a
    streaming aggregation, never touches any store."""
    buckets: Dict[str, List] = {}
    for obs in sorted(minute_observations, key=lambda o: o.window_start):
        bucket_start, _bucket_end = window_bounds(obs.window_start, INTERVAL_FIVE_MINUTE)
        buckets.setdefault(bucket_start, []).append(obs)

    result: Dict[str, dict] = {}
    for bucket_start, members in buckets.items():
        result[bucket_start] = {
            "open": members[0].open, "high": max(m.high for m in members),
            "low": min(m.low for m in members), "close": members[-1].close,
            "member_count": len(members),
        }
    return result


def compare_five_minute_series(
    aggregated: Dict[str, dict], historical_rows, *, instrument: str,
    tolerance: float = DEFAULT_FIVE_MINUTE_TOLERANCE,
) -> List[IntegrityIssue]:
    """`historical_rows`: Phase 19.19 `HistoricalObservation`s for
    `instrument` at RESOLUTION_FIVE_MINUTE (read-only — caller supplies
    them via `HistoricalObservationStore.range()`, never fetched here).
    Compares OHLC where BOTH sides have a bucket at the same timestamp;
    a bucket present on only one side is reported as its own issue
    (timestamp misalignment), never silently skipped."""
    issues: List[IntegrityIssue] = []
    historical_by_ts = {h.observation.identity.timestamp: h.payload for h in historical_rows}

    all_timestamps = sorted(set(aggregated) | set(historical_by_ts))
    for ts in all_timestamps:
        agg = aggregated.get(ts)
        hist = historical_by_ts.get(ts)

        if agg is None:
            issues.append(IntegrityIssue(
                severity=SEVERITY_FAILED, category=CATEGORY_OHLC_MISMATCH,
                description="Phase 19.19 has a 5-minute bar with no corresponding 1-minute aggregate",
                evidence=f"instrument={instrument!r} timestamp={ts!r}", timestamp=ts,
            ))
            continue
        if hist is None:
            issues.append(IntegrityIssue(
                severity=SEVERITY_FAILED, category=CATEGORY_OHLC_MISMATCH,
                description="1-minute aggregate has no corresponding Phase 19.19 5-minute bar",
                evidence=f"instrument={instrument!r} timestamp={ts!r}", timestamp=ts,
            ))
            continue

        for field in ("open", "high", "low", "close"):
            agg_val, hist_val = agg[field], hist.get(field)
            if hist_val is None:
                continue
            if abs(agg_val - hist_val) > tolerance:
                issues.append(IntegrityIssue(
                    severity=SEVERITY_FAILED, category=CATEGORY_OHLC_MISMATCH,
                    description=f"5-minute {field} mismatch beyond tolerance ({tolerance})",
                    evidence=f"instrument={instrument!r} timestamp={ts!r} "
                             f"1min_aggregate={agg_val} phase_19_19={hist_val}",
                    timestamp=ts,
                ))
    return issues


# --------------------------------------------------------------------- #
# Comparison B: NSE bhavcopy
# --------------------------------------------------------------------- #
def load_bhavcopy_csv(path: str) -> List[dict]:
    """Real CSV parsing, no guessing at schema — returns the raw rows
    as dicts, exactly as published (NSE F&O Bhav Copy header fields:
    TckrSymb, XpryDt, StrkPric, OptnTp, ClsPric, SttlmPric, ...)."""
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def find_bhavcopy_row(
    rows: Sequence[dict], *, tckr_symb: str, optn_tp: Optional[str] = None,
    strike: Optional[float] = None, xpry_dt: Optional[str] = None,
) -> Optional[dict]:
    """Exact-match filter only — every supplied criterion must match
    exactly. Returns None (never a best-guess) if zero or more than one
    row matches; ambiguity is the caller's problem to resolve
    explicitly, never silently picked for them."""
    matches = [r for r in rows if r.get("TckrSymb") == tckr_symb]
    if optn_tp is not None:
        matches = [r for r in matches if r.get("OptnTp") == optn_tp]
    if strike is not None:
        matches = [r for r in matches if r.get("StrkPric") not in (None, "") and float(r["StrkPric"]) == strike]
    if xpry_dt is not None:
        matches = [r for r in matches if r.get("XpryDt") == xpry_dt]
    if len(matches) != 1:
        return None
    return matches[0]


def compare_against_bhavcopy(
    captured_close: float, bhavcopy_row: Optional[dict], *,
    close_field: str = "ClsPric",
    rounding_tolerance: float = DEFAULT_BHAVCOPY_ROUNDING_TOLERANCE,
    mismatch_tolerance: float = DEFAULT_BHAVCOPY_MISMATCH_TOLERANCE,
    instrument: str = "",
) -> Optional[IntegrityIssue]:
    """Returns None for a clean match (GREEN — no issue to report).
    ADVISORY for a small rounding difference or missing bhavcopy data.
    FAILED for an actual, material mismatch. Never corrects
    `captured_close` — comparison only."""
    if bhavcopy_row is None:
        return IntegrityIssue(
            severity=SEVERITY_ADVISORY, category=CATEGORY_BHAVCOPY_MISMATCH,
            description="no matching bhavcopy row found for reconciliation",
            evidence=f"instrument={instrument!r} captured_close={captured_close}",
            timestamp="",
        )

    raw = bhavcopy_row.get(close_field)
    try:
        bhav_close = float(raw)
    except (TypeError, ValueError):
        return IntegrityIssue(
            severity=SEVERITY_ADVISORY, category=CATEGORY_BHAVCOPY_MISMATCH,
            description=f"bhavcopy {close_field!r} field is not a parseable number",
            evidence=f"instrument={instrument!r} raw_value={raw!r}", timestamp="",
        )

    diff = abs(captured_close - bhav_close)
    if diff <= rounding_tolerance:
        return None
    if diff <= mismatch_tolerance:
        return IntegrityIssue(
            severity=SEVERITY_ADVISORY, category=CATEGORY_BHAVCOPY_MISMATCH,
            description="small rounding difference against bhavcopy close",
            evidence=f"instrument={instrument!r} captured={captured_close} bhavcopy={bhav_close} diff={diff}",
            timestamp="",
        )
    return IntegrityIssue(
        severity=SEVERITY_FAILED, category=CATEGORY_BHAVCOPY_MISMATCH,
        description="captured close does not reconcile against bhavcopy",
        evidence=f"instrument={instrument!r} captured={captured_close} bhavcopy={bhav_close} diff={diff}",
        timestamp="",
    )


# --------------------------------------------------------------------- #
# Top-level orchestration — combines intraday checks + both
# reconciliation comparisons into one immutable DailyIntegrityReport.
# Pure function: every input is caller-supplied (already-read rows,
# already-loaded bhavcopy), so this module never opens a store or file
# itself — the entrypoint script owns all IO, this function owns only
# the decision logic.
# --------------------------------------------------------------------- #
def _quality_score(issues: Sequence[IntegrityIssue]) -> float:
    """100 minus a per-issue penalty by severity. Disclosed, first-pass
    weights (FAILED=-30, WARNING=-10, ADVISORY=-2), clamped to [0,100] —
    same "first, revisable default" discipline as
    MicrostructureAggregator's own quality-score formula (Phase 19.20.2)."""
    penalty = sum(
        30.0 if i.severity == SEVERITY_FAILED else 10.0 if i.severity == SEVERITY_WARNING else 2.0
        for i in issues
    )
    return max(0.0, min(100.0, round(100.0 - penalty, 2)))


def _overall_status(issues: Sequence[IntegrityIssue]) -> str:
    if any(i.severity == SEVERITY_FAILED for i in issues):
        return STATUS_FAILED
    if any(i.severity == SEVERITY_WARNING for i in issues):
        return STATUS_WARNING
    return STATUS_GREEN


def build_daily_integrity_report(
    *, session_date: str, instrument: str, minute_observations: Sequence,
    session_start_iso: str, session_end_iso: str, now_iso: str,
    capture_session_log_rows: Sequence[dict],
    five_minute_historical_rows=(), five_minute_tolerance: float = DEFAULT_FIVE_MINUTE_TOLERANCE,
    bhavcopy_row: Optional[dict] = None, bhavcopy_close_field: str = "ClsPric",
) -> DailyIntegrityReport:
    """Assembles every Phase 19.20.4 check into one immutable report.
    Never writes anywhere -- every argument is already-read data."""
    issues: List[IntegrityIssue] = []

    missing = intraday_checks.detect_missing_minutes(
        minute_observations, instrument=instrument,
        session_start_iso=session_start_iso, session_end_iso=session_end_iso,
    )
    issues.extend(missing)

    duplicates = intraday_checks.detect_duplicates(minute_observations)
    issues.extend(duplicates)

    timestamp_issues = intraday_checks.validate_timestamps(
        minute_observations, session_start_iso=session_start_iso,
        session_end_iso=session_end_iso, now_iso=now_iso,
    )
    issues.extend(timestamp_issues)

    feed_issues = intraday_checks.analyze_feed_interruptions(capture_session_log_rows)
    issues.extend(feed_issues)

    aggregated = aggregate_one_minute_to_five_minute(minute_observations)
    five_min_issues = compare_five_minute_series(
        aggregated, five_minute_historical_rows, instrument=instrument, tolerance=five_minute_tolerance,
    )
    issues.extend(five_min_issues)

    latest_close = None
    if minute_observations:
        latest_close = sorted(minute_observations, key=lambda o: o.window_start)[-1].close
    bhavcopy_issue = None
    if latest_close is not None:
        bhavcopy_issue = compare_against_bhavcopy(
            latest_close, bhavcopy_row, close_field=bhavcopy_close_field, instrument=instrument,
        )
        if bhavcopy_issue is not None:
            issues.append(bhavcopy_issue)

    capture_status = "NO_DATA" if not minute_observations else (
        "PARTIAL" if missing else "COMPLETE"
    )
    five_min_summary = (
        f"{len(aggregated) - len([i for i in five_min_issues if i.category == CATEGORY_OHLC_MISMATCH])}"
        f"/{len(aggregated)} MATCH" if aggregated else "NO_DATA"
    )
    bhavcopy_summary = (
        "GREEN" if bhavcopy_issue is None and latest_close is not None
        else bhavcopy_issue.severity if bhavcopy_issue is not None else "NO_DATA"
    )

    return DailyIntegrityReport(
        session_date=session_date, capture_status=capture_status,
        missing_intervals=tuple(i.timestamp for i in missing),
        duplicate_records=tuple(i.evidence for i in duplicates),
        timestamp_errors=tuple(i.evidence for i in timestamp_issues),
        feed_interruptions=len(feed_issues),
        five_minute_comparison=five_min_summary,
        bhavcopy_comparison=bhavcopy_summary,
        quality_score=_quality_score(issues),
        issues=tuple(issues),
        overall_status=_overall_status(issues),
    )
