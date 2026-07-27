"""Deterministic JSON round-trip for LiveObservationEvent / ProducerState.

Explicit key ordering throughout -- never relies on dict iteration
order. No uuid4(), no datetime.now(), no unseeded randomness anywhere
in this module (mirrors `bujji.market_observation.serialization`).
"""
from __future__ import annotations

import json
from typing import Any, Dict

from .models import (
    AggregationWindow,
    LateTick,
    LiveObservationEvent,
    ProducerState,
    ProducerStateTransition,
    Tick,
)


def event_to_dict(event: LiveObservationEvent) -> Dict[str, Any]:
    return {
        "event_id": event.event_id,
        "event_type": event.event_type,
        "timestamp": event.timestamp,
        "source": event.source,
        "payload": event.payload,
        "sequence": event.sequence,
    }


def event_from_dict(d: Dict[str, Any]) -> LiveObservationEvent:
    return LiveObservationEvent(
        event_id=d["event_id"],
        event_type=d["event_type"],
        timestamp=d["timestamp"],
        source=d["source"],
        payload=d["payload"],
        sequence=d["sequence"],
    )


def event_to_json(event: LiveObservationEvent) -> str:
    return json.dumps(event_to_dict(event), sort_keys=True)


def event_from_json(text: str) -> LiveObservationEvent:
    return event_from_dict(json.loads(text))


def transition_to_dict(t: ProducerStateTransition) -> Dict[str, Any]:
    return {
        "from_state": t.from_state,
        "to_state": t.to_state,
        "timestamp": t.timestamp,
        "reason": t.reason,
    }


def transition_from_dict(d: Dict[str, Any]) -> ProducerStateTransition:
    return ProducerStateTransition(
        from_state=d["from_state"],
        to_state=d["to_state"],
        timestamp=d["timestamp"],
        reason=d["reason"],
    )


def producer_state_to_dict(state: ProducerState) -> Dict[str, Any]:
    return {
        "producer_id": state.producer_id,
        "current_state": state.current_state,
        "history": [transition_to_dict(t) for t in state.history],
    }


def producer_state_from_dict(d: Dict[str, Any]) -> ProducerState:
    return ProducerState(
        producer_id=d["producer_id"],
        current_state=d["current_state"],
        history=tuple(transition_from_dict(t) for t in d["history"]),
    )


def producer_state_to_json(state: ProducerState) -> str:
    return json.dumps(producer_state_to_dict(state), sort_keys=True)


def producer_state_from_json(text: str) -> ProducerState:
    return producer_state_from_dict(json.loads(text))


def tick_to_dict(tick: Tick) -> Dict[str, Any]:
    return {"timestamp": tick.timestamp, "price": tick.price, "volume": tick.volume}


def tick_from_dict(d: Dict[str, Any]) -> Tick:
    return Tick(timestamp=d["timestamp"], price=d["price"], volume=d.get("volume"))


def late_tick_to_dict(lt: LateTick) -> Dict[str, Any]:
    return {"tick": tick_to_dict(lt.tick), "reason": lt.reason}


def late_tick_from_dict(d: Dict[str, Any]) -> LateTick:
    return LateTick(tick=tick_from_dict(d["tick"]), reason=d["reason"])


def window_to_dict(window: AggregationWindow) -> Dict[str, Any]:
    return {
        "instrument": window.instrument,
        "interval": window.interval,
        "window_start": window.window_start,
        "window_end": window.window_end,
        "ticks": [tick_to_dict(t) for t in window.ticks],
        "late_ticks": [late_tick_to_dict(lt) for lt in window.late_ticks],
        "is_closed": window.is_closed,
    }


def window_from_dict(d: Dict[str, Any]) -> AggregationWindow:
    return AggregationWindow(
        instrument=d["instrument"],
        interval=d["interval"],
        window_start=d["window_start"],
        window_end=d["window_end"],
        ticks=tuple(tick_from_dict(t) for t in d["ticks"]),
        late_ticks=tuple(late_tick_from_dict(lt) for lt in d["late_ticks"]),
        is_closed=d["is_closed"],
    )
