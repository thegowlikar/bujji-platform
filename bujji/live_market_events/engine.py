"""Live Market Event Engine — pure functions, no state, no IO.

Implements Deliverable 3: given a *current* Observation and, at most,
the *immediately previous* Observation in the same series (plus, for
the two event families that need it, minimal explicitly-carried
`RunningState`), determine which `MarketEvent`s should be generated.

This is the "reasoning across TIME, never across MEANING" core: every
function here answers "what changed" — never "what it means", "is it
tradeable", or "is it reasonable." A price moving 40% in one tick is
just as valid a `PriceChanged`/`PriceGapDetected` pair as a 0.01% move
— no judgment about magnitude beyond the purely structural gap
threshold (itself a factual classification, not a market judgment).

---------------------------------------------------------------------
Design tension resolved — NEW_SESSION_HIGH / NEW_SESSION_LOW vs.
Deliverable 3's "no history beyond what is required to establish
factual change":
---------------------------------------------------------------------
A running high/low inherently requires knowing something about the
WHOLE session so far, not just the immediately previous observation.
Read narrowly, that looks like it violates the "no history" rule. It
does not, for the same reason Series 74's `AggregationWindow` is a raw
Observation shape rather than a derived indicator: an OHLC value over
a fixed window has exactly one possible value given its inputs, so
it's a fact, not an inference. A running high/low is the same kind of
object — a single carried-forward FACT (one float, one id, per
observation_type+instrument), updated by a pure `max`/`min` on every
new observation, never re-derived by replaying the whole series. The
caller carries `RunningState` forward exactly the way Series 74's
pipeline carries an `AggregationWindow` forward between ticks; this
engine never reads a series' full history to compute it. That is the
"minimal accompanying state, not full history replay" resolution.

---------------------------------------------------------------------
Design decision — event_id content hash:
---------------------------------------------------------------------
`event_id` is a deterministic hashlib.md5 hash over
(event_type + originating_observation_ids + a canonical repr of
`detail` + timestamp) — content only, never wall-clock-of-detection.
Re-deriving the SAME change from the SAME two observations (e.g. a
deterministic replay rerun) always produces the SAME event_id.
Two structurally-identical changes at different real times get
different ids because `timestamp` here is the observation's own
timestamp (per MOF's timestamp-ownership discipline), which differs
between two distinct real occurrences of "the same kind of change" —
so identity is content-based, but content includes the observation's
own recorded time, not the wall clock the detector happened to run at.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, List, Mapping, Optional, Tuple

from bujji.market_observation import taxonomy as moc_taxonomy
from bujji.market_observation.models import Observation

from . import config as _config
from . import taxonomy
from .models import MarketEvent, MarketEventProvenance, RunningState, SessionExtremesState, initial_session_extremes


# ---------------------------------------------------------------------------
# event_id / MarketEvent construction
# ---------------------------------------------------------------------------
def _event_id(event_type: str, originating_observation_ids: Tuple[str, ...], detail: Mapping[str, Any], timestamp: str) -> str:
    canonical_detail = json.dumps(detail, sort_keys=True, default=repr)
    seed = "|".join([event_type, *originating_observation_ids, canonical_detail, timestamp])
    return "MEVT-" + hashlib.md5(seed.encode()).hexdigest()[:24]


def _make_event(
    event_type: str,
    timestamp: str,
    originating_observation_ids: Tuple[str, ...],
    detail: Mapping[str, Any],
    *,
    originating_source: str = _config.DEFAULT_ORIGINATING_SOURCE,
    detection_context: str = _config.DEFAULT_DETECTION_CONTEXT_LIVE,
    schema_version: str = _config.SCHEMA_VERSION,
) -> MarketEvent:
    event_id = _event_id(event_type, originating_observation_ids, detail, timestamp)
    provenance = MarketEventProvenance(
        originating_source=originating_source,
        detection_context=detection_context,
        schema_version=schema_version,
    )
    return MarketEvent(
        event_id=event_id,
        event_type=event_type,
        timestamp=timestamp,
        originating_observation_ids=tuple(originating_observation_ids),
        detail=dict(detail),
        provenance=provenance,
        schema_version=schema_version,
    )


# ---------------------------------------------------------------------------
# Payload field extraction — generic across value_kind, per MOC v1's
# domain-neutral ObservationValue shape (see market_observation.models
# module docstring). Never fabricates a field: returns None when the
# payload does not carry it.
# ---------------------------------------------------------------------------
# A SCALAR payload is ONE bare number with no key attached, so the only
# thing that says WHAT it measures is the observation's own type. Without
# this map the scalar branch answered every field_name with the same
# number: a spot PRICE observation returned its price when asked for
# "open_interest" and again when asked for "volume", so two real spot
# ticks emitted OI_CHANGED and VOLUME_CHANGED carrying the NIFTY level as
# though it were open interest and traded volume. Verified on the
# production bridge 2026-08-18 (24601.05 -> 24608.30 emitted both).
#
# That is fabrication, and it contradicted this very function's docstring
# promise never to invent a field. It also mattered beyond tidiness: those
# phantom events inflate the episode/event counts that PSI confidence and
# the thesis evidence gates are computed from.
#
# An observation_type absent from this map has no known scalar meaning, so
# it answers nothing -- unknown is not a licence to guess.
_SCALAR_FIELD_ALIASES = {
    moc_taxonomy.TYPE_PRICE: ("price", "close", "last"),
    moc_taxonomy.TYPE_FUTURES: ("price", "close", "last"),
    moc_taxonomy.TYPE_VOLATILITY_VIX: ("vix",),
    moc_taxonomy.TYPE_OPTION_OPEN_INTEREST: ("open_interest",),
    moc_taxonomy.TYPE_FUTURES_OPEN_INTEREST: ("open_interest",),
    moc_taxonomy.TYPE_OPTION_VOLUME: ("volume",),
}


def _numeric_field(observation: Observation, field_name: str) -> Optional[float]:
    value = observation.value
    payload = value.payload
    if value.value_kind == moc_taxonomy.VALUE_KIND_SCALAR:
        aliases = _SCALAR_FIELD_ALIASES.get(observation.identity.observation_type)
        if aliases is None or field_name not in aliases:
            return None
        return float(payload) if isinstance(payload, (int, float)) else None
    if value.value_kind == moc_taxonomy.VALUE_KIND_OHLC:
        if isinstance(payload, Mapping):
            candidate = payload.get(field_name, payload.get("close"))
            return float(candidate) if isinstance(candidate, (int, float)) else None
        return None
    if value.value_kind == moc_taxonomy.VALUE_KIND_MAPPING:
        if isinstance(payload, Mapping):
            candidate = payload.get(field_name)
            return float(candidate) if isinstance(candidate, (int, float)) else None
        return None
    return None


def _representative_price(observation: Observation) -> Optional[float]:
    """The single number PRICE_CHANGED/gap/session-high-low compare on:
    OHLC close, MAPPING "price"/"close"/"last", or a bare SCALAR."""
    for field_name in ("price", "close", "last"):
        candidate = _numeric_field(observation, field_name)
        if candidate is not None:
            return candidate
    return None


# ---------------------------------------------------------------------------
# Duplicate / late / gap detection — need only the previous observation.
# ---------------------------------------------------------------------------
def detect_duplicate(current: Observation, previous: Optional[Observation]) -> Optional[MarketEvent]:
    if previous is None:
        return None
    if current.value.value_kind != previous.value.value_kind:
        return None
    if current.value.payload != previous.value.payload:
        return None
    if current.identity.observation_id == previous.identity.observation_id:
        return None  # identical object, not a "new" duplicate observation event
    return _make_event(
        taxonomy.DUPLICATE_OBSERVATION_DETECTED,
        current.identity.timestamp,
        (current.identity.observation_id, previous.identity.observation_id),
        {"reason": taxonomy.DUPLICATE_REASON_IDENTICAL_VALUE_AND_ID},
    )


def detect_late_observation(current: Observation, previous: Optional[Observation]) -> Optional[MarketEvent]:
    if previous is None:
        return None
    if current.identity.timestamp < previous.identity.timestamp:
        return _make_event(
            taxonomy.LATE_OBSERVATION_RECEIVED,
            current.identity.timestamp,
            (current.identity.observation_id, previous.identity.observation_id),
            {
                "reason": taxonomy.LATE_REASON_TIMESTAMP_BEFORE_LAST_SEEN,
                "last_seen_timestamp": previous.identity.timestamp,
            },
        )
    return None


def detect_series_gap(current: Observation, previous: Optional[Observation]) -> Optional[MarketEvent]:
    """Structural gap signal at the event layer: any pair of consecutive
    observations for the same (observation_type, instrument) whose
    resolution implies a nominal interval, and whose actual gap exceeds
    it, is flagged. Mirrors market_observation.engine.detect_gaps'
    tolerance discipline but reports it as an Event rather than a
    SeriesGap marker (this layer produces events, never mutates a
    Series in place)."""
    if previous is None:
        return None
    if current.identity.timestamp < previous.identity.timestamp:
        return None  # out-of-order is LATE_OBSERVATION_RECEIVED's job, not a gap
    nominal = _RESOLUTION_SECONDS.get(current.identity.resolution)
    if nominal is None:
        return None
    from datetime import datetime

    try:
        t1 = datetime.fromisoformat(previous.identity.timestamp)
        t2 = datetime.fromisoformat(current.identity.timestamp)
    except (ValueError, TypeError):
        return None
    actual = (t2 - t1).total_seconds()
    if actual > nominal * _GAP_TOLERANCE_MULTIPLIER:
        return _make_event(
            taxonomy.OBSERVATION_SERIES_GAP_DETECTED,
            current.identity.timestamp,
            (current.identity.observation_id, previous.identity.observation_id),
            {
                "reason": taxonomy.GAP_REASON_MISSING_OBSERVATION,
                "gap_seconds": actual,
                "nominal_seconds": nominal,
            },
        )
    return None


_RESOLUTION_SECONDS = {
    moc_taxonomy.RESOLUTION_ONE_MINUTE: 60,
    moc_taxonomy.RESOLUTION_FIVE_MINUTE: 300,
    moc_taxonomy.RESOLUTION_FIFTEEN_MINUTE: 900,
    moc_taxonomy.RESOLUTION_HOURLY: 3600,
    moc_taxonomy.RESOLUTION_DAILY: 86400,
    moc_taxonomy.RESOLUTION_WEEKLY: 604800,
}
_GAP_TOLERANCE_MULTIPLIER = 1.5


# ---------------------------------------------------------------------------
# Observation lifecycle events — created / updated / corrected.
# ---------------------------------------------------------------------------
def detect_observation_lifecycle(current: Observation, previous: Optional[Observation]) -> Optional[MarketEvent]:
    """OBSERVATION_CREATED when there is no previous observation for
    this (observation_type, instrument) yet; OBSERVATION_UPDATED when
    the value changed; OBSERVATION_CORRECTED when the same timestamp
    is re-observed with a different value (a correction of an already
    -recorded instant, never silently treated as a new tick)."""
    if previous is None:
        return _make_event(
            taxonomy.OBSERVATION_CREATED,
            current.identity.timestamp,
            (current.identity.observation_id,),
            {"observation_type": current.identity.observation_type, "instrument": current.identity.instrument},
        )
    if current.value.payload == previous.value.payload and current.value.value_kind == previous.value.value_kind:
        return None
    if current.identity.timestamp == previous.identity.timestamp:
        return _make_event(
            taxonomy.OBSERVATION_CORRECTED,
            current.identity.timestamp,
            (current.identity.observation_id, previous.identity.observation_id),
            {"old_payload": previous.value.payload, "new_payload": current.value.payload},
        )
    return _make_event(
        taxonomy.OBSERVATION_UPDATED,
        current.identity.timestamp,
        (current.identity.observation_id, previous.identity.observation_id),
        {"old_payload": previous.value.payload, "new_payload": current.value.payload},
    )


# ---------------------------------------------------------------------------
# Price family — PRICE_CHANGED / PRICE_GAP_DETECTED.
# ---------------------------------------------------------------------------
def detect_price_change(current: Observation, previous: Optional[Observation]) -> Tuple[MarketEvent, ...]:
    if previous is None:
        return ()
    if current.identity.observation_type not in taxonomy._PRICE_OBSERVATION_TYPES:
        return ()
    current_price = _representative_price(current)
    previous_price = _representative_price(previous)
    if current_price is None or previous_price is None:
        return ()
    if current_price == previous_price:
        return ()

    delta = current_price - previous_price
    events: List[MarketEvent] = [
        _make_event(
            taxonomy.PRICE_CHANGED,
            current.identity.timestamp,
            (current.identity.observation_id, previous.identity.observation_id),
            {"old_price": previous_price, "new_price": current_price, "delta": delta},
        )
    ]

    if previous_price != 0 and abs(delta) / abs(previous_price) >= taxonomy.PRICE_GAP_FRACTION_THRESHOLD:
        events.append(
            _make_event(
                taxonomy.PRICE_GAP_DETECTED,
                current.identity.timestamp,
                (current.identity.observation_id, previous.identity.observation_id),
                {
                    "old_price": previous_price,
                    "new_price": current_price,
                    "delta": delta,
                    "fraction": abs(delta) / abs(previous_price),
                    "threshold": taxonomy.PRICE_GAP_FRACTION_THRESHOLD,
                },
            )
        )
    return tuple(events)


# ---------------------------------------------------------------------------
# OI / Volume / VIX field-changed family.
# ---------------------------------------------------------------------------
def _detect_field_changed(
    current: Observation, previous: Optional[Observation], field_name: str, event_type: str
) -> Optional[MarketEvent]:
    if previous is None:
        return None
    current_value = _numeric_field(current, field_name)
    previous_value = _numeric_field(previous, field_name)
    if current_value is None or previous_value is None:
        return None
    if current_value == previous_value:
        return None
    return _make_event(
        event_type,
        current.identity.timestamp,
        (current.identity.observation_id, previous.identity.observation_id),
        {"old_value": previous_value, "new_value": current_value, "delta": current_value - previous_value},
    )


def detect_oi_change(current: Observation, previous: Optional[Observation]) -> Optional[MarketEvent]:
    return _detect_field_changed(current, previous, "open_interest", taxonomy.OI_CHANGED)


def detect_volume_change(current: Observation, previous: Optional[Observation]) -> Optional[MarketEvent]:
    return _detect_field_changed(current, previous, "volume", taxonomy.VOLUME_CHANGED)


def detect_vix_change(current: Observation, previous: Optional[Observation]) -> Optional[MarketEvent]:
    if current.identity.observation_type != moc_taxonomy.TYPE_VOLATILITY_VIX:
        return None
    current_value = _representative_price(current)
    if current_value is None:
        current_value = _numeric_field(current, "vix")
    previous_value = None
    if previous is not None:
        previous_value = _representative_price(previous)
        if previous_value is None:
            previous_value = _numeric_field(previous, "vix")
    if previous is None or current_value is None or previous_value is None or current_value == previous_value:
        return None
    return _make_event(
        taxonomy.VIX_CHANGED,
        current.identity.timestamp,
        (current.identity.observation_id, previous.identity.observation_id),
        {"old_value": previous_value, "new_value": current_value, "delta": current_value - previous_value},
    )


def detect_futures_updated(current: Observation, previous: Optional[Observation]) -> Optional[MarketEvent]:
    if current.identity.observation_type != moc_taxonomy.TYPE_FUTURES:
        return None
    if previous is None or current.value.payload == previous.value.payload:
        return None
    return _make_event(
        taxonomy.FUTURES_UPDATED,
        current.identity.timestamp,
        (current.identity.observation_id, previous.identity.observation_id),
        {"old_payload": previous.value.payload, "new_payload": current.value.payload},
    )


def detect_option_chain_updated(current: Observation, previous: Optional[Observation]) -> Optional[MarketEvent]:
    if current.identity.observation_type != moc_taxonomy.TYPE_OPTION_CHAIN:
        return None
    if previous is None or current.value.payload == previous.value.payload:
        return None
    return _make_event(
        taxonomy.OPTION_CHAIN_UPDATED,
        current.identity.timestamp,
        (current.identity.observation_id, previous.identity.observation_id),
        {"old_payload": previous.value.payload, "new_payload": current.value.payload},
    )


# ---------------------------------------------------------------------------
# Session high/low family — the one family needing RunningState.
# ---------------------------------------------------------------------------
def detect_session_extremes(
    current: Observation, running_state: Optional[RunningState]
) -> Tuple[Tuple[MarketEvent, ...], RunningState]:
    """Update the running high/low fact and emit NEW_SESSION_HIGH /
    NEW_SESSION_LOW when the current observation's representative
    price sets a new extreme. Returns (events, updated_running_state)
    — the caller carries `updated_running_state` forward, exactly like
    Series 74's AggregationWindow, never re-deriving it from full
    series history."""
    if running_state is None:
        extremes = initial_session_extremes(current.identity.observation_type, current.identity.instrument)
    else:
        extremes = running_state.session_extremes

    price = _representative_price(current)
    if price is None or current.identity.observation_type not in taxonomy._PRICE_OBSERVATION_TYPES:
        return (), RunningState(session_extremes=extremes)

    events: List[MarketEvent] = []
    new_high = extremes.session_high
    new_high_id = extremes.session_high_observation_id
    new_low = extremes.session_low
    new_low_id = extremes.session_low_observation_id

    if extremes.session_high is None or price > extremes.session_high:
        new_high = price
        new_high_id = current.identity.observation_id
        events.append(
            _make_event(
                taxonomy.NEW_SESSION_HIGH,
                current.identity.timestamp,
                (current.identity.observation_id,),
                {"new_high": price, "previous_high": extremes.session_high},
            )
        )
    if extremes.session_low is None or price < extremes.session_low:
        new_low = price
        new_low_id = current.identity.observation_id
        events.append(
            _make_event(
                taxonomy.NEW_SESSION_LOW,
                current.identity.timestamp,
                (current.identity.observation_id,),
                {"new_low": price, "previous_low": extremes.session_low},
            )
        )

    updated = RunningState(
        session_extremes=SessionExtremesState(
            observation_type=extremes.observation_type,
            instrument=extremes.instrument,
            session_high=new_high,
            session_high_observation_id=new_high_id,
            session_low=new_low,
            session_low_observation_id=new_low_id,
        )
    )
    return tuple(events), updated


# ---------------------------------------------------------------------------
# Top-level comparison — composes every family above, current vs.
# previous observation plus running state. This is the single function
# runner.py's two entrypoints both delegate to, guaranteeing
# replay/live parity by construction (same function, same inputs, same
# outputs — never two independently-maintained code paths).
# ---------------------------------------------------------------------------
def compare_observations(
    current: Observation,
    previous: Optional[Observation],
    running_state: Optional[RunningState] = None,
    *,
    detection_context: str = _config.DEFAULT_DETECTION_CONTEXT_LIVE,
) -> Tuple[Tuple[MarketEvent, ...], RunningState]:
    events: List[MarketEvent] = []

    duplicate = detect_duplicate(current, previous)
    if duplicate is not None:
        events.append(duplicate)
        session_events, updated_state = detect_session_extremes(current, running_state)
        # Duplicates never move the session extremes (no new fact), but
        # we still thread state forward untouched for a stable caller
        # contract.
        return tuple(events), (running_state if running_state is not None else updated_state)

    late = detect_late_observation(current, previous)
    if late is not None:
        events.append(late)

    gap = detect_series_gap(current, previous)
    if gap is not None:
        events.append(gap)

    lifecycle = detect_observation_lifecycle(current, previous)
    if lifecycle is not None:
        events.append(lifecycle)

    events.extend(detect_price_change(current, previous))

    oi = detect_oi_change(current, previous)
    if oi is not None:
        events.append(oi)

    volume = detect_volume_change(current, previous)
    if volume is not None:
        events.append(volume)

    vix = detect_vix_change(current, previous)
    if vix is not None:
        events.append(vix)

    futures = detect_futures_updated(current, previous)
    if futures is not None:
        events.append(futures)

    option_chain = detect_option_chain_updated(current, previous)
    if option_chain is not None:
        events.append(option_chain)

    session_events, updated_state = detect_session_extremes(current, running_state)
    events.extend(session_events)

    # Re-stamp every event's detection_context if the caller specified
    # a non-default one (e.g. REPLAY/BATCH) -- content-hash-relevant
    # fields (event_type/originating_observation_ids/detail/timestamp)
    # are untouched, so event_id parity across LIVE/REPLAY/BATCH calls
    # for identical underlying data is preserved.
    if detection_context != _config.DEFAULT_DETECTION_CONTEXT_LIVE:
        events = [
            MarketEvent(
                event_id=e.event_id,
                event_type=e.event_type,
                timestamp=e.timestamp,
                originating_observation_ids=e.originating_observation_ids,
                detail=e.detail,
                provenance=MarketEventProvenance(
                    originating_source=e.provenance.originating_source,
                    detection_context=detection_context,
                    schema_version=e.provenance.schema_version,
                ),
                schema_version=e.schema_version,
            )
            for e in events
        ]

    return tuple(events), updated_state
