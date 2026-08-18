"""Daily Intelligence Artifact -- Shadow Runtime, Phase 19.13.

Task 4's own storage requirement, verbatim: preserve reality,
intelligence, phenomena, market state, and health -- not just their
fingerprints (`ShadowIntelligenceCycleArtifact`, Phase 19.10.2, already
stores those and is reused unmodified for LIVE/REPLAY fingerprint
equivalence). This artifact stores the REAL FULL PAYLOAD of each layer,
via each object's own already-existing `to_dict()` -- never a
re-derived or reshaped copy.

Persisted via the same `bujji.state_persistence.store.EventStore`
append-only primitive every durable record in this project already
uses (Phase 15B onward) -- no new persistence mechanism.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional

EVENT_DAILY_INTELLIGENCE_ARTIFACT_RECORDED = "DAILY_INTELLIGENCE_ARTIFACT_RECORDED"


@dataclass(frozen=True)
class DailyIntelligenceArtifact:
    """One cycle's full, real output -- every field either a real
    identifier/timestamp or a real object's own `to_dict()`, never
    fabricated or re-derived."""

    cycle_id: str
    session_id: str
    session_date: str
    as_of_time: str                      # the cycle's own real as_of_time -- never wall-clock.
    execution_mode: str
    runtime_health_status: str
    intelligence_fingerprint: str        # MarketIntelligenceSnapshot.intelligence_snapshot_id -- the chain's own identity anchor.
    reality: Dict[str, Any]              # MarketRealitySnapshot.to_dict()
    intelligence: Dict[str, Any]         # MarketIntelligenceSnapshot.to_dict()
    decision_intelligence: Dict[str, Any]
    phenomena: Dict[str, Any]
    market_state: Dict[str, Any]
    environment: Dict[str, Any]
    completeness_gate_passed: bool       # False means intelligence/decision/phenomena/market_state below are GATED (see gate module) -- never falsely confident.
    artifact_id: str = ""                # set by build_daily_intelligence_artifact() -- see its own docstring.
    # Phase 19.14.1, additive: the real, unmodified
    # `replay_equivalence.LiveReplayEquivalenceReport.to_dict()` when the
    # caller requested the check (`run_live_intelligence_cycle(...,
    # include_replay_equivalence=True)`) -- None when not requested, never
    # fabricated.
    replay_equivalence: Optional[Dict[str, Any]] = None

    def fingerprint_payload(self) -> Dict[str, Any]:
        return {
            "cycle_id": self.cycle_id, "session_id": self.session_id, "session_date": self.session_date,
            "as_of_time": self.as_of_time, "execution_mode": self.execution_mode,
            "runtime_health_status": self.runtime_health_status,
            "intelligence_fingerprint": self.intelligence_fingerprint,
            "reality": self.reality, "intelligence": self.intelligence,
            "decision_intelligence": self.decision_intelligence, "phenomena": self.phenomena,
            "market_state": self.market_state, "environment": self.environment,
            "completeness_gate_passed": self.completeness_gate_passed,
            "replay_equivalence": self.replay_equivalence,
        }

    def to_dict(self) -> dict:
        d = self.fingerprint_payload()
        d["artifact_id"] = self.artifact_id
        return d

    @staticmethod
    def from_dict(d: dict) -> "DailyIntelligenceArtifact":
        return DailyIntelligenceArtifact(
            cycle_id=d["cycle_id"], session_id=d["session_id"], session_date=d["session_date"],
            as_of_time=d["as_of_time"], execution_mode=d["execution_mode"],
            runtime_health_status=d["runtime_health_status"],
            intelligence_fingerprint=d["intelligence_fingerprint"],
            reality=d["reality"], intelligence=d["intelligence"],
            decision_intelligence=d["decision_intelligence"], phenomena=d["phenomena"],
            market_state=d["market_state"], environment=d["environment"],
            completeness_gate_passed=d["completeness_gate_passed"],
            artifact_id=d.get("artifact_id", ""),
            replay_equivalence=d.get("replay_equivalence"),
        )


def build_daily_intelligence_artifact(
    *, cycle, reality_snapshot, cycle_id: str, session_id: str, session_date: str,
    execution_mode: str, runtime_health_status: str, completeness_gate_passed: bool,
    replay_equivalence: Optional[Dict[str, Any]] = None,
) -> DailyIntelligenceArtifact:
    """`cycle`: an `IntelligenceHeartbeatCycle` (Phase 19.10.1).
    `reality_snapshot`: the `MarketRealitySnapshot` it was built from.
    Every payload field below is that real object's own `to_dict()`."""
    from bujji.replay_engine.engine import fingerprint_state

    provisional = DailyIntelligenceArtifact(
        cycle_id=cycle_id, session_id=session_id, session_date=session_date,
        as_of_time=cycle.market_intelligence_snapshot.as_of_time.isoformat(),
        execution_mode=execution_mode, runtime_health_status=runtime_health_status,
        intelligence_fingerprint=cycle.market_intelligence_snapshot.intelligence_snapshot_id,
        reality=reality_snapshot.to_dict(), intelligence=cycle.market_intelligence_snapshot.to_dict(),
        decision_intelligence=cycle.decision_intelligence.to_dict(), phenomena=cycle.phenomena.to_dict(),
        market_state=cycle.state_node.to_dict(), environment=cycle.environment.to_dict(),
        completeness_gate_passed=completeness_gate_passed, replay_equivalence=replay_equivalence,
    )
    artifact_id = fingerprint_state(provisional.fingerprint_payload())
    from dataclasses import replace
    return replace(provisional, artifact_id=artifact_id)


def record_daily_intelligence_artifact(store, artifact: DailyIntelligenceArtifact, *, recorded_at: datetime) -> None:
    """Idempotent: two calls for the same real cycle content produce the
    same `artifact_id`, so a replay never double-records (same
    discipline `cycle_artifact.record_cycle_artifact` already
    established, Phase 19.10.2)."""
    from bujji.state_persistence.models import PersistedEvent
    event = PersistedEvent(
        event_id=artifact.artifact_id, event_type=EVENT_DAILY_INTELLIGENCE_ARTIFACT_RECORDED,
        session_id=artifact.session_id, cycle_id=artifact.cycle_id,
        timestamp=recorded_at.isoformat(), schema_version="1.0.0",
        provenance="shadow_runtime.daily_intelligence_artifact.record_daily_intelligence_artifact",
        payload=artifact.to_dict(),
    )
    store.append(event)


def hydrate_daily_intelligence_artifacts(store) -> Dict[str, DailyIntelligenceArtifact]:
    """Cross-session, like `cycle_artifact.hydrate_cycle_artifacts` --
    no `session_id` gate, since this is durable daily intelligence
    memory meant to span every session, including across a restart."""
    artifacts: Dict[str, DailyIntelligenceArtifact] = {}
    for event in store.read_events():
        if event.event_type == EVENT_DAILY_INTELLIGENCE_ARTIFACT_RECORDED:
            artifact = DailyIntelligenceArtifact.from_dict(event.payload)
            artifacts[artifact.artifact_id] = artifact
    return artifacts
