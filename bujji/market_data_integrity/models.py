"""bujji.market_data_integrity.models — Phase 19.20.4.

`IntegrityIssue` and `DailyIntegrityReport` — both frozen. A report,
once built, is never mutated; a re-run for the same date produces a
NEW report object, appended as a new fact by report_store.py, never an
edit of a prior one.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Tuple

# --- Severity -------------------------------------------------------------
SEVERITY_ADVISORY = "ADVISORY"
SEVERITY_WARNING = "WARNING"
SEVERITY_FAILED = "FAILED"
ALL_SEVERITIES = (SEVERITY_ADVISORY, SEVERITY_WARNING, SEVERITY_FAILED)

# --- Category ---------------------------------------------------------------
CATEGORY_MISSING_DATA = "MISSING_DATA"
CATEGORY_DUPLICATE_DATA = "DUPLICATE_DATA"
CATEGORY_TIMESTAMP_ERROR = "TIMESTAMP_ERROR"
CATEGORY_FEED_INTERRUPTION = "FEED_INTERRUPTION"
CATEGORY_OHLC_MISMATCH = "OHLC_MISMATCH"
CATEGORY_BHAVCOPY_MISMATCH = "BHAVCOPY_MISMATCH"
ALL_CATEGORIES = (
    CATEGORY_MISSING_DATA, CATEGORY_DUPLICATE_DATA, CATEGORY_TIMESTAMP_ERROR,
    CATEGORY_FEED_INTERRUPTION, CATEGORY_OHLC_MISMATCH, CATEGORY_BHAVCOPY_MISMATCH,
)

# --- Overall status ---------------------------------------------------------
STATUS_GREEN = "GREEN"
STATUS_WARNING = "WARNING"
STATUS_FAILED = "FAILED"
ALL_STATUSES = (STATUS_GREEN, STATUS_WARNING, STATUS_FAILED)


@dataclass(frozen=True)
class IntegrityIssue:
    severity: str            # one of ALL_SEVERITIES
    category: str            # one of ALL_CATEGORIES
    description: str
    evidence: str            # human-readable specifics (exact timestamps, values compared, etc.)
    timestamp: str            # when the issue occurred/was detected in the data, not wall-clock detection time

    def to_dict(self) -> dict:
        return {
            "severity": self.severity, "category": self.category,
            "description": self.description, "evidence": self.evidence, "timestamp": self.timestamp,
        }

    @staticmethod
    def from_dict(d: dict) -> "IntegrityIssue":
        return IntegrityIssue(
            severity=d["severity"], category=d["category"],
            description=d["description"], evidence=d["evidence"], timestamp=d["timestamp"],
        )


@dataclass(frozen=True)
class DailyIntegrityReport:
    session_date: str
    capture_status: str                          # e.g. "COMPLETE" | "PARTIAL" | "NO_DATA"
    missing_intervals: Tuple[str, ...]             # exact missing window_start timestamps
    duplicate_records: Tuple[str, ...]             # natural keys / descriptions of duplicates found
    timestamp_errors: Tuple[str, ...]
    feed_interruptions: int
    five_minute_comparison: str                    # summary string: e.g. "3/3 MATCH" or "1 MISMATCH"
    bhavcopy_comparison: str                        # summary string
    quality_score: float                           # 0-100
    issues: Tuple[IntegrityIssue, ...] = field(default_factory=tuple)
    overall_status: str = STATUS_GREEN

    def to_dict(self) -> dict:
        return {
            "session_date": self.session_date, "capture_status": self.capture_status,
            "missing_intervals": list(self.missing_intervals),
            "duplicate_records": list(self.duplicate_records),
            "timestamp_errors": list(self.timestamp_errors),
            "feed_interruptions": self.feed_interruptions,
            "five_minute_comparison": self.five_minute_comparison,
            "bhavcopy_comparison": self.bhavcopy_comparison,
            "quality_score": self.quality_score,
            "issues": [i.to_dict() for i in self.issues],
            "overall_status": self.overall_status,
        }

    @staticmethod
    def from_dict(d: dict) -> "DailyIntegrityReport":
        return DailyIntegrityReport(
            session_date=d["session_date"], capture_status=d["capture_status"],
            missing_intervals=tuple(d.get("missing_intervals") or ()),
            duplicate_records=tuple(d.get("duplicate_records") or ()),
            timestamp_errors=tuple(d.get("timestamp_errors") or ()),
            feed_interruptions=d.get("feed_interruptions", 0),
            five_minute_comparison=d.get("five_minute_comparison", ""),
            bhavcopy_comparison=d.get("bhavcopy_comparison", ""),
            quality_score=d.get("quality_score", 0.0),
            issues=tuple(IntegrityIssue.from_dict(i) for i in (d.get("issues") or ())),
            overall_status=d.get("overall_status", STATUS_GREEN),
        )
