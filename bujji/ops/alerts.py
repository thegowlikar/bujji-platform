"""Alert Engine (Sprint 4). Alert generation only -- never a trading
action. Alerts fire on state TRANSITIONS (not every cycle a bad state
persists, which would spam) and on specific one-shot events (a fresh
restart, a fresh journal failure)."""
from __future__ import annotations

from .models import Alert, HealthState, OpsSnapshot

_ALERT_SEQ = {"n": 0}


def _next_id() -> str:
    _ALERT_SEQ["n"] += 1
    return f"ALERT-{_ALERT_SEQ['n']:06d}"


def evaluate(snapshot: OpsSnapshot, previous_state: HealthState | None) -> list[Alert]:
    """Real alert-worthy conditions this cycle, deduplicated against the
    previous cycle's overall state so a persistent CRITICAL doesn't fire
    a fresh alert every 5 minutes -- only on entry into a worse state,
    or on specific always-alert conditions (auth expiry, journal
    failure) which are one-shot-per-incident by their own nature.
    """
    alerts: list[Alert] = []

    def add(category: str, message: str, subsystem: str, severity: HealthState = None):
        alerts.append(Alert(
            alert_id=_next_id(), as_of=snapshot.as_of,
            severity=severity or snapshot.health_state,
            category=category, message=message, subsystem=subsystem,
        ))

    state_worsened = (
        previous_state is None
        or _rank(snapshot.health_state) > _rank(previous_state)
    )

    if snapshot.auth_expired:
        # Reported every cycle it's true, deliberately -- an operator
        # needs the running clock, not just a first tick. Deduplicating
        # REPEAT delivery (e.g. don't page every 5 min once acknowledged)
        # is a sink-level concern for a real paging integration, not this
        # engine's -- this engine's job is only to always tell the truth.
        add("auth_expired", f"Broker authentication expired: {snapshot.auth_expired_duration_seconds:.0f}s and counting",
            "broker", HealthState.CRITICAL)

    if "candle_age" in "".join(snapshot.reasons):
        add("stale_market_data", f"Market data stale: {snapshot.candle_age_seconds:.0f}s since last candle",
            "market_data", snapshot.health_state)

    if snapshot.restart_count_last_hour >= 3 and state_worsened:
        add("excessive_restart_frequency", f"{snapshot.restart_count_last_hour} restarts in the last hour",
            "process", snapshot.health_state)

    if snapshot.exception_count_last_hour >= 5 and state_worsened:
        add("excessive_exception_rate", f"{snapshot.exception_count_last_hour} ERROR/CRITICAL log events in the last hour",
            "process", snapshot.health_state)

    if not snapshot.journal_write_ok:
        add("journal_failure", "Trade Journal write failed", "journal", HealthState.CRITICAL)

    if not snapshot.decision_journal_write_ok:
        add("journal_failure", "Decision Journal path unavailable", "journal", HealthState.DEGRADED)

    if snapshot.disk_free_pct <= 15.0:
        add("disk_nearly_full", f"Disk free: {snapshot.disk_free_pct:.1f}%", "process", snapshot.health_state)

    if snapshot.memory_rss_kb >= 500_000:
        add("memory_threshold_exceeded", f"Memory RSS: {snapshot.memory_rss_kb:.0f}KB", "process", snapshot.health_state)

    if not snapshot.ws_connected and state_worsened:
        add("broker_disconnected", "WebSocket tick feed disconnected", "broker", HealthState.DEGRADED)

    return alerts


def _rank(state: HealthState) -> int:
    return {HealthState.HEALTHY: 0, HealthState.WARNING: 1,
            HealthState.DEGRADED: 2, HealthState.CRITICAL: 3,
            HealthState.OFFLINE: 4}[state]
