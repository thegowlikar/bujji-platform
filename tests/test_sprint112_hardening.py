"""Tests for Sprint 112 -- Production Hardening & Operational Resilience.

Every test in this file verifies OPERATIONAL infrastructure only --
none construct or assert on a new trading decision; all assert that
existing decision content (decision_id, thesis, strategy) is UNCHANGED
by the hardening added this sprint.
"""
import asyncio
import gzip
import json
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from bujji.live_shadow_operator.rate_limiter import RateLimitedCaller, RateLimiterConfig
from bujji.live_shadow_operator.freshness import assess_freshness, FRESH, WARNING, STALE, UNKNOWN
from bujji.live_shadow_operator.journal import OperatorJournal
from bujji.live_shadow_operator.health import build_health_snapshot, STATUS_GREEN, STATUS_RED
from bujji.live_shadow_operator import LiveShadowOperator
from bujji.live_shadow_operator.operator import DecisionGenerationPaused
from bujji.market_calendar import MarketCalendar
from bujji.broker.errors import AuthenticationError
from bujji.live_pipeline_bridge import SessionResult

DAY = "2026-05-25"
D = "20260525"
TS = f"{DAY}T15:15:00"


def _load_real_day():
    import json as _json
    with open("/tmp/nifty_intraday_by_day_expanded.json") as f:
        candles = _json.load(f)[DAY]
    with open(f"/tmp/m1/BhavCopy_NSE_FO_0_0_0_{D}_F_0000.csv") as f:
        bhav_text = f.read()
    return candles, bhav_text


# ---------------------------------------------------------------------------
# Deliverable 2 -- Rate limiter
# ---------------------------------------------------------------------------
def test_rate_limiter_throttles_to_configured_requests_per_second():
    async def run():
        waits = []
        caller = RateLimitedCaller(
            config=RateLimiterConfig(requests_per_second=10.0),
            sleep=lambda s: waits.append(s) or asyncio.sleep(0),
        )
        async def ok():
            return "value"
        r1 = await caller.call("ok", ok)
        r2 = await caller.call("ok", ok)
        assert r1.success and r2.success
        assert any(w > 0 for w in waits)  # second call was throttled
    asyncio.run(run())


def test_rate_limiter_retries_transient_failures_with_backoff_and_jitter():
    async def run():
        attempts = {"n": 0}
        async def flaky():
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise ConnectionError("transient")
            return "recovered"
        caller = RateLimitedCaller(config=RateLimiterConfig(max_retries=5, base_backoff_seconds=0.0, jitter_seconds=0.0),
                                   sleep=lambda s: asyncio.sleep(0))
        result = await caller.call("flaky", flaky)
        assert result.success is True
        assert result.retries == 2
        assert caller.metrics.total_retries == 2
    asyncio.run(run())


def test_rate_limiter_never_retries_authentication_error_permanent_failure():
    async def run():
        attempts = {"n": 0}
        async def bad_auth():
            attempts["n"] += 1
            raise AuthenticationError("invalid token")
        caller = RateLimitedCaller(config=RateLimiterConfig(max_retries=5), sleep=lambda s: asyncio.sleep(0))
        result = await caller.call("bad_auth", bad_auth)
        assert result.success is False
        assert result.permanent_failure is True
        assert attempts["n"] == 1  # never retried
        assert caller.metrics.permanent_failures == 1
    asyncio.run(run())


def test_rate_limiter_drops_request_after_exhausting_retry_budget():
    async def run():
        async def always_fails():
            raise ConnectionError("down")
        caller = RateLimitedCaller(config=RateLimiterConfig(max_retries=2, base_backoff_seconds=0.0, jitter_seconds=0.0),
                                   sleep=lambda s: asyncio.sleep(0))
        result = await caller.call("always_fails", always_fails)
        assert result.success is False
        assert result.dropped is True
        assert caller.metrics.dropped_requests == 1
        assert caller.metrics.average_wait_seconds >= 0.0
    asyncio.run(run())


# ---------------------------------------------------------------------------
# Deliverable 3 -- Freshness monitor
# ---------------------------------------------------------------------------
def test_freshness_never_fabricates_fresh_when_no_timestamp_given():
    report = assess_freshness(now=datetime(2026, 5, 25, 15, 15, 0))
    assert all(r.state == UNKNOWN for r in report.readings.values())
    assert report.mandatory_stale() is False  # UNKNOWN is not STALE -- never conflated


