"""JSON round-trip for TradingOntologySnapshot / OntologyProvenance."""
from __future__ import annotations

from typing import Any, Dict

from .models import OntologyProvenance, TradingOntologySnapshot


def provenance_to_dict(p: OntologyProvenance) -> Dict[str, Any]:
    return {
        "source": p.source,
        "ruleset_version": p.ruleset_version,
        "reasoning_summary": p.reasoning_summary,
    }


def provenance_from_dict(d: Dict[str, Any]) -> OntologyProvenance:
    return OntologyProvenance(
        source=d["source"],
        ruleset_version=d["ruleset_version"],
        reasoning_summary=d.get("reasoning_summary", ""),
    )


def snapshot_to_dict(s: TradingOntologySnapshot) -> Dict[str, Any]:
    return {
        "snapshot_id": s.snapshot_id,
        "timestamp": s.timestamp,
        "market_state": s.market_state,
        "opportunity_state": s.opportunity_state,
        "risk_state": s.risk_state,
        "execution_intent": s.execution_intent,
        "strategy_intent": s.strategy_intent,
        "capital_intent": s.capital_intent,
        "confidence": s.confidence,
        "provenance": provenance_to_dict(s.provenance),
        "ontology_version": s.ontology_version,
        "context_id": s.context_id,
    }


def snapshot_from_dict(d: Dict[str, Any]) -> TradingOntologySnapshot:
    return TradingOntologySnapshot(
        snapshot_id=d["snapshot_id"],
        timestamp=d["timestamp"],
        market_state=d["market_state"],
        opportunity_state=d["opportunity_state"],
        risk_state=d["risk_state"],
        execution_intent=d["execution_intent"],
        strategy_intent=d["strategy_intent"],
        capital_intent=d["capital_intent"],
        confidence=d["confidence"],
        provenance=provenance_from_dict(d["provenance"]),
        ontology_version=d["ontology_version"],
        context_id=d.get("context_id"),
    )
