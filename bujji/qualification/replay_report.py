"""Replay Report — BUJJI Options OS v3, Engineering Series 46, Sprint
1.

Deliverable 9: produces a deterministic `QualificationReport`
summarizing a set of replay runs. Qualification utility only -- no
production logic.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Callable, List

from .replay_models import QualificationReport, ReplayRunResult
from .replay_statistics import compute_invariants, compute_summary_counts

Clock = Callable[[], datetime]


def _real_clock() -> datetime:
    return datetime.now()


def build_qualification_report(
    results: List[ReplayRunResult], clock: Clock = _real_clock
) -> QualificationReport:
    """Summarize a set of ReplayRunResults into one deterministic
    report. Given the same run results and the same clock, always
    produces a byte-identical report.
    """
    timestamp = clock().isoformat()
    counts = compute_summary_counts(results)
    invariants = compute_invariants(results)

    run_ids = tuple(r.run_id for r in results)
    seed = "|".join(list(run_ids) + [timestamp])
    report_id = "QR-" + hashlib.md5(seed.encode()).hexdigest()[:16]

    return QualificationReport(
        report_id=report_id,
        total_scenarios=counts["total"],
        passed_scenarios=counts["passed"],
        failed_scenarios=counts["failed"],
        deterministic_scenarios=counts["deterministic"],
        chain_valid_scenarios=counts["chain_valid"],
        invariant_results=invariants,
        scenario_run_ids=run_ids,
        timestamp=timestamp,
        version="1.0.0",
    )