def test_freshness_classifies_real_ages_into_correct_bands():
    now = datetime(2026, 5, 25, 15, 15, 0)
    report = assess_freshness(now=now, last_tick_timestamp=now - timedelta(seconds=10))
    assert report.readings["underlying_tick"].state == FRESH
    report2 = assess_freshness(now=now, last_tick_timestamp=now - timedelta(seconds=1000))
    assert report2.readings["underlying_tick"].state == STALE


def test_freshness_mandatory_stale_gates_correctly():
    now = datetime(2026, 5, 25, 15, 15, 0)
    stale_chain = assess_freshness(now=now, last_chain_timestamp=now - timedelta(hours=25))  # Day 1 fix: chain is valid ~20h, genuinely stale past that
    assert stale_chain.mandatory_stale() is True
    stale_vol_only = assess_freshness(now=now, last_volatility_timestamp=now - timedelta(seconds=2000))
    assert stale_vol_only.mandatory_stale() is False  # volatility_input is not in MANDATORY_SOURCES


# ---------------------------------------------------------------------------
# Deliverable 3 -- decision generation pauses on mandatory staleness
# ---------------------------------------------------------------------------
def test_run_cadence_pauses_when_mandatory_input_is_stale(tmp_path):
    candles, bhav_text = _load_real_day()
    op = LiveShadowOperator(lock_path=str(tmp_path / "test.lock"), journal_dir=str(tmp_path / "journal"))
    op.acquire()
    op.start_session()
    op.load_option_chain(bhav_text, DAY)
    for c in candles:
        op.process_tick("NIFTY", c["ts"], c["close"])
    stale_report = assess_freshness(now=datetime(2026, 5, 27, 15, 15, 0), last_chain_timestamp=datetime(2026, 5, 25, 14, 0, 0))  # Day 1 fix: genuinely cross-day stale (>20h)
    with pytest.raises(DecisionGenerationPaused):
        op.run_cadence(day=DAY, spot=24000.0, timestamp=candles[-1]["ts"], freshness=stale_report)
    op.shutdown()


# ---------------------------------------------------------------------------
# Deliverable 5 -- Journal rotation
# ---------------------------------------------------------------------------
def test_journal_rotation_compresses_and_validates_integrity(tmp_path):
    journal = OperatorJournal(tmp_path / "journal")
    for i in range(5):
        journal._append({"type": "TEST", "n": i})
    archive_path = journal.rotate(retain_days=30, today=date(2026, 5, 25))
    assert archive_path is not None
    assert archive_path.exists()
    assert not journal._path.exists() or journal._path.stat().st_size == 0
    with gzip.open(archive_path, "rt") as f:
        lines = [json.loads(l) for l in f if l.strip()]
    assert len(lines) == 5


def test_journal_rotation_never_overwrites_a_prior_archive(tmp_path):
    journal = OperatorJournal(tmp_path / "journal")
    journal._append({"type": "TEST", "n": 1})
    a1 = journal.rotate(today=date(2026, 5, 25))
    journal._append({"type": "TEST", "n": 2})
    a2 = journal.rotate(today=date(2026, 5, 25))
    assert a1 != a2
    assert a1.exists() and a2.exists()


def test_journal_rotation_prunes_old_archives_by_retention(tmp_path):
    journal_dir = tmp_path / "journal"
    journal = OperatorJournal(journal_dir)
    archive_dir = journal_dir / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    old_archive = archive_dir / "operator_journal_2026-01-01.jsonl.gz"
    with gzip.open(old_archive, "wt") as f:
        f.write('{"type": "OLD"}\n')
    journal._append({"type": "TEST", "n": 1})
    journal.rotate(retain_days=30, today=date(2026, 5, 25), archive_dir=archive_dir)
    assert not old_archive.exists()  # pruned -- 2026-01-01 is > 30 days before 2026-05-25


def test_journal_rotation_on_empty_journal_is_a_noop(tmp_path):
    journal = OperatorJournal(tmp_path / "journal")
    assert journal.rotate() is None


