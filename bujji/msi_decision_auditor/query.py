"""Decision Auditor & Learning Observatory query helpers — Series 99.
Pure, read-only lookups AND Deliverable 8's Observatory Dashboard
summary views. No scoring, no judgement, no ranking anywhere here --
every function below only counts, groups, or formats real recorded
fields."""
from __future__ import annotations

from collections import Counter
from typing import Optional, Sequence

from .models import DecisionOutcomePair, DecisionRecord

from . import taxonomy


def by_id(records: Sequence[DecisionRecord], decision_id: str) -> Optional[DecisionRecord]:
    for r in records:
        if r.decision_id == decision_id:
            return r
    return None


def approved_only(records: Sequence[DecisionRecord]) -> tuple:
    return tuple(r for r in records if r.decision_outcome == taxonomy.DECISION_TRADE_APPROVED)


def no_trade_only(records: Sequence[DecisionRecord]) -> tuple:
    return tuple(r for r in records if r.decision_outcome == taxonomy.DECISION_NO_TRADE)


def daily_view(record: DecisionRecord) -> str:
    """Deliverable 8's "Daily" summary card -- plain text, built only
    from real recorded fields, never a judgement of whether the
    decision was good."""
    lines = [
        record.date,
        "",
        "Decision:", f"  {record.trade_thesis.thesis_type}",
        "Family:", f"  {record.strategy_family or 'NONE (no trade)'}",
        "Confidence:", f"  {record.confidence}",
    ]
    if record.execution_plan is not None:
        lines += ["Execution:", f"  {len(record.execution_plan.execution_steps)}-stage plan "
                                 f"({record.execution_plan.estimated_orders} order(s))"]
    else:
        lines += ["Execution:", "  NONE"]
    return "\n".join(lines)


def daily_view_with_outcome(pair: DecisionOutcomePair) -> str:
    base = daily_view(pair.decision)
    outcome_line = (
        f"Outcome:\n  {pair.outcome.realised_direction} "
        f"({pair.outcome.realised_movement_pct}%), thesis {pair.outcome.thesis_survival.lower()}"
        if pair.outcome.realised_movement_pct is not None else "Outcome:\n  no real data available"
    )
    return base + "\n" + outcome_line


def portfolio_view(records: Sequence[DecisionRecord]) -> str:
    """Deliverable 8's "Portfolio" summary -- total counts, thesis/
    strategy distributions. Counting only, never scoring."""
    total = len(records)
    approved = len(approved_only(records))
    no_trade = len(no_trade_only(records))
    thesis_dist = Counter(r.trade_thesis.thesis_type for r in records)
    family_dist = Counter(r.strategy_family or "NONE" for r in records)
    lines = [
        f"{total} Decisions", "", f"{no_trade} No Trade", f"{approved} Trade", "",
        "Thesis distribution:", *[f"  {t}: {c}" for t, c in thesis_dist.most_common()],
        "", "Strategy distribution:", *[f"  {f}: {c}" for f, c in family_dist.most_common()],
    ]
    return "\n".join(lines)


def outcome_distribution(pairs: Sequence[DecisionOutcomePair]) -> Counter:
    return Counter(p.outcome.realised_direction for p in pairs)


def latest(records: Sequence[DecisionRecord]) -> Optional[DecisionRecord]:
    if not records:
        return None
    return max(records, key=lambda r: r.timestamp)
