"""bujji.msi_dynamic_management.query — Series 109. Read-only lookups."""
from __future__ import annotations

from typing import Tuple

from . import taxonomy


def priority_rank(priority: str) -> int:
    return taxonomy.PRIORITY_RANK[priority]


def board_summary(board) -> str:
    """One-line, human-readable summary of all six independent
    decisions -- for dashboards/reports only, never a substitute for
    the individual assessments themselves (Deliverable 3's own 'never
    combine them')."""
    parts = []
    for decision in (board.strike_roll, board.expiry_roll, board.delta_rebalance,
                     board.wing_adjustment, board.strategy_conversion, board.full_exit):
        parts.append(f"{decision.decision_type}={decision.priority}")
    return " | ".join(parts) + f" | transition={'representable(' + board.transition.to_family + ')' if board.transition.representable else 'NOT_REPRESENTABLE'}"


def highest_priority_decision(board):
    """The single highest-ranked decision across all six -- read-only
    convenience, never fed back into any frozen module. Ties broken by
    ALL_DECISION_TYPES order for determinism (never wall-clock/random)."""
    decisions = [board.strike_roll, board.expiry_roll, board.delta_rebalance,
                board.wing_adjustment, board.strategy_conversion, board.full_exit]
    return max(decisions, key=lambda d: (priority_rank(d.priority), -taxonomy.ALL_DECISION_TYPES.index(d.decision_type)))


def mandatory_decisions(board) -> Tuple:
    return tuple(
        d for d in (board.strike_roll, board.expiry_roll, board.delta_rebalance,
                   board.wing_adjustment, board.strategy_conversion, board.full_exit)
        if d.priority == taxonomy.PRIORITY_MANDATORY
    )
