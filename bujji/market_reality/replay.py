"""Deterministic replay — Phase 17E Layer 0, bitemporal bounds Phase 17F.1.2.

The guarantee this module exists to provide:

    same input events  ==  same ordered observation stream

Order is the exact append order recorded in the accepted EventStore --
the recorded order is itself part of the audit trail and is never
re-sorted, re-grouped, or "corrected" here. Two replays of the same
store return identical streams; a replay after a process restart returns
the same stream as one before it.

No wall-clock is read. No interpretation is performed. This module
returns raw observations and honest counts, nothing else.

BITEMPORAL BOUNDS (Phase 17F.1.2). Phase 17F.0.1 designed a TWO-BOUND
contract -- `as_of_event_time` and `as_of_knowledge_time`, independently
-- but the version that shipped with 17F.1 implemented only one bound,
compared against a timestamp already collapsed at write time
(`event_timestamp or capture_timestamp`, see `store.py`). That gap is
closed here.

`as_of` remains as convenience sugar: it sets BOTH bounds to the same
value, which is what every existing caller already means by it. Passing
`as_of` together with either explicit bound is rejected (`ValueError`)
as an ambiguous combination rather than silently picked between.

The bound check now runs AFTER the payload is parsed into a
`RawObservation`/`CaptureEvent`, not on the pre-parse `PersistedEvent.
timestamp` -- only the parsed object exposes `event_timestamp` and
`capture_timestamp` (or `event_time`/`knowledge_time`) as independent
fields.

`event_time` may legitimately be absent on an observation (a source that
publishes no exchange timestamp) -- in that case the event-time bound
cannot exclude it, because there is no basis to say it happened "after"
a bound it cannot be compared to. `knowledge_time` is ALWAYS present
(stamped by the collector at capture) and is what genuinely governs
whether a fact was knowable by a given moment -- it is the true
no-look-ahead safety net; the event-time bound is an additional,
stricter filter applied only when event_time is actually known.
"""
from __future__ import annotations

from typing import Iterator, List, Optional, Tuple, Union

from bujji.state_persistence.models import PersistedEvent
from bujji.state_persistence.store import EventStore

from . import taxonomy
from .capture_events import CaptureEvent
from .models import RawObservation, ReplayReport, StreamItem
from .store import ACCEPTED_FILENAME, RawObservationStore

REPLAY_COMPLETE = "REPLAY_COMPLETE"
REPLAY_PARTIAL = "REPLAY_PARTIAL"
REPLAY_FAILED = "REPLAY_FAILED"
ALL_REPLAY_STATUSES = (REPLAY_COMPLETE, REPLAY_PARTIAL, REPLAY_FAILED)


def _resolve_store(source: Union[str, "RawObservationStore"]) -> EventStore:
    if isinstance(source, RawObservationStore):
        return EventStore(source.accepted_path)
    path = str(source)
    if path.endswith(".jsonl"):
        return EventStore(path)
    return EventStore(str(path.rstrip("/") + "/" + ACCEPTED_FILENAME))


def _resolve_bounds(
    as_of: Optional[str],
    as_of_event_time: Optional[str],
    as_of_knowledge_time: Optional[str],
) -> Tuple[Optional[str], Optional[str]]:
    """Reconciles the convenience `as_of` sugar with the explicit dual
    bounds. Raises rather than guessing when both forms are supplied --
    an ambiguous call is a caller bug, not a value to silently resolve."""
    if as_of is not None and (as_of_event_time is not None or as_of_knowledge_time is not None):
        raise ValueError(
            "pass either `as_of` (both bounds set equal) or the explicit "
            "`as_of_event_time`/`as_of_knowledge_time` pair -- not both; "
            "the combination is ambiguous."
        )
    if as_of is not None:
        return as_of, as_of
    return as_of_event_time, as_of_knowledge_time


