"""Event Bus — lightweight async pub/sub to decouple modules.

The orchestrator publishes domain events (a candle closed, a signal fired, a
position opened, a decision was made, a position closed, the state changed).
Cross-cutting concerns — journaling, dashboard status, logging, and future
additions like Telegram/voice/AI hooks — subscribe without the core modules
knowing they exist.

Handlers may be sync or async. A failing handler is isolated and logged; it
never breaks publishing or other subscribers. This is the seam along which
Version 2 features attach without touching the trading core.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Awaitable, Callable, Union


class EventType(str, Enum):
    CANDLE_CLOSED = "CANDLE_CLOSED"
    SIGNAL_GENERATED = "SIGNAL_GENERATED"
    POSITION_OPENED = "POSITION_OPENED"
    DECISION_MADE = "DECISION_MADE"
    POSITION_CLOSED = "POSITION_CLOSED"
    STATE_CHANGED = "STATE_CHANGED"
    HEALTH_CHANGED = "HEALTH_CHANGED"


@dataclass(frozen=True)
class Event:
    type: EventType
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)


Handler = Callable[[Event], Union[None, Awaitable[None]]]


class EventBus:
    """Minimal in-process async event bus."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._subscribers: dict[EventType, list[Handler]] = defaultdict(list)
        self._log = logger

    def subscribe(self, event_type: EventType, handler: Handler) -> None:
        self._subscribers[event_type].append(handler)

    def unsubscribe(self, event_type: EventType, handler: Handler) -> None:
        if handler in self._subscribers.get(event_type, []):
            self._subscribers[event_type].remove(handler)

    async def publish(self, event: Event) -> None:
        for handler in list(self._subscribers.get(event.type, ())):
            try:
                result = handler(event)
                if inspect.isawaitable(result):
                    await result
            except Exception:  # noqa: BLE001 - isolate subscriber failures.
                if self._log:
                    self._log.exception(
                        "event_handler_error type=%s", event.type.value
                    )

    def publish_nowait(self, event: Event) -> None:
        """Fire-and-forget publish for synchronous call sites (e.g. replay)."""
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.publish(event))
        except RuntimeError:
            asyncio.run(self.publish(event))
