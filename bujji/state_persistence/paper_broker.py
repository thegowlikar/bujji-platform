"""PaperBroker state persistence -- Phase 15B.

PaperBroker itself is untouched except two small, additive methods
(`restore_position`, `restore_realized_pnl` -- see broker/paper.py)
mirroring its own existing `seed_position` precedent exactly.

Snapshot-based, not diff-replayed: after every mutating call
(`place_order`), the caller (an integration wrapper -- see
`record_order_result` below) captures the broker's own CURRENT,
already-computed position/pnl state via its existing public read
methods (`get_open_positions`, `get_realized_pnl`) and persists that as
one event. Hydration reconstructs a fresh `PaperBroker` from the LATEST
such snapshot. This is deliberately NOT full event-sourced order
replay (`_apply_fill`'s netting logic is intentionally NOT
reimplemented here, avoiding any risk of it silently diverging from the
real, protected logic inside paper.py) -- the tradeoff, honestly
disclosed: order HISTORY itself is not reconstructed, only current
positions + realized P&L, which is what continuation actually needs.
Recovery is reported as RECOVERY_PARTIAL for this reason, never
RECOVERY_COMPLETE, so this limitation is never silently hidden.
"""
from __future__ import annotations

import hashlib
from typing import List, Optional

from bujji.broker.paper import PaperBroker

from .models import RECOVERY_COMPLETE, RECOVERY_FAILED, RECOVERY_PARTIAL, SCHEMA_VERSION, PersistedEvent, RecoveryReport
from .store import EventStore, deduplicated_events

EVENT_TYPE_PAPER_STATE_SNAPSHOT = "PAPER_STATE_SNAPSHOT"

# Synthetic bucket key for realized P&L belonging to symbols that are no
# longer open (fully closed positions -- `get_open_positions()` no longer
# lists them, so their per-symbol P&L can't be captured under their own
# symbol). Restoring the leftover under this key keeps `get_realized_pnl()`
# (the total) exact across a restart, at the honestly-disclosed cost of not
# preserving which closed symbol it came from -- consistent with this
# module's existing choice not to reconstruct order history.
_CLOSED_POSITIONS_PNL_KEY = "__CLOSED_POSITIONS__"


def _event_id(session_id: str, cycle_id: str, sequence: int) -> str:
    return "PPR-" + hashlib.md5(f"{session_id}|{cycle_id}|{sequence}".encode()).hexdigest()[:24]


async def record_paper_state(
    store: EventStore, broker: PaperBroker, session_id: str, cycle_id: str, timestamp: str, sequence: int,
) -> None:
    """Snapshot the broker's CURRENT real state (via its own public,
    already-tested read methods -- nothing re-derived) and append it.
    Call this after any `place_order()` whose result should survive a
    restart."""
    positions = await broker.get_open_positions()
    realized_pnl_total = broker.get_realized_pnl()
    per_symbol_pnl = {p["symbol"]: broker.get_realized_pnl(p["symbol"]) for p in positions}
    store.append(PersistedEvent(
        event_id=_event_id(session_id, cycle_id, sequence), event_type=EVENT_TYPE_PAPER_STATE_SNAPSHOT,
        session_id=session_id, cycle_id=cycle_id, timestamp=timestamp, schema_version=SCHEMA_VERSION,
        provenance="bujji.state_persistence.paper_broker.record_paper_state",
        payload={"positions": positions, "realized_pnl_total": realized_pnl_total, "per_symbol_pnl": per_symbol_pnl},
    ))


def hydrate_paper_broker(store: EventStore, session_id: str, seed: int = 42) -> "tuple[PaperBroker, RecoveryReport]":
    """Reconstructs a fresh PaperBroker with the LATEST recorded
    positions/realized-P&L restored via the two additive setters.
    Never restores order history (see module docstring) -- always
    RECOVERY_PARTIAL when any event was found, RECOVERY_COMPLETE only
    for a genuinely fresh session with nothing to recover."""
    raw_events: List[PersistedEvent] = []
    malformed_count = 0
    for event, malformed_line in store.read_events_with_diagnostics():
        if malformed_line is not None:
            malformed_count += 1
            continue
        if event.event_type != EVENT_TYPE_PAPER_STATE_SNAPSHOT or event.session_id != session_id:
            continue
        raw_events.append(event)

    events = deduplicated_events(raw_events)
    duplicate_count = len(raw_events) - len(events)
    schema_mismatch_count = sum(1 for e in events if e.schema_version not in ("1.0.0",))
    events = [e for e in events if e.schema_version in ("1.0.0",)]

    broker = PaperBroker(seed=seed)
    errors = []
    last_cycle_id: Optional[str] = None
    if events:
        latest = max(events, key=lambda e: e.timestamp)
        last_cycle_id = latest.cycle_id
        try:
            for pos in latest.payload.get("positions", []):
                broker.restore_position(
                    pos["symbol"], pos["side"], pos["qty"], pos["avg_price"], pos["entry_timestamp"],
                )
            per_symbol_pnl = latest.payload.get("per_symbol_pnl") or {}
            for symbol, amount in per_symbol_pnl.items():
                broker.restore_realized_pnl(symbol, amount)
            leftover = latest.payload.get("realized_pnl_total", 0.0) - sum(per_symbol_pnl.values())
            if leftover:
                broker.restore_realized_pnl(_CLOSED_POSITIONS_PNL_KEY, leftover)
        except Exception as exc:  # noqa: BLE001 -- a corrupt snapshot must degrade to FAILED, never crash the caller.
            errors.append(f"{type(exc).__name__}: {exc}")

    total_discovered = len(raw_events) + malformed_count
    if total_discovered == 0:
        status = RECOVERY_COMPLETE
    elif errors:
        status = RECOVERY_FAILED
    else:
        status = RECOVERY_PARTIAL  # order history is never reconstructed -- always disclosed, never COMPLETE.

    report = RecoveryReport(
        status=status, events_discovered=total_discovered, events_replayed=1 if (events and not errors) else 0,
        events_skipped_duplicate=duplicate_count, events_skipped_malformed=malformed_count,
        events_skipped_schema_mismatch=schema_mismatch_count, last_recovered_cycle_id=last_cycle_id,
        errors=tuple(errors),
        unresolved_notes=("order history is not reconstructed -- only current positions and realized P&L",) if events else (),
    )
    return broker, report
