"""Market State Graph memory -- Phase 19.8.

Extends Phase 19.5's Market Understanding Memory: "Snapshot + Outcome"
becomes "State Sequence + Phenomena + Outcome." Built directly on
`bujji.state_persistence.store.EventStore` -- the SAME append-only
primitive Phase 19.5's `market_understanding.memory_engine` already
uses, never a new persistence mechanism.

Same `as_of_time`-threaded, no-look-ahead discipline Phase 19.5's own
`memory_query.find_similar_memories_as_of()` established: a node whose
own `timestamp` is strictly after the query's `as_of_time` is future
information relative to that query and is excluded, structurally.
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List

from bujji.state_persistence.models import PersistedEvent
from bujji.state_persistence.store import EventStore

from .models import EVENT_MARKET_STATE_NODE_RECORDED, MarketStateNode


def record_market_state_node(store: EventStore, node: MarketStateNode, *, recorded_at: datetime) -> None:
    """Appends ONE immutable fact. Two calls for the same `state_id`
    (same underlying content, by construction) are idempotent -- never a
    duplicate node."""
    event = PersistedEvent(
        event_id=node.state_id, event_type=EVENT_MARKET_STATE_NODE_RECORDED,
        session_id="cross-session", cycle_id=node.timestamp,
        timestamp=recorded_at.isoformat(), schema_version="1.0.0",
        provenance="market_state_graph.memory.record_market_state_node",
        payload=node.to_dict(),
    )
    store.append(event)


def hydrate_market_state_graph(store: EventStore) -> Dict[str, MarketStateNode]:
    """Replays every event in file order. Cross-session by design (like
    `market_understanding`/`outcome_memory`) -- the entire point of a
    durable state graph is to span every session, not just the current
    one."""
    nodes: Dict[str, MarketStateNode] = {}
    for event in store.read_events():
        if event.event_type == EVENT_MARKET_STATE_NODE_RECORDED:
            node = MarketStateNode.from_dict(event.payload)
            nodes[node.state_id] = node
    return nodes


def _parse(timestamp: str) -> datetime:
    return datetime.fromisoformat(timestamp)


def nodes_as_of(nodes: List[MarketStateNode], as_of_time: datetime) -> List[MarketStateNode]:
    """Only nodes whose own `timestamp` is at or before `as_of_time` --
    a node from the future relative to this query can never leak in,
    regardless of what order it happens to sit in `nodes` or when it was
    actually recorded into the store."""
    return [n for n in nodes if _parse(n.timestamp) <= as_of_time]


def build_state_sequence(nodes: List[MarketStateNode], *, from_state_id: str) -> List[MarketStateNode]:
    """Walks `previous_state_id` links backward from `from_state_id`,
    returning the sequence in chronological (oldest-first) order --
    "compression -> expansion -> trend," per this phase's own worked
    example. Stops at the first node whose predecessor is not present
    in `nodes` (an honest, partial sequence, never a fabricated gap
    fill)."""
    by_id = {n.state_id: n for n in nodes}
    if from_state_id not in by_id:
        return []
    sequence = [by_id[from_state_id]]
    cursor = by_id[from_state_id]
    while cursor.previous_state_id is not None and cursor.previous_state_id in by_id:
        cursor = by_id[cursor.previous_state_id]
        sequence.append(cursor)
    return list(reversed(sequence))
