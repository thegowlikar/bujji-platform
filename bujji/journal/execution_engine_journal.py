"""Execution Engine Journal — BUJJI Options OS v3, Engineering Series
39.

Append-only JSONL, own schema, versioned, own storage path -- entirely
independent of every other journal in the codebase. This journal only
ever records already-produced ExecutionInstructionSet instances from
execution_engine/runner.py -- it performs no orchestration of its own
and makes no decision.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List, Union

from ..trading_brain.execution_engine.models import ExecutionInstructionSet
from ..trading_brain.execution_engine.serialization import (
    instruction_set_from_dict,
    instruction_set_to_dict,
)

SCHEMA_VERSION = "1.0.0"


class ExecutionEngineJournal:
    def __init__(self, path: Union[str, Path]) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, instruction_set: ExecutionInstructionSet) -> None:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "instruction_set": instruction_set_to_dict(instruction_set),
        }
        with open(self._path, "a") as fh:  # Append-only, explicitly.
            fh.write(json.dumps(payload) + "\n")

    def record_many(self, instruction_sets: List[ExecutionInstructionSet]) -> None:
        for i in instruction_sets:
            self.record(i)

    def read_all(self) -> List[ExecutionInstructionSet]:
        if not self._path.exists():
            return []
        out: List[ExecutionInstructionSet] = []
        with open(self._path, "r") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    payload = json.loads(line)
                    out.append(instruction_set_from_dict(payload["instruction_set"]))
        return out
