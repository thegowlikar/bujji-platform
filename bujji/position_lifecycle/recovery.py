"""Position Lifecycle Recovery -- Phase 15G.

Reuses `bujji.state_persistence.EventStore` DIRECTLY -- no new
persistence mechanism was built. Hydration replays every real,
session-scoped lifecycle event through the EXACT SAME `apply_event`
reducer a live session uses, via `EventStore`'s own existing
diagnostics (`read_events_with_diagnostics`) and dedup
(`deduplicated_events`) -- the same pattern Phase 15B-15F all follow.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from bujji.state_persistence.models import (
    RECOGNIZED_SCHEMA_VERSIONS, RECOVERY_COMPLETE, RECOVERY_FAILED, RECOVERY_PARTIAL, RecoveryReport,
)
from bujji.state_persistence.store import EventStore, deduplicated_events

from .engine import apply_event
from .models import PositionLifecycle, TransitionResult


@dataclass(frozen=True)
class LifecycleRecoveryReport:
    """Extends the standard RecoveryReport vocabulary with lifecycle-
    specific outcomes (accepted/idempotent/rejected transitions) --
    composition, not a competing report shape."""

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


def hydrate_position_lifecycles(
    store: EventStore, session_id: str,
) -> Tuple[Dict[str, PositionLifecycle], LifecycleRecoveryReport]:
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

    states: Dict[str, PositionLifecycle] = {}
    accepted = idempotent = rejected = 0
    rejected_reasons = []
    errors = []
    last_ts = None

    for e in deduped:
        try:
            states, result = apply_event(states, session_id, e.event_type, e.session_id, e.payload)
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
    report = LifecycleRecoveryReport(
        base=base, transitions_accepted=accepted, transitions_idempotent=idempotent,
        transitions_rejected=rejected, rejected_reasons=tuple(rejected_reasons),
    )
    return states, report
