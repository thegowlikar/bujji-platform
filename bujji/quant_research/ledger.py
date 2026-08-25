"""The experiment ledger: every trial recorded, not only the ones that worked.

THIS IS THE FILE THAT MAKES THE DEFLATION HONEST. The deflated Sharpe needs N,
the number of trials actually run. If failed experiments are not written down,
N is under-reported, the correction is too weak, and the statistics silently
become the thing they were meant to prevent. An unrecorded experiment is
therefore not a neutral omission -- it corrupts the correction applied to every
later result.

Append-only by construction. Entries are never edited or removed, because the
count of attempts is the evidence.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

LEDGER_SCHEMA = "qr-ledger-1"


class LedgerRefused(RuntimeError):
    pass


class ExperimentLedger:
    """Append-only JSONL record of every experiment attempted."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, evaluation: Dict[str, Any], *,
               recorded_at_ts: float) -> Dict[str, Any]:
        """Append one evaluation. Refuses a result that cannot be reproduced."""
        for key in ("experiment", "dataset_fingerprint", "feature_version",
                    "policy_version", "verdict"):
            if not evaluation.get(key):
                raise LedgerRefused(
                    f"cannot record an experiment with no {key}: a result that "
                    f"cannot be tied to its dataset and code is not reproducible "
                    f"and must not enter the trial count as if it were")
        entry = dict(evaluation)
        entry["schema"] = LEDGER_SCHEMA
        entry["recorded_at_ts"] = recorded_at_ts
        entry["entry_hash"] = hashlib.sha256(
            json.dumps(entry, sort_keys=True, default=str).encode()).hexdigest()
        with open(self.path, "a") as fh:
            fh.write(json.dumps(entry, sort_keys=True, default=str) + "\n")
        return entry

    def entries(self) -> Iterator[Dict[str, Any]]:
        if not self.path.is_file():
            return
        with open(self.path) as fh:
            for line in fh:
                line = line.strip()
                if line:
                    yield json.loads(line)

    def trials_for(self, hypothesis: str) -> int:
        """How many times this hypothesis has been tried, in any form.

        This is the N to pass to the deflation. Counting only the current run's
        configurations would ignore every earlier attempt at the same idea,
        which is precisely the selection bias the correction exists to remove.
        """
        return sum(1 for e in self.entries() if e.get("hypothesis") == hypothesis)

    def summary(self) -> Dict[str, Any]:
        all_e = list(self.entries())
        by_verdict: Dict[str, int] = {}
        for e in all_e:
            by_verdict[e.get("verdict", "?")] = by_verdict.get(
                e.get("verdict", "?"), 0) + 1
        return {
            "total_experiments": len(all_e),
            "by_verdict": by_verdict,
            "distinct_hypotheses": len({e.get("hypothesis") for e in all_e}),
            "note": ("total_experiments is the multiple-testing count. It must "
                     "include failures, or the deflation understates the bar."),
        }
