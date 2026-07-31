"""Exit Engine models — Exit Engine v1 sprint. Frozen, immutable
records. No broker code, no market-data fetching, no MTM math lives
here -- this module only names the shape of a decision already made by
engine.py from data it was handed."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class ExitDecision:
    decision_id: str
    should_exit: bool
    reason: str                          # One of taxonomy.ALL_EXIT_REASONS.
    confidence: str                      # One of taxonomy.ALL_CONFIDENCE_LEVELS.
    affected_positions: Tuple[str, ...]  # Symbols this decision applies to -- empty when should_exit is False.
    triggering_rule: Optional[str]       # Same as `reason` when should_exit else None -- kept as a distinct field per the sprint's own spec, not collapsed into `reason`.
    portfolio_valuation_id: Optional[str]  # Which PortfolioValuation this decision was computed from -- for audit/replay, never recomputed.
    decision_trace: str
    timestamp: str
    version: str = "1.0.0"
