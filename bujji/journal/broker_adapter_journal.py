"""Broker Adapter Journal — BUJJI Options OS v3, Engineering Series 40.

Append-only JSONL, own schema, versioned, own storage path -- entirely
independent of every other journal in the codebase. This journal only
ever records already-produced BrokerExecutionRequest instances from
broker_adapter/runner.py -- it performs no translation of its own and
makes no decision.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List, Union

from ..broker_adapter.models import BrokerExecutionRequest
from ..broker_adapter.serialization import request_from_dict, request_to_dict

SCHEMA_VERSION = "1.0.0"


class BrokerAdapterJournal:
    def __init__(self, path: Union[str, Path]) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, request: BrokerExecutionRequest) -> None:
        payload = {"schema_version": SCHEMA_VERSION, "request": request_to_dict(request)}
        with open(self._path, "a") as fh:  # Append-only, explicitly.
            fh.write(json.dumps(payload) + "\n")

    def record_many(self, requests: List[BrokerExecutionRequest]) -> None:
        for r in requests:
            self.record(r)

    def read_all(self) -> List[BrokerExecutionRequest]:
        if not self._path.exists():
            return []
        out: List[BrokerExecutionRequest] = []
        with open(self._path, "r") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    payload = json.loads(line)
                    out.append(request_from_dict(payload["request"]))
        return out
