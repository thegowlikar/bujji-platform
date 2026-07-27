"""Strategy Selector Journal — BUJJI Options OS v3, Engineering Series
34.

Append-only JSONL, own schema, versioned, own storage path -- entirely
independent of every other journal in the codebase. This journal only
ever records already-produced StrategyDecision instances from
strategy_selector/runner.py -- it performs no selection of its own and
makes no decision.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List, Union

from ..trading_brain.strategy_selector.models import StrategyDecision
from ..trading_brain.strategy_selector.serialization import (
    decision_from_dict,
    decision_to_dict,
)

SCHEMA_VERSION = "1.0.0"


class StrategySelectorJournal:
    def __init__(self, path: Union[str, Path]) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, decision: StrategyDecision) -> None:
        payload = {"schema_version": SCHEMA_VERSION, "decision": decision_to_dict(decision)}
        with open(self._path, "a") as fh:  # Append-only, explicitly.
            fh.write(json.dumps(payload) + "\n")

    def record_many(self, decisions: List[StrategyDecision]) -> None:
        for d in decisions:
            self.record(d)

    def read_all(self) -> List[StrategyDecision]:
        if not self._path.exists():
            return []
        out: List[StrategyDecision] = []
        with open(self._path, "r") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    payload = json.loads(line)
                    out.append(decision_from_dict(payload["decision"]))
        return out
