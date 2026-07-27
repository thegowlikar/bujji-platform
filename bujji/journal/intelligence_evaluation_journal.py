"""Intelligence Evaluation Journal — BUJJI Options OS, Integration
Series 3, Sprint 1.

Append-only JSONL, own schema, own storage -- entirely separate from,
and never modifies, the existing Decision Journal
(`bujji/journal/decision_journal.py`) or the Intelligence Observation
Journal (`bujji/journal/intelligence_observation_journal.py`, Sprint 2).
Records IntelligenceEvaluation comparisons only -- never a trading
decision, never a position, never PnL.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from ..intelligence.mic_adapter.evaluation.policy import IntelligenceEvaluation
from ..intelligence.mic_adapter.evaluation.serialization import (
    intelligence_evaluation_from_dict, intelligence_evaluation_to_dict,
)


class IntelligenceEvaluationJournal:
    def __init__(self, path: Path, logger: logging.Logger) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._log = logger

    def record(self, evaluation: IntelligenceEvaluation) -> None:
        try:
            with open(self._path, "a") as fh:  # Append-only, explicitly.
                fh.write(json.dumps(intelligence_evaluation_to_dict(evaluation)) + "\n")
        except Exception as exc:  # noqa: BLE001 - observational only, must never block trading.
            self._log.error("intelligence_evaluation_journal_write_failed evaluation_id=%s err=%s",
                            evaluation.evaluation_id, exc)

    def read_all(self) -> list[IntelligenceEvaluation]:
        if not self._path.exists():
            return []
        out = []
        with open(self._path, "r") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    out.append(intelligence_evaluation_from_dict(json.loads(line)))
        return out
