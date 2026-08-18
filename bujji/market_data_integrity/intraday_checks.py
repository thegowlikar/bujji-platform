"""bujji.market_data_integrity.intraday_checks — Phase 19.20.4.

Missing-minute detection reuses `bujji.market_observation.engine.
detect_gaps` VERBATIM for the actual gap-existence arithmetic (nominal
interval, 1.5x tolerance) — this module never reimplements that. What
this module adds is a thin, disclosed expansion step: given a detected
`SeriesGap`'s (after_timestamp, before_timestamp) boundary, it
enumerates the individual missing minute boundaries strictly between
them (a simple 60-second stepping loop, not gap-detection logic) so the
report can name exact missing timestamps rather than only "a gap exists
somewhere between X and Y".

Duplicate detection, timestamp validation, and feed-interruption
analysis operate on already-read data (in-memory `MinuteObservation`
lists / `capture_session_log` rows) — read-only, no store writes.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List, Sequence

from bujji.market_observation import taxonomy as moc_taxonomy
from bujji.market_observation.engine import build_observation, detect_gaps

from .models import (
    CATEGORY_DUPLICATE_DATA,
    CATEGORY_FEED_INTERRUPTION,
    CATEGORY_MISSING_DATA,
    CATEGORY_TIMESTAMP_ERROR,
    SEVERITY_ADVISORY,
    SEVERITY_FAILED,
    SEVERITY_WARNING,
    IntegrityIssue,
)

_ONE_MINUTE_SECONDS = 60


def _boundary_observation(instrument: str, timestamp: str, *, source: str):
    """A minimal, structurally-valid Observation used ONLY as a gap
    detection anchor (never persisted, never reported as real
    captured data). `source` distinguishes a synthetic session-boundary
    anchor from a real captured minute, purely for traceability."""
    return build_observation(
        observation_type=moc_taxonomy.TYPE_PRICE, instrument=instrument,
        exchange="NSE", segment="EQUITY", timestamp=timestamp,
        resolution=moc_taxonomy.RESOLUTION_ONE_MINUTE, source=source,
        schema_version=moc_taxonomy.MARKET_OBSERVATION_VERSION,
        value_kind=moc_taxonomy.VALUE_KIND_SCALAR, payload=0.0,
        completeness=1.0, freshness=0.0, confidence=None, missing_fields=(),
        validation_status=moc_taxonomy.VALIDATION_VALID,
        source_quality=moc_taxonomy.SOURCE_QUALITY_HIGH,
        originating_source=source, acquisition_timestamp=timestamp,
        normalization_timestamp=timestamp, origin=moc_taxonomy.ORIGIN_LIVE,
        provenance_version=moc_taxonomy.MARKET_OBSERVATION_VERSION, transformation_history=(),
    )


def detect_missing_minutes(
    observations: Sequence, *, instrument: str, session_start_iso: str, session_end_iso: str,
) -> List[IntegrityIssue]:
    """`observations`: MinuteObservation objects for ONE instrument,
    any order. Returns one IntegrityIssue per exact missing minute
    boundary within [session_start_iso, session_end_iso) — never a
    synthetic row, never inserted anywhere, only reported."""
    real_timestamps = sorted({o.window_start for o in observations})
    real_set = set(real_timestamps)

    anchor_timestamps: List[str] = []
    if not real_timestamps or real_timestamps[0] > session_start_iso:
        anchor_timestamps.append(session_start_iso)
    anchor_timestamps.extend(real_timestamps)
    if not real_timestamps or real_timestamps[-1] < session_end_iso:
        anchor_timestamps.append(session_end_iso)

    anchors = tuple(
        _boundary_observation(instrument, ts, source="market_data_integrity.boundary_anchor")
        for ts in anchor_timestamps
    )
    gaps = detect_gaps(anchors, moc_taxonomy.RESOLUTION_ONE_MINUTE)

    issues: List[IntegrityIssue] = []
    for gap in gaps:
        # Start AT the gap's own after_timestamp, not strictly after it:
        # when after_timestamp is a SYNTHETIC session-boundary anchor
        # (no real observation there), that minute is itself missing
        # too, not just the minutes strictly between the anchors. When
        # after_timestamp IS a real observation, `real_set` excludes it
        # from being reported, so behavior for interior gaps (between
        # two real observations) is unchanged.
        cursor = datetime.fromisoformat(gap.after_timestamp)
        end = datetime.fromisoformat(gap.before_timestamp)
        while cursor < end:
            missing_ts = cursor.isoformat()
            if missing_ts not in real_set:
                issues.append(IntegrityIssue(
                    severity=SEVERITY_WARNING, category=CATEGORY_MISSING_DATA,
                    description=f"missing 1-minute observation for {instrument}",
                    evidence=f"expected window_start={missing_ts}, none captured",
                    timestamp=missing_ts,
                ))
            cursor += timedelta(seconds=_ONE_MINUTE_SECONDS)
    return issues


def detect_duplicates(observations: Sequence) -> List[IntegrityIssue]:
    """Detects repeated (instrument, window_start) pairs WITHIN the
    given in-memory batch (e.g. a capture session's own output, before
    it reaches the store — the store's own UNIQUE natural_key
    constraint is the second, independent layer of defense at write
    time). Never deduplicates — every duplicate found is reported."""
    seen: Dict[tuple, object] = {}
    issues: List[IntegrityIssue] = []
    for obs in observations:
        key = (obs.instrument, obs.window_start)
        if key not in seen:
            seen[key] = obs
            continue
        prior = seen[key]
        identical = (prior.open, prior.high, prior.low, prior.close, prior.tick_count) == (
            obs.open, obs.high, obs.low, obs.close, obs.tick_count,
        )
        issues.append(IntegrityIssue(
            severity=SEVERITY_FAILED if not identical else SEVERITY_WARNING,
            category=CATEGORY_DUPLICATE_DATA,
            description=(
                "conflicting duplicate observation" if not identical else "redundant duplicate observation"
            ),
            evidence=f"instrument={obs.instrument!r} window_start={obs.window_start!r}",
            timestamp=obs.window_start,
        ))
    return issues


def validate_timestamps(
    observations: Sequence, *, session_start_iso: str, session_end_iso: str, now_iso: str,
) -> List[IntegrityIssue]:
    """`now_iso` is caller-supplied — never a wall-clock read inside
    this module, matching this codebase's established discipline."""
    issues: List[IntegrityIssue] = []
    now = datetime.fromisoformat(now_iso)

    for obs in observations:
        try:
            start = datetime.fromisoformat(obs.window_start)
            end = datetime.fromisoformat(obs.window_end)
        except (ValueError, TypeError):
            issues.append(IntegrityIssue(
                severity=SEVERITY_FAILED, category=CATEGORY_TIMESTAMP_ERROR,
                description="malformed ISO 8601 timestamp",
                evidence=f"instrument={obs.instrument!r} window_start={obs.window_start!r} "
                         f"window_end={obs.window_end!r}",
                timestamp=str(obs.window_start),
            ))
            continue

        if start > now or end > now:
            issues.append(IntegrityIssue(
                severity=SEVERITY_FAILED, category=CATEGORY_TIMESTAMP_ERROR,
                description="observation timestamped in the future",
                evidence=f"window=({obs.window_start!r}, {obs.window_end!r}), now={now_iso!r}",
                timestamp=obs.window_start,
            ))

        if end <= start:
            issues.append(IntegrityIssue(
                severity=SEVERITY_FAILED, category=CATEGORY_TIMESTAMP_ERROR,
                description="window_end is not after window_start",
                evidence=f"window_start={obs.window_start!r}, window_end={obs.window_end!r}",
                timestamp=obs.window_start,
            ))

        if obs.window_start < session_start_iso or obs.window_end > session_end_iso:
            issues.append(IntegrityIssue(
                severity=SEVERITY_WARNING, category=CATEGORY_TIMESTAMP_ERROR,
                description="observation falls outside the declared market session window",
                evidence=f"window=({obs.window_start!r}, {obs.window_end!r}), "
                         f"session=({session_start_iso!r}, {session_end_iso!r})",
                timestamp=obs.window_start,
            ))
    return issues


