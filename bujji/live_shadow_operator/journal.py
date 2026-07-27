"""Deliverable 2/6 (Sprint 107) + Sprint 112 Deliverables 4/5 --
Persistence, restart recovery, and journal rotation.

Append-only JSON-lines journal, one line per cadence + one per
end-of-day report + one per state snapshot -- mirrors this project's
own established append-only journal convention (Series 68+
`bujji/journal/`, Series 99's `msi_decision_auditor`) rather than
inventing a new persistence model. Never overwrites a prior line; a
restarted process reads the file back to recover `closes_with_ts`,
shadow positions, portfolio state, and which days already completed a
decision cadence (Deliverable 6: "never lose journals"; Sprint 112
Deliverable 4: "restart recovery"; Deliverable 5: "journal rotation").
"""
from __future__ import annotations

import gzip
import json
import shutil
from dataclasses import asdict, is_dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, List, Optional, Sequence, Set, Tuple

from ..live_pipeline_bridge import SessionResult
from ..live_shadow_validation import FullCadenceResult


def _safe(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {k: _safe(v) for k, v in asdict(value).items()}
    if hasattr(value, "assessment_id"):
        return {"assessment_id": value.assessment_id, "repr": repr(value)}
    if isinstance(value, (tuple, list)):
        return [_safe(v) for v in value]
    if isinstance(value, dict):
        return {k: _safe(v) for k, v in value.items()}
    return value


class OperatorJournal:
    """Append-only. `record_*` methods never raise on a serialization
    quirk (best-effort `_safe` coercion) -- persistence must never crash
    the live session, per Deliverable 6's "never lose journals" (a
    journal write failing silently for one field is preferable to it
    taking down the whole operator)."""

    def __init__(self, directory: Path) -> None:
        self._dir = Path(directory)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path = self._dir / "operator_journal.jsonl"
        self._entries: List[dict] = []

    def record_cadence(
        self, day: str, timestamp: str, result: SessionResult, cadence: FullCadenceResult,
        *, decision_latency_seconds: float, cadence_duration_seconds: float,
    ) -> None:
        entry = {
            "type": "CADENCE", "day": day, "timestamp": timestamp,
            "thesis_type": result.thesis.thesis_type if result.thesis else None,
            "conviction": result.thesis.conviction if result.thesis else None,
            "selected_strategy_family": cadence.selection.selected_strategy_family,
            "decision_id": cadence.decision.decision_id,
            "shadow_position_id": (cadence.shadow_position.shadow_trade_id
                                   if cadence.shadow_position else None),
            "closes_with_ts": list(result.closes_with_ts),
            "decision_latency_seconds": decision_latency_seconds,
            "cadence_duration_seconds": cadence_duration_seconds,
        }
        self._append(entry)

    def record_end_of_day(self, day: str, outcome: Any) -> None:
        self._append({"type": "END_OF_DAY", "day": day, "report": _safe(outcome)})

    def record_state_snapshot(self, day: str, *, admitted_trades: Sequence[Any], open_positions: Sequence[dict]) -> None:
        """Sprint 112 Deliverable 4: persists everything a restart needs
        to recover beyond `closes_with_ts` -- real `AdmittedTrade`s
        (portfolio state, Series 91) and real open shadow-position
        tracking dicts (entry_thesis/strategy_family/construction_type/
        position_close_date/legs). Written once per day, after that
        day's cadence completes."""
        self._append({
            "type": "STATE_SNAPSHOT", "day": day,
            "admitted_trades": [_safe(t) for t in admitted_trades],
            "open_positions": [_safe(p) for p in open_positions],
        })

    def _append(self, entry: dict) -> None:
        self._entries.append(entry)
        try:
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, default=str) + "\n")
        except Exception:  # noqa: BLE001 -- persistence must never crash the session
            pass

    def flush(self) -> None:
        pass  # every _append already wrote through; nothing buffered.

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

    def read_last_closes_with_ts(self) -> Sequence[Tuple[str, float]]:
        """Restart recovery: read the journal file back (works across
        process restarts, unlike the in-memory `self._entries`) and
        return the most recent CADENCE entry's real `closes_with_ts`."""
        last_closes: Optional[list] = None
        for entry in self._iter_entries():
            if entry.get("type") == "CADENCE" and entry.get("closes_with_ts"):
                last_closes = entry["closes_with_ts"]
        if last_closes is None:
            return ()
        return tuple((c[0], c[1]) for c in last_closes)

    def read_last_state_snapshot(self) -> Optional[dict]:
        """Sprint 112 Deliverable 4: the most recent `STATE_SNAPSHOT`
        entry, as real plain dicts (this project's own established
        "best-effort, not full nested rehydration" serialization
        convention -- see `msi_strategy_optimization/serialization.py`).
        A caller reconstructing a live session reads
        `admitted_trades`/`open_positions` from this to recover
        portfolio state and option-chain-dependent tracking without
        re-deriving it from scratch."""
        last = None
        for entry in self._iter_entries():
            if entry.get("type") == "STATE_SNAPSHOT":
                last = entry
        return last

    def completed_cadence_days(self) -> Set[str]:
        """Sprint 112 Deliverable 4 ('decision cadence state'): the set
        of real days that already produced a CADENCE entry -- a
        restarted operator uses this to avoid re-running (and therefore
        never risks double-recording) a day's cadence that already
        completed before the restart."""
        return {entry["day"] for entry in self._iter_entries() if entry.get("type") == "CADENCE"}

    def entries(self) -> Tuple[dict, ...]:
        return tuple(self._entries)

    # -- Sprint 112 Deliverable 5: journal rotation ------------------------
    def rotate(self, *, archive_dir: Optional[Path] = None, retain_days: int = 30, today: Optional[date] = None) -> Optional[Path]:
        """Automatic daily rotation: gzip-compresses the current journal
        into `archive_dir` (default: `<journal_dir>/archive`), verifies
        the archive's integrity by reading it back and comparing line
        counts, then starts a fresh, empty journal file -- the CURRENT
        file is never overwritten in place, only renamed-then-compressed.
        Returns the archive path, or `None` if there was nothing to
        rotate (no journal file / empty file)."""
        if not self._path.exists() or self._path.stat().st_size == 0:
            return None
        archive_dir = Path(archive_dir) if archive_dir else (self._dir / "archive")
        archive_dir.mkdir(parents=True, exist_ok=True)
        stamp = (today or date.today()).isoformat()

        with open(self._path, encoding="utf-8") as f:
            pre_rotation_lines = sum(1 for line in f if line.strip())

        archive_path = archive_dir / f"operator_journal_{stamp}.jsonl.gz"
        suffix = 1
        while archive_path.exists():
            suffix += 1
            archive_path = archive_dir / f"operator_journal_{stamp}_{suffix}.jsonl.gz"

        with open(self._path, "rb") as src, gzip.open(archive_path, "wb") as dst:
            shutil.copyfileobj(src, dst)

        # Archive validation (Deliverable 5): read the compressed
        # archive back and confirm every real line survived rotation.
        with gzip.open(archive_path, "rt", encoding="utf-8") as f:
            post_rotation_lines = sum(1 for line in f if line.strip())
        if post_rotation_lines != pre_rotation_lines:
            raise IOError(
                f"journal rotation integrity check FAILED: {pre_rotation_lines} real lines before rotation, "
                f"{post_rotation_lines} recovered from {archive_path} -- the original journal file was left "
                "untouched (never overwritten before this check passes)."
            )

        self._path.unlink()
        self._path.touch()
        self._entries = []
        self._prune_archives(archive_dir, retain_days=retain_days, today=today or date.today())
        return archive_path

    def _prune_archives(self, archive_dir: Path, *, retain_days: int, today: date) -> None:
        cutoff = today - timedelta(days=retain_days)
        for path in archive_dir.glob("operator_journal_*.jsonl.gz"):
            stem = path.name.removeprefix("operator_journal_").split(".jsonl.gz")[0]
            date_part = stem.split("_")[0]
            try:
                archived_date = date.fromisoformat(date_part)
            except ValueError:
                continue  # non-conforming filename -- never guess, never delete.
            if archived_date < cutoff:
                path.unlink()
