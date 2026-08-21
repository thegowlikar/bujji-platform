"""Live Observation Producer Framework engine — pure functions, no state,
no IO, no broker connection.

Deliverable 4's translation layer: turns an already-received
`LiveObservationEvent` into a Series 73A `Observation`, by delegating to
the SAME builder functions Series 73B/73C use for historical replay --
`bujji.market_observation.engine.build_observation`,
`bujji.futures_observation.engine.build_futures_observation`,
`bujji.options_observation.engine.build_option_observation` -- never a
hand-rolled duplicate. This is the literal meaning of "Historical replay
and live production produce identical Observation objects."

Also implements Deliverable 5's aggregation-window framework (pure
window add/close functions) and Deliverable 7's lifecycle transition
validation.

--------------------------------------------------------------------------
Design note — OHLC-from-ticks is transport, not an indicator (disclosed
per the spec's explicit request to resolve this ambiguity):

MOF Deliverable 1 draws the Observation / Derived Evidence line at
whether a value is *computed/interpreted* (Derived Evidence, e.g. a
moving average, RSI, or any statistic that requires a *choice* of
formula/lookback/interpretation) versus *recorded* (Observation, a raw
fact about what happened). A "1-minute candle's OHLC" has exactly one
possible value given a set of ticks over a window -- open is definitionally
the first tick's price, close the last, high/low the max/min -- there is
no formula choice, no lookback parameter, no interpretive judgment
involved, unlike a moving average (which requires choosing a period) or
an RSI (which requires choosing a smoothing method). This is also the
established precedent in 73A itself: `ObservationValue.value_kind ==
VALUE_KIND_OHLC` is already a first-class raw Observation shape (see
`bujji/market_observation/taxonomy.py`), not something that requires the
Evidence Graph. Therefore: closing an AggregationWindow into a single
Observation (Deliverable 5/10) is in scope for this framework. Computing
a moving average, RSI, or any other statistic OVER a series of closed
windows would NOT be in scope -- that remains MSI/Derived-Evidence work,
untouched by this package.
--------------------------------------------------------------------------

Nothing here reads a socket, calls a broker SDK, or reads the wall
clock -- every timestamp is caller/event-supplied.
"""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Tuple

from bujji.futures_observation import engine as futures_engine
from bujji.market_observation import engine as moc_engine
from bujji.market_observation import taxonomy as moc_taxonomy
from bujji.market_observation.models import Observation
from bujji.options_observation import engine as options_engine

from . import taxonomy
from .config import SCHEMA_VERSION
from .models import AggregationWindow, LateTick, ProducerState, ProducerStateTransition, Tick


# ---------------------------------------------------------------------------
# Lifecycle transition validation (Deliverable 7)
# ---------------------------------------------------------------------------
def is_valid_transition(current_state: str, next_state: str) -> bool:
    """Pure predicate: is `current_state -> next_state` a legal move per
    `taxonomy.VALID_TRANSITIONS`? Unknown current_state is always
    invalid (never permissive by default)."""
    allowed = taxonomy.VALID_TRANSITIONS.get(current_state)
    if allowed is None:
        return False
    return next_state in allowed


def apply_transition(state: ProducerState, next_state: str, timestamp: str, reason: str) -> ProducerState:
    """Return a NEW ProducerState reflecting the transition, with the
    move appended to `history`. Raises ValueError on an invalid
    transition -- this function never silently coerces an illegal move
    into a no-op or into STOPPED/FAILED on the caller's behalf; the
    caller must request a valid next state explicitly."""
    if not is_valid_transition(state.current_state, next_state):
        raise ValueError(
            f"invalid producer state transition: {state.current_state!r} -> {next_state!r}"
        )
    transition = ProducerStateTransition(
        from_state=state.current_state,
        to_state=next_state,
        timestamp=timestamp,
        reason=reason,
    )
    return ProducerState(
        producer_id=state.producer_id,
        current_state=next_state,
        history=state.history + (transition,),
    )