def analyze_feed_interruptions(session_log_rows: Sequence[dict]) -> List[IntegrityIssue]:
    """`session_log_rows`: dicts shaped like `capture_session_log` rows
    (session_date, started_at, stopped_at, connect_count,
    disconnect_events, last_error). Longest-interruption duration is
    NOT computable from this schema's granularity alone and is
    honestly omitted rather than guessed — a future schema addition
    (per-disconnect timestamps) would be required to compute it."""
    issues: List[IntegrityIssue] = []
    total_disconnects = sum(r.get("disconnect_events") or 0 for r in session_log_rows)
    last_errors = [r.get("last_error") for r in session_log_rows if r.get("last_error")]

    if total_disconnects > 0:
        issues.append(IntegrityIssue(
            severity=SEVERITY_FAILED if last_errors else SEVERITY_WARNING,
            category=CATEGORY_FEED_INTERRUPTION,
            description=f"{total_disconnects} feed disconnect event(s) recorded during the session",
            evidence=f"last_error={last_errors[-1] if last_errors else None!r}",
            timestamp=session_log_rows[-1]["started_at"] if session_log_rows else "",
        ))
    elif not session_log_rows:
        issues.append(IntegrityIssue(
            severity=SEVERITY_ADVISORY, category=CATEGORY_FEED_INTERRUPTION,
            description="no capture session log recorded for this date",
            evidence="capture_session_log has zero rows for this session_date",
            timestamp="",
        ))
    return issues
