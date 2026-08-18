"""Intelligence Health Metrics -- Phase 12 Task 3. Pure functions, no
state, no IO. Reads already-computed completeness reports (bujji.
intelligence_completeness) and shadow_validation rows -- never
recomputes or reinterprets a domain's own status/honesty read.
"""
from __future__ import annotations

from collections import Counter
from typing import List, Optional


def understanding_quality(completeness_reports: List[dict]) -> dict:
    """% of domain-cycles COMPLETE/PARTIAL/UNKNOWN across a session.
    Each completeness report's `domains` dict is exactly what
    bujji.intelligence_completeness.engine already produced -- this
    function only tallies it."""
    counts = Counter()
    for report in completeness_reports:
        domains = report.get("domains") if isinstance(report, dict) else None
        if not isinstance(domains, dict):
            continue
        for info in domains.values():
            status = info.get("status") if isinstance(info, dict) else None
            if status:
                counts[status] += 1

    total = sum(counts.values())
    if total == 0:
        return {"complete_pct": None, "partial_pct": None, "unknown_pct": None, "domain_cycles_observed": 0}

    return {
        "complete_pct": round(100.0 * counts.get("COMPLETE", 0) / total, 2),
        "partial_pct": round(100.0 * counts.get("PARTIAL", 0) / total, 2),
        "unknown_pct": round(100.0 * counts.get("UNKNOWN", 0) / total, 2),
        "domain_cycles_observed": total,
    }


def honesty_metrics(
    completeness_reports: List[dict],
    validation_records: Optional[List[dict]] = None,
) -> dict:
    """Critical Phase 12 metric: a system that correctly says UNKNOWN
    must never be treated as worse than one that guesses. Tallies:
    - unknown_correctly_reported: every domain-cycle honestly UNKNOWN
      (by construction, never fabricated -- see intelligence_completeness's
      own design philosophy).
    - false_confidence_events: cycles where honesty_score < 100 -- the
      ONE detectable dishonesty pattern intelligence_completeness itself
      guards against (a domain claiming COMPLETE while its own
      assessment disclosed a contradiction).
    - contradiction_events: cycles where shadow_validation recorded any
      contradiction (consistency-checker warning or narrative-disclosed
      conflict).
    """
    unknown_correctly_reported = 0
    false_confidence_events = 0
    for report in completeness_reports:
        if not isinstance(report, dict):
            continue
        domains = report.get("domains")
        if isinstance(domains, dict):
            unknown_correctly_reported += sum(
                1 for info in domains.values() if isinstance(info, dict) and info.get("status") == "UNKNOWN"
            )
        honesty = report.get("honesty_score")
        if honesty is not None and honesty < 100.0:
            false_confidence_events += 1

    contradiction_events = 0
    if validation_records:
        contradiction_events = sum(1 for r in validation_records if r.get("contradictions"))

    return {
        "unknown_correctly_reported": unknown_correctly_reported,
        "false_confidence_events": false_confidence_events,
        "contradiction_events": contradiction_events,
    }
