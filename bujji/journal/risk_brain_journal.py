"""Risk Brain Journal — BUJJI Options OS v3, Engineering Series 35.

Append-only JSONL, own schema, versioned, own storage path -- entirely
independent of every other journal in the codebase. This journal only
ever records already-produced RiskAssessment instances from
risk_brain/runner.py -- it performs no evaluation of its own and makes
no decision.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List, Union

from ..trading_brain.risk_brain.models import RiskAssessment
from ..trading_brain.risk_brain.serialization import (
    assessment_from_dict,
    assessment_to_dict,
)

SCHEMA_VERSION = "1.0.0"


class RiskBrainJournal:
    def __init__(self, path: Union[str, Path]) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, assessment: RiskAssessment) -> None:
        payload = {"schema_version": SCHEMA_VERSION, "assessment": assessment_to_dict(assessment)}
        with open(self._path, "a") as fh:  # Append-only, explicitly.
            fh.write(json.dumps(payload) + "\n")

    def record_many(self, assessments: List[RiskAssessment]) -> None:
        for a in assessments:
            self.record(a)

    def read_all(self) -> List[RiskAssessment]:
        if not self._path.exists():
            return []
        out: List[RiskAssessment] = []
        with open(self._path, "r") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    payload = json.loads(line)
                    out.append(assessment_from_dict(payload["assessment"]))
        return out
