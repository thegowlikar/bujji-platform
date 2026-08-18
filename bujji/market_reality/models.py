"""Market Reality Layer 0 models — Phase 17E. Frozen records, no IO.

REUSE, NOT REPLACEMENT. The canonical observation record is
`bujji.market_observation.Observation` (identity + quality + provenance +
value), already implemented and already carrying
`ObservationProvenance.transformation_history`. This module does NOT
redefine it. It adds only the block the Phase 17E audit proved missing:
certification state, access method, and the explicit event-vs-capture
timestamp split.

Nothing here computes anything about the market. `derive_confidence()` is
the only function in this file, and it is a pure lookup over two flags --
never a judgment.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional, Tuple

from bujji.market_observation.models import Observation
from bujji.market_observation.serialization import (
    observation_from_dict,
    observation_to_dict,
)

from . import taxonomy


def derive_confidence(certification_status: str, integrity_ok: bool) -> str:
    """HIGH iff the source was certified AND the record passed integrity.
    LOW otherwise. A pure function of two inputs -- there is deliberately
    no code path anywhere in this package that sets confidence by hand."""
    if certification_status == taxonomy.CERTIFIED_AVAILABLE and integrity_ok:
        return taxonomy.CONFIDENCE_HIGH
    return taxonomy.CONFIDENCE_LOW


def payload_hash(payload: Any) -> str:
    """Stable content hash of a raw payload, used to identify a rejected
    observation whose content could not be trusted enough to build a
    proper observation_id from. Sorted keys so the hash is order-stable."""
    try:
        encoded = json.dumps(payload, sort_keys=True, default=str)
    except (TypeError, ValueError):
        encoded = repr(payload)
    return "PLH-" + hashlib.sha256(encoded.encode()).hexdigest()[:24]


@dataclass(frozen=True)
class Layer0Lineage:
    """The lineage block Phase 17D Part 2 requires on every observation.

    `event_timestamp` (when the market event happened, per the source)
    and `capture_timestamp` (when this process received it) are kept
    DISTINCT and are never collapsed. `event_timestamp` is None when the
    source genuinely publishes none -- it is never backfilled from
    `capture_timestamp`, because a lineage that silently substitutes
    arrival time for event time cannot prove absence of look-ahead,
    which is the main thing lineage exists to prove
    (see bujji/epistemics/lineage.py's own statement of this rule).
    """

    source: str
    access_method: str
    event_timestamp: Optional[str]
    capture_timestamp: str
    certification_status: str
    certification_ref: Optional[str]
    confidence: str
    schema_version: str = taxonomy.LAYER0_SCHEMA_VERSION
    transformation_history: Tuple[str, ...] = (taxonomy.TRANSFORMATION_RAW_CAPTURE,)

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "access_method": self.access_method,
            "event_timestamp": self.event_timestamp,
            "capture_timestamp": self.capture_timestamp,
            "certification_status": self.certification_status,
            "certification_ref": self.certification_ref,
            "confidence": self.confidence,
            "schema_version": self.schema_version,
            "transformation_history": list(self.transformation_history),
        }

    @staticmethod
    def from_dict(d: Mapping) -> "Layer0Lineage":
        return Layer0Lineage(
            source=d["source"],
            access_method=d["access_method"],
            event_timestamp=d.get("event_timestamp"),
            capture_timestamp=d["capture_timestamp"],
            certification_status=d["certification_status"],
            certification_ref=d.get("certification_ref"),
            confidence=d["confidence"],
            schema_version=d.get("schema_version", taxonomy.LAYER0_SCHEMA_VERSION),
            transformation_history=tuple(
                d.get("transformation_history") or (taxonomy.TRANSFORMATION_RAW_CAPTURE,)
            ),
        )


@dataclass(frozen=True)
class RawObservation:
    """One immutable Layer 0 record: a canonical MOC `Observation` plus
    the Layer 0 capture context. `observation_id` is delegated to the
    embedded Observation -- minted by `market_observation.engine.
    build_observation()`, never re-derived here, so Layer 0 and MOC can
    never disagree about what an observation's identity is."""

    observation: Observation
    kind: str
    instrument_type: str
    lineage: Layer0Lineage
    identity_fields: Dict[str, Any] = field(default_factory=dict)

    @property
    def observation_id(self) -> str:
        return self.observation.identity.observation_id

    @property
    def instrument(self) -> str:
        return self.observation.identity.instrument

    @property
    def payload(self) -> Any:
        return self.observation.value.payload

    def to_dict(self) -> dict:
        return {
            "observation": observation_to_dict(self.observation),
            "kind": self.kind,
            "instrument_type": self.instrument_type,
            "lineage": self.lineage.to_dict(),
            "identity_fields": dict(self.identity_fields),
        }

    @staticmethod
    def from_dict(d: Mapping) -> "RawObservation":
        return RawObservation(
            observation=observation_from_dict(d["observation"]),
            kind=d["kind"],
            instrument_type=d["instrument_type"],
            lineage=Layer0Lineage.from_dict(d["lineage"]),
            identity_fields=dict(d.get("identity_fields") or {}),
        )


@dataclass(frozen=True)
class ValidationOutcome:
    """Result of the pre-persistence gate. `reasons` is always complete
    -- every check runs unconditionally, never short-circuiting on the
    first failure, mirroring `market_observation.engine.
    validate_observation()`'s own established discipline."""

    is_valid: bool
    reasons: Tuple[str, ...]
    validator_version: str = taxonomy.VALIDATOR_VERSION

    def to_dict(self) -> dict:
        return {
            "is_valid": self.is_valid,
            "reasons": list(self.reasons),
            "validator_version": self.validator_version,
        }


@dataclass(frozen=True)
class RejectedObservation:
    """A permanently stored rejection. Carries everything Phase 17E scope
    item 4 requires: the original payload hash, why it was rejected, which
    validator version decided that, when, and from what source.

    A rejection is a fact about capture, recorded as permanently as an
    acceptance -- so a gap in Layer 0 is never ambiguous between "nothing
    happened" and "something was discarded"."""

    payload_hash: str
    rejection_reasons: Tuple[str, ...]
    validator_version: str
    rejected_at: str
    source: str
    access_method: str
    kind: Optional[str]
    instrument: Optional[str]
    instrument_type: Optional[str]
    certification_status: Optional[str]
    original_payload: Any = None

    def to_dict(self) -> dict:
        return {
            "payload_hash": self.payload_hash,
            "rejection_reasons": list(self.rejection_reasons),
            "validator_version": self.validator_version,
            "rejected_at": self.rejected_at,
            "source": self.source,
            "access_method": self.access_method,
            "kind": self.kind,
            "instrument": self.instrument,
            "instrument_type": self.instrument_type,
            "certification_status": self.certification_status,
            "original_payload": self.original_payload,
        }

    @staticmethod
    def from_dict(d: Mapping) -> "RejectedObservation":
        return RejectedObservation(
            payload_hash=d["payload_hash"],
            rejection_reasons=tuple(d.get("rejection_reasons") or ()),
            validator_version=d["validator_version"],
            rejected_at=d["rejected_at"],
            source=d.get("source", ""),
            access_method=d.get("access_method", ""),
            kind=d.get("kind"),
            instrument=d.get("instrument"),
            instrument_type=d.get("instrument_type"),
            certification_status=d.get("certification_status"),
            original_payload=d.get("original_payload"),
        )


@dataclass(frozen=True)
class AppendResult:
    """Outcome of one append attempt. Exactly one of ACCEPTED /
    DUPLICATE / REJECTED -- never ambiguous, mirroring the three-state
    discipline Phase 17A.5's certification classifier established."""

    outcome: str
    observation_id: Optional[str]
    validation: ValidationOutcome
    rejected: Optional[RejectedObservation] = None

    def to_dict(self) -> dict:
        return {
            "outcome": self.outcome,
            "observation_id": self.observation_id,
            "validation": self.validation.to_dict(),
            "rejected": self.rejected.to_dict() if self.rejected is not None else None,
        }


@dataclass(frozen=True)
class StreamItem:
    """One position in the ordered Layer 0 union stream.

    Exactly one of `observation` / `capture_event` is populated,
    discriminated by `kind`. The union exists because a replay that
    silently omitted its own blind spots would reconstruct a market that
    never went quiet -- consumers wanting only market data must filter
    deliberately, and be seen to have done so."""

    kind: str
    timestamp: str
    observation: Optional["RawObservation"] = None
    capture_event: Optional[Any] = None

    @property
    def is_observation(self) -> bool:
        return self.kind == taxonomy.STREAM_OBSERVATION

    @property
    def is_capture_event(self) -> bool:
        return self.kind == taxonomy.STREAM_CAPTURE_EVENT

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "timestamp": self.timestamp,
            "observation": self.observation.to_dict() if self.observation else None,
            "capture_event": (
                self.capture_event.to_dict() if self.capture_event else None
            ),
        }


