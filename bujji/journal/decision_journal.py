"""Decision Journal foundation — Production Pipeline Entry 11 / Sprint 2.

Persists a DecisionSnapshot, keyed by decision_id, alongside (never inside)
the existing TradeJournal. Deliberately separate storage: TradeJournal's
CSV/SQLite schema is untouched by this module. The goal is narrow and
explicit -- a future Learning Layer must be able to reconstruct "what the
system knew, what it decided, what happened" by joining this journal to
TradeJournal on decision_id, without inferring anything.

Not Learning. Not analysis. Not a decision input. Write-only from the
trading loop's perspective, and best-effort: a failure to persist a
snapshot must never block or alter a live trading decision.
"""
from __future__ import annotations

import dataclasses
import json
import logging
from pathlib import Path
from typing import Optional

from ..core.models import DecisionSnapshot


class DecisionJournal:
    """Append-only JSON-lines file, one line per DecisionSnapshot, keyed by
    decision_id. Chosen over a new SQLite table specifically to avoid any
    temptation to alter TradeJournal's existing schema -- this is a wholly
    separate artifact a future Learning Layer joins against, not a part of
    the trade-outcome record itself.
    """

    def __init__(self, path: Path, logger: logging.Logger) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._log = logger

    def record(self, snapshot: DecisionSnapshot, intelligence_reference: Optional[dict] = None) -> None:
        """`intelligence_reference` (Integration Series 1, Sprint 1) is
        purely additive and OPTIONAL -- omitted (the default), the JSON
        row is byte-identical to every row this journal wrote before
        this parameter existed. When supplied (only ever by the
        Intelligence Adapter integration point in `_enter()`, and only
        when that feature flag is enabled), it must contain ONLY
        `snapshot_id`/`publication_id`/`replay_id` -- three reference
        strings, never a duplicate of the snapshot's own contents, never
        a reasoning field of any kind.
        """
        try:
            row = dataclasses.asdict(snapshot)
            row["as_of"] = snapshot.as_of.isoformat()
            # TradeIntention nests inside the snapshot; asdict() already
            # recurses into it, but its own `as_of` datetime needs the same
            # treatment.
            if row.get("intention", {}).get("as_of"):
                row["intention"]["as_of"] = snapshot.intention.as_of.isoformat()
            if intelligence_reference is not None:
                row["intelligence_reference"] = dict(intelligence_reference)
            with open(self._path, "a") as fh:
                fh.write(json.dumps(row, default=str) + "\n")
        except Exception as exc:  # noqa: BLE001 - must never block trading.
            self._log.error("decision_journal_write_failed decision_id=%s err=%s",
                            snapshot.decision_id, exc)

    def get(self, decision_id: str) -> dict | None:
        """Best-effort lookup by decision_id -- for future Learning/replay
        tooling, not read by any live trading path."""
        if not self._path.exists():
            return None
        with open(self._path) as fh:
            for line in fh:
                row = json.loads(line)
                if row.get("decision_id") == decision_id:
                    return row
        return None
