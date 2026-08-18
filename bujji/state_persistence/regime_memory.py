"""Regime Memory persistence -- Phase 15B. Event-derived: each event
is just the real regime string for one cycle; hydration replays them
through `RegimeMemoryState.advance()` -- the EXACT SAME function the
live IntelligenceCycleRecorder already calls every cycle. Hydration
therefore cannot silently diverge from live behavior.
"""
from __future__ import annotations

import hashlib
from typing import Optional

from bujji.market_regime_memory.models import RegimeMemoryState

from .models import (
    RECOVERY_COMPLETE, RECOVERY_FAILED, RECOVERY_PARTIAL, SCHEMA_VERSION,
    PersistedEvent, RecoveryReport,
)
from .store import EventStore, deduplicated_events

EVENT_TYPE_REGIME_OBSERVED = "REGIME_OBSERVED"


def _event_id(session_id: str, cycle_id: str) -> str:
    """Deterministic -- the SAME (session, cycle) always produces the
    SAME event_id, which is exactly what makes append-on-every-cycle
    safe to call twice for the same cycle (e.g. a retry) without
    double-recording."""
    return "REG-" + hashlib.md5(f"{session_id}|{cycle_id}".encode()).hexdigest()[:24]


def record_regime_cycle(store: EventStore, session_id: str, cycle_id: str, timestamp: str, regime: Optional[str]) -> None:
    """Append one event capturing this cycle's real `market_state.regime`
    value -- `None` (no real reading this cycle) is recorded AS None,
    never omitted and never fabricated into a guess; `RegimeMemoryState.
    advance(None)` already honestly no-ops on it (Phase 11 design)."""
    store.append(PersistedEvent(
        event_id=_event_id(session_id, cycle_id), event_type=EVENT_TYPE_REGIME_OBSERVED,
        session_id=session_id, cycle_id=cycle_id, timestamp=timestamp, schema_version=SCHEMA_VERSION,
        provenance="bujji.state_persistence.regime_memory.record_regime_cycle",
        payload={"regime": regime},
    ))


def hydrate_regime_memory(store: EventStore, session_id: str) -> "tuple[RegimeMemoryState, RecoveryReport]":
    """Pure w.r.t. the store's current contents: replays every REGIME_OBSERVED
    event for `session_id`, in file order, through the real
    `RegimeMemoryState.advance()` -- never reads any live market data."""
    raw_events = []
    malformed_count = 0
    for event, malformed_line in store.read_events_with_diagnostics():
        if malformed_line is not None:
            malformed_count += 1
            continue
        if event.event_type != EVENT_TYPE_REGIME_OBSERVED or event.session_id != session_id:
            continue
        raw_events.append(event)

    events = deduplicated_events(raw_events)
    duplicate_count = len(raw_events) - len(events)

    schema_mismatch_count = sum(1 for e in events if e.schema_version not in ("1.0.0",))
    events = [e for e in events if e.schema_version in ("1.0.0",)]

    state = RegimeMemoryState()
    replayed = 0
    last_cycle_id: Optional[str] = None
    errors = []
    try:
        for e in events:
            state = state.advance(e.payload.get("regime"))
            replayed += 1
            last_cycle_id = e.cycle_id
    except Exception as exc:  # noqa: BLE001 -- a corrupt payload must degrade to PARTIAL, never crash the caller.
        errors.append(f"{type(exc).__name__}: {exc}")

    total_discovered = len(raw_events) + malformed_count
    had_issues = bool(errors or malformed_count or schema_mismatch_count)
    if total_discovered == 0:
        status = RECOVERY_COMPLETE  # a fresh session has nothing to recover -- trivially complete.
    elif errors and replayed == 0:
        status = RECOVERY_FAILED
    elif had_issues and replayed == 0:
        status = RECOVERY_FAILED  # data existed but nothing usable could be reconstructed.
    elif had_issues:
        status = RECOVERY_PARTIAL
    else:
        status = RECOVERY_COMPLETE

    report = RecoveryReport(
        status=status, events_discovered=total_discovered, events_replayed=replayed,
        events_skipped_duplicate=duplicate_count, events_skipped_malformed=malformed_count,
        events_skipped_schema_mismatch=schema_mismatch_count, last_recovered_cycle_id=last_cycle_id,
        errors=tuple(errors),
        unresolved_notes=() if not malformed_count else (f"{malformed_count} malformed line(s) skipped",),
    )
    return state, report
