"""Order Construction Journal — BUJJI Options OS v3, Engineering Series
44.

Append-only JSONL, own schema, versioned, own storage path -- entirely
independent of every other journal in the codebase. This journal only
ever records already-produced OrderConstructionResult instances from
order_construction/runner.py -- it performs no construction of its own
and makes no decision.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List, Union

from ..trading_brain.order_construction.models import OrderConstructionResult
from ..trading_brain.order_construction.serialization import result_from_dict, result_to_dict

SCHEMA_VERSION = "1.0.0"


class OrderConstructionJournal:
    def __init__(self, path: Union[str, Path]) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, result: OrderConstructionResult) -> None:
        payload = {"schema_version": SCHEMA_VERSION, "result": result_to_dict(result)}
        with open(self._path, "a") as fh:  # Append-only, explicitly.
            fh.write(json.dumps(payload) + "\n")

    def record_many(self, results: List[OrderConstructionResult]) -> None:
        for r in results:
            self.record(r)

    def read_all(self) -> List[OrderConstructionResult]:
        if not self._path.exists():
            return []
        out: List[OrderConstructionResult] = []
        with open(self._path, "r") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    payload = json.loads(line)
                    out.append(result_from_dict(payload["result"]))
        return out
