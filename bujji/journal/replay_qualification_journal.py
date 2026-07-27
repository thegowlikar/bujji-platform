"""Replay Qualification Journal — BUJJI Options OS v3, Engineering
Series 46.

Append-only JSONL, own schema, versioned, own storage path -- entirely
independent of every other journal in the codebase. Captures every
stage transition and artifact identifier produced during a replay run
(via `ReplayRunResult.artifact_ids` and `.completed_stages`), plus
qualification reports. This journal performs no replay of its own and
makes no decision.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List, Union

from ..qualification.replay_models import (
    QualificationReport,
    ReplayRunResult,
    qualification_report_from_dict,
    qualification_report_to_dict,
    run_result_from_dict,
    run_result_to_dict,
)

SCHEMA_VERSION = "1.0.0"


class ReplayQualificationJournal:
    def __init__(self, path: Union[str, Path]) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def record_run(self, result: ReplayRunResult) -> None:
        payload = {"schema_version": SCHEMA_VERSION, "kind": "run", "run": run_result_to_dict(result)}
        with open(self._path, "a") as fh:  # Append-only, explicitly.
            fh.write(json.dumps(payload) + "\n")

    def record_report(self, report: QualificationReport) -> None:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "kind": "report",
            "report": qualification_report_to_dict(report),
        }
        with open(self._path, "a") as fh:  # Append-only, explicitly.
            fh.write(json.dumps(payload) + "\n")

    def record_many_runs(self, results: List[ReplayRunResult]) -> None:
        for r in results:
            self.record_run(r)

    def read_all_runs(self) -> List[ReplayRunResult]:
        if not self._path.exists():
            return []
        out: List[ReplayRunResult] = []
        with open(self._path, "r") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    payload = json.loads(line)
                    if payload.get("kind") == "run":
                        out.append(run_result_from_dict(payload["run"]))
        return out

    def read_all_reports(self) -> List[QualificationReport]:
        if not self._path.exists():
            return []
        out: List[QualificationReport] = []
        with open(self._path, "r") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    payload = json.loads(line)
                    if payload.get("kind") == "report":
                        out.append(qualification_report_from_dict(payload["report"]))
        return out
