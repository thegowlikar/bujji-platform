"""Phase 20.20 -- persistence. Built directly on `bujji.state_
persistence.store.EventStore` (Phase 15B) -- the same append-only
primitive Phase 20.15's own `market_memory.store` already uses, never
a new persistence mechanism. No update, no delete method exists
anywhere in this module -- records are immutable by construction.

Restart recovery is DELIBERATELY not a separate mechanism: hydration
IS `read_all_shadow_results()` -- re-reading the same durable file a
fresh process would read on any normal call. This mirrors Phase 15B/
19.x's own established "event-derived reconstruction over a separate
cache" principle exactly.
"""
from __future__ import annotations

from typing import List

from bujji.state_persistence.models import PersistedEvent
from bujji.state_persistence.store import EventStore, deduplicated_events

from .models import EVENT_SHADOW_RESULT_RECORDED, SCHEMA_VERSION, ShadowResultRecord

_PROVENANCE = "bujji.shadow_result"


def record_shadow_result(store: EventStore, record: ShadowResultRecord) -> None:
    """`event_id` is `record.record_id` itself -- writing the same
    record twice (e.g. after a crash-and-retry) produces two lines in
    the underlying file, but `read_all_shadow_results()` explicitly
    deduplicates by `event_id` on read via `deduplicated_events()`
    (first occurrence wins), never double-counting."""
    store.append(PersistedEvent(
        event_id=record.record_id, event_type=EVENT_SHADOW_RESULT_RECORDED, session_id=record.session_id,
        cycle_id=record.timestamp, timestamp=record.timestamp, schema_version=SCHEMA_VERSION,
        provenance=_PROVENANCE, payload=record.to_dict(),
    ))


def read_all_shadow_results(store: EventStore) -> List[ShadowResultRecord]:
    events = [e for e in store.read_events() if e.event_type == EVENT_SHADOW_RESULT_RECORDED]
    return [ShadowResultRecord.from_dict(e.payload) for e in deduplicated_events(events)]
