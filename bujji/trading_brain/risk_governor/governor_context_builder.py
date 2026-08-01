"""Live Portfolio Context Builder -- BUJJI Options OS v3, Gate E.2.

PURPOSE: assemble a complete GovernorPipelineContext from Bujji's
existing state, so the already-complete D.1-D.6 Risk Governor pipeline
and E.1's Strategy Adapter can be invoked with real data instead of
hand-built test fixtures. This module introduces NO new risk logic,
NO new margin logic, NO new sizing logic, NO new thresholds, and NO
new adapter -- it is a data-assembly layer only.

================================================================
PART 1 -- MANDATORY INSPECTION FINDINGS (read, not summarized, before
writing any code below). One row per GovernorPipelineContext field
plus the strategy-proposal inputs E.1's adapter itself requires.
================================================================

  capital_snapshot (CapitalSafetySnapshot)
    Owner: no module in this codebase currently tracks LIVE account
    telemetry (funds/margin/daily P&L) into a CapitalSafetySnapshot --
    D.1 (capital_safety_governor.py) only CONSUMES this shape, it does
    not produce it. CALLER-SUPPLIED, always. This builder never
    fabricates it.

  proposed_trade_effect (ProposedTradeEffect)
    Owner: none. Nothing in this codebase automatically derives
    additional_margin/additional_max_loss for a hypothetical trade --
    E.1's own adapter already required requested_risk as an explicit
    caller input for exactly this reason (TradeConstructionAssessment
    structurally excludes any numeric risk figure -- see its own
    StrikeLeg docstring: sizing "is Position Construction's job, out
    of this package's scope"). CALLER-SUPPLIED.

  capital_safety_thresholds / portfolio_risk_thresholds / risk_policy /
  position_health_thresholds
    Owner: D.1 / D.2 / D.3 / D.4 respectively (each ships its own
    dataclass with its own illustrative defaults, applied internally
    by that governor when None is passed). CALLER-SUPPLIED config,
    Optional -- passing None is NOT inventing a default, it is
    honestly deferring to each governor's OWN already-established
    default object.

  position_groups (List[PositionGroupState])
    Owner: Gate A's Position Group Journal
    (bujji/journal/position_group_journal.py) -- AUTHORITATIVE, real,
    durable (SQLite). Its own public read API is `read_all_group_ids()`
    (a `SELECT DISTINCT`, so duplicate ids are structurally impossible
    from this source) and `read_events(position_group_id)`, combined
    with `position_group_fold.fold()` (both already-existing,
    unmodified functions) to produce each PositionGroupState. NO
    "Position Registry" or "Portfolio Registry" module exists anywhere
    in this codebase (verified via an exhaustive class-name grep) --
    the Journal itself IS the registry. This is the ONE field this
    Builder actually gathers from a live source rather than accepting
    as a caller parameter, and it does so via pure iteration over
    existing read functions -- zero new logic.

  margin_snapshot / margin_explanation
    Owner: Gate C.1's SimulatedMarginProvider (or Gate C.3's
    FyersMarginProvider for a live/broker-backed figure) --
    get_portfolio_margin_with_explanation(legs, clock). CRITICAL
    FINDING, STOPPED ON per Part 1's own instruction: producing these
    requires `legs: List[MarginLegRequest]`, which itself requires
    contract/side/reference-price data PER CLIENT ORDER ID --
    whole_book_margin_provider.project_whole_book_to_margin_legs()'s
    own docstring confirms "Gate A's own journal deliberately carries
    NO contract data and NO buy/sell side." Cross-checked directly
    against msi_entry_bridge.py's CONSTRUCTED event payload (the only
    place this data briefly exists, held only in local in-memory
    variables at construction time): its payload is
    `{"contract_client_order_map", "requested_quantities", "actions",
    "target_position_group_ids", "target_contract_ids",
    "flip_link_ids"}` -- no contract symbol, no side, no price is ever
    persisted anywhere queryable after construction. THERE IS NO
    AUTHORITATIVE, DURABLE SOURCE THIS BUILDER COULD READ TO
    RECONSTRUCT A MARGIN SNAPSHOT FROM GATE A STATE ALONE. Per Part
    1's explicit instruction not to invent reconciliation logic, this
    Builder does NOT attempt to rebuild margin data from the journal.
    margin_snapshot/margin_explanation remain CALLER-SUPPLIED (already
    computed by Gate C's own provider elsewhere), and the Builder only
    VALIDATES their presence/consistency against the gathered
    position_groups (Part 5), never recomputes them.

  risk_by_position_group_id (Dict[str, float])
    Owner: none -- confirmed via direct re-reading of
    portfolio_risk_aggregator.py's own module docstring/prior session
    findings: "risk_by_position_group_id is still an external caller
    responsibility, not self-contained" (an existing, pre-established
    finding from D.2/D.3, not new). CALLER-SUPPLIED; validated for
    referential integrity against gathered position_groups (Part 5),
    never computed.

  desired_quantity / requested_risk
    Owner: none -- same structural gap E.1 already documented
    (TradeConstructionAssessment excludes both by design). CALLER-
    SUPPLIED, matching E.1's own established precedent exactly.

  position_snapshot / strategy_type
    Owner: derived from the Strategy Engine's own proposal
    (TradeConstructionAssessment.strategy_family/expected_credit_debit)
    via E.1's OWN adapter (strategy_risk_adapter.
    adapt_strategy_proposal_to_governor_context). This Builder calls
    that EXISTING function directly rather than re-implementing any
    part of its field mapping -- "No new adapters" (Part 7) is
    satisfied by delegation, not duplication.

  market_regime
    Owner: none currently wired into this pipeline. market_regime_
    adapter.py (Gate D.5) exists and CAN translate an already-computed
    RegimeBrain/msi_volatility_structure reading, but nothing in this
    codebase automatically feeds a live regime reading into this
    context today. CALLER-SUPPLIED (honestly disclosed real gap, not
    papered over with a fabricated "UNKNOWN").

  memory_entries
    Owner: Gate D.5's AdaptiveRiskMemory -- caller supplies the STORE
    (not pre-filtered entries); this Builder calls the store's own
    EXISTING `lookup(strategy_type=...)` method itself (pure
    delegation to an already-existing method with the correct key,
    not new filtering logic).

  clock
    Owner: caller, shared across the whole build + downstream pipeline
    run for a single consistent evaluation instant.

  Calibration Store (Gate C.5's MarginCalibrationStore)
    NO FIELD ON GovernorPipelineContext REFERENCES CALIBRATION AT ALL,
    and no other object in this codebase links a MarginSnapshot back
    to a specific MarginCalibrationSample -- MarginSnapshot.
    margin_source is a plain string literal set by whichever provider
    produced it; the calibration store is an entirely separate,
    human-operated audit trail (margin_calibration_runner.py) with no
    live back-reference from any MarginSnapshot object. STOPPED ON:
    inventing a staleness threshold or a synthetic linkage field here
    would be exactly the "new heuristic"/"new calibration logic" Part
    7 forbids. The ONE real, non-invented check this Builder CAN
    perform is entirely OPT-IN: if a caller explicitly supplies BOTH a
    calibration_store and a calibration_sample_id (asserting "this
    margin_snapshot was calibrated against this specific sample"),
    the Builder verifies that sample_id actually exists in the store
    -- a real referential-integrity check, never a fabricated
    freshness rule. When neither is supplied (the default), this
    check is skipped entirely, not defaulted to any pass/fail verdict.

  "Margin snapshot matches account" / "Portfolio IDs match" (Part 5's
  own example checks)
    STOPPED ON: neither MarginSnapshot nor CapitalSafetySnapshot nor
    PortfolioRiskSnapshot carries any account_id/portfolio_id field
    anywhere in this codebase (verified by re-reading all three
    dataclasses in full). There is no shared identifier to reconcile.
    Implementing this check would require inventing a field that does
    not exist -- explicitly prohibited. Both examples are therefore
    NOT implemented as literal ID-comparison checks; this is disclosed
    here rather than silently skipped.
================================================================

PART 3 -- DATA OWNERSHIP SUMMARY: this Builder owns NONE of the
domain objects it assembles. It owns exactly one thing: the ORDER in
which existing objects are read, validated for referential
consistency against each other, and handed to E.1's existing adapter.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Dict, List, Optional, Tuple, Union

from bujji.msi_trade_construction.models import TradeConstructionAssessment
from bujji.msi_trade_construction.taxonomy import SUPPORTED_FAMILIES
from bujji.journal.position_group_journal import PositionGroupJournal
from bujji.trading_brain.risk_governor.position_group_fold import PositionGroupState, fold

from .capital_safety_governor import CapitalSafetySnapshot, CapitalSafetyThresholds, ProposedTradeEffect
from .portfolio_risk_aggregator import PortfolioRiskThresholds
from .risk_budget_governor import RiskPolicy
from .position_lifecycle_intelligence import PositionHealthThresholds
from .adaptive_risk_memory import AdaptiveRiskMemory, RiskMemoryEntry
from .margin_calibration_runner import MarginCalibrationStore
from .risk_governor_pipeline import GovernorPipelineContext
from .strategy_risk_adapter import InvalidStrategyProposalError, adapt_strategy_proposal_to_governor_context

Clock = Callable[[], datetime]

_ACTIVE_LIFECYCLE_STATES = ("OPEN", "PARTIALLY_OPEN", "CONSTRUCTED")


@dataclass(frozen=True)
class ContextBuildFailure:
    """Part 6: every failure names exactly which object, which field,
    and why -- never a generic 'invalid context' message."""
    object_name: str
    field_name: str
    reason: str

    def explanation(self) -> str:
        return f"{self.object_name}.{self.field_name}: {self.reason} Context cannot be assembled."


class GovernorContextBuilder:
    """Part 2: read existing objects -> collect values -> assemble
    GovernorPipelineContext -> return. Nothing else. Never classifies,
    never calculates risk/margin/drawdown/sizing/budgets/memory scores
    -- all of that already exists elsewhere and is only ever consumed
    here, never re-derived."""

    def __init__(self, journal: PositionGroupJournal) -> None:
        self._journal = journal

    def _gather_position_groups(self) -> Union[Tuple[PositionGroupState, ...], ContextBuildFailure]:
        group_ids = self._journal.read_all_group_ids()
        seen = set()
        states: List[PositionGroupState] = []
        for group_id in group_ids:
            if group_id in seen:
                return ContextBuildFailure(
                    "PositionGroupJournal", group_id, f"duplicate position_group_id {group_id!r} encountered.",
                )
            seen.add(group_id)
            events = self._journal.read_events(group_id)
            state = fold(events) if events else None
            if state is not None:
                states.append(state)
        return tuple(states)

    def build(
        self,
        proposal: TradeConstructionAssessment,
        desired_quantity: int,
        requested_risk: float,
        capital_snapshot: CapitalSafetySnapshot,
        proposed_trade_effect: ProposedTradeEffect,
        capital_safety_thresholds: Optional[CapitalSafetyThresholds],
        margin_snapshot: Optional[object],
        margin_explanation: Optional[object],
        risk_by_position_group_id: Optional[Dict[str, float]],
        portfolio_risk_thresholds: Optional[PortfolioRiskThresholds],
        risk_policy: RiskPolicy,
        position_health_thresholds: Optional[PositionHealthThresholds],
        market_regime: Optional[str],
        memory: AdaptiveRiskMemory,
        clock: Clock,
        calibration_store: Optional[MarginCalibrationStore] = None,
        calibration_sample_id: Optional[str] = None,
    ) -> Union[GovernorPipelineContext, ContextBuildFailure]:
        # -- Gather position_groups from the ONE authoritative source ---
        gathered = self._gather_position_groups()
        if isinstance(gathered, ContextBuildFailure):
            return gathered
        position_groups = list(gathered)
        active_ids = {s.position_group_id for s in position_groups if s.lifecycle_state in _ACTIVE_LIFECYCLE_STATES}

        # -- Part 5: consistency validation, fail closed, never repair --
        if active_ids and margin_snapshot is None:
            return ContextBuildFailure(
                "MarginSnapshot", "margin_snapshot",
                f"{len(active_ids)} active position group(s) exist in the Position Group Journal "
                f"but no MarginSnapshot was supplied.",
            )

        if risk_by_position_group_id:
            for pg_id in risk_by_position_group_id:
                if pg_id not in active_ids:
                    return ContextBuildFailure(
                        "risk_by_position_group_id", pg_id,
                        f"position group {pg_id!r} referenced by risk_by_position_group_id does not exist "
                        f"among active Position Group Journal groups.",
                    )

        if memory is not None:
            for entry in memory.all_entries():
                if entry.strategy_type not in SUPPORTED_FAMILIES:
                    return ContextBuildFailure(
                        "AdaptiveRiskMemory", entry.entry_id,
                        f"references unrecognized strategy_type {entry.strategy_type!r}, not one of the "
                        f"{len(SUPPORTED_FAMILIES)} Strategy Engine-supported families.",
                    )

        if calibration_sample_id is not None:
            if calibration_store is None:
                return ContextBuildFailure(
                    "MarginCalibrationStore", "calibration_store",
                    f"calibration_sample_id {calibration_sample_id!r} was supplied but no "
                    f"MarginCalibrationStore was given to verify it against.",
                )
            if calibration_store.get(calibration_sample_id) is None:
                return ContextBuildFailure(
                    "MarginCalibrationStore", calibration_sample_id,
                    f"calibration_sample_id {calibration_sample_id!r} does not exist in the supplied "
                    f"MarginCalibrationStore.",
                )

        # -- Delegate proposal-derived assembly to E.1's own adapter -----
        memory_entries: Tuple[RiskMemoryEntry, ...] = (
            memory.lookup(strategy_type=proposal.strategy_family) if memory is not None else ()
        )
        try:
            return adapt_strategy_proposal_to_governor_context(
                proposal, desired_quantity, requested_risk, capital_snapshot, proposed_trade_effect,
                capital_safety_thresholds, position_groups, margin_snapshot, margin_explanation,
                risk_by_position_group_id, portfolio_risk_thresholds, risk_policy, position_health_thresholds,
                market_regime, memory_entries, clock,
            )
        except InvalidStrategyProposalError as exc:
            return ContextBuildFailure("TradeConstructionAssessment", "proposal", str(exc))
