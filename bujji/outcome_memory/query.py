"""Outcome Memory Query Interface -- Phase 15N. STRICTLY READ-ONLY /
ANALYTICAL. This module answers historical questions about
`OutcomeMemoryRecord`s -- it NEVER writes, NEVER mutates a record, and
NEVER imports anything from strategy selection, decision synthesis,
trade intent, or execution (Step 7's hard boundary, enforced by a
dedicated AST safety test in `test_outcome_memory_safety.py`).

Architecture: `Decision -> Outcome -> Memory` (this module answers
questions ABOUT that history) -- NEVER `Decision -> Outcome -> Memory
-> Decision` (that feedback loop is an explicit future phase with a
much higher trust bar, per the mission's own instruction). Nothing in
this module is called by, or returns a value consumed by, any live
decision path -- it is queried by a human, a report, or a future
phase's own separate, carefully-audited bridge.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

MIN_SAMPLE_SIZE = 3
INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
SUFFICIENT = "SUFFICIENT"

OUTCOME_PROFIT = "PROFIT"
OUTCOME_LOSS = "LOSS"
OUTCOME_BREAKEVEN = "BREAKEVEN"
OUTCOME_UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class OutcomeSample:
    """The result of any query below -- ALWAYS carries `sample_size`
    and `status`, so a caller can never mistake a tiny sample for a
    confident conclusion. `status` is `INSUFFICIENT_HISTORY` whenever
    `sample_size < MIN_SAMPLE_SIZE` -- the `result` payload is still
    returned (transparency), but callers must check `status` before
    treating it as meaningful."""

    filters: Dict[str, object]
    sample_size: int
    status: str  # SUFFICIENT | INSUFFICIENT_HISTORY.
    result: Dict[str, object]

    def to_dict(self) -> dict:
        return {"filters": self.filters, "sample_size": self.sample_size, "status": self.status, "result": self.result}


def filter_records(
    records: List, *, session_id: Optional[str] = None, strategy_family: Optional[str] = None,
    entry_regime: Optional[str] = None, entry_direction: Optional[str] = None,
    underlying_symbol: Optional[str] = None,
) -> List:
    """The ONLY way a caller narrows a cohort -- every filter is an
    EXPLICIT, real field on `OutcomeMemoryRecord`; nothing here ever
    silently mixes incompatible cohorts (Step 9). Passing no filters at
    all returns every known record, unfiltered -- intentional (the
    caller has explicitly asked for the whole population); callers who
    need a comparable cohort must supply the filters themselves."""
    out = []
    for r in records:
        if session_id is not None and r.session_id != session_id:
            continue
        if strategy_family is not None and r.strategy_family != strategy_family:
            continue
        if entry_regime is not None and r.entry_regime != entry_regime:
            continue
        if entry_direction is not None and r.entry_direction != entry_direction:
            continue
        if underlying_symbol is not None and r.underlying_symbol != underlying_symbol:
            continue
        out.append(r)
    return out


def _status_for(sample_size: int) -> str:
    return SUFFICIENT if sample_size >= MIN_SAMPLE_SIZE else INSUFFICIENT_HISTORY


def query_outcome_distribution(records: List, filters: Optional[Dict[str, object]] = None) -> OutcomeSample:
    """"What is the historical outcome distribution for a particular
    setup?" -- `records` should already be the caller's own
    pre-filtered cohort (via `filter_records`); `filters` here is
    purely a label attached to the result for traceability, never
    re-applied."""
    counts = {OUTCOME_PROFIT: 0, OUTCOME_LOSS: 0, OUTCOME_BREAKEVEN: 0, OUTCOME_UNKNOWN: 0}
    for r in records:
        counts[r.outcome_direction] = counts.get(r.outcome_direction, 0) + 1
    return OutcomeSample(filters=filters or {}, sample_size=len(records), status=_status_for(len(records)),
                          result={"outcome_counts": counts})


def query_thesis_invalidation_rate(records: List, filters: Optional[Dict[str, object]] = None) -> OutcomeSample:
    """"How often were directional theses invalidated?" -- reads the
    already-recorded `final_thesis_status`, never re-evaluates a
    thesis itself (that stays exclusively `position_intelligence`'s
    own job, Phase 15F)."""
    counts: Dict[str, int] = {}
    for r in records:
        key = r.final_thesis_status or "UNKNOWN"
        counts[key] = counts.get(key, 0) + 1
    invalidated = counts.get("THESIS_INVALIDATED", 0)
    n = len(records)
    rate = (invalidated / n) if n > 0 else None
    return OutcomeSample(filters=filters or {}, sample_size=n, status=_status_for(n),
                          result={"thesis_status_counts": counts, "invalidation_rate": rate})


def query_management_recommendation_outcomes(records: List, recommendation: str,
                                              filters: Optional[Dict[str, object]] = None) -> OutcomeSample:
    """"When management recommended HEDGE, what happened afterward?" --
    filters to records whose `management_assessments` (inside the
    verbatim `lifecycle_snapshot`) contain at least one assessment with
    the given `recommendation`, then reports THEIR outcome distribution.
    Records with `management_status == NOT_APPLICABLE` (no management
    event ever recorded) are correctly excluded -- never counted as a
    silent "no" answer."""
    matching = []
    for r in records:
        assessments = r.lifecycle_snapshot.get("management_assessments", [])
        if any(a.get("recommendation") == recommendation for a in assessments):
            matching.append(r)
    counts = {OUTCOME_PROFIT: 0, OUTCOME_LOSS: 0, OUTCOME_BREAKEVEN: 0, OUTCOME_UNKNOWN: 0}
    for r in matching:
        counts[r.outcome_direction] = counts.get(r.outcome_direction, 0) + 1
    return OutcomeSample(
        filters={**(filters or {}), "recommendation": recommendation}, sample_size=len(matching),
        status=_status_for(len(matching)), result={"outcome_counts": counts},
    )


def query_attribution_cause_distribution(records: List, filters: Optional[Dict[str, object]] = None) -> OutcomeSample:
    """"Are failures more often caused by selection, timing,
    construction, management, or execution?" -- reads the already-
    computed `primary_cause` (Phase 15J's own causal dimension
    vocabulary), never re-attributes anything."""
    counts: Dict[str, int] = {}
    for r in records:
        key = r.primary_cause or "UNDETERMINED"
        counts[key] = counts.get(key, 0) + 1
    return OutcomeSample(filters=filters or {}, sample_size=len(records), status=_status_for(len(records)),
                          result={"primary_cause_counts": counts})


def query_pnl_summary(records: List, filters: Optional[Dict[str, object]] = None) -> OutcomeSample:
    """Realized P&L across a cohort -- ONLY sums records whose
    `pnl_status == KNOWN`; a record with UNKNOWN P&L contributes
    nothing and is reported separately (`unknown_pnl_count`), never
    silently treated as zero."""
    known = [r for r in records if r.pnl_status == "KNOWN" and r.realized_pnl is not None]
    unknown_count = len(records) - len(known)
    total = sum(r.realized_pnl for r in known) if known else None
    return OutcomeSample(
        filters=filters or {}, sample_size=len(records), status=_status_for(len(records)),
        result={"total_realized_pnl": total, "known_pnl_count": len(known), "unknown_pnl_count": unknown_count},
    )


def query_regime_performance(records: List, entry_regime: str, entry_direction: Optional[str] = None) -> OutcomeSample:
    """"How have these trades performed during RANGING + ...?" --
    convenience wrapper composing `filter_records` +
    `query_outcome_distribution`, never re-implementing filtering."""
    cohort = filter_records(records, entry_regime=entry_regime, entry_direction=entry_direction)
    return query_outcome_distribution(cohort, filters={"entry_regime": entry_regime, "entry_direction": entry_direction})