def _within_bounds(
    event_time: Optional[str],
    knowledge_time: Optional[str],
    event_bound: Optional[str],
    knowledge_bound: Optional[str],
) -> bool:
    """The bitemporal no-look-ahead check, applied identically to
    observations and capture events. See the module docstring for why
    `knowledge_time` is the real safety net and `event_time` is an
    additional filter only when known."""
    if event_bound is not None and event_time is not None and event_time > event_bound:
        return False
    if knowledge_bound is not None and knowledge_time is not None and knowledge_time > knowledge_bound:
        return False
    return True


def replay_stream(
    source: Union[str, "RawObservationStore"],
    as_of: Optional[str] = None,
    *,
    as_of_event_time: Optional[str] = None,
    as_of_knowledge_time: Optional[str] = None,
) -> Iterator[StreamItem]:
    """Yield the ordered UNION of market observations and capture events.

    This is the canonical view of Layer 0. A stream that omitted capture
    events would let a consumer reconstruct a market that never went
    quiet -- so blind spots arrive in sequence with the data, and any
    consumer wanting observations alone must filter deliberately.

    Order is exact append order; the recorded order is itself part of
    the audit trail and is never re-sorted here.

    Bitemporal bounds: see the module docstring. `as_of` sets both bounds
    equal; `as_of_event_time`/`as_of_knowledge_time` set them
    independently. Supplying both forms raises `ValueError`.
    """
    event_bound, knowledge_bound = _resolve_bounds(as_of, as_of_event_time, as_of_knowledge_time)
    store = _resolve_store(source)
    seen = set()
    for event in store.read_events():
        if event.event_id in seen:
            continue
        seen.add(event.event_id)
        if event.schema_version not in taxonomy.RECOGNIZED_LAYER0_SCHEMA_VERSIONS:
            continue
        try:
            if event.event_type == taxonomy.KIND_CAPTURE_EVENT:
                capture_event = CaptureEvent.from_dict(event.payload)
                if not _within_bounds(
                    capture_event.event_time, capture_event.knowledge_time,
                    event_bound, knowledge_bound,
                ):
                    continue
                yield StreamItem(
                    kind=taxonomy.STREAM_CAPTURE_EVENT,
                    timestamp=event.timestamp,
                    capture_event=capture_event,
                )
            else:
                observation = RawObservation.from_dict(event.payload)
                if not _within_bounds(
                    observation.lineage.event_timestamp, observation.lineage.capture_timestamp,
                    event_bound, knowledge_bound,
                ):
                    continue
                yield StreamItem(
                    kind=taxonomy.STREAM_OBSERVATION,
                    timestamp=event.timestamp,
                    observation=observation,
                )
        except (KeyError, TypeError, ValueError):
            continue


def replay_capture_events(
    source: Union[str, "RawObservationStore"],
    as_of: Optional[str] = None,
    *,
    as_of_event_time: Optional[str] = None,
    as_of_knowledge_time: Optional[str] = None,
) -> List[CaptureEvent]:
    """Only the capture events, in order -- for reconciling a
    completeness report's missing intervals against recorded blind
    spots (an unexplained gap is a finding; an explained one is not)."""
    return [
        item.capture_event
        for item in replay_stream(
            source, as_of=as_of,
            as_of_event_time=as_of_event_time, as_of_knowledge_time=as_of_knowledge_time,
        )
        if item.is_capture_event
    ]


def replay(
    source: Union[str, "RawObservationStore"],
    as_of: Optional[str] = None,
    *,
    as_of_event_time: Optional[str] = None,
    as_of_knowledge_time: Optional[str] = None,
) -> Iterator[RawObservation]:
    """Yield stored MARKET OBSERVATIONS in exact original append order.

    Capture events are excluded -- use `replay_stream()` for the
    canonical union. This function is what a materializer consumes, and
    a materializer must never fold a capture event into a candle.

    Bitemporal bounds: see the module docstring. Records outside either
    bound are withheld -- so a replay pinned to a past instant sees
    exactly what existed AND was knowable then, never something written
    or learned of later. This is the no-look-ahead guarantee applied at
    the source.

    A malformed/truncated line is skipped silently here; callers needing
    the counts should use `replay_with_report`.
    """
    for item in replay_stream(
        source, as_of=as_of,
        as_of_event_time=as_of_event_time, as_of_knowledge_time=as_of_knowledge_time,
    ):
        if item.is_observation:
            yield item.observation


