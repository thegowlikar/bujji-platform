"""Shadow Trade Timeline -- BUJJI Options OS v3, Gate F.1.

PURPOSE: an immutable, ordered record of every event the Trading Brain
Shadow Runtime publishes -- not a dashboard, just the event stream
itself, replayable later. This is a SUBSCRIBER to the existing
`bujji.core.event_bus.EventBus`, never a second publish path -- it
never calls `publish()` itself, only `subscribe()`. No new EventType
is introduced (see `trading_brain_runtime.py`'s own module docstring
for why): every domain-specific stage label (STRATEGY_PROPOSED,
RISK_APPROVED, ORDER_SUBMITTED, ORDER_FILLED, POSITION_CREATED, ...)
lives in `event.payload["stage"]`, carried straight through into each
`TimelineEntry.stage` field, so the timeline reads exactly like the
brief's own worked example without editing `event_bus.py` at all.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Tuple

from bujji.core.event_bus import Event, EventBus, EventType


@dataclass(frozen=True)
class TimelineEntry:
    sequence: int
    event_type: str
    stage: str
    payload: Dict
    timestamp: datetime


class ShadowTradeTimeline:
    """Append-only. No update/delete method exists anywhere on this
    class -- once recorded, a timeline entry never changes."""

    def __init__(self) -> None:
        self._entries: list = []

    def attach(self, event_bus: EventBus) -> None:
        """Subscribes to every EventType this runtime publishes.
        Idempotent to call once per runtime lifetime -- calling twice
        on the same bus would double-record, so callers attach exactly
        once (verified by the runtime's own construction path)."""
        for event_type in EventType:
            event_bus.subscribe(event_type, self._record)

    def _record(self, event: Event) -> None:
        stage = event.payload.get("stage", event.type.value)
        self._entries.append(TimelineEntry(
            sequence=len(self._entries), event_type=event.type.value, stage=stage,
            payload=dict(event.payload), timestamp=event.timestamp,
        ))

    def entries(self) -> Tuple[TimelineEntry, ...]:
        return tuple(self._entries)

    def __len__(self) -> int:
        return len(self._entries)