@dataclass(frozen=True)
class ReplayReport:
    """Diagnostics for one replay pass. Field names deliberately mirror
    `state_persistence.RecoveryReport` so the two read identically to an
    operator, without importing a state-recovery type into a
    market-data package."""

    status: str
    observations_discovered: int
    observations_replayed: int
    observations_skipped_duplicate: int
    observations_skipped_malformed: int
    observations_skipped_schema_mismatch: int
    first_observation_id: Optional[str]
    last_observation_id: Optional[str]
    # Capture events encountered in the stream. Its own category: neither
    # an observation nor a defect. A recorded blind spot must never be
    # counted as corruption.
    capture_events_seen: int = 0
    errors: Tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "observations_discovered": self.observations_discovered,
            "observations_replayed": self.observations_replayed,
            "observations_skipped_duplicate": self.observations_skipped_duplicate,
            "observations_skipped_malformed": self.observations_skipped_malformed,
            "observations_skipped_schema_mismatch": self.observations_skipped_schema_mismatch,
            "capture_events_seen": self.capture_events_seen,
            "first_observation_id": self.first_observation_id,
            "last_observation_id": self.last_observation_id,
            "errors": list(self.errors),
        }


@dataclass(frozen=True)
class CompletenessReport:
    """Observation Completeness Monitor output (Phase 17E scope item 6).

    Measures CAPTURE, never market behaviour. `source_health` describes
    the feed's delivery rate and says nothing whatsoever about price,
    volatility, or market condition -- reading it as a market signal
    would be a category error this package exists to prevent."""

    instrument: str
    kind: str
    window_start: str
    window_end: str
    interval_seconds: int
    expected_count: int
    received_count: int
    missing_intervals: Tuple[Tuple[str, str], ...]
    source_health: str

    def to_dict(self) -> dict:
        return {
            "instrument": self.instrument,
            "kind": self.kind,
            "window_start": self.window_start,
            "window_end": self.window_end,
            "interval_seconds": self.interval_seconds,
            "expected_count": self.expected_count,
            "received_count": self.received_count,
            "missing_intervals": [list(pair) for pair in self.missing_intervals],
            "source_health": self.source_health,
        }
