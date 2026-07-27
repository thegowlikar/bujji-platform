"""JSON round-trip for ContractConstructionResult / NiftyOptionContract
and the two input snapshot types.
"""
from __future__ import annotations

from typing import Any, Dict, List

from .models import (
    ContractConstructionResult,
    NiftyOptionChainEntry,
    NiftyOptionChainSnapshot,
    NiftyOptionContract,
    NiftySpotSnapshot,
)


def spot_to_dict(s: NiftySpotSnapshot) -> Dict[str, Any]:
    return {"spot": s.spot, "as_of": s.as_of}


def spot_from_dict(d: Dict[str, Any]) -> NiftySpotSnapshot:
    return NiftySpotSnapshot(spot=d.get("spot"), as_of=d["as_of"])


def chain_entry_to_dict(e: NiftyOptionChainEntry) -> Dict[str, Any]:
    return {
        "strike": e.strike,
        "option_type": e.option_type,
        "expiry": e.expiry,
        "contract_symbol": e.contract_symbol,
    }


def chain_entry_from_dict(d: Dict[str, Any]) -> NiftyOptionChainEntry:
    return NiftyOptionChainEntry(
        strike=d["strike"],
        option_type=d["option_type"],
        expiry=d["expiry"],
        contract_symbol=d["contract_symbol"],
    )


def chain_to_dict(c: NiftyOptionChainSnapshot) -> Dict[str, Any]:
    return {
        "expiries": list(c.expiries),
        "entries": [chain_entry_to_dict(e) for e in c.entries],
        "as_of": c.as_of,
    }


def chain_from_dict(d: Dict[str, Any]) -> NiftyOptionChainSnapshot:
    entries: List[NiftyOptionChainEntry] = [chain_entry_from_dict(e) for e in d["entries"]]
    return NiftyOptionChainSnapshot(
        expiries=tuple(d["expiries"]), entries=tuple(entries), as_of=d["as_of"]
    )


def contract_to_dict(c: NiftyOptionContract) -> Dict[str, Any]:
    return {
        "contract_id": c.contract_id,
        "underlying": c.underlying,
        "expiry": c.expiry,
        "strike": c.strike,
        "option_type": c.option_type,
        "side": c.side,
        "contract_symbol": c.contract_symbol,
        "capital_intent": c.capital_intent,
        "strategy_id": c.strategy_id,
        "selection_reason": c.selection_reason,
        "construction_trace": c.construction_trace,
        "timestamp": c.timestamp,
        "version": c.version,
    }


def contract_from_dict(d: Dict[str, Any]) -> NiftyOptionContract:
    return NiftyOptionContract(
        contract_id=d["contract_id"],
        underlying=d["underlying"],
        expiry=d["expiry"],
        strike=d["strike"],
        option_type=d["option_type"],
        side=d["side"],
        contract_symbol=d["contract_symbol"],
        capital_intent=d["capital_intent"],
        strategy_id=d["strategy_id"],
        selection_reason=d["selection_reason"],
        construction_trace=d["construction_trace"],
        timestamp=d["timestamp"],
        version=d["version"],
    )


def result_to_dict(r: ContractConstructionResult) -> Dict[str, Any]:
    return {
        "construction_id": r.construction_id,
        "status": r.status,
        "strategy_id": r.strategy_id,
        "capital_intent": r.capital_intent,
        "contracts": [contract_to_dict(c) for c in r.contracts],
        "failure_reason": r.failure_reason,
        "construction_trace": r.construction_trace,
        "spot_used": r.spot_used,
        "atm_strike_used": r.atm_strike_used,
        "timestamp": r.timestamp,
        "version": r.version,
    }


def result_from_dict(d: Dict[str, Any]) -> ContractConstructionResult:
    contracts: List[NiftyOptionContract] = [contract_from_dict(c) for c in d["contracts"]]
    return ContractConstructionResult(
        construction_id=d["construction_id"],
        status=d["status"],
        strategy_id=d.get("strategy_id"),
        capital_intent=d["capital_intent"],
        contracts=tuple(contracts),
        failure_reason=d.get("failure_reason"),
        construction_trace=d["construction_trace"],
        spot_used=d.get("spot_used"),
        atm_strike_used=d.get("atm_strike_used"),
        timestamp=d["timestamp"],
        version=d["version"],
    )
