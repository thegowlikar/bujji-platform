"""Phase 20.13 -- persistence. Append-only JSONL, one line per
artifact, tagged by type -- the SAME append-only journal convention
every other durable record in this codebase already uses
(`SessionStore`, `EventStore`, Phase 20.11's own `ShadowDecisionLog`
pattern). No update, no overwrite, no delete method exists anywhere in
this module.

Stores ONLY intelligence artifacts: `DecisionObservation`,
`CampaignSession`, `CampaignMetrics`, `HealthReport`. No holdings- or
realized-outcome record is ever constructed to persist here.
"""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import List

from bujji.shadow_decision_runtime import DecisionObservation


def save_campaign_artifact(path, artifact_type: str, artifact) -> None:
    payload = artifact.to_dict() if hasattr(artifact, "to_dict") else dataclasses.asdict(artifact)
    record = {"artifact_type": artifact_type, "payload": payload}
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a") as f:
        f.write(json.dumps(record) + "\n")


def load_campaign_artifacts(path) -> List[dict]:
    p = Path(path)
    if not p.exists():
        return []
    records = []
    with open(p) as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def load_decision_observations(path) -> List[DecisionObservation]:
    """Reconstructs every persisted `DecisionObservation` from disk --
    the restart-recovery path: a runtime that crashes mid-session can
    reload every intelligence artifact it already recorded before
    resuming, never losing a real observation."""
    observations = []
    for record in load_campaign_artifacts(path):
        if record.get("artifact_type") != "DecisionObservation":
            continue
        p = record["payload"]
        observations.append(DecisionObservation(
            observation_id=p["observation_id"], timestamp=p["timestamp"], market_state=p["market_state"],
            candidate_strategy=p["candidate_strategy"], decision_state=p["decision_state"],
            priority_score=p["priority_score"], allocation_class=p["allocation_class"],
            confidence=p["confidence"], reason_codes=tuple(p["reason_codes"]),
            uncertainty=tuple(p["uncertainty"]), data_quality=p["data_quality"],
        ))
    return observations
