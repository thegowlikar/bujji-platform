"""Phase 20.14 -- Shadow Decision Campaign continuity. Read-only.
Answers "what actually happened, per expected trading day, across the
Cycle-1 shadow decision campaign so far" -- the SAME five-way
classification discipline `bujji.shadow_runtime.campaign_continuity`
(Phase 19.17) already established for the unrelated Phase 19.x
lineage, reused here as a PATTERN (never as a shared import, since
that module's own internals hydrate Phase 19.x-specific artifact
stores -- `daily_intelligence_artifact`/`cycle_artifact` -- that this
lineage does not write to; importing it would create exactly the kind
of sideways lineage coupling the Interface Map exists to prevent).

Reuses `bujji.market_calendar.MarketCalendar.is_trading_day()`
(unmodified) and this package's own `load_campaign_artifacts()`
(Phase 20.13/20.14, unmodified) -- no new persistence mechanism, no
new store.
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from typing import Dict, List, Tuple

from bujji.market_calendar import MarketCalendar

from .persistence import load_campaign_artifacts

STATUS_NON_TRADING_DAY = "NON_TRADING_DAY"
STATUS_SESSION_COMPLETE = "SESSION_COMPLETE"
STATUS_INCOMPLETE = "INCOMPLETE"
STATUS_SESSION_FAILED = "SESSION_FAILED"
STATUS_MISSING = "MISSING"


@dataclass(frozen=True)
class SessionClassification:
    date: str
    status: str
    reason: str


@dataclass(frozen=True)
class CampaignContinuityReport:
    start_date: str
    end_date: str
    classifications: Tuple[SessionClassification, ...]
    status_counts: Dict[str, int]

    def render(self) -> str:
        lines = [f"Shadow Decision Campaign Continuity {self.start_date} .. {self.end_date}",
                  f"  {self.status_counts}"]
        for c in self.classifications:
            if c.status != STATUS_NON_TRADING_DAY:
                lines.append(f"  {c.date}: {c.status} -- {c.reason}")
        return "\n".join(lines)


def classify_session(date_str: str, *, calendar: MarketCalendar, artifact_dir: str) -> SessionClassification:
    """`artifact_dir`: the directory `save_campaign_artifact()` writes
    `<date>.jsonl` files into (Phase 20.13's own `--artifact-path`
    convention: `data/live_shadow_campaign/<date>.jsonl`)."""
    day = _dt.date.fromisoformat(date_str)
    is_trading, reason = calendar.is_trading_day(day)
    if not is_trading:
        return SessionClassification(date=date_str, status=STATUS_NON_TRADING_DAY, reason=reason)

    artifact_path = f"{artifact_dir.rstrip('/')}/{date_str}.jsonl"
    records = load_campaign_artifacts(artifact_path)
    if not records:
        return SessionClassification(date=date_str, status=STATUS_MISSING,
                                      reason="expected trading day with no campaign artifact file")

    types = [r.get("artifact_type") for r in records]
    health_records = [r["payload"] for r in records if r.get("artifact_type") == "HealthReport"]

    if health_records and health_records[-1].get("runtime_status") == "FAILED":
        return SessionClassification(date=date_str, status=STATUS_SESSION_FAILED,
                                      reason=f"HealthReport recorded runtime_status=FAILED: "
                                             f"{health_records[-1].get('reasons')}")

    has_session = "CampaignSession" in types
    has_metrics = "CampaignMetrics" in types
    has_health = "HealthReport" in types

    if has_session and has_metrics and has_health:
        return SessionClassification(date=date_str, status=STATUS_SESSION_COMPLETE,
                                      reason="DecisionObservation, HealthReport, CampaignSession, and "
                                             "CampaignMetrics all recorded")

    return SessionClassification(date=date_str, status=STATUS_INCOMPLETE,
                                  reason=f"artifact file exists but session did not fully close "
                                         f"(has_session={has_session}, has_metrics={has_metrics}, "
                                         f"has_health={has_health})")


def build_campaign_continuity_report(
    start_date: str, end_date: str, *, calendar: MarketCalendar, artifact_dir: str,
) -> CampaignContinuityReport:
    start = _dt.date.fromisoformat(start_date)
    end = _dt.date.fromisoformat(end_date)
    if end < start:
        raise ValueError(f"end_date {end_date!r} precedes start_date {start_date!r}")

    classifications: List[SessionClassification] = []
    day = start
    while day <= end:
        classifications.append(classify_session(day.isoformat(), calendar=calendar, artifact_dir=artifact_dir))
        day += _dt.timedelta(days=1)

    counts: Dict[str, int] = {}
    for c in classifications:
        counts[c.status] = counts.get(c.status, 0) + 1

    return CampaignContinuityReport(
        start_date=start_date, end_date=end_date,
        classifications=tuple(classifications), status_counts=counts,
    )
