"""Intelligence Observation Journal — BUJJI Options OS, Integration
Series 2, Sprint 2.

Append-only JSONL, own schema, own storage -- entirely separate from,
and never modifies, the existing Decision Journal
(`bujji/journal/decision_journal.py`, Sprint 2 of the Production
Engineering series). Records ObservationHealth cycles only -- never a
trading decision, never a position, never PnL.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from ..intelligence.mic_adapter.monitor.health import ObservationHealth
from ..intelligence.mic_adapter.monitor.serialization import observation_health_from_dict, observation_health_to_dict


class IntelligenceObservationJournal:
    def __init__(self, path: Path, logger: logging.Logger) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._log = logger

    def record(self, health: ObservationHealth) -> None:
        try:
            with open(self._path, "a") as fh:  # Append-only, explicitly.
                fh.write(json.dumps(observation_health_to_dict(health)) + "\n")
        except Exception as exc:  # noqa: BLE001 - observational only, must never block trading.
            self._log.error("intelligence_observation_journal_write_failed health_id=%s err=%s",
                            health.health_id, exc)

    def read_all(self) -> list[ObservationHealth]:
        if not self._path.exists():
            return []
        out = []
        with open(self._path, "r") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    out.append(observation_health_from_dict(json.loads(line)))
        return out
