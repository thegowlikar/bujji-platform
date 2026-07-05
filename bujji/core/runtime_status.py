"""Shared read-only runtime status.

A single mutable object the orchestrator updates and the dashboard reads. Keeps
the dashboard fully decoupled from the trading modules — it only observes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional


@dataclass
class RuntimeStatus:
    state: str = "WAITING"
    vwap: Optional[float] = None
    vwap_real: bool = False
    orb_high: Optional[float] = None
    orb_low: Optional[float] = None
    spot: Optional[float] = None
    direction: Optional[str] = None
    position_symbol: Optional[str] = None
    entry_premium: Optional[float] = None
    current_premium: Optional[float] = None
    mtm: Optional[float] = None
    last_decision: Optional[str] = None
    last_reason: Optional[str] = None
    healthy: bool = True
    health_detail: str = "ok"
    # E1/E2: distinct from generic `healthy=False` — specifically means the
    # broker session/token is invalid and needs a human to refresh it and
    # restart the process. Kept separate so the dashboard/alerting can tell
    # "credentials problem" apart from "transient/unknown failure" at a glance.
    auth_expired: bool = False
    # D1: whether the wall clock is currently trusted (no recent drift/jump
    # detected). When False, new entries are blocked; existing positions are
    # still managed/exited normally.
    clock_trusted: bool = True
    clock_drift_detail: str = ""
    # C_D1: candle feed quality — detection only, purely observational.
    duplicate_candles_ignored: int = 0
    last_candle_gap_seconds: Optional[float] = None
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    recent_logs: list[str] = field(default_factory=list)
    # Latest VWAP audit record + a rolling history for "Market Data Health".
    market_data_health: Optional[dict[str, Any]] = None
    vwap_audit_history: list[dict[str, Any]] = field(default_factory=list)

    def touch(self) -> None:
        self.updated_at = datetime.now().isoformat()

    def push_audit(self, record: dict[str, Any], cap: int = 100) -> None:
        self.vwap_audit_history.append(record)
        if len(self.vwap_audit_history) > cap:
            self.vwap_audit_history = self.vwap_audit_history[-cap:]
        self.touch()

    def push_log(self, line: str, cap: int = 200) -> None:
        self.recent_logs.append(line)
        if len(self.recent_logs) > cap:
            self.recent_logs = self.recent_logs[-cap:]
        self.touch()
