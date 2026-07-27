"""Evidence Interpreter Journal — BUJJI Options OS v3, Engineering
Series 32.

Append-only JSONL, own schema, versioned, own storage path -- entirely
independent of every other journal in the codebase. This journal only
ever records already-produced EvidenceInterpretation instances from
evidence_interpreter/runner.py -- it performs no translation of its
own and makes no decision.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List, Union

from ..trading_brain.evidence_interpreter.models import EvidenceInterpretation
from ..trading_brain.evidence_interpreter.serialization import (
    interpretation_from_dict,
    interpretation_to_dict,
)

SCHEMA_VERSION = "1.0.0"


class EvidenceInterpreterJournal:
    def __init__(self, path: Union[str, Path]) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, interpretation: EvidenceInterpretation) -> None:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "interpretation": interpretation_to_dict(interpretation),
        }
        with open(self._path, "a") as fh:  # Append-only, explicitly.
            fh.write(json.dumps(payload) + "\n")

    def record_many(self, interpretations: List[EvidenceInterpretation]) -> None:
        for i in interpretations:
            self.record(i)

    def read_all(self) -> List[EvidenceInterpretation]:
        if not self._path.exists():
            return []
        out: List[EvidenceInterpretation] = []
        with open(self._path, "r") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    payload = json.loads(line)
                    out.append(interpretation_from_dict(payload["interpretation"]))
        return out
