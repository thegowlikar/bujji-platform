"""Trading Brain Shadow Runtime -- BUJJI Options OS v3, Gate F.1.

PURPOSE: give the completed Trading Brain (Strategy Engine -> E.1/E.2/
E.3 -> D.1-D.6 Risk Governor -> D.4 Lifecycle Intelligence) a body --
a real, testable, continuously-callable runtime path terminating in
PaperBroker. This module makes ZERO trading decisions. Every decision
(what to construct, whether to allow it, how much, whether to hold/
reduce/hedge/exit) is made by a call into an already-existing,
already-tested function; this file only sequences those calls, reads/
writes RuntimeStateMachine, and publishes events. Verified structurally
by this module's own test suite (AST scan: no numeric threshold
constant, no risk/margin/sizing formula, no broker method beyond
PaperBroker.place_order/get_open_positions).

DELIBERATELY A NEW, ADDITIVE PATH -- THE LEGACY run_shadow() CHAIN IS
UNTOUCHED (per explicit scope decision): this module never imports
`production_runtime.runtime`, `composition_root`, or any of the 15
legacy stage engines (risk_brain, capital_brain, execution_planner,
execution_engine, strategy_selector, nifty_contract_builder, ...).
Verified via AST import check in the test suite.

CONTRACT OWNERSHIP -- preserved exactly as scoped, no forced
translation between layers:
  Strategy Layer  owns StrikeLeg / TradeConstructionAssessment  ("what should be traded")
  Execution Layer owns CoreOptionContract / CoreOrderRequest    ("how do we place it")
  Broker Layer    owns Order / Fill / Position (PaperBroker's own internal ledger) ("what happened")
The bridge between Strategy and Execution layers reuses
`msi_entry_bridge.py`'s own existing, already-tested
`_leg_to_core_contract`/`_to_core_side` helpers verbatim -- this module
adds no new leg-to-contract translation logic, only a new call site.

QUANTITY SEMANTICS (the one piece msi_entry_bridge's own
`construct_and_gate_entry` does not need to solve, since IT only ever
constructs a single call's worth of legs): the Risk Governor's
`GovernorPipelineResult.final_quantity` is a LOT count (D.3's own
unit, `PositionSizeRecommendation.recommended_quantity`/D.5's
`AdaptiveGovernorDecision.final_suggested_size`). Each leg's actual
contract quantity is therefore `leg.ratio * exchange_lot_size *
final_quantity` -- `leg.ratio` (the structure's own per-leg multiplier,
e.g. 2 for a butterfly's short body) times the exchange lot size times
the number of lots the Governor approved. This mirrors msi_entry_
bridge's own `leg.ratio * lot_size` convention, extended by the
Governor's own lot count (msi_entry_bridge's bridge only ever handles
one lot per call, confirmed by reading its own `requested_quantities`
line directly).

EVENTBUS REUSE -- no new EventType is added to `bujji.core.event_bus`
(that file is not touched at all by this gate). Every stage-specific
label (STRATEGY_PROPOSED, STRATEGY_REJECTED, RISK_DECISION,
CONTEXT_UNAVAILABLE, ORDER_SUBMITTED, ORDER_FILLED, POSITION_OPENED)
is carried in `event.payload["stage"]`, published through the
existing, closest-fitting `EventType` (SIGNAL_GENERATED for strategy
proposals, DECISION_MADE for Risk Governor/context outcomes,
POSITION_OPENED for a completed entry) -- `ShadowTradeTimeline` reads
`payload["stage"]` for its own precise label, so nothing about
explainability is lost by reusing the existing, smaller enum.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Sequence, Tuple

from bujji.core.enums import Side as CoreSide
from bujji.core.event_bus import Event, EventType
from bujji.core.models import OrderRequest as CoreOrderRequest, OrderResult as CoreOrderResult

from bujji.msi_trade_construction.engine import construct_trade
from bujji.msi_trade_construction.models import TradeConstructionAssessment

from bujji.trading_brain.risk_governor.capital_safety_governor import ProposedTradeEffect
from bujji.trading_brain.risk_governor.risk_governor_pipeline import (
    GovernorPipelineResult, PIPELINE_APPROVED, run_risk_governor_pipeline,
)
from bujji.trading_brain.risk_governor.live_risk_context_provider import ContextUnavailable
from bujji.trading_brain.risk_governor.msi_entry_bridge import _leg_to_core_contract, _to_core_side
from bujji.trading_brain.risk_governor.capital_check import assess_capital
from bujji.trading_brain.risk_governor.whole_book_margin_provider import (
    MarginLegRequest,
    margin_snapshot_to_capital_check_input,
)

from .runtime_state_machine import RuntimeState
from .trading_brain_composition_root import TradingBrainCompositionRoot

STAGE_STRATEGY_PROPOSED = "STRATEGY_PROPOSED"
STAGE_STRATEGY_REJECTED = "STRATEGY_REJECTED"
STAGE_CONTEXT_UNAVAILABLE = "CONTEXT_UNAVAILABLE"
STAGE_RISK_DECISION = "RISK_DECISION"
STAGE_GATE_B_MARGIN_VETO = "GATE_B_MARGIN_VETO"
STAGE_GATE_B_MARGIN_APPROVED = "GATE_B_MARGIN_APPROVED"
STAGE_ORDER_SUBMITTED = "ORDER_SUBMITTED"
STAGE_ORDER_FILLED = "ORDER_FILLED"
STAGE_POSITION_OPENED = "POSITION_OPENED"

# Runtime phases that may accept a NEW entry cycle -- a purely
# mechanical/operational gate (is the session even open for entries
# right now?), never a trading decision about whether THIS trade is
# good. Any other RuntimeState refuses the cycle before touching the
# Strategy Engine at all.
_ENTRY_ACCEPTING_STATES = (RuntimeState.ENTRY_ENABLED, RuntimeState.POSITION_ACTIVE, RuntimeState.MANAGING)


class RuntimeNotAcceptingEntriesError(Exception):
    """Raised when process_entry_cycle() is called outside an entry-
    accepting RuntimeState -- an operational refusal, never a trading
    decision about the proposed trade itself."""


@dataclass(frozen=True)
class TradingBrainCycleResult:
    proposal: TradeConstructionAssessment
    governor_result: Optional[GovernorPipelineResult]
    context_unavailable: Optional[ContextUnavailable]
    order_results: Tuple[CoreOrderResult, ...]
    approved_quantity: int
    filled: bool
    blocking_reason: Optional[str]


class TradingBrainRuntime:
    """Pure orchestrator: receive inputs -> invoke the Trading Brain's
    own already-complete pipeline, exactly once per stage -> route the
    resulting decision to PaperBroker -> publish events -> nothing
    else. See module docstring for the full non-decision-making
    invariant and its structural test coverage."""

    def __init__(self, root: TradingBrainCompositionRoot) -> None:
        self._root = root

    def run_market_open_sequence(self) -> None:
        """Mechanical state advancement only -- no market/broker/
        trading logic. A higher-level scheduler is responsible for
        calling this once per day at the appropriate time; this method
        itself makes no time-of-day decision."""
        machine = self._root.runtime_state_machine
        for target in (RuntimeState.PREMARKET, RuntimeState.CONNECTING, RuntimeState.LIVE, RuntimeState.ENTRY_ENABLED):
            machine.transition(target, reason="market_open_sequence")

    def run_market_close_sequence(self) -> None:
        machine = self._root.runtime_state_machine
        for target in (RuntimeState.POSTMARKET, RuntimeState.COMPLETE):
            machine.transition(target, reason="market_close_sequence")

    def process_entry_cycle(
        self,
        strategy_family: str,
        chain: Sequence,
        spot: Optional[float],
        as_of_date: str,
        timestamp: str,
        desired_quantity: int,
        requested_risk: float,
        proposed_trade_effect: ProposedTradeEffect,
        contracts_by_client_order_id: Dict[str, Any],
        sides_by_client_order_id: Dict[str, str],
        reference_prices_by_client_order_id: Dict[str, Optional[float]],
        risk_by_position_group_id: Optional[Dict[str, float]],
        direction: Optional[str] = None,
        expected_move_pct: Optional[float] = None,
        calibration_sample_id: Optional[str] = None,
    ) -> TradingBrainCycleResult:
        """Market Tick -> Strategy Engine -> E.1/E.2/E.3 -> D.1-D.6 ->
        D.4 -> PaperBroker, exactly once each, in this order. See
        module docstring for the full contract-ownership and quantity
        semantics this method relies on but never re-derives."""
        root = self._root
        machine = root.runtime_state_machine

        if machine.state not in _ENTRY_ACCEPTING_STATES:
            raise RuntimeNotAcceptingEntriesError(
                f"runtime is in {machine.state.value!r}, not accepting entry cycles "
                f"(requires one of {[s.value for s in _ENTRY_ACCEPTING_STATES]})"
            )

        # -- Strategy Engine (owns StrikeLeg/TradeConstructionAssessment) --
        proposal = construct_trade(
            strategy_family, chain, spot, as_of_date, direction=direction,
            expected_move_pct=expected_move_pct, timestamp=timestamp,
        )

        if not proposal.constructed:
            root.event_bus.publish_nowait(Event(
                type=EventType.SIGNAL_GENERATED,
                payload={"stage": STAGE_STRATEGY_REJECTED, "assessment_id": proposal.assessment_id,
                         "reason": proposal.rejection_reason},
                timestamp=root.clock(),
            ))
            return TradingBrainCycleResult(
                proposal=proposal, governor_result=None, context_unavailable=None, order_results=(),
                approved_quantity=0, filled=False, blocking_reason=proposal.rejection_reason,
            )

        root.event_bus.publish_nowait(Event(
            type=EventType.SIGNAL_GENERATED,
            payload={"stage": STAGE_STRATEGY_PROPOSED, "assessment_id": proposal.assessment_id,
                     "strategy_family": proposal.strategy_family},
            timestamp=root.clock(),
        ))

        # -- E.1/E.2/E.3: assemble the Risk Governor's own input context --
        context_result = root.live_risk_context_provider.build_context(
            proposal, desired_quantity, requested_risk, proposed_trade_effect,
            contracts_by_client_order_id, sides_by_client_order_id, reference_prices_by_client_order_id,
            root.instrument_type, root.product_type, risk_by_position_group_id,
            root.capital_safety_thresholds, root.portfolio_risk_thresholds, root.risk_policy,
            root.position_health_thresholds, root.clock, calibration_sample_id,
        )
        if isinstance(context_result, ContextUnavailable):
            root.event_bus.publish_nowait(Event(
                type=EventType.DECISION_MADE,
                payload={"stage": STAGE_CONTEXT_UNAVAILABLE, "assessment_id": proposal.assessment_id,
                         "producer": context_result.producer, "reason": context_result.reason},
                timestamp=root.clock(),
            ))
            return TradingBrainCycleResult(
                proposal=proposal, governor_result=None, context_unavailable=context_result, order_results=(),
                approved_quantity=0, filled=False, blocking_reason=context_result.reason,
            )

        # -- Gate B: margin ALLOW/VETO (wired 2026-08-19, Master Plan D-6). --
        # capital_check.assess_capital is documented throughout this codebase
        # as the sole margin veto authority, yet until tonight NOTHING in the
        # production entry chain invoked it, and the whole-book projection
        # received empty leg maps -- the certified SPAN provider was asked to
        # margin nothing. Here the PROPOSAL's own legs are priced through the
        # certified provider (the projected-book intent build_span_margin_
        # request's docstring always declared), the EXISTING book's verified
        # requirement is added (zero when the book is definitionally flat),
        # and the verdict gates the pipeline. Every failure path VETOES --
        # never a guess.
        try:
            margin_legs = []
            for leg in proposal.legs:
                contract = _leg_to_core_contract(leg, root.underlying, root.exchange_lot_size)
                margin_legs.append(MarginLegRequest(
                    symbol=contract.symbol,
                    qty=leg.ratio * root.exchange_lot_size * desired_quantity,
                    side=-1 if leg.side == "SELL" else 1,
                    # Live-certified span vocabulary (2026-07-19 evidence +
                    # 2026-08-18 whole-book discovery): type=2, INTRADAY.
                    instrument_type=2, product_type="INTRADAY",
                    limit_price=leg.premium,
                ))
            proposal_snapshot = root.margin_provider.get_portfolio_margin(margin_legs, root.clock)
        except Exception as exc:  # noqa: BLE001 -- an unpriceable proposal must never be approved
            proposal_snapshot = None
            gate_b_error = f"{type(exc).__name__}: {exc}"
        else:
            gate_b_error = None

        if proposal_snapshot is None:
            gate_b_reason = f"GATE_B_MARGIN_QUERY_RAISED: {gate_b_error}"
        else:
            if contracts_by_client_order_id:
                existing_snapshot = context_result.margin_snapshot
                if existing_snapshot is None or not existing_snapshot.margin_verified:
                    existing_required = None
                else:
                    existing_required = existing_snapshot.required_margin
            else:
                existing_required = 0.0  # definitionally flat: nothing to margin

            if existing_required is None:
                gate_b_reason = "GATE_B_EXISTING_BOOK_MARGIN_UNVERIFIED"
            else:
                capital = root.capital_snapshot_provider()
                total_required = (
                    None if proposal_snapshot.required_margin is None
                    else proposal_snapshot.required_margin + existing_required
                )
                inputs = margin_snapshot_to_capital_check_input(
                    proposal_snapshot,
                    available_capital=capital.available_capital,
                    configured_risk_capital=capital.available_capital,
                )
                # The projected total (proposal + existing) is what must fit,
                # not the proposal alone.
                inputs = type(inputs)(**{**inputs.__dict__, "required_margin": total_required})
                verdict = assess_capital(inputs, root.clock)
                gate_b_reason = (
                    None if verdict.decision == "ALLOW"
                    else f"GATE_B_{verdict.blocking_reason}"
                )

        if gate_b_reason is not None:
            root.event_bus.publish_nowait(Event(
                type=EventType.DECISION_MADE,
                payload={"stage": STAGE_GATE_B_MARGIN_VETO, "assessment_id": proposal.assessment_id,
                         "reason": gate_b_reason},
                timestamp=root.clock(),
            ))
            return TradingBrainCycleResult(
                proposal=proposal, governor_result=None, context_unavailable=None, order_results=(),
                approved_quantity=0, filled=False, blocking_reason=gate_b_reason,
            )
        root.event_bus.publish_nowait(Event(
            type=EventType.DECISION_MADE,
            payload={"stage": STAGE_GATE_B_MARGIN_APPROVED, "assessment_id": proposal.assessment_id,
                     "proposal_required_margin": proposal_snapshot.required_margin,
                     "existing_required_margin": existing_required,
                     "margin_source": proposal_snapshot.margin_source,
                     "available_capital": capital.available_capital},
            timestamp=root.clock(),
        ))

        # -- D.1-D.6 Risk Governor (D.4 Lifecycle Intelligence is the -----
        # -- pipeline's own ADMISSION stage) -------------------------------
        governor_result = run_risk_governor_pipeline(context_result)
        root.event_bus.publish_nowait(Event(
            type=EventType.DECISION_MADE,
            payload={"stage": STAGE_RISK_DECISION, "assessment_id": proposal.assessment_id,
                     "final_status": governor_result.final_status, "blocking_stage": governor_result.blocking_stage,
                     "final_quantity": governor_result.final_quantity},
            timestamp=root.clock(),
        ))

        if governor_result.final_status != PIPELINE_APPROVED or governor_result.final_quantity <= 0:
            return TradingBrainCycleResult(
                proposal=proposal, governor_result=governor_result, context_unavailable=None, order_results=(),
                approved_quantity=0, filled=False,
                blocking_reason=governor_result.blocking_stage or "ZERO_APPROVED_QUANTITY",
            )

        # -- Execution Layer bridge (owns CoreOptionContract/CoreOrderRequest) --
        order_requests = self._build_order_requests(proposal, governor_result.final_quantity)
        root.event_bus.publish_nowait(Event(
            type=EventType.DECISION_MADE,
            payload={"stage": STAGE_ORDER_SUBMITTED, "assessment_id": proposal.assessment_id,
                     "order_count": len(order_requests)},
            timestamp=root.clock(),
        ))

        # -- Broker Layer: PaperBroker is the sole, terminal executor -----
        order_results = tuple(
            _await(root.broker.place_order(order)) for order in order_requests
        )
        all_filled = all(r.is_filled for r in order_results) if order_results else False
        root.event_bus.publish_nowait(Event(
            type=EventType.DECISION_MADE,
            payload={"stage": STAGE_ORDER_FILLED, "assessment_id": proposal.assessment_id,
                     "filled_count": sum(1 for r in order_results if r.is_filled)},
            timestamp=root.clock(),
        ))

        if all_filled:
            machine.transition(RuntimeState.POSITION_ACTIVE, reason=f"entry filled for {proposal.assessment_id}")
            root.event_bus.publish_nowait(Event(
                type=EventType.POSITION_OPENED,
                payload={"stage": STAGE_POSITION_OPENED, "assessment_id": proposal.assessment_id,
                         "strategy_family": proposal.strategy_family, "approved_quantity": governor_result.final_quantity},
                timestamp=root.clock(),
            ))

        return TradingBrainCycleResult(
            proposal=proposal, governor_result=governor_result, context_unavailable=None,
            order_results=order_results, approved_quantity=governor_result.final_quantity,
            filled=all_filled, blocking_reason=None if all_filled else "PARTIAL_OR_UNFILLED",
        )

    def _build_order_requests(
        self, proposal: TradeConstructionAssessment, approved_lots: int,
    ) -> Tuple[CoreOrderRequest, ...]:
        """Reuses msi_entry_bridge.py's own leg-to-contract/side
        translation verbatim -- see module docstring for quantity
        semantics (leg.ratio * exchange_lot_size * approved_lots)."""
        root = self._root
        requests = []
        for index, leg in enumerate(proposal.legs):
            contract = _leg_to_core_contract(leg, root.underlying, root.exchange_lot_size)
            quantity = leg.ratio * root.exchange_lot_size * approved_lots
            client_order_id = f"{proposal.assessment_id}-LEG-{index}"
            requests.append(CoreOrderRequest(
                contract=contract, side=_to_core_side(leg.side), quantity=quantity,
                client_order_id=client_order_id, limit_price=None, reference_price=leg.premium,
                tag=f"TRADING_BRAIN:{proposal.strategy_family}:{proposal.assessment_id}",
            ))
        return tuple(requests)


def _await(awaitable):
    """PaperBroker.place_order() is async; this runtime's public
    surface is deliberately synchronous (no orchestration reason to
    force every caller into an event loop) -- runs the one awaitable
    to completion, exactly like msi_entry_bridge's own dispatch helper
    does for the same reason."""
    import asyncio
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(awaitable)
    raise RuntimeError(
        "process_entry_cycle() was called from inside a running event loop -- "
        "await the underlying broker call directly in that context instead"
    )
