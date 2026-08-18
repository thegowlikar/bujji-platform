"""Phase 20.14 -- Shadow Decision Campaign continuity tests."""
from __future__ import annotations

import os

import pytest

from bujji.market_calendar import MarketCalendar
from bujji.live_shadow_runner import (
    STATUS_INCOMPLETE, STATUS_MISSING, STATUS_NON_TRADING_DAY,
    STATUS_SESSION_COMPLETE, STATUS_SESSION_FAILED,
    build_campaign_continuity_report, classify_session, save_campaign_artifact,
)
from bujji.live_shadow_runner.models import HealthReport, FeedHealth, IntelligenceHealth, RUNTIME_FAILED, RUNTIME_HEALTHY
from bujji.shadow_market_campaign import CampaignSession, CampaignMetrics


def _health(date_str, status):
    return HealthReport(
        session_date=date_str, runtime_status=status,
        feed_health=FeedHealth(last_observation_timestamp=f"{date_str}T09:30:00+05:30",
                                missing_intervals=(), data_fresh=True),
        intelligence_health=IntelligenceHealth(decision_cycles_completed=1, missing_intelligence_count=0,
                                                uncertainty_frequency=1.0),
        reasons=(),
    )


def _session(date_str):
    return CampaignSession(
        session_date=date_str, market_open_time=f"{date_str}T09:15:00+05:30",
        market_close_time=f"{date_str}T15:30:00+05:30", observation_count=1, decision_count=1,
        data_quality_summary={"SUFFICIENT": 1}, uncertainty_summary={}, decision_distribution={"WATCH": 1},
        health_status="HEALTHY",
    )


def _metrics(date_str):
    return CampaignMetrics(
        session_date=date_str, decision_change_count=0, decision_stability="LOW",
        confidence_oscillation_count=0, pct_complete_intelligence=1.0, pct_insufficient_intelligence=0.0,
        explanation_completeness_pct=1.0, market_coverage_pct=1.0, missing_intervals=(),
    )


# A real Monday in the codebase's own known-good window.
TRADING_DAY = "2026-08-17"
WEEKEND_DAY = "2026-08-16"  # real Sunday.


def test_non_trading_day_classified_without_touching_disk(tmp_path):
    calendar = MarketCalendar()
    result = classify_session(WEEKEND_DAY, calendar=calendar, artifact_dir=str(tmp_path))
    assert result.status == STATUS_NON_TRADING_DAY
    assert "weekend" in result.reason.lower()


def test_missing_day_when_no_artifact_file_exists(tmp_path):
    calendar = MarketCalendar()
    result = classify_session(TRADING_DAY, calendar=calendar, artifact_dir=str(tmp_path))
    assert result.status == STATUS_MISSING


def test_session_complete_when_all_four_artifacts_present(tmp_path):
    path = tmp_path / f"{TRADING_DAY}.jsonl"
    save_campaign_artifact(str(path), "DecisionObservation", _health(TRADING_DAY, RUNTIME_HEALTHY))  # placeholder record
    save_campaign_artifact(str(path), "HealthReport", _health(TRADING_DAY, RUNTIME_HEALTHY))
    save_campaign_artifact(str(path), "CampaignSession", _session(TRADING_DAY))
    save_campaign_artifact(str(path), "CampaignMetrics", _metrics(TRADING_DAY))

    calendar = MarketCalendar()
    result = classify_session(TRADING_DAY, calendar=calendar, artifact_dir=str(tmp_path))
    assert result.status == STATUS_SESSION_COMPLETE


def test_incomplete_when_session_never_closed(tmp_path):
    path = tmp_path / f"{TRADING_DAY}.jsonl"
    save_campaign_artifact(str(path), "HealthReport", _health(TRADING_DAY, RUNTIME_HEALTHY))
    # No CampaignSession/CampaignMetrics -- the process presumably crashed mid-session.

    calendar = MarketCalendar()
    result = classify_session(TRADING_DAY, calendar=calendar, artifact_dir=str(tmp_path))
    assert result.status == STATUS_INCOMPLETE


def test_session_failed_when_health_report_says_failed(tmp_path):
    path = tmp_path / f"{TRADING_DAY}.jsonl"
    save_campaign_artifact(str(path), "HealthReport", _health(TRADING_DAY, RUNTIME_FAILED))
    save_campaign_artifact(str(path), "CampaignSession", _session(TRADING_DAY))
    save_campaign_artifact(str(path), "CampaignMetrics", _metrics(TRADING_DAY))

    calendar = MarketCalendar()
    result = classify_session(TRADING_DAY, calendar=calendar, artifact_dir=str(tmp_path))
    assert result.status == STATUS_SESSION_FAILED


def test_campaign_continuity_report_counts_match_classifications(tmp_path):
    complete_day = "2026-08-17"  # Monday
    missing_day = "2026-08-18"   # Tuesday
    weekend_day = "2026-08-15"   # Saturday

    path = tmp_path / f"{complete_day}.jsonl"
    save_campaign_artifact(str(path), "HealthReport", _health(complete_day, RUNTIME_HEALTHY))
    save_campaign_artifact(str(path), "CampaignSession", _session(complete_day))
    save_campaign_artifact(str(path), "CampaignMetrics", _metrics(complete_day))

    calendar = MarketCalendar()
    report = build_campaign_continuity_report(weekend_day, missing_day, calendar=calendar, artifact_dir=str(tmp_path))

    assert len(report.classifications) == 4  # Sat, Sun, Mon, Tue
    assert sum(report.status_counts.values()) == 4
    assert report.status_counts.get(STATUS_NON_TRADING_DAY) == 2  # Sat + Sun
    assert report.status_counts.get(STATUS_SESSION_COMPLETE) == 1
    assert report.status_counts.get(STATUS_MISSING) == 1

    text = report.render()
    assert complete_day in text
    assert missing_day in text
    # Non-trading days are omitted from the rendered PER-DAY detail lines
    # (the header's own date range naturally still names the boundary date).
    detail_lines = text.splitlines()[2:]
    assert not any(line.strip().startswith(weekend_day) for line in detail_lines)


def test_continuity_report_rejects_inverted_date_range(tmp_path):
    calendar = MarketCalendar()
    with pytest.raises(ValueError):
        build_campaign_continuity_report("2026-08-20", "2026-08-17", calendar=calendar, artifact_dir=str(tmp_path))
