"""EPS serialization — Series 101. Pure dict/JSON round-trip, mirrors
every prior MSI package's convention."""
from __future__ import annotations

import json
from typing import Any, Dict

from .models import EngineeringProposal, EvidencePacket, Metric, TraceabilityEdge


def metric_to_dict(m: Metric) -> Dict[str, Any]:
    return {"name": m.name, "value": m.value, "unit": m.unit}


def metric_from_dict(d: Dict[str, Any]) -> Metric:
    return Metric(name=d["name"], value=d["value"], unit=d.get("unit"))


def packet_to_dict(p: EvidencePacket) -> Dict[str, Any]:
    return {
        "packet_id": p.packet_id, "created_timestamp": p.created_timestamp,
        "mle_version": p.mle_version, "production_version": p.production_version,
        "replay_version": p.replay_version,
        "decision_record_ids": list(p.decision_record_ids), "outcome_record_ids": list(p.outcome_record_ids),
        "journal_references": list(p.journal_references), "market_recorder_session": p.market_recorder_session,
        "replay_session": p.replay_session, "market_classification": p.market_classification,
        "trading_day_classification": p.trading_day_classification, "regime": p.regime,
        "expiry_context": p.expiry_context, "volatility_context": p.volatility_context,
        "statistics": [metric_to_dict(m) for m in p.statistics], "charts": list(p.charts),
        "derived_measurements": [metric_to_dict(m) for m in p.derived_measurements],
        "schema_version": p.schema_version,
    }


def packet_from_dict(d: Dict[str, Any]) -> EvidencePacket:
    return EvidencePacket(
        packet_id=d["packet_id"], created_timestamp=d["created_timestamp"], mle_version=d["mle_version"],
        production_version=d["production_version"], replay_version=d["replay_version"],
        decision_record_ids=tuple(d["decision_record_ids"]), outcome_record_ids=tuple(d["outcome_record_ids"]),
        journal_references=tuple(d["journal_references"]), market_recorder_session=d["market_recorder_session"],
        replay_session=d["replay_session"], market_classification=d["market_classification"],
        trading_day_classification=d["trading_day_classification"], regime=d["regime"],
        expiry_context=d["expiry_context"], volatility_context=d["volatility_context"],
        statistics=tuple(metric_from_dict(m) for m in d["statistics"]), charts=tuple(d["charts"]),
        derived_measurements=tuple(metric_from_dict(m) for m in d["derived_measurements"]),
        schema_version=d["schema_version"],
    )


def packet_to_json(p: EvidencePacket) -> str:
    return json.dumps(packet_to_dict(p), sort_keys=True, default=repr)


def packet_from_json(text: str) -> EvidencePacket:
    return packet_from_dict(json.loads(text))


def proposal_to_dict(p: EngineeringProposal) -> Dict[str, Any]:
    return {
        "proposal_id": p.proposal_id, "created_timestamp": p.created_timestamp,
        "knowledge_candidate_ids": list(p.knowledge_candidate_ids),
        "evidence_packet_ids": list(p.evidence_packet_ids),
        "proposed_change": p.proposed_change, "reasoning": list(p.reasoning),
        "schema_version": p.schema_version,
    }


def proposal_from_dict(d: Dict[str, Any]) -> EngineeringProposal:
    return EngineeringProposal(
        proposal_id=d["proposal_id"], created_timestamp=d["created_timestamp"],
        knowledge_candidate_ids=tuple(d["knowledge_candidate_ids"]),
        evidence_packet_ids=tuple(d["evidence_packet_ids"]),
        proposed_change=d["proposed_change"], reasoning=tuple(d["reasoning"]),
        schema_version=d["schema_version"],
    )


def edge_to_dict(e: TraceabilityEdge) -> Dict[str, Any]:
    return {
        "edge_id": e.edge_id, "from_type": e.from_type, "from_id": e.from_id,
        "to_type": e.to_type, "to_id": e.to_id, "timestamp": e.timestamp, "schema_version": e.schema_version,
    }


def edge_from_dict(d: Dict[str, Any]) -> TraceabilityEdge:
    return TraceabilityEdge(
        edge_id=d["edge_id"], from_type=d["from_type"], from_id=d["from_id"],
        to_type=d["to_type"], to_id=d["to_id"], timestamp=d["timestamp"], schema_version=d["schema_version"],
    )
