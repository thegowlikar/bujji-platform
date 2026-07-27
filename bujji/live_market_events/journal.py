"""Live Market Event Engine journal — append-only, deterministic
recorder for MarketEvents (Deliverable 5).

Mirrors `bujji.live_observation.journal.LiveObservationJournal`'s
established pattern: append-only JSONL, own schema, own storage path,
entirely independent of every other journal in the codebase. Never
modifies or deletes a recorded event; deterministic ordering (append
order == recorded order, never re-sorted).

Deliverable 6's publication interface lives in `runner.py`
(`MarketEventPublisher` Protocol) -- this journal is a *recorder*, not
a publisher; a caller may use both (record for audit, publish for
downstream consumers) independently.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List

from .models import MarketEvent
from .serialization import event_from_dict, event_to_dict

SCHEMA_VERSION = "1.0.0"


class MarketEventJournal:
    """Append-only JSONL journal of MarketEvents. Never modifies or
    deletes a previously-written record."""

    def __init__(self, path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def record_event(self, event: MarketEvent) -> None:
        self._append({"schema_version": SCHEMA_VERSION, "kind": "MARKET_EVENT", "event": event_to_dict(event)})

    def _append(self, payload: dict) -> None:
        with open(self._path, "a") as fh:
            fh.write(json.dumps(payload, sort_keys=True, default=repr) + "\n")

    def read_all(self) -> List[dict]:
        if not self._path.exists():
            return []
        out: List[dict] = []
        with open(self._path, "r") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                out.append(json.loads(line))
        return out

    def read_events(self) -> List[MarketEvent]:
        """Convenience: read_all() filtered/decoded back into
        MarketEvent objects, in recorded (append) order."""
        return [event_from_dict(record["event"]) for record in self.read_all() if record.get("kind") == "MARKET_EVENT"]
