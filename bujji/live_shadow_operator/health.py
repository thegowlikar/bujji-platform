"""Deliverable 4 (Sprint 107) + Sprint 112 Deliverable 7 -- Health
monitoring, extended with operational status.

Every field is a real, measured value -- either a counter already
tracked on `SessionResult` (Sprint 105/106), a real `resource.getrusage`
reading, a real `shutil.disk_usage` reading, or a caller-supplied real
timing/age. Nothing here is fabricated or estimated. Exposed via a plain
dict/text snapshot (`build_health_snapshot`/`render_health_dashboard`)
rather than the pre-existing `bujji.dashboard.server.DashboardServer` --
that server is built around the legacy Series 1-54 `RuntimeStatus`/
`TradeJournal` object graph (confirmed by reading `dashboard/server.py`
directly), which this MSI-arc operator does not construct at all.
"""
from __future__ import annotations

import shutil
import time
from dataclasses import dataclass, field
from typing import Optional, Tuple

import resource

from ..live_pipeline_bridge import SessionResult
from .freshness import FreshnessReport, STALE, WARNING

STATUS_GREEN = "GREEN"
STATUS_AMBER = "AMBER"
STATUS_RED = "RED"

# Structural, disclosed thresholds -- reused BY IDENTITY from the
# legacy, real `HealthMonitor` (Sprint 4) wherever an equivalent
# already exists; new, disclosed defaults otherwise.
from bujji.ops.health_monitor import (  # noqa: E402
    MEMORY_WARNING_KB, MEMORY_CRITICAL_KB, DISK_WARNING_FREE_PCT, DISK_CRITICAL_FREE_PCT,
)


@dataclass(frozen=True)
class HealthSnapshot:
    websocket_status: str
    reconnect_count: int
    dropped_ticks: int
    duplicate_observations: int
    quote_latency_seconds: Optional[float]
    cadence_duration_seconds: Optional[float]
    decision_latency_seconds: Optional[float]
    peak_memory_kb: int
    cpu_user_seconds: float
    cpu_system_seconds: float
    token_expires_in_seconds: Optional[float]
    uptime_seconds: float
    # --- Sprint 112 Deliverable 7 additions (all defaulted -- backward compatible) ---
    websocket_age_seconds: Optional[float] = None
    last_quote_age_seconds: Optional[float] = None
    option_chain_age_seconds: Optional[float] = None
    api_retry_count: int = 0
    api_dropped_requests: int = 0
    stale_warnings: Tuple[str, ...] = field(default_factory=tuple)
    journal_health: str = "UNKNOWN"
    disk_free_pct: Optional[float] = None
    overall_status: str = STATUS_GREEN
    status_reasons: Tuple[str, ...] = field(default_factory=tuple)
    # --- Sprint P1 additions (all defaulted -- backward compatible):
    # real, disclosed TickSilenceWatchdog metrics. Purely observational --
    # never consumed by any decision function. ---------------------------
    watchdog_state: Optional[str] = None
    watchdog_reconnect_attempt: int = 0
    watchdog_reconnect_reason: Optional[str] = None
    subscription_state: Optional[str] = None


