"""Shadow Trading Engine models — Series 100. Frozen dataclasses
throughout (house convention). "Tracking" a position produces a NEW
frozen instance each day (exactly like Position Lifecycle's own
`assess_position_lifecycle`), never a mutated one."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from bujji.msi_trade_construction.models import StrikeLeg


@dataclass(frozen=True)
class Explanation:
    assessment_id: str
    why_entered: Tuple[str, ...]
    why_current_state: Tuple[str, ...]
    why_exit_or_still_open: Tuple[str, ...]
    schema_version: str


@dataclass(frozen=True)
class ShadowPosition:
    shadow_trade_id: str
    decision_id: str
    execution_plan_id: str
    entry_time: str
    entry_date: str
    entry_price: float                  # Real net entry credit/debit, Series 90's own sign convention.
    entry_structure: str                 # "<strategy_family> (<construction_type>)".
    entry_legs: Tuple[StrikeLeg, ...]     # Real legs (Series 90) -- needed to reprice against later real chains.
    position_close_date: str
    simulated_margin: Optional[float]
    lifecycle_state: str
    realised_pnl: Optional[float]        # Set only once `completed=True`.
    unrealised_pnl: Optional[float]      # Real mark-to-market against the current real chain; None if any leg's contract is unpriceable today.
    exit_time: Optional[str]
    exit_reason: Optional[str]           # taxonomy.ALL_EXIT_REASONS, reused directly from Position Lifecycle's own states.
    completed: bool
    explanation: Explanation
    provenance: str
    schema_version: str
