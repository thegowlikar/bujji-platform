"""D-8b: make the outcome memory outlive the process that formed it.

`attribute_and_remember()` already builds a real OutcomeMemoryRecord at the
end of a closed position -- and handed it back into an in-memory dict. The
session then exited and the record went with it. Bujji forgot every trade it
had ever made, which makes "adaptive risk memory" and every cross-session
statistic a promise nothing could keep. The record was roughly five lines
from durable.

The five lines are here, and they invent no new persistence mechanism:
`bujji.outcome_memory.recovery.hydrate_outcome_memory` already replays an
`EventStore` CROSS-SESSION, and `outcome_memory.engine.apply_event` already
accepts exactly one event type. This module writes that event, following the
same shape `market_state_graph.memory.record_market_state_node` established.

IDEMPOTENCY BY CONSTRUCTION: `event_id` is the record's own `memory_id`, so
a retry (or a re-run over the same position) is skipped on replay rather
than double-counted. The reducer is stricter still -- an identical repeat is
IDEMPOTENT, a conflicting one for the same memory_id is REJECTED outright, so
a memory record can never be silently rewritten into a different history.
"""
from __future__ import annotations

from typing import Any, Optional

from bujji.outcome_memory.models import EVENT_OUTCOME_MEMORY_RECORDED, SCHEMA_VERSION
from bujji.state_persistence.models import PersistedEvent

PROVENANCE = "production_runtime.outcome_memory_writer.persist_outcome_memory"


def build_outcome_memory_event(record: Any, *, session_id: str, recorded_at: str) -> PersistedEvent:
    """One durable event carrying the whole record.

    `session_id` is the ORIGINATING session, kept for lineage only -- the
    hydration path is deliberately cross-session and never filters on it.
    """
    return PersistedEvent(
        event_id=record.memory_id,
        event_type=EVENT_OUTCOME_MEMORY_RECORDED,
        session_id=session_id,
        cycle_id=getattr(record, "recorded_at", None),
        timestamp=recorded_at,
        schema_version=SCHEMA_VERSION,
        provenance=PROVENANCE,
        payload={"memory_id": record.memory_id, "record": record.to_dict()},
    )


def persist_outcome_memory(store: Any, record: Optional[Any], *, session_id: str,
                           recorded_at: str) -> str:
    """Append the record to the durable cross-session store.

    Returns a short outcome string for the session summary. A None record is
    NOT an error: `attribute_and_remember` returns None whenever attribution
    was not READY, and persisting nothing is the honest response to having
    nothing -- a speculative memory would poison every statistic computed
    over the campaign afterwards.
    """
    if record is None:
        return "NO_RECORD"
    if store is None:
        return "NO_STORE"
    store.append(build_outcome_memory_event(record, session_id=session_id, recorded_at=recorded_at))
    return "PERSISTED"
