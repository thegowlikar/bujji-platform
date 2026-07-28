"""MPC journal — Series 103. Append-only JSONL, mirrors
bujji.live_shadow_operator.journal.OperatorJournal's persistence
discipline (failed_writes tracked, never silently swallowed)."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import List, Optional

from .models import MarketPhenomenaReport
from .serialization import report_from_dict, report_to_dict


class MarketPhenomenaJournal:
    def __init__(self, directory: Path, logger: Optional[logging.Logger] = None) -> None:
        self._dir = Path(directory)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path = self._dir / "market_phenomena_reports.jsonl"
        self._log = logger or logging.getLogger("bujji.msi_market_phenomena.journal")
        self.failed_writes: int = 0

    def record(self, report: MarketPhenomenaReport) -> None:
        entry = report_to_dict(report)
        try:
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, default=str) + "\n")
        except Exception as exc:  # noqa: BLE001 -- persistence must never crash the caller
            self.failed_writes += 1
            self._log.error("mpc_journal_write_failed report_id=%s failed_writes=%d: %s",
                            report.report_id, self.failed_writes, exc, exc_info=True)

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

    def by_day(self, day: str) -> Optional[MarketPhenomenaReport]:
        last = None
        for entry in self._iter_entries():
            if entry["day"] == day:
                last = entry
        return report_from_dict(last) if last else None

    def all(self) -> List[MarketPhenomenaReport]:
        return [report_from_dict(e) for e in self._iter_entries()]