def new_producer_state(producer_id: str) -> ProducerState:
    return ProducerState(producer_id=producer_id, current_state=taxonomy.STATE_CREATED, history=())


# ---------------------------------------------------------------------------
# Event -> Observation translation (Deliverable 4)
# ---------------------------------------------------------------------------
def translate_event(event, *, origin: str = moc_taxonomy.ORIGIN_LIVE) -> Optional[Observation]:
    """Translate a LiveObservationEvent into a Series 73A Observation by
    delegating to the appropriate domain builder. Returns None for
    event types with nothing to translate (connection lifecycle,
    heartbeat, producer error) -- per taxonomy.TRANSLATABLE_EVENT_TYPES.

    `event.payload` must already be a normalized mapping of plain
    values (this function never parses a raw broker frame -- that is
    the Producer implementation's job, upstream of this call).
    """
    if event.event_type not in taxonomy.TRANSLATABLE_EVENT_TYPES:
        return None

    payload: Mapping[str, Any] = event.payload

    if event.event_type == taxonomy.EVENT_FUTURES_UPDATED:
        return futures_engine.build_futures_observation(
            underlying=payload["underlying"],
            instrument_symbol=payload["instrument_symbol"],
            expiry=payload["expiry"],
            exchange=payload.get("exchange", "NSE"),
            segment=payload.get("segment", "FUTURES"),
            timestamp=event.timestamp,
            resolution=payload.get("resolution", moc_taxonomy.RESOLUTION_TICK),
            open_=payload.get("open"),
            high=payload.get("high"),
            low=payload.get("low"),
            close=payload.get("close"),
            volume=payload.get("volume"),
            open_interest=payload.get("open_interest"),
            change_in_open_interest=payload.get("change_in_open_interest"),
            settlement_price=payload.get("settlement_price"),
            underlying_price=payload.get("underlying_price"),
            origin=origin,
            acquisition_timestamp=event.timestamp,
            normalization_timestamp=event.timestamp,
        )

    if event.event_type == taxonomy.EVENT_OPTION_CHAIN_UPDATED:
        return options_engine.build_option_observation(
            # REQUIRED FROM THE PAYLOAD, with no default. This translator
            # never sees the raw broker frame -- the Producer normalises it
            # upstream -- so this function does not know where the symbol came
            # from and must not guess. A producer that cannot state it fails
            # here with a KeyError naming the field, which is the correct
            # outcome. (No production emitter of EVENT_OPTION_CHAIN_UPDATED
            # exists today; this branch is reachable only from tests.)
            symbol_provenance=payload["symbol_provenance"],
            underlying=payload["underlying"],
            instrument_symbol=payload["instrument_symbol"],
            strike=payload["strike"],
            expiry=payload["expiry"],
            option_type=payload["option_type"],
            exchange=payload.get("exchange", "NSE"),
            segment=payload.get("segment", "OPTIONS"),
            timestamp=event.timestamp,
            resolution=payload.get("resolution", moc_taxonomy.RESOLUTION_TICK),
            open_=payload.get("open"),
            high=payload.get("high"),
            low=payload.get("low"),
            close=payload.get("close"),
            settlement=payload.get("settlement"),
            volume=payload.get("volume"),
            open_interest=payload.get("open_interest"),
            change_in_open_interest=payload.get("change_in_open_interest"),
            underlying_price=payload.get("underlying_price"),
            origin=origin,
            acquisition_timestamp=event.timestamp,
            normalization_timestamp=event.timestamp,
            bid=payload.get("bid"),
            ask=payload.get("ask"),
            bid_quantity=payload.get("bid_quantity"),
            ask_quantity=payload.get("ask_quantity"),
        )

    # TICK_RECEIVED, CANDLE_CLOSED, VIX_UPDATED all ride the generic MOC
    # shape directly (PRICE / VOLATILITY_VIX observation types) -- there
    # is no dedicated 73B/73C-style domain package for these, so MOC's
    # own build_observation is the correct, non-duplicating call.
    observation_type = {
        taxonomy.EVENT_TICK_RECEIVED: moc_taxonomy.TYPE_PRICE,
        taxonomy.EVENT_CANDLE_CLOSED: moc_taxonomy.TYPE_PRICE,
        taxonomy.EVENT_VIX_UPDATED: moc_taxonomy.TYPE_VOLATILITY_VIX,
    }[event.event_type]

    resolution = payload.get(
        "resolution",
        moc_taxonomy.RESOLUTION_TICK if event.event_type == taxonomy.EVENT_TICK_RECEIVED
        else moc_taxonomy.RESOLUTION_ONE_MINUTE,
    )

    if event.event_type == taxonomy.EVENT_CANDLE_CLOSED:
        value_kind = moc_taxonomy.VALUE_KIND_OHLC
        candle_payload = {
            "open": payload["open"],
            "high": payload["high"],
            "low": payload["low"],
            "close": payload["close"],
        }
        if payload.get("volume") is not None:
            candle_payload["volume"] = payload["volume"]
        value_payload: Any = candle_payload
        missing_fields = tuple(k for k in ("open", "high", "low", "close") if payload.get(k) is None)
    else:
        value_kind = moc_taxonomy.VALUE_KIND_SCALAR
        value_payload = payload["price"] if "price" in payload else payload.get("value")
        missing_fields = () if value_payload is not None else ("price",)

    completeness = 1.0 if not missing_fields else 0.0
    validation_status = (
        moc_taxonomy.VALIDATION_VALID if not missing_fields else moc_taxonomy.VALIDATION_INCOMPLETE
    )

    return moc_engine.build_observation(
        observation_type=observation_type,
        instrument=payload["instrument"],
        exchange=payload.get("exchange", "NSE"),
        segment=payload.get("segment", "EQUITY"),
        timestamp=event.timestamp,
        resolution=resolution,
        source=event.source,
        schema_version=moc_taxonomy.MARKET_OBSERVATION_VERSION,
        value_kind=value_kind,
        payload=value_payload,
        completeness=completeness,
        freshness=0.0,
        confidence=None,
        missing_fields=missing_fields,
        validation_status=validation_status,
        source_quality=moc_taxonomy.SOURCE_QUALITY_HIGH,
        originating_source=event.source,
        acquisition_timestamp=event.timestamp,
        normalization_timestamp=event.timestamp,
        origin=origin,
        provenance_version=moc_taxonomy.MARKET_OBSERVATION_VERSION,
        transformation_history=(),
    )


