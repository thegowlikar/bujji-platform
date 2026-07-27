"""Runtime Execution Journal — BUJJI Options OS v3, Engineering Series
45.

Append-only JSONL, own schema, versioned, own storage path -- entirely
independent of every other journal in the codebase. This journal only
ever records already-produced ExecutionSession instances from
runtime_execution/runner.py -- it performs no orchestration of its own
and makes no decision.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List, Union

from ..runtime_execution.models import ExecutionSession
from ..runtime_execution.serialization import session_from_dict, session_to_dict

SCHEMA_VERSION = "1.0.0"


class RuntimeExecutionJournal:
    def __init__(self, path: Union[str, Path]) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, session: ExecutionSession) -> None:
        payload = {"schema_version": SCHEMA_VERSION, "session": session_to_dict(session)}
        with open(self._path, "a") as fh:  # Append-only, explicitly.
            fh.write(json.dumps(payload) + "\n")

    def record_many(self, sessions: List[ExecutionSession]) -> None:
        for s in sessions:
            self.record(s)

    def read_all(self) -> List[ExecutionSession]:
        if not self._path.exists():
            return []
        out: List[ExecutionSession] = []
        with open(self._path, "r") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    payload = json.loads(line)
                    out.append(session_from_dict(payload["session"]))
        return out
