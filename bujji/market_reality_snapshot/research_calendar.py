"""ResearchCalendar — Phase 18.7.

Read-only over Reality data. Reuses, does not duplicate:
`market_calendar.MarketCalendar.is_trading_day()` (Sprint 112, wired in
for the first time -- Phase 18.6's own audit found it existed but had
zero real call sites) and `readiness.check_research_session_readiness()`
(Phase 18.5) for every per-date instrument-availability fact. This
module adds no new store, no new query -- only a per-date STATUS
classification over facts those two already compute.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Optional, Tuple

from bujji.historical_reality.store import HistoricalObservationStore
from bujji.market_calendar import MarketCalendar

from .models import RESOLUTION_DAILY
from .readiness import ResearchSessionReadiness, check_research_session_readiness

STATUS_READY = "READY"                  # every required instrument present.
STATUS_PARTIAL = "PARTIAL"              # some, not all, instruments present.
STATUS_FAILED = "FAILED"                # a real (or possibly-real -- see
                                          # NON_TRADING_DAY's own caveat)
                                          # trading day with zero data.
# Not one of this phase's own literal three requested states -- added
# because "identify trading sessions" (this phase's own requirement 1)
# is not honestly implementable without it: without a distinct state, a
# real weekend/holiday with zero data would have to report FAILED,
# falsely implying a capture gap. Restricted strictly to what
# `MarketCalendar.is_trading_day()` can ACTUALLY verify offline
# (weekends by real date arithmetic; explicit manual closures) -- an
# unlisted real NSE holiday, which the calendar's own
# `holiday_calendar_verified=False` cannot rule out, still reports
# FAILED, honestly reflecting "no data, and this module cannot prove
# it's not a gap" (Phase 18.6 §1's own disclosed limitation, preserved
# here rather than silently resolved).
STATUS_NON_TRADING_DAY = "NON_TRADING_DAY"
ALL_STATUSES = (STATUS_READY, STATUS_PARTIAL, STATUS_FAILED, STATUS_NON_TRADING_DAY)


@dataclass(frozen=True)
class ResearchCalendarEntry:
    date: str
    resolution: str
    is_trading_day: Optional[bool]      # None only if the calendar itself was not consulted (never here).
    trading_day_reason: Optional[str]   # MarketCalendar.is_trading_day()'s own real explanation.
    readiness: ResearchSessionReadiness
    status: str

    def to_dict(self) -> dict:
        return {
            "date": self.date, "resolution": self.resolution,
            "is_trading_day": self.is_trading_day, "trading_day_reason": self.trading_day_reason,
            "readiness": self.readiness.to_dict(), "status": self.status,
        }


def _classify_status(is_trading_day: bool, readiness: ResearchSessionReadiness) -> str:
    if not is_trading_day:
        return STATUS_NON_TRADING_DAY
    if readiness.is_complete:
        return STATUS_READY
    any_present = readiness.spot_available or readiness.futures_available \
        or readiness.vix_available or readiness.options_available
    return STATUS_PARTIAL if any_present else STATUS_FAILED


def build_research_calendar_entry(
    date: str,
    *,
    historical_store: HistoricalObservationStore,
    resolution: str = RESOLUTION_DAILY,
    as_of_time: Optional[str] = None,
    calendar: Optional[MarketCalendar] = None,
) -> ResearchCalendarEntry:
    calendar = calendar or MarketCalendar()
    is_trading_day, reason = calendar.is_trading_day(datetime.date.fromisoformat(date))
    readiness = check_research_session_readiness(
        date, historical_store=historical_store, resolution=resolution, as_of_time=as_of_time,
    )
    status = _classify_status(is_trading_day, readiness)
    return ResearchCalendarEntry(
        date=date, resolution=resolution, is_trading_day=is_trading_day,
        trading_day_reason=reason, readiness=readiness, status=status,
    )


def build_research_calendar(
    start_date: str,
    end_date: str,
    *,
    historical_store: HistoricalObservationStore,
    resolution: str = RESOLUTION_DAILY,
    as_of_time_of_day: Optional[str] = None,
    calendar: Optional[MarketCalendar] = None,
) -> Tuple[ResearchCalendarEntry, ...]:
    """Every date in [start_date, end_date], inclusive, in order. Never
    skips a date, never guesses a status for one it hasn't checked --
    every date gets a real `build_research_calendar_entry()` call.

    `as_of_time_of_day` is a TIME-OF-DAY suffix only (e.g.
    `"09:20:00+05:30"`), combined with EACH date individually to form
    that date's own `as_of_time` -- a single shared full timestamp
    across a multi-date range would be wrong for every date except the
    one it literally names (PHASE_18_7's own fix: an earlier draft of
    this function accepted a full `as_of_time` and applied it
    unchanged to every date, which is a real correctness bug for
    `RESOLUTION_FIVE_MINUTE` ranges -- caught before deployment, not
    shipped)."""
    calendar = calendar or MarketCalendar()
    start = datetime.date.fromisoformat(start_date)
    end = datetime.date.fromisoformat(end_date)
    if end < start:
        raise ValueError(f"end_date {end_date!r} is before start_date {start_date!r}")
    entries = []
    current = start
    while current <= end:
        date_str = current.isoformat()
        as_of_time = f"{date_str}T{as_of_time_of_day}" if as_of_time_of_day else None
        entries.append(build_research_calendar_entry(
            date_str, historical_store=historical_store,
            resolution=resolution, as_of_time=as_of_time, calendar=calendar,
        ))
        current += datetime.timedelta(days=1)
    return tuple(entries)
