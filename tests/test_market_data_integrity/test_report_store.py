"""Phase 19.20.4 -- report_store tests: append-only, idempotent, never overwrites."""
from __future__ import annotations

from datetime import datetime, timezone

from bujji.market_data_integrity.models import DailyIntegrityReport, STATUS_GREEN, STATUS_FAILED
from bujji.market_data_integrity.report_store import hydrate_integrity_reports, record_integrity_report
from bujji.state_persistence.store import EventStore

DAY = "2026-08-17"


def _report(status=STATUS_GREEN, quality=100.0):
    return DailyIntegrityReport(
        session_date=DAY, capture_status="COMPLETE", missing_intervals=(), duplicate_records=(),
        timestamp_errors=(), feed_interruptions=0, five_minute_comparison="1/1 MATCH",
        bhavcopy_comparison="GREEN", quality_score=quality, issues=(), overall_status=status,
    )


def test_record_and_hydrate_round_trip(tmp_path):
    store = EventStore(str(tmp_path / "reports.jsonl"))
    report = _report()
    record_integrity_report(store, report, recorded_at=datetime(2026, 8, 17, 16, 0, tzinfo=timezone.utc))

    hydrated = hydrate_integrity_reports(store)
    assert DAY in hydrated
    assert hydrated[DAY].to_dict() == report.to_dict()


def test_recording_the_identical_report_twice_is_idempotent_one_effective_report(tmp_path):
    from bujji.state_persistence.store import deduplicated_events

    store = EventStore(str(tmp_path / "reports.jsonl"))
    report = _report()
    record_integrity_report(store, report, recorded_at=datetime(2026, 8, 17, 16, 0, tzinfo=timezone.utc))
    record_integrity_report(store, report, recorded_at=datetime(2026, 8, 17, 16, 5, tzinfo=timezone.utc))

    # Both calls physically append (EventStore.append is pure append,
    # never conditional) -- but they share the SAME content-derived
    # event_id, so deduplicated_events() (first-occurrence-wins) and
    # hydrate_integrity_reports()'s dict-keyed-by-date view both
    # collapse to exactly one effective report -- no double-counting.
    events = list(store.read_events())
    assert len(events) == 2
    assert len(deduplicated_events(events)) == 1
    hydrated = hydrate_integrity_reports(store)
    assert len(hydrated) == 1


def test_a_different_report_for_the_same_date_is_appended_not_overwritten(tmp_path):
    """The file is append-only and immutable -- a genuinely different
    re-run for the same date adds a NEW event, never replaces the old
    one in the file. hydrate() exposing only the latest is a read-time
    view, not a file mutation."""
    store = EventStore(str(tmp_path / "reports.jsonl"))
    first = _report(status=STATUS_GREEN, quality=100.0)
    second = _report(status=STATUS_FAILED, quality=40.0)

    record_integrity_report(store, first, recorded_at=datetime(2026, 8, 17, 16, 0, tzinfo=timezone.utc))
    record_integrity_report(store, second, recorded_at=datetime(2026, 8, 17, 17, 0, tzinfo=timezone.utc))

    events = list(store.read_events())
    assert len(events) == 2   # BOTH facts remain in the file, immutably

    hydrated = hydrate_integrity_reports(store)
    assert len(hydrated) == 1               # read-time view: one entry per date
    assert hydrated[DAY].overall_status == STATUS_FAILED   # the latest one


def test_hydrate_on_empty_store_returns_empty_dict(tmp_path):
    store = EventStore(str(tmp_path / "reports.jsonl"))
    assert hydrate_integrity_reports(store) == {}
