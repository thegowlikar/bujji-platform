"""CRE serialization — Series 102. Pure dict/JSON round-trip, mirrors
every prior MSI package's convention."""
from __future__ import annotations

import json
from typing import Any, Dict

from .models import CounterfactualSession, ExploredPath


def path_to_dict(p: ExploredPath) -> Dict[str, Any]:
    return {
        "path_id": p.path_id, "label": p.label, "decision_timestamp": p.decision_timestamp,
        "thesis_type": p.thesis_type, "selected_family": p.selected_family,
        "data_timestamps_used": list(p.data_timestamps_used), "rationale": list(p.rationale),
        "schema_version": p.schema_version,
    }


def path_from_dict(d: Dict[str, Any]) -> ExploredPath:
    return ExploredPath(
        path_id=d["path_id"], label=d["label"], decision_timestamp=d["decision_timestamp"],
        thesis_type=d["thesis_type"], selected_family=d["selected_family"],
        data_timestamps_used=tuple(d["data_timestamps_used"]), rationale=tuple(d["rationale"]),
        schema_version=d["schema_version"],
    )


def session_to_dict(s: CounterfactualSession) -> Dict[str, Any]:
    return {
        "session_id": s.session_id, "replay_id": s.replay_id, "replay_timestamp": s.replay_timestamp,
        "baseline_timestamp": s.baseline_timestamp, "production_version": s.production_version,
        "replay_version": s.replay_version, "assumptions": list(s.assumptions),
        "explored_paths": [path_to_dict(p) for p in s.explored_paths],
        "rejected_paths": list(s.rejected_paths), "replay_legality": s.replay_legality,
        "legality_reasoning": list(s.legality_reasoning), "causality_verdicts": list(s.causality_verdicts),
        "earliest_causal_timestamp": s.earliest_causal_timestamp,
        "supporting_references": list(s.supporting_references), "schema_version": s.schema_version,
    }


def session_from_dict(d: Dict[str, Any]) -> CounterfactualSession:
    return CounterfactualSession(
        session_id=d["session_id"], replay_id=d["replay_id"], replay_timestamp=d["replay_timestamp"],
        baseline_timestamp=d["baseline_timestamp"], production_version=d["production_version"],
        replay_version=d["replay_version"], assumptions=tuple(d["assumptions"]),
        explored_paths=tuple(path_from_dict(p) for p in d["explored_paths"]),
        rejected_paths=tuple(d["rejected_paths"]), replay_legality=d["replay_legality"],
        legality_reasoning=tuple(d["legality_reasoning"]), causality_verdicts=tuple(d["causality_verdicts"]),
        earliest_causal_timestamp=d["earliest_causal_timestamp"],
        supporting_references=tuple(d["supporting_references"]), schema_version=d["schema_version"],
    )


def session_to_json(s: CounterfactualSession) -> str:
    return json.dumps(session_to_dict(s), sort_keys=True, default=repr)


def session_from_json(text: str) -> CounterfactualSession:
    return session_from_dict(json.loads(text))
