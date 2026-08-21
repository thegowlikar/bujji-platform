"""KVE journal — Series 105. Append-only JSONL, mirrors
bujji.live_shadow_operator.journal.OperatorJournal's persistence
discipline (failed_writes tracked, never silently swallowed). Every
real KnowledgeValidationReport write is a new, append-only record --
this IS the real 'Validation history' (Deliverable 6): re-validating
the same hypothesis later just appends a new report with the same
hypothesis_label, and by_hypothesis returns the full real history in
order, never overwriting a prior state."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import List, Optional

from .models import KnowledgeValidationReport
from .serialization import report_from_dict, report_to_dict


class KnowledgeValidationJournal:
    def __init__(self, directory: Path, logger: Optional[logging.Logger] = None) -> None:
        self._dir = Path(directory)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path = self._dir / "knowledge_validation_reports.jsonl"
        self._log = logger or logging.getLogger("bujji.msi_knowledge_validation.journal")
        self.failed_writes: int = 0

    def record(self, report: KnowledgeValidationReport) -> None:
        entry = report_to_dict(report)
        try:
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, default=str) + "\n")
        except Exception as exc:  # noqa: BLE001 -- persistence must never crash the caller
            self.failed_writes += 1
            self._log.error("kve_journal_write_failed validation_id=%s failed_writes=%d: %s",
                            report.validation_id, self.failed_writes, exc, exc_info=True)

    def _iter_entries(self):
        if not self._path.exists():
            return
        with open(self._path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue

    def latest_for(self, hypothesis_label: str) -> Optional[KnowledgeValidationReport]:
        last = None
        for entry in self._iter_entries():
            if entry["hypothesis_label"] == hypothesis_label:
                last = entry
        return report_from_dict(last) if last else None

    def history_for(self, hypothesis_label: str) -> List[KnowledgeValidationReport]:
        return [report_from_dict(e) for e in self._iter_entries() if e["hypothesis_label"] == hypothesis_label]

    def all(self) -> List[KnowledgeValidationReport]:
        return [report_from_dict(e) for e in self._iter_entries()]
