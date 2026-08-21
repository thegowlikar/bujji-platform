"""State Persistence + Hydration — Phase 15B. Pure models, no IO here
(IO lives in store.py), no broker, no execution.

Design principle (mission's own explicit preference): prefer
EVENT-DERIVED reconstruction over blindly serializing mutable objects.
Every component's hydration function below replays a sequence of small,
append-only `PersistedEvent`s through the SAME pure state-transition
function the live code already uses (`RegimeMemoryState.advance()`,
etc.) -- so hydration can never silently diverge from live behavior;
it IS the live behavior, just fed historical input.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

SCHEMA_VERSION = "1.0.0"
RECOGNIZED_SCHEMA_VERSIONS = ("1.0.0",)

RECOVERY_COMPLETE = "RECOVERY_COMPLETE"
RECOVERY_PARTIAL = "RECOVERY_PARTIAL"
RECOVERY_FAILED = "RECOVERY_FAILED"

ALL_RECOVERY_STATUSES = (RECOVERY_COMPLETE, RECOVERY_PARTIAL, RECOVERY_FAILED)


@dataclass(frozen=True)
class PersistedEvent:
    """One append-only record. `event_id` is the idempotency key --
    two events with the same `event_id` are the SAME event; the second
    is skipped on replay, never double-applied. `cycle_id` is the
    originating intelligence cycle's own timestamp (this project's
    established id convention -- no separate cycle-id field exists
    upstream, mirrors Phase 12/14's own `source_cycle_id` precedent)."""

    event_id: str
    event_type: str
    session_id: str
    cycle_id: Optional[str]
    timestamp: str
    schema_version: str
    provenance: str
    payload: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "event_id": self.event_id, "event_type": self.event_type, "session_id": self.session_id,
            "cycle_id": self.cycle_id, "timestamp": self.timestamp, "schema_version": self.schema_version,
            "provenance": self.provenance, "payload": self.payload,
        }

    @staticmethod
    def from_dict(d: dict) -> "PersistedEvent":
        return PersistedEvent(
            event_id=d["event_id"], event_type=d["event_type"], session_id=d["session_id"],
            cycle_id=d.get("cycle_id"), timestamp=d["timestamp"], schema_version=d["schema_version"],
            provenance=d.get("provenance", ""), payload=d.get("payload") or {},
        )


@dataclass(frozen=True)
class RecoveryReport:
    status: str
    events_discovered: int
    events_replayed: int
    events_skipped_duplicate: int
    events_skipped_malformed: int
    events_skipped_schema_mismatch: int
    last_recovered_cycle_id: Optional[str]
    errors: Tuple[str, ...]
    unresolved_notes: Tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "events_discovered": self.events_discovered,
            "events_replayed": self.events_replayed,
            "events_skipped_duplicate": self.events_skipped_duplicate,
            "events_skipped_malformed": self.events_skipped_malformed,
            "events_skipped_schema_mismatch": self.events_skipped_schema_mismatch,
            "last_recovered_cycle_id": self.last_recovered_cycle_id,
            "errors": list(self.errors),
            "unresolved_notes": list(self.unresolved_notes),
        }
