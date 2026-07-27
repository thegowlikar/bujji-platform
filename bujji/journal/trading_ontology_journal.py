"""Trading Ontology Journal — BUJJI Options OS v3, Engineering Series 31.

Append-only JSONL, own schema, versioned, own storage path. Never
modifies any other journal in the codebase. This journal only ever
records already-validated TradingOntologySnapshot instances produced
by ontology/runner.py::build_snapshot -- it performs no validation of
its own and makes no decision.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List, Union

from ..trading_brain.ontology.models import TradingOntologySnapshot
from ..trading_brain.ontology.serialization import snapshot_from_dict, snapshot_to_dict

SCHEMA_VERSION = "1.0.0"


class TradingOntologyJournal:
    def __init__(self, path: Union[str, Path]) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, snapshot: TradingOntologySnapshot) -> None:
        payload = {"schema_version": SCHEMA_VERSION, "snapshot": snapshot_to_dict(snapshot)}
        with open(self._path, "a") as fh:  # Append-only, explicitly.
            fh.write(json.dumps(payload) + "\n")

    def record_many(self, snapshots: List[TradingOntologySnapshot]) -> None:
        for s in snapshots:
            self.record(s)

    def read_all(self) -> List[TradingOntologySnapshot]:
        if not self._path.exists():
            return []
        out: List[TradingOntologySnapshot] = []
        with open(self._path, "r") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    payload = json.loads(line)
                    out.append(snapshot_from_dict(payload["snapshot"]))
        return out
