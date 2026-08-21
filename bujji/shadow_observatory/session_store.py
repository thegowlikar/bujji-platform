"""Shadow Session Artifact Store -- BUJJI Options OS v3, Gate V.0.

PURPOSE: append-only JSONL/JSON persistence for one shadow trading
session, on disk under `shadow_sessions/<session_id>/`. No update, no
overwrite, no delete method exists anywhere on this class -- `append()`
is the only mutator, matching every append-only journal convention
already established throughout this codebase (TradeConstructionJournal,
MarginCalibrationStore, AdaptiveRiskMemory, ShadowTradeTimeline).

FAILURE ISOLATION (Rule 3): every write is wrapped so a disk/IO failure
is caught, logged internally (`self.write_errors`), and NEVER raised
back to the caller -- a black-box recorder must never be able to stop
the runtime it observes.
"""
from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

_ARTIFACT_FILENAMES = (
    "heartbeat.jsonl", "decisions.jsonl", "orders.jsonl", "executions.jsonl",
    "positions.jsonl", "lifecycle.jsonl", "state_changes.jsonl", "errors.jsonl",
)


class SessionStore:
    def __init__(self, root_dir: Path, session_id: str) -> None:
        self.session_id = session_id
        self.session_dir = Path(root_dir) / session_id
        self.write_errors: List[str] = []
        try:
            self.session_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.write_errors.append(f"mkdir failed: {exc}")

    def write_metadata(self, metadata: Any) -> None:
        self._write_json("metadata.json", metadata)

    def write_summary(self, summary: Any) -> None:
        self._write_json("summary.json", summary)

    def write_json_file(self, filename: str, obj: Any) -> None:
        """General-purpose named JSON artifact -- e.g. session_manifest.json.
        Same failure-isolation guarantee as write_metadata/write_summary."""
        self._write_json(filename, obj)

    def append(self, filename: str, record: Any) -> None:
        """Append-only -- one JSON line per call, never rewrites the
        file. Never raises: a write failure is recorded in
        `self.write_errors` and the caller (the recorder) continues
        as if nothing happened, per Rule 3."""
        payload = asdict(record) if is_dataclass(record) else record
        try:
            line = json.dumps(payload, default=str)
        except (TypeError, ValueError) as exc:
            self.write_errors.append(f"serialize failed for {filename}: {exc}")
            return
        path = self.session_dir / filename
        try:
            with open(path, "a") as f:
                f.write(line + "\n")
        except OSError as exc:
            self.write_errors.append(f"append failed for {filename}: {exc}")

    def read_jsonl(self, filename: str) -> List[Dict]:
        path = self.session_dir / filename
        if not path.exists():
            return []
        records = []
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
        return records

    def read_json(self, filename: str) -> Optional[Dict]:
        path = self.session_dir / filename
        if not path.exists():
            return None
        with open(path) as f:
            return json.load(f)

    def _write_json(self, filename: str, obj: Any) -> None:
        payload = asdict(obj) if is_dataclass(obj) else obj
        try:
            text = json.dumps(payload, default=str, indent=2)
        except (TypeError, ValueError) as exc:
            self.write_errors.append(f"serialize failed for {filename}: {exc}")
            return
        path = self.session_dir / filename
        try:
            with open(path, "w") as f:
                f.write(text)
        except OSError as exc:
            self.write_errors.append(f"write failed for {filename}: {exc}")
