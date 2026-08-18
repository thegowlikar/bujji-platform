"""Phase 20.21 -- persistence. Built directly on `bujji.state_
persistence.store.EventStore` -- the SAME append-only primitive
`bujji.market_memory.store` (Phase 20.15) and `bujji.shadow_result.
store` (Phase 20.20) already use. Per the explicit "reuse
bujji.market_memory, do not create another memory database"
instruction: callers are expected to pass the SAME `EventStore`
instance (same durable file) `bujji.market_memory` already writes
`MARKET_MEMORY_RECORDED`/`DECISION_MEMORY_RECORDED`/
`OUTCOME_MEMORY_RECORDED` events into for a given session -- this
module adds a fourth, additive event type
(`LEARNING_UPDATE_RECORDED`) to that SAME file, never a second
database. `market_memory`'s own read functions filter by their own
event_type and are structurally unaffected by this addition (verified
by a dedicated coexistence test).

No update, no delete method exists anywhere in this module -- records
are immutable by construction.
"""
from __future__ import annotations

from typing import List

from bujji.state_persistence.models import PersistedEvent
from bujji.state_persistence.store import EventStore, deduplicated_events

from .models import EVENT_LEARNING_UPDATE_RECORDED, SCHEMA_VERSION, LearningUpdateRecord

_PROVENANCE = "bujji.learning_update"


def record_learning_update(store: EventStore, record: LearningUpdateRecord, *, session_id: str) -> None:
    """`event_id` is `record.update_id` itself -- deterministic from
    `source_shadow_result_id` alone, so re-evaluating and re-recording
    the same `ShadowResultRecord` (e.g. after a crash-and-retry)
    produces a duplicate line, correctly collapsed to one on read by
    `deduplicated_events()` -- never double-counted."""
    store.append(PersistedEvent(
        event_id=record.update_id, event_type=EVENT_LEARNING_UPDATE_RECORDED, session_id=session_id,
        cycle_id=record.created_at, timestamp=record.created_at, schema_version=SCHEMA_VERSION,
        provenance=_PROVENANCE, payload=record.to_dict(),
    ))


def read_all_learning_updates(store: EventStore) -> List[LearningUpdateRecord]:
    events = [e for e in store.read_events() if e.event_type == EVENT_LEARNING_UPDATE_RECORDED]
    return [LearningUpdateRecord.from_dict(e.payload) for e in deduplicated_events(events)]
