"""JSON round-trip for PositionPlan / CapitalPolicy / LotSpecification."""
from __future__ import annotations

from typing import Any, Dict, List

from ..nifty_contract_builder.models import NiftyOptionContract
from ..nifty_contract_builder.serialization import contract_from_dict, contract_to_dict
from .models import CapitalPolicy, LotSpecification, PositionPlan


def capital_policy_to_dict(p: CapitalPolicy) -> Dict[str, Any]:
    return {"policy": p.policy, "version": p.version}


def capital_policy_from_dict(d: Dict[str, Any]) -> CapitalPolicy:
    return CapitalPolicy(policy=d["policy"], version=d["version"])


def lot_specification_to_dict(s: LotSpecification) -> Dict[str, Any]:
    return {
        "underlying": s.underlying,
        "lot_size": s.lot_size,
        "effective_date": s.effective_date,
        "version": s.version,
    }


def lot_specification_from_dict(d: Dict[str, Any]) -> LotSpecification:
    return LotSpecification(
        underlying=d["underlying"],
        lot_size=d["lot_size"],
        effective_date=d["effective_date"],
        version=d["version"],
    )


def plan_to_dict(p: PositionPlan) -> Dict[str, Any]:
    return {
        "plan_id": p.plan_id,
        "contracts": [contract_to_dict(c) for c in p.contracts],
        "lots_per_leg": p.lots_per_leg,
        "quantity_per_leg": p.quantity_per_leg,
        "capital_intent": p.capital_intent,
        "sizing_policy": p.sizing_policy,
        "validation": p.validation,
        "sizing_reason": p.sizing_reason,
        "sizing_trace": p.sizing_trace,
        "failure_reason": p.failure_reason,
        "capital_decision_id": p.capital_decision_id,
        "timestamp": p.timestamp,
        "version": p.version,
    }


def plan_from_dict(d: Dict[str, Any]) -> PositionPlan:
    contracts: List[NiftyOptionContract] = [contract_from_dict(c) for c in d["contracts"]]
    return PositionPlan(
        plan_id=d["plan_id"],
        contracts=tuple(contracts),
        lots_per_leg=d["lots_per_leg"],
        quantity_per_leg=d["quantity_per_leg"],
        capital_intent=d["capital_intent"],
        sizing_policy=d["sizing_policy"],
        validation=d["validation"],
        sizing_reason=d["sizing_reason"],
        sizing_trace=d["sizing_trace"],
        failure_reason=d.get("failure_reason"),
        capital_decision_id=d.get("capital_decision_id"),
        timestamp=d["timestamp"],
        version=d["version"],
    )
