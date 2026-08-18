#!/usr/bin/env python
"""Bujji Shadow Intelligence Campaign -- operator status command, Phase 19.17.

    python bujji_campaign_status.py --campaign-start-date YYYY-MM-DD [--json]

Read-only. Combines two already-existing, unmodified read paths -- never
a third status mechanism:

- `bujji.shadow_runtime.status.get_operational_status()` (Phase 19.12)
  for TODAY's live/latest snapshot (the heartbeat file is real-time but
  overwritten daily, so it is the right source for "right now," never
  for history).
- `bujji.shadow_runtime.campaign_continuity.build_campaign_continuity_report()`
  (Phase 19.17) for the campaign-to-date aggregate, reusing the same
  persisted artifacts/events the daily runtime itself already wrote.

Never fabricates a campaign history -- `build_campaign_continuity_report`
classifies every day genuinely, including MISSING when nothing was
recorded; this script only renders what that report actually found.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone

from bujji.historical_reality.store import HistoricalObservationStore
from bujji.market_calendar import MarketCalendar
from bujji.shadow_runtime.campaign_continuity import (
    STATUS_SESSION_COMPLETE,
    build_campaign_continuity_report,
)
from bujji.shadow_runtime.status import get_operational_status
from bujji.state_persistence.store import EventStore

HISTORICAL_STORE_PATH = "data/historical_reality/normalized/historical_observations.db"


def _fyers_field(last_failure) -> str:
    if last_failure is None:
        return "OK"
    lowered = last_failure.lower()
    if "fyers" in lowered or "authenticationerror" in lowered or "not set in environment" in lowered:
        return "FAILED (FYERS_AUTHENTICATION)"
    return "OK"


def _bool_field(value, true_label: str, false_label: str, unknown_label: str = "PENDING") -> str:
    if value is None:
        return unknown_label
    return true_label if value else false_label


def render_campaign_box(*, today_status, today_classification_status: str, continuity, today_date: str) -> str:
    overall = "HEALTHY" if continuity.missing == 0 and continuity.failed == 0 else "ATTENTION_NEEDED"

    if today_status.lifecycle_state == "UNKNOWN":
        # No heartbeat file exists at all for today yet -- honestly
        # distinct from "ran and failed." Never presented as FAILED,
        # which would misleadingly imply an attempt happened.
        capture_field = intelligence_field = eod_field = replay_field = "NO_DATA_YET"
    else:
        capture_field = _bool_field(
            today_status.last_successful_observation is not None, "COMPLETE", "FAILED", unknown_label="PENDING",
        )
        intelligence_field = _bool_field(
            today_status.last_heartbeat_at is not None and today_status.last_failure is None
            and today_status.lifecycle_state == STATUS_SESSION_COMPLETE,
            "COMPLETE", "FAILED" if today_status.last_failure else "PENDING", unknown_label="PENDING",
        )
        eod_field = "PASS" if today_status.lifecycle_state == STATUS_SESSION_COMPLETE else (
            "FAIL" if today_status.last_failure and "completeness" in (today_status.last_failure or "").lower() else "PENDING"
        )
        replay_field = "MATCH" if today_status.lifecycle_state == STATUS_SESSION_COMPLETE else (
            "MISMATCH" if today_status.last_failure and "replay" in (today_status.last_failure or "").lower() else "PENDING"
        )
    artifact_field = "PERSISTED" if today_classification_status == STATUS_SESSION_COMPLETE else "NOT_PERSISTED"

    lines = [
        "+------------------------------------------------+",
        "| BUJJI SHADOW INTELLIGENCE CAMPAIGN              |",
        "|                                                  |",
        f"| Today's session ({today_date}): {today_classification_status}",
        f"| FYERS:                 {_fyers_field(today_status.last_failure)}",
        f"| Capture:               {capture_field}",
        f"| Intelligence:          {intelligence_field}",
        f"| EOD completeness:      {eod_field}",
        f"| LIVE/REPLAY:           {replay_field}",
        f"| Artifact:              {artifact_field}",
        f"| Heartbeat stale:       {'YES' if today_status.stale else 'no'}",
        "|                                                  |",
        f"| Expected sessions:     {continuity.expected_sessions}",
        f"| Completed:             {continuity.completed}",
        f"| Incomplete:            {continuity.incomplete}",
        f"| Failed:                {continuity.failed}",
        f"| Missing:               {continuity.missing}",
        f"| Non-trading days:      {continuity.non_trading_days}",
        f"| Current streak:        {continuity.current_streak}",
        "|                                                  |",
        f"| Overall:               {overall}",
        "+------------------------------------------------+",
    ]
    if continuity.gaps:
        lines.append("")
        lines.append(f"GAPS (expected trading days with no record at all): {', '.join(continuity.gaps)}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-start-date", required=True, help="YYYY-MM-DD -- first day of the campaign window to report on")
    parser.add_argument("--heartbeat-path", default="data/daily_session_heartbeat.json")
    parser.add_argument("--lock-path", default="data/daily_intelligence.lock")
    parser.add_argument("--cycle-artifact-store-path", default="data/shadow_intelligence_cycle_artifacts.jsonl")
    parser.add_argument("--daily-artifact-store-path", default="data/daily_intelligence_artifacts.jsonl")
    parser.add_argument("--historical-store-path", default=HISTORICAL_STORE_PATH)
    parser.add_argument("--as-of-date", default=None, help="YYYY-MM-DD -- defaults to today (real wall clock)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    today_date = args.as_of_date or date.today().isoformat()

    today_status = get_operational_status(args.heartbeat_path, lock_path=args.lock_path, now=now)

    calendar = MarketCalendar()
    historical_store = HistoricalObservationStore(args.historical_store_path)
    cycle_artifact_store = EventStore(args.cycle_artifact_store_path)
    daily_artifact_store = EventStore(args.daily_artifact_store_path)

    continuity = build_campaign_continuity_report(
        args.campaign_start_date, today_date, calendar=calendar, historical_store=historical_store,
        cycle_artifact_store=cycle_artifact_store, daily_artifact_store=daily_artifact_store, now=now,
    )
    today_classification = next((c for c in continuity.classifications if c.date == today_date), None)
    today_classification_status = today_classification.status if today_classification else "UNKNOWN"

    if args.json:
        print(json.dumps({
            "today_status": today_status.to_dict(), "today_classification": today_classification_status,
            "continuity": continuity.to_dict(),
        }))
    else:
        print(render_campaign_box(
            today_status=today_status, today_classification_status=today_classification_status,
            continuity=continuity, today_date=today_date,
        ))
    return 0


if __name__ == "__main__":
    sys.exit(main())
