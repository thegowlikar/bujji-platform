"""Raw Observation Store — Phase 17E Layer 0.

              Certified Source
                     |
                     v
             Observation Validator
                     |
          +----------+----------+
          |                     |
      Accepted              Rejected
      EventStore          Rejection Store

NO PARALLEL PERSISTENCE SYSTEM. Durability is `state_persistence.
EventStore`, used UNMODIFIED -- the same append-only JSONL writer with
per-record flush+fsync, atomic single-line writes, and torn-trailing-line
tolerance that has carried RegimeMemoryState, PaperBroker, Observation
Memory and Outcome Memory across five phases. This module writes no
bytes of its own; it routes records into one of two EventStores and
nothing else.

IMMUTABILITY: nothing here updates or deletes. A record, once appended,
is permanent. Accepted and rejected records live in separate stores and a
record NEVER appears in both -- an untrustworthy observation gets a
permanent, honest home, never a foothold in the trusted store.

No wall-clock is read in this module. Every timestamp is injected by the
caller (`now`), so a replayed capture sequence produces byte-identical
records. Replay-safety depends on this.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterator, List, Optional, Set, Union

from bujji.state_persistence.models import PersistedEvent
from bujji.state_persistence.store import EventStore

from . import taxonomy, validator
from .certification import CertificationGate
from .models import (
    AppendResult,
    RawObservation,
    RejectedObservation,
    ValidationOutcome,
    payload_hash,
)

ACCEPTED_FILENAME = "raw_observations.jsonl"
REJECTED_FILENAME = "rejected_observations.jsonl"

_EVENT_TYPE_REJECTION = "LAYER0_OBSERVATION_REJECTED"


class RawObservationStore:
    """Layer 0's write surface.

    Safe to construct repeatedly against the same directory, including
    across a process restart -- never truncates, never rewrites. On
    construction it replays the accepted log once to rebuild the set of
    observation ids it already holds, which is what makes duplicate
    detection survive a restart.
    """

    def __init__(
        self,
        directory: Union[str, Path],
        certification_gate: CertificationGate,
        session_id: str = "layer0",
    ) -> None:
        self._dir = Path(directory)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._accepted = EventStore(str(self._dir / ACCEPTED_FILENAME))
        self._rejected = EventStore(str(self._dir / REJECTED_FILENAME))
        self._gate = certification_gate
        self._session_id = session_id
        self._seen_ids: Set[str] = {
            event.event_id for event in self._accepted.read_events()
        }

    @property
    def directory(self) -> Path:
        return self._dir

    @property
    def accepted_path(self) -> str:
        return self._accepted.path

    @property
    def rejected_path(self) -> str:
        return self._rejected.path

    def holds(self, observation_id: str) -> bool:
        return observation_id in self._seen_ids

    def __len__(self) -> int:
        return len(self._seen_ids)

    # -- Write ----------------------------------------------------------- #
    def append(self, raw: RawObservation, now: str) -> AppendResult:
        """Validate, then route to exactly one destination.

        Three outcomes, never ambiguous:

        * ACCEPTED  -- valid and new; appended to the accepted store.
        * DUPLICATE -- an observation with this id is already held. This
          is an idempotent NO-OP, deliberately NOT a rejection: because
          `observation_id` is a deterministic content hash over identity
          plus value (minted by `market_observation.engine.
          build_observation`), an identical id means an identical fact.
          Re-capturing the same fact is normal (a retried poll, a
          reconnecting feed) and recording a rejection for each retry
          would pollute the rejection log with non-events. Nothing is
          written, and nothing is lost.
        * REJECTED  -- failed one or more validation checks; written to
          the rejection store with its payload hash, reasons, validator
          version, timestamp and source. Never written to the accepted
          store.
        """
        status, cert_ref = self._gate.status_for(
            raw.lineage.access_method, raw.instrument_type
        )
        outcome = validator.validate(raw, certification_status=status, now=now)

        if not outcome.is_valid:
            rejection = RejectedObservation(
                payload_hash=payload_hash(raw.payload),
                rejection_reasons=outcome.reasons,
                validator_version=outcome.validator_version,
                rejected_at=now,
                source=raw.lineage.source,
                access_method=raw.lineage.access_method,
                kind=raw.kind,
                instrument=raw.instrument or None,
                instrument_type=raw.instrument_type,
                certification_status=status,
                original_payload=raw.payload,
            )
            self._rejected.append(
                PersistedEvent(
                    event_id=rejection.payload_hash,
                    event_type=_EVENT_TYPE_REJECTION,
                    session_id=self._session_id,
                    cycle_id=None,
                    timestamp=now,
                    schema_version=taxonomy.LAYER0_SCHEMA_VERSION,
                    provenance=raw.lineage.access_method,
                    payload=rejection.to_dict(),
                )
            )
            return AppendResult(
                outcome=taxonomy.OUTCOME_REJECTED,
                observation_id=None,
                validation=outcome,
                rejected=rejection,
            )

        observation_id = raw.observation_id
        if observation_id in self._seen_ids:
            return AppendResult(
                outcome=taxonomy.OUTCOME_DUPLICATE,
                observation_id=observation_id,
                validation=outcome,
            )

        # The certification_ref resolved at write time is stamped onto the
        # stored record, so the record's certification claim stays
        # auditable against a real dated run even if the artifact on disk
        # later changes. History is never rewritten.
        stored = raw
        if cert_ref is not None and raw.lineage.certification_ref != cert_ref:
            from dataclasses import replace

            stored = replace(
                raw, lineage=replace(raw.lineage, certification_ref=cert_ref)
            )

        self._accepted.append(
            PersistedEvent(
                event_id=observation_id,
                event_type=stored.kind,
                session_id=self._session_id,
                cycle_id=None,  # Layer 0 is market reality, not an intelligence cycle.
                timestamp=(
                    stored.lineage.event_timestamp or stored.lineage.capture_timestamp
                ),
                schema_version=stored.lineage.schema_version,
                provenance=stored.lineage.access_method,
                payload=stored.to_dict(),
            )
        )
        self._seen_ids.add(observation_id)
        return AppendResult(
            outcome=taxonomy.OUTCOME_ACCEPTED,
            observation_id=observation_id,
            validation=outcome,
        )

    def append_many(self, observations, now: str) -> List[AppendResult]:
        return [self.append(obs, now) for obs in observations]

    def append_capture_event(self, event) -> AppendResult:
        """Append a CaptureEvent -- a fact about the observer.

        Deliberately does NOT pass the certification gate: a capture
        event describes our own process, not data received from a
        broker, and recording "we were disconnected" cannot sensibly
        require FYERS to certify it. It is still structurally validated,
        so a malformed event never enters the permanent log.

        Shares the accepted store, its ordering, and its id space with
        market observations -- a replay must encounter blind spots in
        sequence with data. It does NOT share the observation record
        shape, and `validator.validate()` is never applied to it.
        """
        from .capture_events import validate_capture_event

        ok, problems = validate_capture_event(event)
        outcome = ValidationOutcome(
            is_valid=ok, reasons=problems, validator_version=taxonomy.VALIDATOR_VERSION
        )

        if not ok:
            rejection = RejectedObservation(
                payload_hash=payload_hash(event.to_dict()),
                rejection_reasons=problems,
                validator_version=taxonomy.VALIDATOR_VERSION,
                rejected_at=event.knowledge_time,
                source=event.source,
                access_method=event.access_method,
                kind=taxonomy.KIND_CAPTURE_EVENT,
                instrument=None,
                instrument_type=None,
                certification_status=event.certification_status,
                original_payload=event.to_dict(),
            )
            self._rejected.append(
                PersistedEvent(
                    event_id=rejection.payload_hash,
                    event_type=_EVENT_TYPE_REJECTION,
                    session_id=self._session_id,
                    cycle_id=None,
                    timestamp=event.knowledge_time,
                    schema_version=taxonomy.LAYER0_SCHEMA_VERSION,
                    provenance=event.access_method,
                    payload=rejection.to_dict(),
                )
            )
            return AppendResult(
                outcome=taxonomy.OUTCOME_REJECTED, observation_id=None,
                validation=outcome, rejected=rejection,
            )

        if event.event_id in self._seen_ids:
            return AppendResult(
                outcome=taxonomy.OUTCOME_DUPLICATE,
                observation_id=event.event_id,
                validation=outcome,
            )

        self._accepted.append(
            PersistedEvent(
                event_id=event.event_id,
                event_type=taxonomy.KIND_CAPTURE_EVENT,
                session_id=self._session_id,
                cycle_id=None,
                timestamp=event.event_time,
                schema_version=event.schema_version,
                provenance=event.access_method,
                payload=event.to_dict(),
            )
        )
        self._seen_ids.add(event.event_id)
        return AppendResult(
            outcome=taxonomy.OUTCOME_ACCEPTED,
            observation_id=event.event_id,
            validation=outcome,
        )

    # -- Read ------------------------------------------------------------ #
    def read_accepted_events(self) -> Iterator[PersistedEvent]:
        """Raw stored events in exact append order."""
        return self._accepted.read_events()

    def read_rejected(self) -> List[RejectedObservation]:
        return [
            RejectedObservation.from_dict(event.payload)
            for event in self._rejected.read_events()
        ]

    def rejection_count(self) -> int:
        return sum(1 for _ in self._rejected.read_events())