def build_health_snapshot(
    result: SessionResult, *, reconnect_count: int, process_start_monotonic: float,
    token_expires_in_seconds: Optional[float] = None,
    freshness: Optional[FreshnessReport] = None,
    api_retry_count: int = 0, api_dropped_requests: int = 0,
    journal_path: Optional[str] = None, disk_check_path: str = ".",
    journal_failed_writes: int = 0,
    watchdog_state: Optional[str] = None, watchdog_reconnect_attempt: int = 0,
    watchdog_reconnect_reason: Optional[str] = None, subscription_state: Optional[str] = None,
) -> HealthSnapshot:
    usage = resource.getrusage(resource.RUSAGE_SELF)
    websocket_status = "CONNECTED" if result.observations else "IDLE"

    stale_warnings: Tuple[str, ...] = ()
    if freshness is not None:
        stale_warnings = tuple(
            f"{r.source}={r.state}: {r.reason}" for r in freshness.readings.values() if r.state in (WARNING, STALE)
        )

    # Deep audit finding (2026-07-28): file EXISTENCE alone never proves
    # writes are actually succeeding -- a disk-full/permissions failure
    # mid-session would keep this HEALTHY forever. `journal_failed_writes`
    # is the real signal, sourced from `OperatorJournal.failed_writes`.
    journal_health = "UNKNOWN"
    if journal_path is not None:
        import os
        if not os.path.exists(journal_path):
            journal_health = "MISSING"
        elif journal_failed_writes > 0:
            journal_health = "DEGRADED"
        else:
            journal_health = "HEALTHY"

    disk_free_pct = None
    try:
        usage_disk = shutil.disk_usage(disk_check_path)
        disk_free_pct = (usage_disk.free / usage_disk.total) * 100.0
    except OSError:
        disk_free_pct = None

    reasons = []
    status = STATUS_GREEN
    if freshness is not None and freshness.mandatory_stale():
        status, reasons = STATUS_RED, reasons + ["a mandatory input (underlying_tick/option_chain) is STALE"]
    elif freshness is not None and any(r.state == STALE for r in freshness.readings.values()):
        status = max(status, STATUS_AMBER, key=lambda s: {"GREEN": 0, "AMBER": 1, "RED": 2}[s])
        reasons.append("a non-mandatory input is STALE")
    elif stale_warnings:
        status = max(status, STATUS_AMBER, key=lambda s: {"GREEN": 0, "AMBER": 1, "RED": 2}[s])
        reasons.append(f"{len(stale_warnings)} input(s) at WARNING freshness")
    if usage.ru_maxrss >= MEMORY_CRITICAL_KB:
        status, reasons = STATUS_RED, reasons + [f"peak_memory_kb={usage.ru_maxrss} >= MEMORY_CRITICAL_KB={MEMORY_CRITICAL_KB}"]
    elif usage.ru_maxrss >= MEMORY_WARNING_KB:
        status = max(status, STATUS_AMBER, key=lambda s: {"GREEN": 0, "AMBER": 1, "RED": 2}[s])
        reasons.append(f"peak_memory_kb={usage.ru_maxrss} >= MEMORY_WARNING_KB={MEMORY_WARNING_KB}")
    if disk_free_pct is not None and disk_free_pct <= DISK_CRITICAL_FREE_PCT:
        status, reasons = STATUS_RED, reasons + [f"disk_free_pct={disk_free_pct:.1f} <= DISK_CRITICAL_FREE_PCT={DISK_CRITICAL_FREE_PCT}"]
    elif disk_free_pct is not None and disk_free_pct <= DISK_WARNING_FREE_PCT:
        status = max(status, STATUS_AMBER, key=lambda s: {"GREEN": 0, "AMBER": 1, "RED": 2}[s])
        reasons.append(f"disk_free_pct={disk_free_pct:.1f} <= DISK_WARNING_FREE_PCT={DISK_WARNING_FREE_PCT}")
    if journal_health == "MISSING":
        status, reasons = STATUS_RED, reasons + ["journal file is missing"]
    elif journal_health == "DEGRADED":
        status, reasons = STATUS_RED, reasons + [f"journal has {journal_failed_writes} failed write(s) -- real data may be lost"]
    # Sprint P1: a watchdog that has given up (CRITICAL_FAILURE) means the
    # real tick feed is silently dead and no further automatic recovery
    # will be attempted -- escalate exactly like journal_health's own
    # DEGRADED/MISSING escalation, same fail-closed-and-disclose
    # convention already used throughout this project.
    if watchdog_state == "CRITICAL_FAILURE":
        status, reasons = STATUS_RED, reasons + [
            f"tick-silence watchdog reached CRITICAL_FAILURE after {watchdog_reconnect_attempt} "
            f"failed reconnect attempt(s) -- real market data is silently dead, no further automatic recovery"
        ]
    if not reasons:
        reasons = ["all monitored inputs within tolerance"]

    return HealthSnapshot(
        websocket_status=websocket_status,
        reconnect_count=reconnect_count,
        dropped_ticks=result.dropped_ticks,
        duplicate_observations=result.duplicate_events,
        quote_latency_seconds=(result.latencies[-1].seconds if result.latencies else None),
        cadence_duration_seconds=None,
        decision_latency_seconds=None,
        peak_memory_kb=usage.ru_maxrss,
        cpu_user_seconds=usage.ru_utime,
        cpu_system_seconds=usage.ru_stime,
        token_expires_in_seconds=token_expires_in_seconds,
        uptime_seconds=time.monotonic() - process_start_monotonic,
        websocket_age_seconds=(freshness.readings["underlying_tick"].age_seconds if freshness else None),
        last_quote_age_seconds=(freshness.readings["option_quote"].age_seconds if freshness else None),
        option_chain_age_seconds=(freshness.readings["option_chain"].age_seconds if freshness else None),
        api_retry_count=api_retry_count, api_dropped_requests=api_dropped_requests,
        stale_warnings=stale_warnings, journal_health=journal_health, disk_free_pct=disk_free_pct,
        overall_status=status, status_reasons=tuple(reasons),
        watchdog_state=watchdog_state, watchdog_reconnect_attempt=watchdog_reconnect_attempt,
        watchdog_reconnect_reason=watchdog_reconnect_reason, subscription_state=subscription_state,
    )


