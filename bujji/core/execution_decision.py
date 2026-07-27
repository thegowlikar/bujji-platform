"""Execution Decision — Production Pipeline Stage 5.

Production Engineering Sprint 2 (Stage Interface Extraction). This is an
EXTRACTION, not a rewrite: every field and value computed here is
identical to what previously ran inline inside Orchestrator._enter()
between the Risk Validation and Journal Recording stages. No new field,
no new value, no change in computation order.

Pure and broker-free: takes only already-resolved contracts/quantity/
candle/intention/intelligence snapshot/broker name, and returns the two
immutable evidence objects (DecisionSnapshot, ExecutionPlan) plus the
three derived identifiers (decision_id, ce_cid, pe_cid) downstream stages
need. No I/O, no broker call, no mutable state read or written -- which
is exactly what makes this stage independently unit-testable without
constructing a live Orchestrator.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from . import evidence_assembly
from .enums import Side
from .models import Candle, DecisionSnapshot, ExecutionPlan, OptionContract, TradeIntention


@dataclass(frozen=True)
class ExecutionDecisionResult:
    snapshot: DecisionSnapshot
    plan: ExecutionPlan
    decision_id: str
    ce_cid: str
    pe_cid: str


def build_execution_decision(
    candle: Candle,
    intention: TradeIntention,
    ce_contract: OptionContract,
    pe_contract: OptionContract,
    requested_qty: int,
    intelligence_snapshot: dict,
    broker_name: str,
) -> ExecutionDecisionResult:
    ts = candle.timestamp.strftime('%Y%m%d%H%M%S')
    decision_id = intention.decision_id or f"DEC-{ts}"
    ce_cid = f"ENTRY-CE-{ts}"
    pe_cid = f"ENTRY-PE-{ts}"

    snapshot = DecisionSnapshot(
        decision_id=decision_id,
        as_of=candle.timestamp,
        strategy_version=evidence_assembly.STRATEGY_TYPE,
        market_observations=evidence_assembly.market_observations_snapshot(candle),
        intelligence_snapshot=intelligence_snapshot,
        intention=intention,
        planned_contracts={"ce": ce_contract.symbol, "pe": pe_contract.symbol,
                           "strike": ce_contract.strike},
        planned_structure="ATM_STRADDLE",
        broker_session=broker_name,
        replay_reference=candle.timestamp.isoformat(),
    )

    plan = ExecutionPlan(
        execution_id=f"EXEC-{ts}",
        decision_id=decision_id,
        strategy_type=evidence_assembly.STRATEGY_TYPE,
        structure_type="ATM_STRADDLE",
        contracts={"ce": ce_contract, "pe": pe_contract},
        side_per_leg={"ce": Side.SELL, "pe": Side.SELL},
        quantities={"ce": requested_qty, "pe": requested_qty},
        execution_sequence=["ce", "pe"],
        idempotency_keys={"ce": ce_cid, "pe": pe_cid},
        broker_account=broker_name,
        as_of=candle.timestamp,
    )

    return ExecutionDecisionResult(
        snapshot=snapshot, plan=plan, decision_id=decision_id, ce_cid=ce_cid, pe_cid=pe_cid,
    )
