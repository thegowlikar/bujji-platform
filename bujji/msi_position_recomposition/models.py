"""bujji.msi_position_recomposition.models — Series 110. Frozen dataclasses."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class Explanation:
    """Deliverable 9: six named facets, never folded into one generic
    'why'."""
    assessment_id: str
    why_this_strike: Tuple[str, ...]
    why_this_expiry: Tuple[str, ...]
    why_this_width: Tuple[str, ...]
    why_keep_this_leg: Tuple[str, ...]
    why_replace_that_leg: Tuple[str, ...]
    why_not_rebuild_everything: Tuple[str, ...]
    schema_version: str


@dataclass(frozen=True)
class CloseLeg:
    """A leg being closed. Deliberately NOT a `StrikeLeg` (Series 90) --
    a currently-held leg (`HeldLeg`, Series 91) carries no delta/premium/
    role/ratio/reasoning, and fabricating those fields here would
    misrepresent real evidence as computed when it was not. `OpenLeg`
    below IS a real `StrikeLeg`, reused by identity, since new legs come
    directly from a real Trade Construction call."""
    option_type: str
    strike: float
    expiry: str
    side: str


@dataclass(frozen=True)
class ExecutionDelta:
    """Deliverable 6: directly consumable by Execution Planning (Series
    98, frozen) -- `open_legs` are real `StrikeLeg` objects (Series 90's
    own model, reused by identity), the exact type
    `ExecutionPlanAssessment.order_sequence` already expects."""
    close_legs: Tuple[CloseLeg, ...]
    open_legs: Tuple[object, ...]   # Tuple[StrikeLeg, ...] -- typed loosely to avoid a hard model dependency loop
    kept_legs: Tuple[CloseLeg, ...]


@dataclass(frozen=True)
class RecompositionAssessment:
    assessment_id: str
    possible: bool
    reason_not_possible: Optional[str]
    trigger: Optional[str]              # taxonomy.TRIGGER_* -- the Series 109 decision type that produced this
    from_family: str
    to_family: Optional[str]            # same as from_family unless this is a conversion
    new_expiry: Optional[str]
    target_delta: Optional[float]
    wing_width: Optional[float]
    expected_move: Optional[float]
    execution_delta: Optional[ExecutionDelta]
    strike_replacements: int
    expiry_replacements: int
    legs_reused: int
    full_rebuild: bool
    timestamp: str
    explanation: Explanation
    provenance: str
    schema_version: str
