from datetime import date, datetime, timezone

from bujji.mil_next import taxonomy as tx
from bujji.mil_next.event_calendar import (
    EventCalendarSource,
    load_calendar_view,
    resolve_event_risk_state,
)
from bujji.mil_next.models import EventCalendarEntry


def _source(version, eff_from, eff_to, entries=()):
    return EventCalendarSource(calendar_version=version, effective_from=eff_from, effective_to=eff_to, entries=entries)


def test_version_selection_by_effective_range():
    sources = [
        _source("v1", date(2026, 1, 1), date(2026, 6, 1)),
        _source("v2", date(2026, 6, 1), None),
    ]
    view = load_calendar_view(sources, date(2026, 7, 31))
    assert view.calendar_version == "v2"


def test_missing_coverage_is_none():
    sources = [_source("v1", date(2026, 1, 1), date(2026, 3, 1))]
    view = load_calendar_view(sources, date(2026, 7, 31))
    assert view is None


def test_resolve_coverage_unknown_when_no_view():
    state = resolve_event_risk_state(None, datetime(2026, 7, 31, 9, 15, tzinfo=timezone.utc))
    assert state == tx.COVERAGE_UNKNOWN


def test_resolve_known_event_inside_window():
    entry = EventCalendarEntry(
        label="RBI_POLICY", window_start=datetime(2026, 7, 31, 9, 0, tzinfo=timezone.utc),
        window_end=datetime(2026, 7, 31, 11, 0, tzinfo=timezone.utc),
    )
    sources = [_source("v1", date(2026, 1, 1), None, entries=(entry,))]
    view = load_calendar_view(sources, date(2026, 7, 31))
    state = resolve_event_risk_state(view, datetime(2026, 7, 31, 10, 0, tzinfo=timezone.utc))
    assert state == tx.KNOWN_EVENT


def test_resolve_known_no_event_outside_window():
    entry = EventCalendarEntry(
        label="RBI_POLICY", window_start=datetime(2026, 7, 31, 9, 0, tzinfo=timezone.utc),
        window_end=datetime(2026, 7, 31, 11, 0, tzinfo=timezone.utc),
    )
    sources = [_source("v1", date(2026, 1, 1), None, entries=(entry,))]
    view = load_calendar_view(sources, date(2026, 7, 31))
    state = resolve_event_risk_state(view, datetime(2026, 7, 31, 14, 0, tzinfo=timezone.utc))
    assert state == tx.KNOWN_NO_EVENT
