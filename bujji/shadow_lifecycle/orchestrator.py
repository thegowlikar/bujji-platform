"""Shadow Lifecycle Orchestrator -- Phase 15O. The INTEGRATION layer
that finally carries one position through the complete chain:

    ShadowTradeCandidate -> POSITION_OPENED -> (PaperBroker entry fills)
    -> THESIS_EVALUATED / MANAGEMENT_ASSESSED (per cycle)
    -> (PaperBroker exit fills) -> POSITION_CLOSED (structured, real P&L)
    -> OUTCOME_ATTRIBUTED -> OUTCOME_MEMORY_RECORDED

This module builds NO new intelligence and NO new state machine. Every
step delegates to the already-proven engine that owns it:
  identity/state   -> bujji.position_lifecycle (Phase 15G)
  broker evidence  -> bujji.position_lifecycle.paper_bridge (Phase 15L)
  economics        -> bujji.position_lifecycle.pnl, via build_structured_exit (Phase 15K)
  thesis           -> bujji.position_intelligence (Phase 15F)
  management       -> bujji.position_management (Phase 15I)
  attribution      -> bujji.outcome_attribution (Phase 15J)
  memory           -> bujji.outcome_memory (Phase 15N)
  persistence      -> bujji.state_persistence.EventStore (Phase 15B)

HARD SAFETY BOUNDARY (Phase 15O Step 3): PaperBroker ONLY. This module
never imports FyersBroker, never touches `broker/guard.py`, and never
reads Outcome Memory (no feedback path -- memory is written at the END
of a lifecycle and never consulted to make a decision). Proven by
`tests/test_shadow_lifecycle_orchestrator_safety.py`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from bujji.core.enums import Side
from bujji.core.models import OptionContract, OrderRequest
from bujji.outcome_attribution.engine import attribute_position_outcome
from bujji.outcome_memory.engine import (
    build_outcome_memory_record, build_outcome_memory_recorded_payload,
)
from bujji.outcome_memory.models import EVENT_OUTCOME_MEMORY_RECORDED, SCHEMA_VERSION as MEMORY_SCHEMA_VERSION
from bujji.position_lifecycle.engine import (
    apply_event, build_entry_snapshot_for_position, build_management_assessed_payload,
    build_outcome_attributed_payload, build_position_closed_payload, build_position_opened_payload,
    build_structured_exit, build_thesis_evaluated_payload,
)
from bujji.position_lifecycle.models import (
    SCHEMA_VERSION as LIFECYCLE_SCHEMA_VERSION, STATUS_CLOSED, STATUS_OPEN,
)
from bujji.position_lifecycle.paper_bridge import client_order_id_for, reconcile_position_exit_async
from bujji.position_management.engine import assess_position_management
from bujji.state_persistence.models import PersistedEvent
from bujji.state_persistence.store import EventStore

CONSTRUCTION_STATUS_CONSTRUCTED = "CONSTRUCTED"

# Orchestration outcomes -- explicit, never a silent no-op.
OPENED = "OPENED"
NOT_OPENED_NO_CANDIDATE = "NOT_OPENED_NO_CANDIDATE"
NOT_OPENED_CANDIDATE_NOT_CONSTRUCTED = "NOT_OPENED_CANDIDATE_NOT_CONSTRUCTED"
NOT_OPENED_ALREADY_OPEN = "NOT_OPENED_ALREADY_OPEN"
NOT_OPENED_NO_FILL = "NOT_OPENED_NO_FILL"


@dataclass(frozen=True)
class OrchestrationStep:
    """One recorded orchestration action -- what happened, and why.
    Returned to the caller (and loggable) so a shadow session can
    explain every lifecycle decision it made or declined to make."""

    step: str
    outcome: str
    position_id: Optional[str]
    detail: str

    def to_dict(self) -> dict:
        return {"step": self.step, "outcome": self.outcome, "position_id": self.position_id, "detail": self.detail}


def _append(store: EventStore, event_id: str, event_type: str, session_id: str, cycle_id: str,
            timestamp: str, payload: dict, schema_version: str = LIFECYCLE_SCHEMA_VERSION) -> None:
    store.append(PersistedEvent(
        event_id=event_id, event_type=event_type, session_id=session_id, cycle_id=cycle_id,
        timestamp=timestamp, schema_version=schema_version, provenance="shadow_lifecycle_orchestrator",
        payload=payload,
    ))


def _contract_for_leg(leg, underlying_symbol: str, lot_size: Optional[int]) -> OptionContract:
    """Builds the OptionContract PaperBroker needs from the leg's OWN
    real, already-solved fields (Phase 14) -- nothing invented."""
    from bujji.core.enums import OptionType
    option_type = OptionType.CE if leg.option_type == "CE" else OptionType.PE
    symbol = f"{underlying_symbol}{int(leg.strike)}{leg.option_type}"
    return OptionContract(symbol, underlying_symbol, leg.strike, option_type, "WEEKLY", lot_size or 1)


async def open_position_from_candidate(
    store: EventStore, broker, session_id: str, candidate, cycle_id: str, entry_timestamp: str,
    states: Dict[str, Any], source_intelligence_cycle_record: Optional[dict] = None,
) -> Tuple[Dict[str, Any], OrchestrationStep]:
    """Step 6 -- opens ONE position from a real, CONSTRUCTED
    `ShadowTradeCandidate`. Sends entry orders to PaperBroker ONLY,
    using `client_order_id_for` (Phase 15L) so broker identity is
    always DERIVED from lifecycle identity, never the reverse.
    Declines honestly (never fabricates) when the candidate was not
    actually constructed."""
    if candidate is None:
        return states, OrchestrationStep("OPEN", NOT_OPENED_NO_CANDIDATE, None, "no candidate this cycle")
    if getattr(candidate, "construction_status", None) != CONSTRUCTION_STATUS_CONSTRUCTED:
        return states, OrchestrationStep(
            "OPEN", NOT_OPENED_CANDIDATE_NOT_CONSTRUCTED, None,
            f"candidate construction_status={getattr(candidate, 'construction_status', None)!r} -- nothing real to open",
        )
    if any(lc.status == STATUS_OPEN for lc in states.values()):
        return states, OrchestrationStep("OPEN", NOT_OPENED_ALREADY_OPEN, None,
                                          "an open position already exists -- single-position slice, by design")

    entry = build_entry_snapshot_for_position(candidate, source_intelligence_cycle_record)
    open_payload = build_position_opened_payload(session_id, candidate, entry, entry_timestamp)
    position_id = open_payload["position_id"]

    new_states, result = apply_event(states, session_id, "POSITION_OPENED", session_id, open_payload)
    if result.outcome != "ACCEPTED":
        return states, OrchestrationStep("OPEN", result.outcome, position_id, result.reason)

    _append(store, f"OPEN-{position_id}", "POSITION_OPENED", session_id, cycle_id, entry_timestamp, open_payload)

    # Entry fills -- PaperBroker ONLY, one order per leg, identity derived from the lifecycle.
    lifecycle = new_states[position_id]
    filled_legs = 0
    for leg in lifecycle.legs:
        request = OrderRequest(
            contract=_contract_for_leg(leg, candidate.underlying_symbol, lifecycle.entry.lot_size),
            side=Side.BUY if leg.side == "BUY" else Side.SELL,
            quantity=leg.quantity or 1,
            client_order_id=client_order_id_for(position_id, leg.leg_id, 1),
            reference_price=leg.entry_premium,
        )
        order_result = await broker.place_order(request)
        if (order_result.filled_quantity or 0) > 0:
            filled_legs += 1

    if filled_legs == 0:
        return new_states, OrchestrationStep(
            "OPEN", NOT_OPENED_NO_FILL, position_id,
            "position recorded as OPENED but PaperBroker filled zero legs -- disclosed, never silently treated as filled",
        )

    return new_states, OrchestrationStep("OPEN", OPENED, position_id,
                                          f"opened with {filled_legs}/{len(lifecycle.legs)} legs filled by PaperBroker")


def monitor_position(
    store: EventStore, session_id: str, position_id: str, cycle_id: str, timestamp: str,
    states: Dict[str, Any], thesis_evaluation, greeks=None, premium_behaviour=None,
) -> Tuple[Dict[str, Any], List[OrchestrationStep]]:
    """Step 7 -- records ONE monitoring cycle's real thesis evaluation
    (Phase 15F, supplied by the caller -- this module never evaluates a
    thesis itself) and the resulting management assessment (Phase 15I,
    the REAL engine, never a second one). Both are advisory evidence;
    neither ever triggers an order."""
    steps: List[OrchestrationStep] = []
    lifecycle = states.get(position_id)
    if lifecycle is None or lifecycle.status != STATUS_OPEN:
        return states, [OrchestrationStep("MONITOR", "SKIPPED", position_id, "position is not OPEN")]

    if thesis_evaluation is not None:
        payload = build_thesis_evaluated_payload(position_id, cycle_id, thesis_evaluation)
        states, result = apply_event(states, session_id, "THESIS_EVALUATED", session_id, payload)
        if result.outcome == "ACCEPTED":
            _append(store, f"THESIS-{position_id}-{cycle_id}", "THESIS_EVALUATED", session_id, cycle_id, timestamp, payload)
        steps.append(OrchestrationStep("THESIS_EVALUATED", result.outcome, position_id, result.reason))

        assessment = assess_position_management(position_id, cycle_id, thesis_evaluation, greeks, premium_behaviour)
        mgmt_payload = build_management_assessed_payload(position_id, cycle_id, assessment)
        states, mgmt_result = apply_event(states, session_id, "MANAGEMENT_ASSESSED", session_id, mgmt_payload)
        if mgmt_result.outcome == "ACCEPTED":
            _append(store, f"MGMT-{position_id}-{cycle_id}", "MANAGEMENT_ASSESSED", session_id, cycle_id, timestamp, mgmt_payload)
        steps.append(OrchestrationStep("MANAGEMENT_ASSESSED", mgmt_result.outcome, position_id,
                                        f"recommendation={assessment.recommendation}"))
    return states, steps


async def close_position(
    store: EventStore, broker, session_id: str, position_id: str, cycle_id: str, exit_timestamp: str,
    exit_reason: str, states: Dict[str, Any],
) -> Tuple[Dict[str, Any], List[OrchestrationStep]]:
    """Step 8 -- sends the real closing orders to PaperBroker ONLY,
    reconciles the ACTUAL fill evidence through the Phase 15L bridge,
    and flows it through structured_exit -> realized P&L ->
    POSITION_CLOSED -> OUTCOME_ATTRIBUTED -> OUTCOME_MEMORY_RECORDED.
    Attribution is computed strictly AFTER the close and never
    influences it."""
    steps: List[OrchestrationStep] = []
    lifecycle = states.get(position_id)
    if lifecycle is None or lifecycle.status != STATUS_OPEN:
        return states, [OrchestrationStep("CLOSE", "SKIPPED", position_id, "position is not OPEN")]

    # Closing orders -- opposite side of each entry leg, sequence 2 so the
    # bridge can distinguish them from the entry fills (sequence 1).
    for leg in lifecycle.legs:
        request = OrderRequest(
            contract=_contract_for_leg(leg, lifecycle.entry.underlying_symbol or "NIFTY", lifecycle.entry.lot_size),
            side=Side.SELL if leg.side == "BUY" else Side.BUY,
            quantity=leg.quantity or 1,
            client_order_id=client_order_id_for(position_id, leg.leg_id, 2),
            reference_price=None,
        )
        await broker.place_order(request)

    # Phase 20.2.1: exit_sequence_start=2 matches this function's own
    # entry=1/exit=2 convention (comment above) -- makes the bridge
    # actually scope its observation to exit-only fills instead of
    # blending them with the entry fill, and folds entry-side
    # fees/slippage into the position-level totals.
    reconciliation = await reconcile_position_exit_async(broker, position_id, lifecycle.legs, exit_sequence_start=2)
    structured_exit = build_structured_exit(
        lifecycle.legs, lifecycle.entry.lot_size, exit_timestamp, exit_reason,
        reconciliation.exit_prices, fees=reconciliation.fees, slippage=reconciliation.slippage,
    )
    close_payload = build_position_closed_payload(position_id, exit_timestamp, exit_reason, structured_exit)
    states, result = apply_event(states, session_id, "POSITION_CLOSED", session_id, close_payload)
    if result.outcome != "ACCEPTED":
        return states, [OrchestrationStep("CLOSE", result.outcome, position_id, result.reason)]
    _append(store, f"CLOSE-{position_id}", "POSITION_CLOSED", session_id, cycle_id, exit_timestamp, close_payload)
    steps.append(OrchestrationStep("CLOSE", result.outcome, position_id,
                                    f"pnl_status={structured_exit.pnl_status}, gross={structured_exit.gross_realized_pnl}"))

    attribution = attribute_position_outcome(states[position_id])
    attr_payload = build_outcome_attributed_payload(position_id, attribution)
    states, attr_result = apply_event(states, session_id, "OUTCOME_ATTRIBUTED", session_id, attr_payload)
    if attr_result.outcome == "ACCEPTED":
        _append(store, f"ATTR-{position_id}", "OUTCOME_ATTRIBUTED", session_id, cycle_id, exit_timestamp, attr_payload)
    steps.append(OrchestrationStep("OUTCOME_ATTRIBUTED", attr_result.outcome, position_id,
                                    f"outcome_direction={attribution.outcome_direction}"))
    return states, steps


def record_outcome_memory(
    memory_store: EventStore, session_id: str, position_id: str, cycle_id: str, recorded_at: str,
    states: Dict[str, Any], portfolio_snapshot=None,
) -> OrchestrationStep:
    """Final step -- writes the durable, cross-session memory record
    (Phase 15N). WRITE-ONLY: this orchestrator never READS outcome
    memory anywhere, so no feedback path into any decision can exist
    (Step 3's hard boundary)."""
    lifecycle = states.get(position_id)
    if lifecycle is None or lifecycle.status != STATUS_CLOSED:
        return OrchestrationStep("OUTCOME_MEMORY", "SKIPPED", position_id, "position is not CLOSED")
    attribution_dict = lifecycle.outcome_attribution
    if attribution_dict is None:
        return OrchestrationStep("OUTCOME_MEMORY", "SKIPPED", position_id, "no outcome attribution recorded")

    attribution = _AttributionView(attribution_dict)
    record = build_outcome_memory_record(lifecycle, attribution, recorded_at, portfolio_snapshot)
    if record is None:
        return OrchestrationStep("OUTCOME_MEMORY", "SKIPPED", position_id,
                                  "attribution not READY -- no memory recorded, never a speculative one")
    payload = build_outcome_memory_recorded_payload(record)
    _append(memory_store, f"MEM-{record.memory_id}", EVENT_OUTCOME_MEMORY_RECORDED, session_id, cycle_id,
            recorded_at, payload, schema_version=MEMORY_SCHEMA_VERSION)
    return OrchestrationStep("OUTCOME_MEMORY", "RECORDED", position_id, f"memory_id={record.memory_id}")


class _AttributionView:
    """Adapts the persisted attribution DICT (as replayed onto
    `PositionLifecycle.outcome_attribution`) back to the small
    attribute surface `build_outcome_memory_record` reads. Not a
    re-derivation -- every value is the verbatim persisted one."""

    def __init__(self, d: dict) -> None:
        self._d = d
        self.readiness = d.get("readiness")
        self.evaluation_timestamp = d.get("evaluation_timestamp")
        self.outcome_direction = d.get("outcome_direction")
        self.realized_pnl = d.get("realized_pnl")
        self.primary_cause = d.get("primary_cause")

    def to_dict(self) -> dict:
        return self._d
