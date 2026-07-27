"""Decision Pipeline Qualification Journal — BUJJI Options OS v3,
Engineering Series 38.

Append-only JSONL, own schema, versioned, own storage path -- entirely
independent of every other journal in the codebase. This journal only
ever records already-produced DecisionPipelineQualification instances
from qualification/runner.py -- it performs no validation of its own
and makes no decision.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List, Union

from ..trading_brain.qualification.models import DecisionPipelineQualification
from ..trading_brain.qualification.serialization import (
    qualification_from_dict,
    qualification_to_dict,
)

SCHEMA_VERSION = "1.0.0"


class DecisionPipelineQualificationJournal:
    def __init__(self, path: Union[str, Path]) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, qualification: DecisionPipelineQualification) -> None:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "qualification": qualification_to_dict(qualification),
        }
        with open(self._path, "a") as fh:  # Append-only, explicitly.
            fh.write(json.dumps(payload) + "\n")

    def record_many(self, qualifications: List[DecisionPipelineQualification]) -> None:
        for q in qualifications:
            self.record(q)

    def read_all(self) -> List[DecisionPipelineQualification]:
        if not self._path.exists():
            return []
        out: List[DecisionPipelineQualification] = []
        with open(self._path, "r") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    payload = json.loads(line)
                    out.append(qualification_from_dict(payload["qualification"]))
        return out
