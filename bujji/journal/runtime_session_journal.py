"""Runtime Session Journal — BUJJI Options OS v3, Engineering Series
48.

Append-only JSONL, own schema, versioned, own storage path -- entirely
independent of every other journal in the codebase. This journal only
ever records already-produced RuntimeSession instances from
runtime_session/runner.py (and each subsequent lifecycle transition,
if the caller chooses to record them) -- it performs no lifecycle
management of its own and makes no decision.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List, Union

from ..runtime_session.models import RuntimeSession
from ..runtime_session.serialization import session_from_dict, session_to_dict

SCHEMA_VERSION = "1.0.0"


class RuntimeSessionJournal:
    def __init__(self, path: Union[str, Path]) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, session: RuntimeSession) -> None:
        payload = {"schema_version": SCHEMA_VERSION, "session": session_to_dict(session)}
        with open(self._path, "a") as fh:  # Append-only, explicitly.
            fh.write(json.dumps(payload) + "\n")

    def record_many(self, sessions: List[RuntimeSession]) -> None:
        for s in sessions:
            self.record(s)

    def read_all(self) -> List[RuntimeSession]:
        if not self._path.exists():
            return []
        out: List[RuntimeSession] = []
        with open(self._path, "r") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    payload = json.loads(line)
                    out.append(session_from_dict(payload["session"]))
        return out