# ---------------------------------------------------------------------------
# Aggregation window framework (Deliverable 5) -- thin, structural only.
# See module docstring above for the OHLC-is-transport reasoning.
# ---------------------------------------------------------------------------
def new_window(instrument: str, interval: str, window_start: str, window_end: str) -> AggregationWindow:
    return AggregationWindow(
        instrument=instrument,
        interval=interval,
        window_start=window_start,
        window_end=window_end,
        ticks=(),
        late_ticks=(),
        is_closed=False,
    )


def add_tick(window: AggregationWindow, tick: Tick) -> AggregationWindow:
    """Return a NEW AggregationWindow with `tick` accumulated. A tick
    timestamped before `window.window_start` is never dropped -- it is
    recorded in `late_ticks` instead (see taxonomy.py's disclosed
    out-of-window-order policy). Adding to an already-closed window
    raises -- a closed window is immutable evidence, never reopened."""
    if window.is_closed:
        raise ValueError("cannot add a tick to an already-closed AggregationWindow")

    if tick.timestamp < window.window_start:
        late = LateTick(tick=tick, reason=taxonomy.LATE_TICK_REASON_BEFORE_WINDOW_START)
        return AggregationWindow(
            instrument=window.instrument,
            interval=window.interval,
            window_start=window.window_start,
            window_end=window.window_end,
            ticks=window.ticks,
            late_ticks=window.late_ticks + (late,),
            is_closed=window.is_closed,
        )

    return AggregationWindow(
        instrument=window.instrument,
        interval=window.interval,
        window_start=window.window_start,
        window_end=window.window_end,
        ticks=window.ticks + (tick,),
        late_ticks=window.late_ticks,
        is_closed=window.is_closed,
    )


