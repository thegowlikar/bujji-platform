"""Trade Intent journal — append-only, deterministic recorder for
TradeIntentAssessments.

Mirrors `bujji.msi_strategy_eligibility.journal.
StrategyEligibilityJournal`'s established pattern exactly: append-only
JSONL, own schema, own storage path, entirely independent of every
other journal in the codebase. Never modifies or deletes a recorded
assessment.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List

from .models import TradeIntentAssessment
from .serialization import assessment_from_dict, assessment_to_dict

SCHEMA_VERSION = "1.0.0"


class TradeIntentJournal:
    """Append-only JSONL journal of TradeIntentAssessments. Never
    modifies or deletes a previously-written record."""

    def __init__(self, path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def record_assessment(self, assessment: TradeIntentAssessment) -> None:
        self._append({"schema_version": SCHEMA_VERSION, "kind": "TRADE_INTENT_ASSESSMENT", "assessment": assessment_to_dict(assessment)})

    def _append(self, payload: dict) -> None:
        with open(self._path, "a") as fh:
            fh.write(json.dumps(payload, sort_keys=True, default=repr) + "\n")

    def read_all(self) -> List[dict]:
        if not self._path.exists():
            return []
        out: List[dict] = []
        with open(self._path, "r") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                out.append(json.loads(line))
        return out

    def read_assessments(self) -> List[TradeIntentAssessment]:
        return [
            assessment_from_dict(record["assessment"])
            for record in self.read_all()
            if record.get("kind") == "TRADE_INTENT_ASSESSMENT"
        ]
