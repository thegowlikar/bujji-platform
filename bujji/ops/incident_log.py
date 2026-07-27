"""Incident Log (Sprint 4). Operational only -- separate from TradeJournal
and DecisionJournal, exactly as specified. Append-only JSONL, one line per
write (open or resolve), keyed by incident_id."""
from __future__ import annotations

import dataclasses
import json
import logging
from pathlib import Path
from typing import Optional

from ..core.clock import now_ist
from .models import Incident

_INCIDENT_SEQ = {"n": 0}


class IncidentLog:
    def __init__(self, path: Path, logger: logging.Logger) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._log = logger
        self._open_by_subsystem: dict[str, Incident] = {}

    def open_incident(self, severity: str, root_cause: str, affected_subsystem: str) -> Optional[Incident]:
        """One OPEN incident per subsystem at a time -- a second open()
        for an already-open subsystem is a no-op, returning the existing
        incident, so a persistent CRITICAL doesn't open a new incident
        every cycle."""
        if affected_subsystem in self._open_by_subsystem:
            return self._open_by_subsystem[affected_subsystem]
        _INCIDENT_SEQ["n"] += 1
        incident = Incident(
            incident_id=f"INC-{_INCIDENT_SEQ['n']:06d}",
            opened_at=now_ist().isoformat(), severity=severity,
            root_cause=root_cause, affected_subsystem=affected_subsystem,
        )
        self._open_by_subsystem[affected_subsystem] = incident
        self._write(incident)
        return incident

    def resolve_incident(self, affected_subsystem: str, resolution: str) -> Optional[Incident]:
        incident = self._open_by_subsystem.pop(affected_subsystem, None)
        if incident is None:
            return None
        incident.resolution = resolution
        incident.resolved_at = now_ist().isoformat()
        incident.status = "RESOLVED"
        self._write(incident)
        return incident

    def _write(self, incident: Incident) -> None:
        try:
            with open(self._path, "a") as fh:
                fh.write(json.dumps(dataclasses.asdict(incident)) + "\n")
        except Exception as exc:  # noqa: BLE001 - must never block operations.
            self._log.error("incident_log_write_failed incident_id=%s err=%s",
                            incident.incident_id, exc)

    def open_incidents(self) -> list[Incident]:
        return list(self._open_by_subsystem.values())