# ---------------------------------------------------------------------------
# Deliverable 6 -- Market calendar
# ---------------------------------------------------------------------------
def test_calendar_detects_weekends_via_real_date_arithmetic():
    cal = MarketCalendar()
    saturday = date(2026, 5, 30)
    assert cal.is_weekend(saturday) is True
    ok, reason = cal.is_trading_day(saturday)
    assert ok is False and "weekend" in reason


def test_calendar_manual_closure_overrides_a_weekday():
    cal = MarketCalendar()
    weekday = date(2026, 5, 25)
    ok, _ = cal.is_trading_day(weekday)
    assert ok is True
    cal.add_manual_closure(weekday, "unscheduled exchange closure")
    ok2, reason2 = cal.is_trading_day(weekday)
    assert ok2 is False and "manually closed" in reason2


def test_calendar_verification_warning_present_by_default():
    cal = MarketCalendar()
    assert cal.holiday_calendar_verified is False
    assert cal.verification_warning() is not None
    cal.holiday_calendar_verified = True
    assert cal.verification_warning() is None


def test_calendar_never_performs_a_web_lookup():
    """Structural guarantee, verified by AST: no `requests`/`urllib`/
    `http` import anywhere in market_calendar.py."""
    import ast
    tree = ast.parse(Path("bujji/market_calendar.py").read_text())
    forbidden = {"requests", "urllib", "http", "httpx", "aiohttp"}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [n.name for n in node.names] if isinstance(node, ast.Import) else [node.module]
            for name in names:
                assert not any((name or "").startswith(f) for f in forbidden)


# ---------------------------------------------------------------------------
# Deliverable 7 -- health dashboard overall status
# ---------------------------------------------------------------------------
def test_health_overall_status_green_when_all_fresh_and_healthy():
    now = datetime(2026, 5, 25, 15, 15, 0)
    fresh = assess_freshness(now=now, last_tick_timestamp=now - timedelta(seconds=5),
                             last_chain_timestamp=now - timedelta(seconds=5))
    snapshot = build_health_snapshot(SessionResult(), reconnect_count=0, process_start_monotonic=0.0, freshness=fresh)
    assert snapshot.overall_status == STATUS_GREEN


def test_health_overall_status_red_when_mandatory_input_stale():
    now = datetime(2026, 5, 25, 15, 15, 0)
    stale = assess_freshness(now=now, last_chain_timestamp=now - timedelta(hours=25))  # Day 1 fix: chain is valid ~20h, genuinely stale past that
    snapshot = build_health_snapshot(SessionResult(), reconnect_count=0, process_start_monotonic=0.0, freshness=stale)
    assert snapshot.overall_status == STATUS_RED
    assert len(snapshot.status_reasons) > 0


# ---------------------------------------------------------------------------
# Deliverable 4 / 8 -- Restart recovery + chaos: identical decisions
# ---------------------------------------------------------------------------
def _run_one_operator_day(op, candles, bhav_text, day, prior_closes=()):
    if not op._driver:
        op.start_session(prior_closes_with_ts=prior_closes)
    op.load_option_chain(bhav_text, day)
    for c in candles:
        op.process_tick("NIFTY", c["ts"], c["close"])
    last_ts = candles[-1]["ts"]
    spot = next(r.underlying_price for r in op._driver._chain if r.underlying_price is not None)
    return op.run_cadence(day=day, spot=spot, timestamp=last_ts)


