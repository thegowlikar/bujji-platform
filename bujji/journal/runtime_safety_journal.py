"""Runtime Safety Journal — BUJJI Options OS v3, Engineering Series 47.

Append-only JSONL, own schema, versioned, own storage path -- entirely
independent of every other journal in the codebase. This journal only
ever records already-produced RuntimeAuthorization instances from
runtime_safety/runner.py -- it performs no authorization of its own
and makes no decision.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List, Union

from ..runtime_safety.models import RuntimeAuthorization
from ..runtime_safety.serialization import authorization_from_dict, authorization_to_dict

SCHEMA_VERSION = "1.0.0"


class RuntimeSafetyJournal:
    def __init__(self, path: Union[str, Path]) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, authorization: RuntimeAuthorization) -> None:
        payload = {"schema_version": SCHEMA_VERSION, "authorization": authorization_to_dict(authorization)}
        with open(self._path, "a") as fh:  # Append-only, explicitly.
            fh.write(json.dumps(payload) + "\n")

    def record_many(self, authorizations: List[RuntimeAuthorization]) -> None:
        for a in authorizations:
            self.record(a)

    def read_all(self) -> List[RuntimeAuthorization]:
        if not self._path.exists():
            return []
        out: List[RuntimeAuthorization] = []
        with open(self._path, "r") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    payload = json.loads(line)
                    out.append(authorization_from_dict(payload["authorization"]))
        return out
