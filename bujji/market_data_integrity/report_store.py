"""bujji.market_data_integrity.report_store — Phase 19.20.4.

Append-only persistence for `DailyIntegrityReport`, via the SAME
`bujji.state_persistence.store.EventStore` every other Phase 19.x
artifact already uses (`cycle_artifact.py`, `daily_intelligence_
artifact.py`) — no new persistence mechanism invented.

Idempotency: `event_id` is a deterministic hash of `session_date` +
the report's own content (mirrors `cycle_artifact.py`'s own
`fingerprint_state`-based idempotency) — re-running reconciliation for
a day whose data hasn't changed produces the SAME event_id and is a
safe no-op on replay. Re-running with genuinely different underlying
data (e.g. late-arriving capture) produces a DIFFERENT event, appended
as a new fact — the file itself never truncates, never overwrites, and
every report ever recorded remains readable forever; `hydrate_
integrity_reports` exposes only the MOST RECENT report per date as a
read-time convenience, which is an in-memory reduction over an
untouched, fully immutable file, not a mutation of it.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Dict

from .models import DailyIntegrityReport

EVENT_MARKET_INTEGRITY_REPORT_RECORDED = "MARKET_INTEGRITY_REPORT_RECORDED"


def _report_event_id(report: DailyIntegrityReport) -> str:
    seed = report.session_date + "|" + json.dumps(report.to_dict(), sort_keys=True)
    return "MIR-" + hashlib.md5(seed.encode()).hexdigest()[:24]


def record_integrity_report(store, report: DailyIntegrityReport, *, recorded_at: datetime) -> None:
    """Appends ONE immutable fact. `store` is a
    `bujji.state_persistence.store.EventStore` instance, constructed
    and owned by the caller — this function never opens its own file
    handle to any other store."""
    from bujji.state_persistence.models import PersistedEvent

    event = PersistedEvent(
        event_id=_report_event_id(report), event_type=EVENT_MARKET_INTEGRITY_REPORT_RECORDED,
        session_id=report.session_date, cycle_id=None,
        timestamp=recorded_at.isoformat(), schema_version="1.0.0",
        provenance="market_data_integrity.report_store.record_integrity_report",
        payload=report.to_dict(),
    )
    store.append(event)


def hydrate_integrity_reports(store) -> Dict[str, DailyIntegrityReport]:
    """Cross-session, keyed by session_date. When more than one report
    exists for the same date (a genuine re-run with different content),
    the LAST one appended wins for this read-time view — the file
    itself retains every report, immutably, in order."""
    reports: Dict[str, DailyIntegrityReport] = {}
    for event in store.read_events():
        if event.event_type == EVENT_MARKET_INTEGRITY_REPORT_RECORDED:
            report = DailyIntegrityReport.from_dict(event.payload)
            reports[report.session_date] = report
    return reports
