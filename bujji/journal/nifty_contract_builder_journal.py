"""NIFTY Contract Builder Journal — BUJJI Options OS v3, Engineering
Series 42.

Append-only JSONL, own schema, versioned, own storage path -- entirely
independent of every other journal in the codebase. This journal only
ever records already-produced ContractConstructionResult instances
from nifty_contract_builder/runner.py -- it performs no construction
of its own and makes no decision.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List, Union

from ..trading_brain.nifty_contract_builder.models import ContractConstructionResult
from ..trading_brain.nifty_contract_builder.serialization import (
    result_from_dict,
    result_to_dict,
)

SCHEMA_VERSION = "1.0.0"


class NiftyContractBuilderJournal:
    def __init__(self, path: Union[str, Path]) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, result: ContractConstructionResult) -> None:
        payload = {"schema_version": SCHEMA_VERSION, "result": result_to_dict(result)}
        with open(self._path, "a") as fh:  # Append-only, explicitly.
            fh.write(json.dumps(payload) + "\n")

    def record_many(self, results: List[ContractConstructionResult]) -> None:
        for r in results:
            self.record(r)

    def read_all(self) -> List[ContractConstructionResult]:
        if not self._path.exists():
            return []
        out: List[ContractConstructionResult] = []
        with open(self._path, "r") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    payload = json.loads(line)
                    out.append(result_from_dict(payload["result"]))
        return out
