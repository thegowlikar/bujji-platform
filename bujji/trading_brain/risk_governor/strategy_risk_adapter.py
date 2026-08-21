"""Strategy Risk Adapter -- BUJJI Options OS v3, Gate E.1, Part 2.

PURPOSE: translate a Strategy Engine proposal (msi_trade_construction's
TradeConstructionAssessment -- the real, current "what trade to build"
output, confirmed via Step 1 inspection) into a D.6
GovernorPipelineContext. Pure mapping. No calculation.

STEP 1 FINDING THAT SHAPES THIS FILE'S DESIGN: TradeConstructionAssessment
has NO quantity field and NO numeric risk figure of its own, BY ITS OWN
DOCUMENTED DESIGN -- its own StrikeLeg docstring states sizing "is
Position Construction's job, out of this package's scope," and its own
module docstring discloses required_margin is "Always None in this
package... no deterministic/replay-safe margin source exists." A
sibling module, msi_entry_bridge.py, already exists to bridge a
TradeConstructionAssessment into Gate A position-group state and Gate
B's assess_defined_risk() (the ORIGINAL single-shot engine.assess()
Governor) -- but that is real, non-trivial machinery (Gate A journal
minting, per-leg role translation, defined-risk formula evaluation)
that is a SEPARATE, already-existing, already-tested integration
targeting a DIFFERENT governor (Gate B's engine.assess(), not Gate
D.6's pipeline). Reimplementing any part of it here would be exactly
the "duplicate risk validation"/"duplicate any calculations" this
phase is explicitly prohibited from doing.

The honest, non-duplicating, "keep it boring" resolution: quantity and
requested_risk are NOT computed here -- they are required, explicit
caller-supplied inputs (standing in for whatever a live caller has
already obtained, e.g. from msi_entry_bridge.py's own defined-risk
assessment, or a later E-phase that performs that wiring). This
adapter never invents a default for either. Fields the proposal CAN
honestly supply are mapped directly, with the exact translation
documented per-field below.
"""
from __future__ import annotations

from datetime import datetime
from typing import Callable, Dict, List, Optional, Tuple

from bujji.msi_trade_construction.models import TradeConstructionAssessment

from .capital_safety_governor import CapitalSafetySnapshot, CapitalSafetyThresholds, ProposedTradeEffect
from .portfolio_risk_aggregator import PortfolioRiskThresholds
from .risk_budget_governor import RiskPolicy
from .position_lifecycle_intelligence import PositionHealthThresholds, build_position_risk_snapshot
from .adaptive_risk_memory import RiskMemoryEntry
from .risk_governor_pipeline import GovernorPipelineContext

Clock = Callable[[], datetime]

# The lifecycle label applied to every freshly-adapted proposal. A
# proposal being evaluated for admission has not yet been minted into
# Gate A at all -- "CONSTRUCTED" is Gate A's OWN existing vocabulary
# term for "exists, not yet filled" (position_group_fold.py's
# LIFECYCLE_CONSTRUCTED), reused verbatim here rather than inventing a
# new label for the same concept.
PROPOSAL_LIFECYCLE_STATE = "CONSTRUCTED"


class InvalidStrategyProposalError(Exception):
    """Raised when the proposal itself cannot be adapted at all (not
    constructed, or constructed with zero legs) -- callers should
    check this before invoking the governor, matching Part 6's "stop
    before governor" rule; the pipeline module catches this itself."""


def adapt_strategy_proposal_to_governor_context(
    proposal: TradeConstructionAssessment,
    desired_quantity: int,
    requested_risk: float,
    capital_snapshot: CapitalSafetySnapshot,
    proposed_trade_effect: ProposedTradeEffect,
    capital_safety_thresholds: Optional[CapitalSafetyThresholds],
    position_groups: List,
    margin_snapshot: Optional[object],
    margin_explanation: Optional[object],
    risk_by_position_group_id: Optional[Dict[str, float]],
    portfolio_risk_thresholds: Optional[PortfolioRiskThresholds],
    risk_policy: RiskPolicy,
    position_health_thresholds: Optional[PositionHealthThresholds],
    market_regime: Optional[str],
    memory_entries: Tuple[RiskMemoryEntry, ...],
    clock: Clock,
) -> GovernorPipelineContext:
    """Fields mapped directly from `proposal` (zero calculation):

      - strategy_type <- proposal.strategy_family. A pure passthrough:
        Step 1 inspection confirmed msi_trade_construction's own
        taxonomy already uses the identical vocabulary Gate B/D use
        (IRON_CONDOR, IRON_FLY, BUTTERFLY, CALENDAR, ...) -- no
        translation table exists or is needed.
      - position_group_id <- proposal.assessment_id. The proposal's own
        existing unique identifier, standing in for a Gate A
        position_group_id that has not been minted yet (minting only
        happens downstream of an ALLOW decision, outside this adapter's
        scope).
      - lifecycle_state <- PROPOSAL_LIFECYCLE_STATE (constant, see above).
      - entry_value / current_value <- proposal.expected_credit_debit,
        used for BOTH fields. At the moment a proposal is evaluated for
        admission no time has elapsed and no fill has occurred, so
        current_value == entry_value by definition (unrealized_pnl==0)
        -- this is a definitional identity for an unfilled proposal, not
        a computation on market data.
      - margin_consumed <- proposal.required_margin (already always
        None per that module's own disclosed limitation -- passed
        through honestly, never fabricated).

    Fields the proposal CANNOT supply (see module docstring) are
    REQUIRED caller parameters, never defaulted: desired_quantity,
    requested_risk. requested_risk stands in as both initial_risk and
    current_risk for the same T=0 reasoning as entry/current value
    above."""
    if not proposal.constructed or not proposal.legs:
        raise InvalidStrategyProposalError(
            f"proposal {proposal.assessment_id!r} is not constructed or has no legs "
            f"(constructed={proposal.constructed}, rejection_reason={proposal.rejection_reason!r})"
        )

    position_snapshot = build_position_risk_snapshot(
        position_group_id=proposal.assessment_id,
        strategy_type=proposal.strategy_family,
        entry_value=proposal.expected_credit_debit,
        current_value=proposal.expected_credit_debit,
        quantity=desired_quantity,
        lifecycle_state=PROPOSAL_LIFECYCLE_STATE,
        initial_risk=requested_risk,
        current_risk=requested_risk,
        margin_consumed=proposal.required_margin,
        clock=clock,
    )

    return GovernorPipelineContext(
        capital_snapshot=capital_snapshot,
        proposed_trade_effect=proposed_trade_effect,
        capital_safety_thresholds=capital_safety_thresholds,
        position_groups=position_groups,
        margin_snapshot=margin_snapshot,
        margin_explanation=margin_explanation,
        risk_by_position_group_id=risk_by_position_group_id,
        portfolio_risk_thresholds=portfolio_risk_thresholds,
        risk_policy=risk_policy,
        desired_quantity=desired_quantity,
        requested_risk=requested_risk,
        position_snapshot=position_snapshot,
        position_health_thresholds=position_health_thresholds,
        strategy_type=proposal.strategy_family,
        market_regime=market_regime,
        memory_entries=memory_entries,
        clock=clock,
    )
