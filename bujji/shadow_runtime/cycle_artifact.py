"""Shadow Intelligence Cycle Artifact -- Shadow Runtime, Phase 19.10.2.

"What the shadow runtime produced during this cycle" -- a small,
session-scoped record, deliberately NOT confused with `DatasetArtifact`
(Phase 18.12, a certified historical dataset publication),
`MarketIntelligenceSnapshot` (Phase 19.3, the intelligence composition
itself), or `MarketMemoryEntry`/Market Understanding Memory (Phase 19.5,
permanent cross-session memory). Those identities already exist and are
untouched by this module; this artifact only links to them by reference
(their own real fingerprints), never re-derives or duplicates them.

Persisted via `bujji.state_persistence.store.EventStore` -- the SAME
append-only primitive every other durable record in this project
already uses (Phase 15B onward, reused again here rather than a new
mechanism).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

EVENT_SHADOW_INTELLIGENCE_CYCLE_RECORDED = "SHADOW_INTELLIGENCE_CYCLE_RECORDED"
EVENT_SHADOW_INTELLIGENCE_CYCLE_FAILED = "SHADOW_INTELLIGENCE_CYCLE_FAILED"


@dataclass(frozen=True)
class ConfidenceSummary:
    """Two separate confidences, never merged -- same discipline Phase
    19.6/19.9 already established: what the evidence itself supports
    (observation) vs. how strongly a specific classification applies
    (environment)."""

    observation_confidence: float  # DecisionIntelligenceSnapshot.evidence_bundle["confidence"], verbatim.
    environment_confidence: str    # MarketEnvironmentAssessment.confidence, verbatim (NONE/LOW/MODERATE/HIGH).

    def to_dict(self) -> dict:
        return {"observation_confidence": self.observation_confidence, "environment_confidence": self.environment_confidence}


@dataclass(frozen=True)
class ShadowIntelligenceCycleArtifact:
    cycle_id: str
    session_id: str
    as_of_time: str                          # isoformat -- the cycle's own real as_of_time, never wall-clock.
    execution_mode: str
    reality_snapshot_fingerprint: str
    intelligence_snapshot_fingerprint: str
    decision_intelligence_fingerprint: str
    detected_phenomena: Tuple[str, ...]
    market_state_transition: Optional[str]   # transition_type, or None when no transition was detected.
    environment_classification: str
    confidence_summary: ConfidenceSummary
    runtime_health_status: str
    cycle_artifact_id: str = ""              # set by build_cycle_artifact() -- see its own docstring.

    def fingerprint_payload(self) -> Dict[str, Any]:
        """Excludes `cycle_artifact_id` itself (self-referential) --
        `as_of_time`/`execution_mode`/etc. below are all real content,
        not runtime metadata, so they ARE included. No `created_at` or
        process-identifier field exists anywhere on this object to
        exclude in the first place."""
        return {
            "cycle_id": self.cycle_id, "session_id": self.session_id, "as_of_time": self.as_of_time,
            "execution_mode": self.execution_mode,
            "reality_snapshot_fingerprint": self.reality_snapshot_fingerprint,
            "intelligence_snapshot_fingerprint": self.intelligence_snapshot_fingerprint,
            "decision_intelligence_fingerprint": self.decision_intelligence_fingerprint,
            "detected_phenomena": list(self.detected_phenomena),
            "market_state_transition": self.market_state_transition,
            "environment_classification": self.environment_classification,
            "confidence_summary": self.confidence_summary.to_dict(),
            "runtime_health_status": self.runtime_health_status,
        }

    def fingerprint(self) -> str:
        from bujji.replay_engine.engine import fingerprint_state
        return fingerprint_state(self.fingerprint_payload())

    def to_dict(self) -> dict:
        d = self.fingerprint_payload()
        d["cycle_artifact_id"] = self.cycle_artifact_id
        return d

    @staticmethod
    def from_dict(d: dict) -> "ShadowIntelligenceCycleArtifact":
        return ShadowIntelligenceCycleArtifact(
            cycle_id=d["cycle_id"], session_id=d["session_id"], as_of_time=d["as_of_time"],
            execution_mode=d["execution_mode"],
            reality_snapshot_fingerprint=d["reality_snapshot_fingerprint"],
            intelligence_snapshot_fingerprint=d["intelligence_snapshot_fingerprint"],
            decision_intelligence_fingerprint=d["decision_intelligence_fingerprint"],
            detected_phenomena=tuple(d.get("detected_phenomena", ())),
            market_state_transition=d.get("market_state_transition"),
            environment_classification=d["environment_classification"],
            confidence_summary=ConfidenceSummary(**d["confidence_summary"]),
            runtime_health_status=d["runtime_health_status"],
            cycle_artifact_id=d.get("cycle_artifact_id", ""),
        )


def build_cycle_artifact(
    *, cycle, cycle_id: str, session_id: str, execution_mode: str, runtime_health_status: str,
) -> ShadowIntelligenceCycleArtifact:
    """`cycle`: an `IntelligenceHeartbeatCycle` (Phase 19.10.1) -- every
    fingerprint below is copied verbatim from an already-real object
    this artifact only references, never recomputes."""
    from bujji.replay_engine.engine import fingerprint_state

    confidence_summary = ConfidenceSummary(
        observation_confidence=cycle.decision_intelligence.evidence_bundle.get("confidence", 0.0),
        environment_confidence=cycle.environment.confidence,
    )
    transition = cycle.state_node.transition
    provisional = ShadowIntelligenceCycleArtifact(
        cycle_id=cycle_id, session_id=session_id,
        as_of_time=cycle.market_intelligence_snapshot.as_of_time.isoformat(),
        execution_mode=execution_mode,
        reality_snapshot_fingerprint=cycle.market_intelligence_snapshot.reality_fingerprint or "",
        intelligence_snapshot_fingerprint=cycle.market_intelligence_snapshot.intelligence_snapshot_id,
        decision_intelligence_fingerprint=cycle.decision_intelligence.decision_intelligence_id,
        detected_phenomena=tuple(p.phenomenon_type for p in cycle.phenomena.phenomena),
        market_state_transition=transition.transition_type if transition else None,
        environment_classification=cycle.environment.environment_type.value,
        confidence_summary=confidence_summary,
        runtime_health_status=runtime_health_status,
    )
    cycle_artifact_id = fingerprint_state(provisional.fingerprint_payload())
    from dataclasses import replace
    return replace(provisional, cycle_artifact_id=cycle_artifact_id)


def record_cycle_artifact(store, artifact: ShadowIntelligenceCycleArtifact, *, recorded_at: datetime) -> None:
    """Appends ONE immutable fact via the existing `EventStore` (Phase
    15B) -- no new persistence mechanism. Idempotent: two calls for the
    same real cycle content produce the same `cycle_artifact_id`, so a
    replay never double-records."""
    from bujji.state_persistence.models import PersistedEvent
    event = PersistedEvent(
        event_id=artifact.cycle_artifact_id, event_type=EVENT_SHADOW_INTELLIGENCE_CYCLE_RECORDED,
        session_id=artifact.session_id, cycle_id=artifact.cycle_id,
        timestamp=recorded_at.isoformat(), schema_version="1.0.0",
        provenance="shadow_runtime.cycle_artifact.record_cycle_artifact",
        payload=artifact.to_dict(),
    )
    store.append(event)


def record_cycle_failure(
    store, *, session_id: str, cycle_id: str, execution_mode: str, as_of_time: str,
    error: str, recorded_at: datetime,
) -> None:
    """A cycle that failed intelligence generation is still a real,
    disclosed fact -- recorded as its own event, never silently
    dropped, never disguised as a successful (but empty) artifact."""
    from bujji.state_persistence.models import PersistedEvent
    event = PersistedEvent(
        event_id=f"{session_id}|{cycle_id}|FAILED", event_type=EVENT_SHADOW_INTELLIGENCE_CYCLE_FAILED,
        session_id=session_id, cycle_id=cycle_id,
        timestamp=recorded_at.isoformat(), schema_version="1.0.0",
        provenance="shadow_runtime.cycle_artifact.record_cycle_failure",
        payload={"execution_mode": execution_mode, "as_of_time": as_of_time, "error": error},
    )
    store.append(event)


def hydrate_cycle_artifacts(store) -> Dict[str, ShadowIntelligenceCycleArtifact]:
    """Cross-session, like `market_understanding`/`market_state_graph`'s
    own hydration functions -- no `session_id` gate, since durable
    intelligence memory is meant to span every session."""
    artifacts: Dict[str, ShadowIntelligenceCycleArtifact] = {}
    for event in store.read_events():
        if event.event_type == EVENT_SHADOW_INTELLIGENCE_CYCLE_RECORDED:
            artifact = ShadowIntelligenceCycleArtifact.from_dict(event.payload)
            artifacts[artifact.cycle_artifact_id] = artifact
    return artifacts
