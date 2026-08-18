"""Decision Artifact Journal — append-only JSONL persistence, matching
the established convention used throughout this codebase (PositionGroupJournal,
TradeThesisJournal, StrategySelectorJournal, ...): one JSON object per
line, never rewritten, never deleted. This is the durable record a
future learning system reads -- not a cache, not a scratch log.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional

from .models import DecisionArtifact


class DecisionArtifactJournal:
    def __init__(self, path: str) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if not self._path.exists():
            self._path.touch()

    def append(self, artifact: DecisionArtifact) -> None:
        with open(self._path, "a") as f:
            f.write(json.dumps(artifact.to_dict()) + "\n")

    def read_all(self) -> List[DecisionArtifact]:
        if not self._path.exists():
            return []
        records = []
        with open(self._path) as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(DecisionArtifact.from_dict(json.loads(line)))
        return records

    def find_by_decision_id(self, decision_id: str) -> Optional[DecisionArtifact]:
        for record in self.read_all():
            if record.decision_id == decision_id:
                return record
        return None

    def find_by_learning_tag(self, learning_tag: str) -> List[DecisionArtifact]:
        return [r for r in self.read_all() if r.learning_tag == learning_tag]
