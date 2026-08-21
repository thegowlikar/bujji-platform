"""Outcome Memory Recovery -- Phase 15N.

Reuses `bujji.state_persistence.EventStore` DIRECTLY -- no new
persistence mechanism. UNLIKE `position_lifecycle.recovery` (Phase
15G, per-session), this hydration is deliberately CROSS-SESSION: it
takes no target `session_id` and replays every well-formed event in
the store, regardless of origin session (see `models.py`'s own module
docstring for the architectural justification)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Tuple

from bujji.state_persistence.models import (
    RECOGNIZED_SCHEMA_VERSIONS, RECOVERY_COMPLETE, RECOVERY_FAILED, RECOVERY_PARTIAL, RecoveryReport,
)
from bujji.state_persistence.store import EventStore, deduplicated_events

from .engine import apply_event
from .models import OutcomeMemoryRecord, TransitionResult


@dataclass(frozen=True)
class OutcomeMemoryRecoveryReport:
    base: RecoveryReport
    transitions_accepted: int
    transitions_idempotent: int
    transitions_rejected: int
    rejected_reasons: Tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "base": self.base.to_dict(), "transitions_accepted": self.transitions_accepted,
            "transitions_idempotent": self.transitions_idempotent, "transitions_rejected": self.transitions_rejected,
            "rejected_reasons": list(self.rejected_reasons),
        }


def hydrate_outcome_memory(store: EventStore) -> Tuple[Dict[str, OutcomeMemoryRecord], OutcomeMemoryRecoveryReport]:
    raw_events = []
    malformed = 0
    for event, malformed_line in store.read_events_with_diagnostics():
        if malformed_line is not None:
            malformed += 1
            continue
        raw_events.append(event)

    deduped = deduplicated_events(raw_events)
    duplicate_count = len(raw_events) - len(deduped)

    schema_mismatch = sum(1 for e in deduped if e.schema_version not in RECOGNIZED_SCHEMA_VERSIONS)
    deduped = [e for e in deduped if e.schema_version in RECOGNIZED_SCHEMA_VERSIONS]

    states: Dict[str, OutcomeMemoryRecord] = {}
    accepted = idempotent = rejected = 0
    rejected_reasons = []
    errors = []
    last_ts = None

    for e in deduped:
        try:
            states, result = apply_event(states, e.event_type, e.payload)
        except Exception as exc:  # noqa: BLE001 -- a corrupt-but-parseable event must degrade to FAILED, never crash the caller.
            errors.append(f"{type(exc).__name__}: {exc}")
            continue
        last_ts = e.timestamp
        if result.outcome == "ACCEPTED":
            accepted += 1
        elif result.outcome == "IDEMPOTENT":
            idempotent += 1
        else:
            rejected += 1
            rejected_reasons.append(result.reason)

    total_discovered = len(raw_events) + malformed
    had_issues = bool(errors or malformed or schema_mismatch)
    replayed = accepted + idempotent  # rejected events were discovered/read but not applied -- not "replayed."
    if total_discovered == 0:
        status = RECOVERY_COMPLETE
    elif errors and replayed == 0 and accepted == 0:
        status = RECOVERY_FAILED
    elif had_issues and replayed == 0:
        status = RECOVERY_FAILED
    elif had_issues:
        status = RECOVERY_PARTIAL
    else:
        status = RECOVERY_COMPLETE

    base = RecoveryReport(
        status=status, events_discovered=total_discovered, events_replayed=replayed,
        events_skipped_duplicate=duplicate_count, events_skipped_malformed=malformed,
        events_skipped_schema_mismatch=schema_mismatch, last_recovered_cycle_id=last_ts,
        errors=tuple(errors),
        unresolved_notes=() if not had_issues else (
            f"{malformed} malformed, {schema_mismatch} schema-mismatched, {duplicate_count} duplicate event(s) skipped",
        ),
    )
    return states, OutcomeMemoryRecoveryReport(
        base=base, transitions_accepted=accepted, transitions_idempotent=idempotent,
        transitions_rejected=rejected, rejected_reasons=tuple(rejected_reasons),
    )
