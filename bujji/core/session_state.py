"""Crash-recovery session state.

A JSON snapshot written on every meaningful transition so the bot can recover
its FSM state and open position after a restart, then reconcile against the
broker's live positions (C1).

The write is **atomic** (temp file + ``os.replace``) so a crash mid-write can
never leave a half-written, unparseable snapshot — a corrupt snapshot would
silently discard knowledge of an open position and defeat recovery.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from .clock import now_ist


@dataclass
class SessionSnapshot:
    # D2: the trading day is defined by IST, not the host's local date — a
    # host running in UTC would otherwise roll over to "tomorrow" up to 5.5
    # hours early relative to the actual IST trading day.
    trading_date: str = field(default_factory=lambda: now_ist().date().isoformat())
    state: str = "WAITING"
    trades_taken: int = 0
    position: Optional[dict[str, Any]] = None  # Full serialized Position.
    # Idempotency keys for in-flight orders, so a restart mid-order can resume
    # the *same* order rather than placing a duplicate (C3).
    entry_client_order_id: Optional[str] = None
    exit_client_order_id: Optional[str] = None

    def is_today(self) -> bool:
        return self.trading_date == now_ist().date().isoformat()


class SessionStore:
    """Reads/writes the session snapshot to disk atomically."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> SessionSnapshot:
        if not self._path.exists():
            return SessionSnapshot()
        try:
            data = json.loads(self._path.read_text())
            snap = SessionSnapshot(**data)
        except (json.JSONDecodeError, TypeError, ValueError, OSError):
            # A corrupt/unreadable snapshot must not crash startup; treat as
            # absent. Recovery will then rely on broker reconciliation.
            return SessionSnapshot()
        # A stale snapshot from a previous day starts fresh.
        return snap if snap.is_today() else SessionSnapshot()

    def save(self, snapshot: SessionSnapshot) -> None:
        payload = json.dumps(asdict(snapshot), indent=2, default=str)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        # Write fully, flush+fsync, then atomically replace the live file.
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self._path)
