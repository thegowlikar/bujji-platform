"""Deliverable 2 (Sprint 107) -- Live Shadow Operator, extended by
Sprint 112 with rate-limited live calls, freshness-gated cadence, and
full restart recovery (Deliverables 2/3/4).

Orchestrates FROZEN components only:
  ProcessLock (bujji.core.process_lock, unmodified)
  -> Authentication state machine (bujji.authentication.engine, unmodified)
  -> FyersTickFeed (bujji.broker.fyers_ws, unmodified, market-data only)
  -> SessionDriver (bujji.live_pipeline_bridge, Sprint 105, unmodified)
  -> run_full_cadence (bujji.live_shadow_validation, Sprint 106, unmodified)
  -> ShadowPosition (bujji.msi_shadow_trading, Series 100, unmodified)
  -> DecisionRecord (bujji.msi_decision_auditor, Series 99, unmodified)

Contains zero trading intelligence. Every real decision is delegated to
the modules above; this file only sequences calls, records metrics, and
persists results.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Sequence

from ..core.process_lock import LockAcquisitionError, ProcessLock
from ..live_pipeline_bridge import SessionDriver, SessionResult, fetch_live_atm_premiums
from ..live_shadow_validation import (
    FullCadenceResult, run_full_cadence, record_operational_metrics,
    OperationalMetrics,
)
from ..msi_portfolio_construction.models import PortfolioState

from .safety import SHADOW_MODE, assert_shadow_safe, render_shadow_banner
from .health import HealthSnapshot, build_health_snapshot
from .journal import OperatorJournal
from .report import build_end_of_day_report
from .freshness import FreshnessReport, assess_freshness
from .rate_limiter import RateLimitedCaller, RateLimiterConfig

# NSE regular trading session, IST. A well-known, structural constant --
# not tuned, not read from a broker capability call (Deliverable 1's own
# audit found no such call anywhere in this codebase).
MARKET_OPEN_IST = "09:15"
MARKET_CLOSE_IST = "15:30"


class DecisionGenerationPaused(RuntimeError):
    """Raised by `run_cadence` when Sprint 112 Deliverable 3's own rule
    fires: a MANDATORY freshness input is STALE. Fail closed -- never
    generate a decision on data known to be stale."""


@dataclass
class DailyOutcome:
    day: str
    decisions: int = 0
    no_trade_count: int = 0
    strategy_distribution: Dict[str, int] = field(default_factory=dict)
    thesis_distribution: Dict[str, int] = field(default_factory=dict)
    virtual_pnl: float = 0.0
    metrics: Optional[OperationalMetrics] = None
    cadence_results: List[FullCadenceResult] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    failures: List[str] = field(default_factory=list)
    report_text: Optional[str] = None


class LiveShadowOperator:
    """Deliverable 2's session runner. `SHADOW_MODE` is asserted at every
    stage boundary (Deliverable 3) -- never assumed true once and
    forgotten."""

    def __init__(
        self, *, lock_path: str, journal_dir: str, underlying: str = "NIFTY",
        logger: Optional[logging.Logger] = None,
        rate_limiter_config: RateLimiterConfig = RateLimiterConfig(),
    ) -> None:
        assert_shadow_safe()
        self._log = logger or logging.getLogger("bujji.live_shadow_operator")
        self._lock = ProcessLock(lock_path)
        self._journal = OperatorJournal(Path(journal_dir), logger=self._log)
        self._underlying = underlying
        self._driver: Optional[SessionDriver] = None
        self._portfolio = PortfolioState()
        self._open_positions: List[Dict[str, Any]] = []
        self._reconnect_count = 0
        self._started_monotonic: Optional[float] = None
        self._rate_limiter = RateLimitedCaller(config=rate_limiter_config, logger=self._log)
        self._last_freshness: Optional[FreshnessReport] = None
        self._completed_cadence_days: set = set()

    # -- Deliverable 2 stage 1: acquire process lock -----------------------
    def acquire(self) -> None:
        assert_shadow_safe()
        try:
            self._lock.acquire()
        except LockAcquisitionError as exc:
            self._log.critical("startup_blocked_duplicate_instance: %s", exc)
            raise
        if not self._lock.enforced:
            self._log.warning("process_lock_not_enforced: flock unavailable on this platform")
        self._log.info(render_shadow_banner())

    # -- Deliverable 2 stage 2: authenticate FYERS (structural only) ------
    def authenticate(self, broker_session: Any = None) -> Any:
        """Real authentication uses `bujji.authentication.engine`'s own
        state machine (`begin_authentication` -> `complete_authentication`
        -> `connect` -> `mark_ready`) unmodified. This environment has no
        real FYERS credentials (disclosed, unchanged since Sprint 104), so
        the caller is expected to supply an already-authenticated
        `BrokerSession` when one is available, or `None` when running
        against recorded data only (`load_option_chain`/`process_tick`
        with recorded ticks, exactly as every prior follow-up did).
        `SessionDriver` itself never requires a broker session -- it is
        duck-typed against `get_quote`, per Sprint 105's follow-up 3."""
        assert_shadow_safe()
        return broker_session

    # -- Deliverable 2 stage 3: start websocket / start live pipeline -----
    def start_session(self, prior_closes_with_ts: Sequence = ()) -> None:
        assert_shadow_safe()
        self._driver = SessionDriver(
            lock_path=str(self._lock._path) + ".session",  # distinct sub-lock, never re-acquires the operator's own lock
            prior_closes_with_ts=prior_closes_with_ts, underlying=self._underlying,
        )
        self._driver.acquire()
        self._started_monotonic = time.monotonic()

    def load_option_chain(self, bhavcopy_text: str, day: str) -> None:
        assert_shadow_safe()
        self._driver.load_option_chain(bhavcopy_text, day, underlying=self._underlying)

    def process_tick(self, instrument: str, timestamp: str, price: float, source: str = "live") -> None:
        assert_shadow_safe()
        self._driver.process_tick(instrument, timestamp, price, source=source)

    def note_reconnect(self) -> None:
        """Deliverable 6: real reconnect counting -- called by the
        caller's own tick-feed reconnect handler (FyersTickFeed's real
        reconnect logic is unmodified; this operator only counts)."""
        self._reconnect_count += 1

    # -- Sprint 112 Deliverable 2: rate-limited live premium fetch --------
    async def fetch_live_premiums(self, broker: Any, ce_contract: Any, pe_contract: Any):
        """Wraps the real `fetch_live_atm_premiums` (Sprint 105 follow-up
        3, frozen) with the rate limiter -- never calls the broker
        directly, never bypasses throttling/retry/backoff/jitter."""
        assert_shadow_safe()
        result = await self._rate_limiter.call("fetch_live_atm_premiums", fetch_live_atm_premiums, broker, ce_contract, pe_contract)
        if result.success:
            ce, pe = result.value
            self._driver.set_live_atm_premiums(ce, pe)
        return result

    # -- Sprint 112 Deliverable 3: freshness gate --------------------------
    def check_freshness(self, **kwargs) -> FreshnessReport:
        """Caller supplies real timestamps for whatever sources it has
        (e.g. `last_tick_timestamp=...`); `now` defaults to real IST if
        not supplied by the caller, mirroring `core.clock.now_ist`'s own
        real-clock convention. Fail-closed: `run_cadence` refuses to
        proceed if a MANDATORY source is STALE."""
        from ..core.clock import now_ist
        kwargs.setdefault("now", now_ist().replace(tzinfo=None))
        self._last_freshness = assess_freshness(**kwargs)
        return self._last_freshness

    # -- Deliverable 2 stages 4-6: decision cadence -> shadow trades ------
    def run_cadence(self, *, day: str, spot: Optional[float], timestamp: str, freshness: Optional[FreshnessReport] = None) -> FullCadenceResult:
        assert_shadow_safe()
        active_freshness = freshness if freshness is not None else self._last_freshness
        if active_freshness is not None and active_freshness.mandatory_stale():
            stale = [r for r in active_freshness.readings.values() if r.state == "STALE"]
            raise DecisionGenerationPaused(
                f"decision generation paused for {day}: mandatory input(s) STALE -- "
                + "; ".join(f"{r.source}: {r.reason}" for r in stale)
            )

        t0 = time.monotonic()
        result = self._driver.run_decision_cadence(timestamp=timestamp)
        t1 = time.monotonic()
        cadence = run_full_cadence(
            self._driver, spot=spot, day=day, portfolio=self._portfolio,
            open_positions=tuple(self._open_positions), timestamp=timestamp,
        )
        t2 = time.monotonic()
        assert_shadow_safe()  # re-asserted immediately after every cadence, per Deliverable 3

        if cadence.admitted_trade is not None:
            self._portfolio = PortfolioState(self._portfolio.admitted_trades + (cadence.admitted_trade,))
            self._open_positions.append({
                "entry_date": day, "entry_thesis": result.thesis,
                "strategy_family": cadence.selection.selected_strategy_family,
                "construction_type": cadence.position_construction.construction_type,
                "position_close_date": cadence.admitted_trade.position_close_date,
            })
        self._open_positions = [p for p in self._open_positions if p["position_close_date"] >= day]

        # Deliverable 2: persist every assessment -- append-only, real
        # journal entries, never overwritten (mirrors this whole
        # project's own append-only journal convention, Series 68+).
        self._journal.record_cadence(day, timestamp, result, cadence,
                                     decision_latency_seconds=t1 - t0,
                                     cadence_duration_seconds=t2 - t0)
        # Sprint 112 Deliverable 4: persist full state (portfolio +
        # open-position inventory) after every cadence, not just closes.
        self._journal.record_state_snapshot(day, admitted_trades=self._portfolio.admitted_trades, open_positions=self._open_positions)
        self._completed_cadence_days.add(day)
        return cadence

    def close_cadence_metrics(self, day: str) -> OperationalMetrics:
        return record_operational_metrics(
            day, self._driver.result,
            decision_latency_seconds=0.0, cadence_duration_seconds=(
                time.monotonic() - self._started_monotonic if self._started_monotonic else 0.0
            ),
        )

    # -- Deliverable 4 / Sprint 112 Deliverable 7: health snapshot --------
    def health_snapshot(self, *, token_expires_in_seconds: Optional[float] = None,
                         watchdog_state: Optional[str] = None, watchdog_reconnect_attempt: int = 0,
                         watchdog_reconnect_reason: Optional[str] = None,
                         subscription_state: Optional[str] = None) -> HealthSnapshot:
        # Sprint P1: real, disclosed watchdog metrics -- all optional,
        # defaulted, additive-only kwargs (this project's own established
        # schema-evolution convention). Purely observational: nothing
        # here feeds back into `_driver`/any decision path.
        result = self._driver.result if self._driver else SessionResult()
        return build_health_snapshot(
            result, reconnect_count=self._reconnect_count,
            token_expires_in_seconds=token_expires_in_seconds,
            process_start_monotonic=self._started_monotonic or time.monotonic(),
            freshness=self._last_freshness,
            api_retry_count=self._rate_limiter.metrics.total_retries,
            api_dropped_requests=self._rate_limiter.metrics.dropped_requests,
            journal_path=str(self._journal._path),
            journal_failed_writes=self._journal.failed_writes,
            watchdog_state=watchdog_state, watchdog_reconnect_attempt=watchdog_reconnect_attempt,
            watchdog_reconnect_reason=watchdog_reconnect_reason, subscription_state=subscription_state,
        )

    # -- Deliverable 7: end-of-day report ----------------------------------
    def end_of_day(self, day: str, cadence_results: Sequence[FullCadenceResult],
                   *, replay_parity_pct: Optional[float] = None) -> DailyOutcome:
        assert_shadow_safe()
        metrics = self.close_cadence_metrics(day)
        outcome = build_end_of_day_report(
            day, self._driver.result, cadence_results, metrics,
            reconnect_count=self._reconnect_count, replay_parity_pct=replay_parity_pct,
        )
        self._journal.record_end_of_day(day, outcome)
        return outcome

    # -- Deliverable 2 stage 8 / Deliverable 6: graceful shutdown ---------
    def shutdown(self) -> None:
        assert_shadow_safe()
        if self._driver is not None:
            self._driver.close_session()
        self._lock.release()
        self._journal.flush()
        self._log.info("shutdown: clean")

    # -- Deliverable 6 (Sprint 107) / Sprint 112 Deliverable 4: recovery --
    def resume_prior_closes(self) -> Sequence:
        """Restart-safety: read the operator's own append-only journal for
        the most recent session's real `closes_with_ts`, so a restarted
        process re-threads the SAME multi-day volatility history a
        continuously-running process would have -- reuses the exact
        `prior_closes_with_ts` seam Sprint 105's second follow-up already
        built into `SessionDriver`, never a new mechanism."""
        return self._journal.read_last_closes_with_ts()

    def resume_state(self) -> None:
        """Sprint 112 Deliverable 4: full restart recovery. Reads the
        journal's most recent `STATE_SNAPSHOT` and reconstructs
        `self._portfolio` (real `AdmittedTrade`-shaped `PortfolioState`)
        and `self._open_positions`. `entry_thesis` is recovered as a
        `SimpleNamespace` exposing exactly the four real attributes
        `msi_position_lifecycle.engine.assess_position_lifecycle`
        actually reads (`thesis_type`, `conviction`,
        `volatility_expectation`, `assessment_id` -- confirmed by
        reading that engine directly) -- not a full
        `TradeThesisAssessment` reconstruction (this project's own
        established "best-effort, not full nested rehydration"
        serialization convention), but sufficient for identical
        subsequent lifecycle/roll/recomposition decisions, which is what
        this Deliverable's own "restarting mid-session should produce
        identical subsequent decisions" requirement asks for.

        Also recovers `completed_cadence_days` (Deliverable 4's own
        "decision cadence state") so a restarted operator's caller can
        skip re-running a day whose cadence already completed --
        avoiding ANY duplicate decision, not just relying on content-hash
        idempotency after the fact.
        """
        from ..msi_portfolio_construction.models import AdmittedTrade, HeldLeg

        self._completed_cadence_days = self._journal.completed_cadence_days()
        snapshot = self._journal.read_last_state_snapshot()
        if snapshot is None:
            return

        admitted_trades = []
        for t in snapshot.get("admitted_trades", []):
            legs = tuple(HeldLeg(**leg) for leg in t.get("legs", ()))
            admitted_trades.append(AdmittedTrade(
                assessment_id=t["assessment_id"], strategy_family=t["strategy_family"],
                underlying=t["underlying"], position_close_date=t["position_close_date"],
                legs=legs, approved_lots=t["approved_lots"], capital_required=t["capital_required"],
                admitted_date=t["admitted_date"],
            ))
        self._portfolio = PortfolioState(tuple(admitted_trades))

        open_positions = []
        for p in snapshot.get("open_positions", []):
            entry_thesis_dict = p.get("entry_thesis") or {}
            open_positions.append({
                "entry_date": p["entry_date"], "entry_thesis": SimpleNamespace(**entry_thesis_dict),
                "strategy_family": p["strategy_family"], "construction_type": p["construction_type"],
                "position_close_date": p["position_close_date"],
            })
        self._open_positions = open_positions

    def is_cadence_completed(self, day: str) -> bool:
        """Sprint 112 Deliverable 4/8: lets a caller skip re-running a
        day's cadence after a restart -- the real, primary duplicate-
        decision guard (content-hash idempotency, Sprint 107, remains a
        second, independent safeguard even if this check is bypassed)."""
        return day in self._completed_cadence_days
