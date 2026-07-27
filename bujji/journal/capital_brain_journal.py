"""Capital Brain Journal — BUJJI Options OS v3, Engineering Series 36.

Append-only JSONL, own schema, versioned, own storage path -- entirely
independent of every other journal in the codebase. This journal only
ever records already-produced CapitalDecision instances from
capital_brain/runner.py -- it performs no authorization of its own and
makes no decision.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List, Union

from ..trading_brain.capital_brain.models import CapitalDecision
from ..trading_brain.capital_brain.serialization import (
    decision_from_dict,
    decision_to_dict,
)

SCHEMA_VERSION = "1.0.0"


class CapitalBrainJournal:
    def __init__(self, path: Union[str, Path]) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, decision: CapitalDecision) -> None:
        payload = {"schema_version": SCHEMA_VERSION, "decision": decision_to_dict(decision)}
        with open(self._path, "a") as fh:  # Append-only, explicitly.
            fh.write(json.dumps(payload) + "\n")

    def record_many(self, decisions: List[CapitalDecision]) -> None:
        for d in decisions:
            self.record(d)

    def read_all(self) -> List[CapitalDecision]:
        if not self._path.exists():
            return []
        out: List[CapitalDecision] = []
        with open(self._path, "r") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    payload = json.loads(line)
                    out.append(decision_from_dict(payload["decision"]))
        return out
