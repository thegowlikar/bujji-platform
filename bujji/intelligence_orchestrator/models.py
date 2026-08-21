"""Intelligence Decision Orchestrator models — frozen, immutable
records. `DecisionContext` carries only caller-supplied, already-real
evidence (never recomputed here); `DecisionOutcome`/`DecisionTrace`
narrate what four already-real engines produced, in order.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class DecisionContext:
    """Every field is a real, already-computed object (or None,
    honestly, when not available) -- this orchestrator never builds
    one of these itself. `market_state_assessment` is Trading Brain
    v3's own object (`bujji.trading_brain.market_state.models.
    MarketStateAssessment`), required by `strategy_selector.select()`;
    it is NOT derived from the MSI evidence below -- no verified path
    from MDI/PSI/MSSI/VSB into `evidence_interpreter`/`market_state`
    exists anywhere in this codebase (confirmed by the Phase 1
    architecture audit), so this orchestrator does not fabricate one.
    `premium_behaviour` is accepted for completeness but is not
    consumed by any downstream real engine yet -- carried through and
    disclosed as such in `DecisionTrace.steps`, never silently
    dropped."""

    timestamp: str
    psi: Optional[object] = None
    mssi: Optional[object] = None
    mdi: Optional[object] = None
    mppi: Optional[object] = None
    vsb: Optional[object] = None
    consensus: Optional[object] = None
    liquidity: Optional[object] = None
    volatility_intelligence: Optional[object] = None
    premium_behaviour: Optional[object] = None
    market_state_assessment: Optional[object] = None


@dataclass(frozen=True)
class DecisionOutcome:
    decision_status: str                      # taxonomy.ALL_DECISION_STATUSES.
    selected_strategy: Optional[str]           # Trading Brain v3 strategy_id (strategy_evaluator's own winner), or None.
    confidence: str                            # taxonomy.ALL_CONFIDENCE_LEVELS.
    msi_cross_reference: str                   # Human-readable disclosure of whether selected_strategy's
                                                # MSI family (if a verified mapping exists) is preferred/
                                                # rejected/insufficient/unverified per the market thesis.
    reasons: Tuple[str, ...]


@dataclass(frozen=True)
class DecisionTrace:
    decision_id: str
    timestamp: str

    market_thesis_assessment_id: Optional[str]
    strategy_decision_id: Optional[str]
    ranked_candidates_id: Optional[str]

    steps: Tuple[str, ...]                     # Ordered narration -- what happened, in the order it happened.
    outcome: DecisionOutcome
    supporting_assessment_ids: Tuple[str, ...]
    provenance: str
    schema_version: str
