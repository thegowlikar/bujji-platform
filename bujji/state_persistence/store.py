"""State Persistence — append-only event store. Pure IO, no broker, no
execution, no market-data dependency (hydration NEVER needs a live
connection -- it only ever reads its own previously-written file).

Atomicity: each event is written as ONE `write()` call of a single
complete JSON line + newline, flushed and fsync'd before returning --
on POSIX, a single `write()` of less than PIPE_BUF bytes is atomic, so
a crash mid-write can only ever produce a truncated LAST line, never a
corrupted line in the middle of the file. `read_events` explicitly
handles exactly that case (a truncated/malformed trailing line is
skipped, never raised, never silently included).
"""
from __future__ import annotations

import json
import os
from typing import Iterator, List

from .models import PersistedEvent


class EventStore:
    """One append-only JSONL file. Safe to construct repeatedly against
    the same path (e.g. across a process restart) -- never truncates or
    overwrites existing content."""

    def __init__(self, path: str) -> None:
        self._path = path

    @property
    def path(self) -> str:
        return self._path

    def append(self, event: PersistedEvent) -> None:
        line = json.dumps(event.to_dict()) + "\n"
        directory = os.path.dirname(self._path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(self._path, "a") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())

    def read_events(self) -> Iterator[PersistedEvent]:
        """Yields every structurally valid event in file order. A
        malformed or truncated line (e.g. a torn final write) is
        silently skipped here -- callers that need to COUNT malformed
        lines (for an honest RecoveryReport) should use
        `read_events_with_diagnostics` instead."""
        for event, _malformed in self.read_events_with_diagnostics():
            if event is not None:
                yield event

    def read_events_with_diagnostics(self):
        """Yields (event_or_None, malformed_line_or_None) pairs -- exactly
        one of the two is non-None per yielded item. Never raises on a
        missing file (an unstarted session has genuinely no events yet)."""
        if not os.path.exists(self._path):
            return
        with open(self._path) as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                    event = PersistedEvent.from_dict(d)
                except (json.JSONDecodeError, KeyError, TypeError):
                    yield None, line
                    continue
                yield event, None


def deduplicated_events(events: List[PersistedEvent]) -> List[PersistedEvent]:
    """First occurrence wins for a repeated `event_id` -- idempotent
    replay protection. Order-preserving."""
    seen = set()
    out = []
    for e in events:
        if e.event_id in seen:
            continue
        seen.add(e.event_id)
        out.append(e)
    return out
