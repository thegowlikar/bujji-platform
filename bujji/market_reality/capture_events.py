"""Capture events — Phase 17F.0.1 step 3.

A CaptureEvent records a fact about THE OBSERVER, never about the market.
"The websocket disconnected at 11:42" is not something NIFTY did.

WHY THIS IS NOT A MARKET OBSERVATION, AND NOT IN MOC:
`market_observation.ALL_OBSERVATION_TYPES` enumerates things the MARKET
does — price, futures, option chain, volatility, depth. A capture gap is
not one of them, so `MARKET_OBSERVATION_VERSION` stays at 1.1.0 and no
`TYPE_CAPTURE_GAP` is added. Wrapping a gap in a MOC `Observation` would
have required lying with `TYPE_UNKNOWN` — asserting it is a market
observation of unknown kind, rather than what it is: a fact about
capture.

So a CaptureEvent is a SIBLING record: it shares Layer 0's append-only
log and its ordering (a replay must encounter blind spots in sequence
with data, or it reconstructs a market that never went quiet), but it
does not share the observation record shape.

POINT EVENTS, NOT INTERVALS — a consequence of immutability.
Layer 0 records are immutable, so a "gap record" cannot be opened when a
disconnect starts and closed when it ends: closing it would be a
mutation, which the store forbids outright. Instead each condition is a
point event, and a recovery event links back to the event it closes via
`related_event_id`. An interval is therefore reconstructed by reading a
pair, and an UNCLOSED interval (process killed before recovery) is the
honest representation of a gap whose end we genuinely never observed.

NOT CERTIFICATION-GATED. Capture events describe our own process, not
data received from a broker, so they do not pass the certification gate
— recording "we were disconnected" cannot require FYERS to certify it.
The event still CARRIES the certification status in force at the time,
as context for anyone reading the stream later.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Tuple

from . import taxonomy


def _mint_event_id(payload: Mapping[str, Any]) -> str:
    """Deterministic content hash over the event's identifying fields.
    Two identical capture events are the same event, so a re-emission is
    idempotent — the same discipline `observation_id` provides for market
    observations, reached the same way."""
    seed = json.dumps(payload, sort_keys=True, default=str)
    return "CAP-" + hashlib.sha256(seed.encode()).hexdigest()[:24]


@dataclass(frozen=True)
class CaptureEvent:
    """One immutable fact about the capture process.

    `event_time` is when the CAPTURE CONDITION occurred — the disconnect
    genuinely happened at a moment. It is deliberately NOT a market event
    time; nothing here asserts anything about prices.

    `knowledge_time` is when the collector recorded it. The two are kept
    distinct for the same reason market observations keep them distinct:
    a record that collapses them cannot support bitemporal queries.
    """

    event_id: str
    reason: str
    event_time: str
    knowledge_time: str
    source: str
    access_method: str
    affected_instruments: Tuple[str, ...] = ()
    certification_status: Optional[str] = None
    count: Optional[int] = None
    related_event_id: Optional[str] = None
    detail: Optional[str] = None
    schema_version: str = taxonomy.LAYER0_SCHEMA_VERSION

    def to_dict(self) -> dict:
        return {
            "event_id": self.event_id,
            "reason": self.reason,
            "event_time": self.event_time,
            "knowledge_time": self.knowledge_time,
            "source": self.source,
            "access_method": self.access_method,
            "affected_instruments": list(self.affected_instruments),
            "certification_status": self.certification_status,
            "count": self.count,
            "related_event_id": self.related_event_id,
            "detail": self.detail,
            "schema_version": self.schema_version,
        }

    @staticmethod
    def from_dict(d: Mapping) -> "CaptureEvent":
        return CaptureEvent(
            event_id=d["event_id"],
            reason=d["reason"],
            event_time=d["event_time"],
            knowledge_time=d["knowledge_time"],
            source=d.get("source", ""),
            access_method=d.get("access_method", ""),
            affected_instruments=tuple(d.get("affected_instruments") or ()),
            certification_status=d.get("certification_status"),
            count=d.get("count"),
            related_event_id=d.get("related_event_id"),
            detail=d.get("detail"),
            schema_version=d.get("schema_version", taxonomy.LAYER0_SCHEMA_VERSION),
        )


def build_capture_event(
    *,
    reason: str,
    event_time: str,
    knowledge_time: str,
    source: str,
    access_method: str,
    affected_instruments: Tuple[str, ...] = (),
    certification_status: Optional[str] = None,
    count: Optional[int] = None,
    related_event_id: Optional[str] = None,
    detail: Optional[str] = None,
    schema_version: str = taxonomy.LAYER0_SCHEMA_VERSION,
) -> CaptureEvent:
    """Construct a CaptureEvent, minting its deterministic id.

    No wall clock is read: both timestamps are supplied by the caller,
    so a replayed capture sequence reproduces byte-identical events.
    """
    instruments = tuple(affected_instruments)
    event_id = _mint_event_id({
        "reason": reason,
        "event_time": event_time,
        "knowledge_time": knowledge_time,
        "source": source,
        "access_method": access_method,
        "affected_instruments": sorted(instruments),
        "count": count,
        "related_event_id": related_event_id,
    })
    return CaptureEvent(
        event_id=event_id,
        reason=reason,
        event_time=event_time,
        knowledge_time=knowledge_time,
        source=source,
        access_method=access_method,
        affected_instruments=instruments,
        certification_status=certification_status,
        count=count,
        related_event_id=related_event_id,
        detail=detail,
        schema_version=schema_version,
    )


def validate_capture_event(event: CaptureEvent) -> Tuple[bool, Tuple[str, ...]]:
    """Structural check only. Capture events are generated internally, so
    they do not pass the certification gate — but a malformed one must
    still not enter the permanent log.

    Every check runs unconditionally, so `reasons` is always complete."""
    from .validator import _is_well_formed_timestamp

    problems = []
    if event.reason not in taxonomy.ALL_CAPTURE_REASONS:
        problems.append(f"UNKNOWN_CAPTURE_REASON:{event.reason}")
    if not _is_well_formed_timestamp(event.event_time):
        problems.append("MALFORMED_EVENT_TIME")
    if not _is_well_formed_timestamp(event.knowledge_time):
        problems.append("MALFORMED_KNOWLEDGE_TIME")
    if not event.source:
        problems.append("MISSING_SOURCE")
    if event.schema_version not in taxonomy.RECOGNIZED_LAYER0_SCHEMA_VERSIONS:
        problems.append("UNRECOGNIZED_SCHEMA_VERSION")
    if event.count is not None and event.count < 0:
        problems.append("NEGATIVE_COUNT")
    return (not problems), tuple(problems)