def replay_with_report(
    source: Union[str, "RawObservationStore"],
    as_of: Optional[str] = None,
    *,
    as_of_event_time: Optional[str] = None,
    as_of_knowledge_time: Optional[str] = None,
):
    """Replay plus honest diagnostics.

    Returns (observations, ReplayReport). Duplicates, malformed lines and
    unrecognized schema versions are COUNTED, never silently absorbed --
    an operator can always tell the difference between "the feed was
    quiet" and "records were unreadable".
    """
    event_bound, knowledge_bound = _resolve_bounds(as_of, as_of_event_time, as_of_knowledge_time)
    store = _resolve_store(source)
    observations: List[RawObservation] = []
    errors: List[str] = []
    seen = set()
    discovered = 0
    duplicates = 0
    capture_events = 0
    malformed = 0
    schema_mismatch = 0

    for event, malformed_line in store.read_events_with_diagnostics():
        if malformed_line is not None:
            malformed += 1
            continue

        discovered += 1

        if event.event_id in seen:
            duplicates += 1
            continue
        seen.add(event.event_id)

        if event.schema_version not in taxonomy.RECOGNIZED_LAYER0_SCHEMA_VERSIONS:
            schema_mismatch += 1
            continue

        if event.event_type == taxonomy.KIND_CAPTURE_EVENT:
            # Counted as its own category -- neither an observation nor a
            # defect. Silently lumping capture events into `malformed`
            # would make recorded blind spots look like corruption.
            try:
                capture_event = CaptureEvent.from_dict(event.payload)
            except (KeyError, TypeError, ValueError) as exc:
                malformed += 1
                errors.append(f"unreadable capture event payload for {event.event_id}: {exc}")
                continue
            if _within_bounds(
                capture_event.event_time, capture_event.knowledge_time,
                event_bound, knowledge_bound,
            ):
                capture_events += 1
            continue

        try:
            observation = RawObservation.from_dict(event.payload)
        except (KeyError, TypeError, ValueError) as exc:
            malformed += 1
            errors.append(f"unreadable payload for {event.event_id}: {exc}")
            continue

        if not _within_bounds(
            observation.lineage.event_timestamp, observation.lineage.capture_timestamp,
            event_bound, knowledge_bound,
        ):
            continue

        observations.append(observation)

    if malformed or schema_mismatch:
        status = REPLAY_PARTIAL
    else:
        status = REPLAY_COMPLETE

    report = ReplayReport(
        status=status,
        observations_discovered=discovered,
        observations_replayed=len(observations),
        observations_skipped_duplicate=duplicates,
        observations_skipped_malformed=malformed,
        observations_skipped_schema_mismatch=schema_mismatch,
        first_observation_id=observations[0].observation_id if observations else None,
        last_observation_id=observations[-1].observation_id if observations else None,
        capture_events_seen=capture_events,
        errors=tuple(errors),
    )
    return observations, report


def observation_id_stream(
    source: Union[str, "RawObservationStore"],
    as_of: Optional[str] = None,
    *,
    as_of_event_time: Optional[str] = None,
    as_of_knowledge_time: Optional[str] = None,
) -> List[str]:
    """The ordered id sequence alone -- the cheapest possible proof that
    two replays produced the same stream."""
    return [
        obs.observation_id
        for obs in replay(
            source, as_of=as_of,
            as_of_event_time=as_of_event_time, as_of_knowledge_time=as_of_knowledge_time,
        )
    ]