def render_health_dashboard(snapshot: HealthSnapshot) -> str:
    lines = [
        f"BUJJI Live Shadow Operator -- Health [{snapshot.overall_status}]",
        f"  websocket_status:          {snapshot.websocket_status}",
        f"  reconnect_count:           {snapshot.reconnect_count}",
        f"  dropped_ticks:             {snapshot.dropped_ticks}",
        f"  duplicate_observations:    {snapshot.duplicate_observations}",
        f"  peak_memory_kb:            {snapshot.peak_memory_kb}",
        f"  cpu_user_seconds:          {snapshot.cpu_user_seconds:.3f}",
        f"  cpu_system_seconds:        {snapshot.cpu_system_seconds:.3f}",
        f"  uptime_seconds:            {snapshot.uptime_seconds:.1f}",
        f"  api_retry_count:           {snapshot.api_retry_count}",
        f"  api_dropped_requests:      {snapshot.api_dropped_requests}",
        f"  journal_health:            {snapshot.journal_health}",
    ]
    if snapshot.watchdog_state is not None:
        lines.append(f"  watchdog_state:            {snapshot.watchdog_state}")
        lines.append(f"  watchdog_reconnect_attempt: {snapshot.watchdog_reconnect_attempt}")
        if snapshot.watchdog_reconnect_reason:
            lines.append(f"  watchdog_reconnect_reason: {snapshot.watchdog_reconnect_reason}")
    if snapshot.subscription_state is not None:
        lines.append(f"  subscription_state:        {snapshot.subscription_state}")
    if snapshot.quote_latency_seconds is not None:
        lines.append(f"  quote_latency_seconds:     {snapshot.quote_latency_seconds:.6f}")
    if snapshot.token_expires_in_seconds is not None:
        lines.append(f"  token_expires_in_seconds:  {snapshot.token_expires_in_seconds:.0f}")
    else:
        lines.append("  token_expires_in_seconds:  UNKNOWN (no authenticated broker session supplied)")
    if snapshot.disk_free_pct is not None:
        lines.append(f"  disk_free_pct:             {snapshot.disk_free_pct:.1f}")
    if snapshot.stale_warnings:
        lines.append("  stale_warnings:")
        lines.extend(f"    - {w}" for w in snapshot.stale_warnings)
    lines.append(f"  overall_status: {snapshot.overall_status} -- {'; '.join(snapshot.status_reasons)}")
    return "\n".join(lines)
