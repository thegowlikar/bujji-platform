"""MSI Decision Synthesis Engine journal — append-only, deterministic
recorder for MarketOpportunityAssessment + Explanation pairs
(Deliverable 7: "never rewrite assessments, track every synthesis
revision, replay must reproduce byte-identical assessments").

Mirrors `bujji.market_episode.journal.MarketEpisodeJournal`'s
established pattern exactly: append-only JSONL, own schema, own
storage path, entirely independent of every other journal in the
codebase. Never modifies or deletes a recorded assessment -- every
synthesis run appends a NEW record, never rewrites a prior line.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional

from .models import Explanation, MarketOpportunityAssessment
from .serialization import (
    assessment_from_dict,
    assessment_to_dict,
    explanation_from_dict,
    explanation_to_dict,
)

SCHEMA_VERSION = "1.0.0"


class DecisionSynthesisJournal:
    """Append-only JSONL journal of MarketOpportunityAssessment +
    Explanation pairs. Never modifies or deletes a previously-written
    record."""

    def __init__(self, path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def record_assessment(
        self, assessment: MarketOpportunityAssessment, explanation: Optional[Explanation] = None
    ) -> None:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "kind": "MARKET_OPPORTUNITY_ASSESSMENT",
            "assessment": assessment_to_dict(assessment),
            "explanation": explanation_to_dict(explanation) if explanation is not None else None,
        }
        self._append(payload)

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

    def read_assessments(self) -> List[MarketOpportunityAssessment]:
        """Convenience: read_all() filtered/decoded back into
        MarketOpportunityAssessment objects, in recorded (append) order."""
        return [
            assessment_from_dict(record["assessment"])
            for record in self.read_all()
            if record.get("kind") == "MARKET_OPPORTUNITY_ASSESSMENT"
        ]

    def read_explanations(self) -> List[Explanation]:
        return [
            explanation_from_dict(record["explanation"])
            for record in self.read_all()
            if record.get("kind") == "MARKET_OPPORTUNITY_ASSESSMENT" and record.get("explanation") is not None
        ]
