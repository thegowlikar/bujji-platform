"""Phase 20.15 -- persistence. Built directly on `bujji.state_
persistence.store.EventStore` -- the SAME append-only primitive Phase
15B established and Phase 19.5/19.8's own memory systems already use,
never a new persistence mechanism. No update, no delete method exists
anywhere in this module -- historical records are immutable by
construction, not by convention.
"""
from __future__ import annotations

from typing import List

from bujji.state_persistence.models import PersistedEvent
from bujji.state_persistence.store import EventStore

from .models import (
    DecisionMemoryRecord, EVENT_DECISION_MEMORY_RECORDED, EVENT_MARKET_MEMORY_RECORDED,
    EVENT_OUTCOME_MEMORY_RECORDED, MarketMemoryRecord, OutcomeMemoryRecord, SCHEMA_VERSION,
)

_PROVENANCE = "bujji.market_memory"


def record_market_memory(store: EventStore, record: MarketMemoryRecord, *, session_id: str) -> None:
    store.append(PersistedEvent(
        event_id=f"{EVENT_MARKET_MEMORY_RECORDED}:{record.memory_id}",
        event_type=EVENT_MARKET_MEMORY_RECORDED, session_id=session_id, cycle_id=record.as_of_time,
        timestamp=record.as_of_time, schema_version=SCHEMA_VERSION, provenance=_PROVENANCE,
        payload=record.to_dict(),
    ))


def record_decision_memory(store: EventStore, record: DecisionMemoryRecord, *, session_id: str) -> None:
    store.append(PersistedEvent(
        event_id=f"{EVENT_DECISION_MEMORY_RECORDED}:{record.memory_id}:{record.candidate_strategy or 'UNKNOWN'}",
        event_type=EVENT_DECISION_MEMORY_RECORDED, session_id=session_id, cycle_id=record.as_of_time,
        timestamp=record.as_of_time, schema_version=SCHEMA_VERSION, provenance=_PROVENANCE,
        payload=record.to_dict(),
    ))


def record_outcome_memory(store: EventStore, record: OutcomeMemoryRecord, *, session_id: str,
                           recorded_at: str) -> None:
    """`recorded_at`: when this outcome record was written (may be long
    after `record.observed_at`) -- kept distinct from `observed_at`
    itself, which is the real, factual later-cycle timestamp."""
    store.append(PersistedEvent(
        event_id=f"{EVENT_OUTCOME_MEMORY_RECORDED}:{record.memory_id}:{record.status}:{recorded_at}",
        event_type=EVENT_OUTCOME_MEMORY_RECORDED, session_id=session_id, cycle_id=record.observed_at,
        timestamp=recorded_at, schema_version=SCHEMA_VERSION, provenance=_PROVENANCE,
        payload=record.to_dict(),
    ))


def read_all_market_memories(store: EventStore) -> List[MarketMemoryRecord]:
    return [MarketMemoryRecord.from_dict(e.payload) for e in store.read_events()
            if e.event_type == EVENT_MARKET_MEMORY_RECORDED]


def read_all_decision_memories(store: EventStore) -> List[DecisionMemoryRecord]:
    return [DecisionMemoryRecord.from_dict(e.payload) for e in store.read_events()
            if e.event_type == EVENT_DECISION_MEMORY_RECORDED]


def read_all_outcome_memories(store: EventStore) -> List[OutcomeMemoryRecord]:
    return [OutcomeMemoryRecord.from_dict(e.payload) for e in store.read_events()
            if e.event_type == EVENT_OUTCOME_MEMORY_RECORDED]