def should_close(window: AggregationWindow, current_timestamp: str) -> bool:
    """True once `current_timestamp` has reached or passed the window's
    own end boundary. Pure boundary comparison -- no wall clock read."""
    return current_timestamp >= window.window_end


def close_window(
    window: AggregationWindow,
    *,
    exchange: str = "NSE",
    segment: str = "EQUITY",
    source: str,
    origin: str = moc_taxonomy.ORIGIN_LIVE,
) -> Tuple[AggregationWindow, Optional[Observation]]:
    """Close `window`, returning (closed_window, Observation | None).
    The OHLC of the closed Observation is the raw transport reduction
    over the window's accumulated ticks (see module docstring) -- open
    is the first tick's price, close the last, high/low the max/min.
    Returns (closed_window, None) if the window received no ticks at
    all (nothing to observe -- never fabricates a candle from zero
    ticks)."""
    closed = AggregationWindow(
        instrument=window.instrument,
        interval=window.interval,
        window_start=window.window_start,
        window_end=window.window_end,
        ticks=window.ticks,
        late_ticks=window.late_ticks,
        is_closed=True,
    )

    if not window.ticks:
        return closed, None

    prices = [t.price for t in window.ticks]
    volumes = [t.volume for t in window.ticks if t.volume is not None]
    candle_payload: Dict[str, Any] = {
        "open": prices[0],
        "high": max(prices),
        "low": min(prices),
        "close": prices[-1],
    }
    if volumes:
        candle_payload["volume"] = sum(volumes)

    observation = moc_engine.build_observation(
        observation_type=moc_taxonomy.TYPE_PRICE,
        instrument=window.instrument,
        exchange=exchange,
        segment=segment,
        timestamp=window.window_end,
        resolution=_interval_to_resolution(window.interval),
        source=source,
        schema_version=moc_taxonomy.MARKET_OBSERVATION_VERSION,
        value_kind=moc_taxonomy.VALUE_KIND_OHLC,
        payload=candle_payload,
        completeness=1.0,
        freshness=0.0,
        confidence=None,
        missing_fields=(),
        validation_status=moc_taxonomy.VALIDATION_VALID,
        source_quality=moc_taxonomy.SOURCE_QUALITY_HIGH,
        originating_source=source,
        acquisition_timestamp=window.window_end,
        normalization_timestamp=window.window_end,
        origin=origin,
        provenance_version=moc_taxonomy.MARKET_OBSERVATION_VERSION,
        transformation_history=("AGGREGATED_FROM_TICKS",),
    )
    return closed, observation


_INTERVAL_TO_RESOLUTION = {
    taxonomy.INTERVAL_ONE_MINUTE: moc_taxonomy.RESOLUTION_ONE_MINUTE,
    taxonomy.INTERVAL_FIVE_MINUTE: moc_taxonomy.RESOLUTION_FIVE_MINUTE,
}


def _interval_to_resolution(interval: str) -> str:
    """Sub-minute synthetic intervals (ONE_SECOND..THIRTY_SECOND) have no
    corresponding MOC resolution constant -- MOC's coarsest sub-minute
    resolution is RESOLUTION_TICK, so they map to TICK, disclosed
    explicitly here rather than silently guessed."""
    return _INTERVAL_TO_RESOLUTION.get(interval, moc_taxonomy.RESOLUTION_TICK)
