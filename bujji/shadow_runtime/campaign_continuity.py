"""Campaign Continuity Checker -- Shadow Runtime, Phase 19.17.

Read-only. Answers "what actually happened, per expected trading day,
across the campaign so far" by reusing exactly what already exists:
`MarketCalendar.is_trading_day()` (unmodified, Sprint 112 Deliverable
6), `validate_end_of_day_completeness()` (Phase 19.11/19.14.4,
unmodified), `hydrate_cycle_artifacts()`/`EVENT_SHADOW_INTELLIGENCE_CYCLE_FAILED`
(Phase 19.10.2, unmodified), `hydrate_daily_intelligence_artifacts()`
(Phase 19.13, unmodified). No new persistence mechanism, no new
database, no new event store, no new capture/intelligence logic --
this module only READS what those already-canonical components already
wrote.

Five-way classification, task 4's own required distinction, never
collapsed:

    NON_TRADING_DAY  -- calendar says this date was never expected to run.
    SESSION_COMPLETE -- a real DailyIntelligenceArtifact exists, gate passed, EOD complete.
    INCOMPLETE       -- a DailyIntelligenceArtifact exists but the gate/EOD check did not fully pass.
    SESSION_FAILED   -- a real, recorded failure exists (cycle failure event, or capture-without-intelligence).
    MISSING          -- an expected trading day with NO record anywhere -- never silently treated as success.
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from bujji.historical_reality.store import HistoricalObservationStore
from bujji.market_calendar import MarketCalendar
from bujji.market_reality_snapshot.models import RESOLUTION_FIVE_MINUTE
from bujji.shadow_runtime.completeness import validate_end_of_day_completeness
from bujji.shadow_runtime.cycle_artifact import EVENT_SHADOW_INTELLIGENCE_CYCLE_FAILED, hydrate_cycle_artifacts
from bujji.shadow_runtime.daily_intelligence_artifact import hydrate_daily_intelligence_artifacts

STATUS_NON_TRADING_DAY = "NON_TRADING_DAY"
STATUS_SESSION_COMPLETE = "SESSION_COMPLETE"
STATUS_INCOMPLETE = "INCOMPLETE"
STATUS_SESSION_FAILED = "SESSION_FAILED"
STATUS_MISSING = "MISSING"

# Derived from the single authority rather than restated. "matches capture's
# own MARKET_CLOSE constant" was true when written and is exactly the coupling
# that let four different closes drift apart.
from bujji.market_calendar import FO_MARKET_CLOSE  # noqa: E402

EOD_AS_OF_TIME_SUFFIX = f"T{FO_MARKET_CLOSE.strftime('%H:%M:%S')}+05:30"


@dataclass(frozen=True)
class SessionClassification:
    date: str
    status: str
    reason: str

    def to_dict(self) -> dict:
        return {"date": self.date, "status": self.status, "reason": self.reason}


@dataclass(frozen=True)
class CampaignContinuityReport:
    classifications: Tuple[SessionClassification, ...]
    expected_sessions: int
    completed: int
    incomplete: int
    failed: int
    missing: int
    non_trading_days: int
    current_streak: int
    gaps: Tuple[str, ...]  # dates classified MISSING among expected trading days -- the real, disclosed gaps.

    def to_dict(self) -> dict:
        return {
            "classifications": [c.to_dict() for c in self.classifications],
            "expected_sessions": self.expected_sessions, "completed": self.completed,
            "incomplete": self.incomplete, "failed": self.failed, "missing": self.missing,
            "non_trading_days": self.non_trading_days, "current_streak": self.current_streak,
            "gaps": list(self.gaps),
        }


def classify_session(
    date_str: str, *, calendar: MarketCalendar, historical_store: HistoricalObservationStore,
    cycle_artifact_store, daily_artifact_store, now: _dt.datetime,
) -> SessionClassification:
    """`cycle_artifact_store`/`daily_artifact_store`: the same `EventStore`
    instances (Phase 15B) the daily runtime itself already writes to --
    never a second store, never duplicated."""
    day = _dt.date.fromisoformat(date_str)
    is_trading, reason = calendar.is_trading_day(day)
    if not is_trading:
        return SessionClassification(date=date_str, status=STATUS_NON_TRADING_DAY, reason=reason)

    daily_artifacts = hydrate_daily_intelligence_artifacts(daily_artifact_store)
    matching_daily = [a for a in daily_artifacts.values() if a.session_date == date_str]

    cycle_artifacts = hydrate_cycle_artifacts(cycle_artifact_store)
    session_id = f"daily-{date_str}"
    matching_cycle_recorded = [a for a in cycle_artifacts.values() if a.session_id == session_id]

    failed_cycle_events = [
        e for e in cycle_artifact_store.read_events()
        if e.event_type == EVENT_SHADOW_INTELLIGENCE_CYCLE_FAILED and e.session_id == session_id
    ]

    eod_report = validate_end_of_day_completeness(
        date_str, historical_store=historical_store, now=now,
        resolution=RESOLUTION_FIVE_MINUTE, as_of_time=f"{date_str}{EOD_AS_OF_TIME_SUFFIX}",
    )

    if matching_daily:
        artifact = matching_daily[0]
        if artifact.completeness_gate_passed and eod_report.is_complete:
            return SessionClassification(date=date_str, status=STATUS_SESSION_COMPLETE, reason="daily artifact persisted, gate passed, EOD complete")
        return SessionClassification(
            date=date_str, status=STATUS_INCOMPLETE,
            reason=f"daily artifact exists but gate_passed={artifact.completeness_gate_passed} eod_is_complete={eod_report.is_complete}",
        )

    if failed_cycle_events:
        last_error = failed_cycle_events[-1].payload.get("error", "unknown")
        return SessionClassification(date=date_str, status=STATUS_SESSION_FAILED, reason=f"cycle failure recorded: {last_error}")

    any_capture = eod_report.spot_present or eod_report.options_present or eod_report.vix_present
    if not any_capture and not matching_cycle_recorded:
        return SessionClassification(date=date_str, status=STATUS_MISSING, reason="expected trading day with no capture, no cycle record, no artifact anywhere")

    return SessionClassification(
        date=date_str, status=STATUS_SESSION_FAILED,
        reason="capture/cycle data present but no successful daily artifact was ever persisted",
    )


def build_campaign_continuity_report(
    start_date: str, end_date: str, *, calendar: MarketCalendar,
    historical_store: HistoricalObservationStore, cycle_artifact_store, daily_artifact_store,
    now: _dt.datetime,
) -> CampaignContinuityReport:
    start = _dt.date.fromisoformat(start_date)
    end = _dt.date.fromisoformat(end_date)
    if end < start:
        raise ValueError(f"end_date {end_date!r} is before start_date {start_date!r}")

    classifications: List[SessionClassification] = []
    day = start
    while day <= end:
        classifications.append(classify_session(
            day.isoformat(), calendar=calendar, historical_store=historical_store,
            cycle_artifact_store=cycle_artifact_store, daily_artifact_store=daily_artifact_store, now=now,
        ))
        day += _dt.timedelta(days=1)

    completed = sum(1 for c in classifications if c.status == STATUS_SESSION_COMPLETE)
    incomplete = sum(1 for c in classifications if c.status == STATUS_INCOMPLETE)
    failed = sum(1 for c in classifications if c.status == STATUS_SESSION_FAILED)
    missing = sum(1 for c in classifications if c.status == STATUS_MISSING)
    non_trading = sum(1 for c in classifications if c.status == STATUS_NON_TRADING_DAY)
    expected = len(classifications) - non_trading

    # Current streak: walk backwards from the most recent date, counting
    # consecutive SESSION_COMPLETE trading days; non-trading days are
    # skipped (they don't break continuity), any other status stops it.
    streak = 0
    for c in reversed(classifications):
        if c.status == STATUS_NON_TRADING_DAY:
            continue
        if c.status == STATUS_SESSION_COMPLETE:
            streak += 1
        else:
            break

    gaps = tuple(c.date for c in classifications if c.status == STATUS_MISSING)

    return CampaignContinuityReport(
        classifications=tuple(classifications), expected_sessions=expected, completed=completed,
        incomplete=incomplete, failed=failed, missing=missing, non_trading_days=non_trading,
        current_streak=streak, gaps=gaps,
    )