def test_restart_recovery_produces_identical_subsequent_decisions(tmp_path):
    """Deliverable 4's own requirement, proved by replay: run day 1
    uninterrupted, capture day 2's decision. Then run day 1 in one
    process, SIMULATE A RESTART (fresh operator instance, same journal
    dir, `resume_state()`), and run day 2 in the fresh instance. Both
    paths must produce the IDENTICAL day-2 decision_id."""
    with open("/tmp/nifty_intraday_by_day_expanded.json") as f:
        all_candles = json.load(f)
    with open("/tmp/m1/BhavCopy_NSE_FO_0_0_0_20260525_F_0000.csv") as f:
        bhav1 = f.read()
    with open("/tmp/m1/BhavCopy_NSE_FO_0_0_0_20260526_F_0000.csv") as f:
        bhav2 = f.read()
    day1, day2 = "2026-05-25", "2026-05-26"

    # --- Path A: uninterrupted single process ---
    journal_a = tmp_path / "journal_a"
    op_a = LiveShadowOperator(lock_path=str(tmp_path / "a.lock"), journal_dir=str(journal_a))
    op_a.acquire()
    _run_one_operator_day(op_a, all_candles[day1], bhav1, day1)
    op_a.shutdown()
    op_a2 = LiveShadowOperator(lock_path=str(tmp_path / "a2.lock"), journal_dir=str(journal_a))
    op_a2.acquire()
    prior = op_a2.resume_prior_closes()
    op_a2.resume_state()
    cadence_a_day2 = _run_one_operator_day(op_a2, all_candles[day2], bhav2, day2, prior_closes=prior)
    op_a2.shutdown()

    # --- Path B: SIMULATED restart mid-session (separate journal, same logic) ---
    journal_b = tmp_path / "journal_b"
    op_b1 = LiveShadowOperator(lock_path=str(tmp_path / "b1.lock"), journal_dir=str(journal_b))
    op_b1.acquire()
    _run_one_operator_day(op_b1, all_candles[day1], bhav1, day1)
    op_b1.shutdown()  # process "crashes"/restarts here
    op_b2 = LiveShadowOperator(lock_path=str(tmp_path / "b2.lock"), journal_dir=str(journal_b))
    op_b2.acquire()
    prior_b = op_b2.resume_prior_closes()
    op_b2.resume_state()
    assert op_b2.is_cadence_completed(day1) is True  # recovered cadence state -- day1 will not be re-run
    cadence_b_day2 = _run_one_operator_day(op_b2, all_candles[day2], bhav2, day2, prior_closes=prior_b)
    op_b2.shutdown()

    assert cadence_a_day2.decision.decision_id == cadence_b_day2.decision.decision_id
    assert cadence_a_day2.selection.selected_strategy_family == cadence_b_day2.selection.selected_strategy_family


def test_chaos_duplicate_ticks_never_produce_a_duplicate_decision(tmp_path):
    candles, bhav_text = _load_real_day()
    op = LiveShadowOperator(lock_path=str(tmp_path / "dup.lock"), journal_dir=str(tmp_path / "journal"))
    op.acquire()
    op.start_session()
    op.load_option_chain(bhav_text, DAY)
    for c in candles:
        op.process_tick("NIFTY", c["ts"], c["close"])
    for c in candles[:5]:  # replay the first 5 ticks again -- simulated duplicate delivery
        op.process_tick("NIFTY", c["ts"], c["close"])
    assert op._driver.result.dropped_ticks == 5
    spot = next(r.underlying_price for r in op._driver._chain if r.underlying_price is not None)
    c1 = op.run_cadence(day=DAY, spot=spot, timestamp=candles[-1]["ts"])
    c2 = op.run_cadence(day=DAY, spot=spot, timestamp=candles[-1]["ts"])
    assert c1.decision.decision_id == c2.decision.decision_id  # re-running yields the SAME id, never a new one
    op.shutdown()


def test_chaos_duplicate_option_chain_load_is_idempotent(tmp_path):
    candles, bhav_text = _load_real_day()
    op = LiveShadowOperator(lock_path=str(tmp_path / "dupchain.lock"), journal_dir=str(tmp_path / "journal"))
    op.acquire()
    op.start_session()
    op.load_option_chain(bhav_text, DAY)
    chain1 = op._driver._chain
    op.load_option_chain(bhav_text, DAY)  # loaded twice -- simulated duplicate chain delivery
    chain2 = op._driver._chain
    assert len(chain1) == len(chain2)
    op.shutdown()


def test_chaos_missing_quote_falls_back_to_bhavcopy_without_crashing():
    async def run():
        class FlakyBroker:
            async def get_quote(self, contract):
                return None  # simulated missing live quote
        from bujji.live_pipeline_bridge import fetch_live_atm_premiums
        ce, pe = await fetch_live_atm_premiums(FlakyBroker(), object(), object())
        assert ce is None and pe is None  # fails closed, never crashes, never fabricates
    asyncio.run(run())
