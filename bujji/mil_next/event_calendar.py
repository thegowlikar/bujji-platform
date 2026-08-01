"""Deterministic scheduled-event calendar -- BUJJI MIL Next.

Content ownership: a human operational role (Trading Operations)
maintains calendar entries; this module owns only loading/lookup code.
Loaded once per session (never hot-reloaded mid-session, matching this
codebase's own fixed-clock replay-determinism discipline elsewhere).
Coverage is three-valued -- KNOWN_NO_EVENT / KNOWN_EVENT /
COVERAGE_UNKNOWN -- because absence of a calendar entry is never proof
of absence of a real scheduled event.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional, Sequence

from . import taxonomy as tx
from .models import EventCalendarEntry, EventCalendarView


class EventCalendarSource:
    """A versioned, effective-dated calendar. Callers construct this
    from whatever operator-maintained config file exists; this
    laboratory does not read any file itself."""

    def __init__(
        self,
        calendar_version: str,
        effective_from: date,
        effective_to: Optional[date],
        entries: Sequence[EventCalendarEntry],
    ) -> None:
        self.calendar_version = calendar_version
        self.effective_from = effective_from
        self.effective_to = effective_to
        self.entries = tuple(entries)

    def covers(self, session_date: date) -> bool:
        if session_date < self.effective_from:
            return False
        if self.effective_to is not None and session_date >= self.effective_to:
            return False
        return True


def load_calendar_view(sources: Sequence[EventCalendarSource], session_date: date) -> Optional[EventCalendarView]:
    for source in sources:
        if source.covers(session_date):
            return EventCalendarView(
                calendar_version=source.calendar_version,
                effective_from=source.effective_from,
                effective_to=source.effective_to,
                entries=source.entries,
                coverage_state="COVERED",
            )
    return None


def resolve_event_risk_state(view: Optional[EventCalendarView], as_of_event_time: datetime) -> str:
    if view is None:
        return tx.COVERAGE_UNKNOWN
    for entry in view.entries:
        if entry.window_start <= as_of_event_time <= entry.window_end:
            return tx.KNOWN_EVENT
    return tx.KNOWN_NO_EVENT
