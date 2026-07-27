"""Live Observation Producer Framework models — frozen, immutable records.

Every dataclass here is `frozen=True` and carries no logic -- construction
lives in `engine.py`, never here (mirrors `bujji.market_observation.models`'
own discipline). Nothing in this file connects to a broker, parses a raw
WebSocket frame, or interprets a value into Derived Evidence.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Tuple


# ---------------------------------------------------------------------------
# LiveObservationEvent — the unit a Producer emits (Deliverable 3).
# `event_type` is one of taxonomy.ALL_EVENT_TYPES. `payload` is generic
# (this is infrastructure, not a specific domain) -- its shape depends on
# `event_type` and is interpreted only by engine.translate_event.
# `timestamp` is always caller/source-supplied, never wall-clock read
# inside this module.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class LiveObservationEvent:
    event_id: str
    event_type: str
    timestamp: str
    source: str
    payload: Any
    sequence: int              # Monotonic per-producer sequence number, for tracing/ordering.


# ---------------------------------------------------------------------------
# ProducerStateTransition — one recorded lifecycle move, for the
# append-only ProducerState.history below.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ProducerStateTransition:
    from_state: str
    to_state: str
    timestamp: str
    reason: str


# ---------------------------------------------------------------------------
# ProducerState — current lifecycle state + append-only transition
# history (Deliverable 7), for auditability. `history` is a tuple,
# never mutated in place; engine.apply_transition returns a new
# ProducerState rather than mutating this one.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ProducerState:
    producer_id: str
    current_state: str
    history: Tuple[ProducerStateTransition, ...] = ()


# ---------------------------------------------------------------------------
# AggregationWindow — a genuinely thin structural representation of
# "ticks accumulating toward window close" (Deliverable 5). This sprint
# only defines the framework: it stores raw ticks and boundaries, it
# does NOT compute indicators. See engine.py's module docstring for the
# disclosed reasoning on why closing a window into OHLC is treated as
# raw transport (definitional of what a candle IS) rather than an
# indicator computation.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class AggregationWindow:
    instrument: str
    interval: str                              # One of taxonomy.ALL_AGGREGATION_INTERVALS.
    window_start: str
    window_end: str
    ticks: Tuple["Tick", ...] = ()
    late_ticks: Tuple["LateTick", ...] = ()
    is_closed: bool = False


# ---------------------------------------------------------------------------
# Tick — the minimal raw fact an AggregationWindow accumulates. Not the
# 73A Observation itself (that is only minted for a TICK_RECEIVED event
# via engine.translate_event, or for a closed window) -- this is the
# window's own internal raw-value record.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Tick:
    timestamp: str
    price: float
    volume: Optional[float] = None


# ---------------------------------------------------------------------------
# LateTick — a tick that arrived with a timestamp before the window's
# own start. Never dropped (see taxonomy.py's "out-of-window-order"
# note) -- recorded here instead, disclosed and queryable.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class LateTick:
    tick: Tick
    reason: str                                # One of taxonomy.ALL_LATE_TICK_REASONS.
