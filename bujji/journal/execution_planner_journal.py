"""Execution Planner Journal — BUJJI Options OS v3, Engineering Series
37.

Append-only JSONL, own schema, versioned, own storage path -- entirely
independent of every other journal in the codebase. This journal only
ever records already-produced ExecutionPlan instances from
execution_planner/runner.py -- it performs no planning of its own and
makes no decision.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List, Union

from ..trading_brain.execution_planner.models import ExecutionPlan
from ..trading_brain.execution_planner.serialization import (
    plan_from_dict,
    plan_to_dict,
)

SCHEMA_VERSION = "1.0.0"


class ExecutionPlannerJournal:
    def __init__(self, path: Union[str, Path]) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, plan: ExecutionPlan) -> None:
        payload = {"schema_version": SCHEMA_VERSION, "plan": plan_to_dict(plan)}
        with open(self._path, "a") as fh:  # Append-only, explicitly.
            fh.write(json.dumps(payload) + "\n")

    def record_many(self, plans: List[ExecutionPlan]) -> None:
        for p in plans:
            self.record(p)

    def read_all(self) -> List[ExecutionPlan]:
        if not self._path.exists():
            return []
        out: List[ExecutionPlan] = []
        with open(self._path, "r") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    payload = json.loads(line)
                    out.append(plan_from_dict(payload["plan"]))
        return out
