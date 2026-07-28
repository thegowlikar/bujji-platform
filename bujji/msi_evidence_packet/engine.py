"""Evidence Packet System (EPS) engine — Series 101. Pure functions: no
IO, no state, no wall-clock reads, no randomness -- mirrors every other
MSI engine.py in this project. Zero imports from any Production
decision/execution/broker package (same isolation discipline as
bujji.msi_market_learning, Series 100) -- every real input here is a
plain string/id/Metric the caller already computed elsewhere."""
from __future__ import annotations

import hashlib
from typing import Sequence, Tuple

from . import config as _config
from . import taxonomy
from .models import EngineeringProposal, EvidencePacket, Metric, TraceabilityEdge


def _packet_id(
    decision_record_ids: Tuple[str, ...], outcome_record_ids: Tuple[str, ...],
    market_recorder_session: str, replay_session: str, production_version: str,
    mle_version: str, replay_version: str, market_classification: str,
    trading_day_classification: str, regime: str, expiry_context: str, volatility_context: str,
    statistics: Tuple[Metric, ...], derived_measurements: Tuple[Metric, ...], schema_version: str,
) -> str:
    """The packet's identity IS its content hash -- this is what makes
    immutability real rather than promised: two calls with identical real
    inputs always produce the identical id (Reproducibility, Deliverable
    8), and any genuine correction (different real inputs) necessarily
    produces a different id, never a silent overwrite of the old one."""
    content = "|".join([
        ",".join(decision_record_ids), ",".join(outcome_record_ids),
        market_recorder_session, replay_session, production_version, mle_version, replay_version,
        market_classification, trading_day_classification, regime, expiry_context, volatility_context,
        ";".join(f"{m.name}={m.value}{m.unit or ''}" for m in statistics),
        ";".join(f"{m.name}={m.value}{m.unit or ''}" for m in derived_measurements),
        schema_version,
    ])
    return "EP-" + hashlib.md5(content.encode("utf-8")).hexdigest()[:24]


def build_evidence_packet(
    *, decision_record_ids: Sequence[str], outcome_record_ids: Sequence[str],
    journal_references: Sequence[str], market_recorder_session: str, replay_session: str,
    production_version: str, mle_version: str, replay_version: str,
    market_classification: str, trading_day_classification: str, regime: str,
    expiry_context: str, volatility_context: str,
    statistics: Sequence[Metric] = (), charts: Sequence[str] = (),
    derived_measurements: Sequence[Metric] = (),
    created_timestamp: str, schema_version: str = _config.SCHEMA_VERSION,
) -> EvidencePacket:
    """Constructs exactly one, real, immutable Evidence Packet. Contains
    ONLY the fields EvidencePacket declares -- there is structurally no
    field to put a recommendation/suggestion/threshold-change into
    (Deliverable: 'No Opinions')."""
    decision_ids_t = tuple(decision_record_ids)
    outcome_ids_t = tuple(outcome_record_ids)
    stats_t = tuple(statistics)
    derived_t = tuple(derived_measurements)
    pid = _packet_id(
        decision_ids_t, outcome_ids_t, market_recorder_session, replay_session,
        production_version, mle_version, replay_version, market_classification,
        trading_day_classification, regime, expiry_context, volatility_context,
        stats_t, derived_t, schema_version,
    )
    return EvidencePacket(
        packet_id=pid, created_timestamp=created_timestamp, mle_version=mle_version,
        production_version=production_version, replay_version=replay_version,
        decision_record_ids=decision_ids_t, outcome_record_ids=outcome_ids_t,
        journal_references=tuple(journal_references), market_recorder_session=market_recorder_session,
        replay_session=replay_session, market_classification=market_classification,
        trading_day_classification=trading_day_classification, regime=regime,
        expiry_context=expiry_context, volatility_context=volatility_context,
        statistics=stats_t, charts=tuple(charts), derived_measurements=derived_t,
        schema_version=schema_version,
    )


def _proposal_id(knowledge_candidate_ids: Tuple[str, ...], evidence_packet_ids: Tuple[str, ...],
                  proposed_change: str, schema_version: str) -> str:
    content = "|".join([",".join(knowledge_candidate_ids), ",".join(evidence_packet_ids), proposed_change, schema_version])
    return "PROP-" + hashlib.md5(content.encode("utf-8")).hexdigest()[:24]


def build_engineering_proposal(
    *, knowledge_candidate_ids: Sequence[str], evidence_packet_ids: Sequence[str],
    proposed_change: str, reasoning: Sequence[str], created_timestamp: str,
    schema_version: str = _config.SCHEMA_VERSION,
) -> EngineeringProposal:
    """A proposal is a disclosed, plain-language LINK -- never
    implementation. It requires at least one real Evidence Packet or
    Knowledge Candidate reference; a proposal with no real ancestry is
    rejected (an opinion with nothing behind it is exactly what this
    system exists to prevent)."""
    kc_ids_t = tuple(knowledge_candidate_ids)
    ep_ids_t = tuple(evidence_packet_ids)
    if not kc_ids_t and not ep_ids_t:
        raise ValueError("an EngineeringProposal must cite at least one real KnowledgeCandidate or EvidencePacket id")
    pid = _proposal_id(kc_ids_t, ep_ids_t, proposed_change, schema_version)
    return EngineeringProposal(
        proposal_id=pid, created_timestamp=created_timestamp,
        knowledge_candidate_ids=kc_ids_t, evidence_packet_ids=ep_ids_t,
        proposed_change=proposed_change, reasoning=tuple(reasoning), schema_version=schema_version,
    )


def _edge_id(from_type: str, from_id: str, to_type: str, to_id: str) -> str:
    content = "|".join([from_type, from_id, to_type, to_id])
    return hashlib.md5(content.encode("utf-8")).hexdigest()


def link(from_type: str, from_id: str, to_type: str, to_id: str, *, timestamp: str,
          schema_version: str = _config.SCHEMA_VERSION) -> TraceabilityEdge:
    """Records exactly one real, immutable, directed edge in the
    traceability graph. Only the mission's own disclosed edge directions
    are allowed (taxonomy.is_allowed_edge) -- an attempt to link, say, a
    ProductionRule back to an EvidencePacket directly (skipping the real
    ancestry chain) is rejected."""
    if not taxonomy.is_allowed_edge(from_type, to_type):
        raise ValueError(f"illegal traceability edge: {from_type} -> {to_type} is not a real, disclosed edge direction")
    return TraceabilityEdge(
        edge_id=_edge_id(from_type, from_id, to_type, to_id), from_type=from_type, from_id=from_id,
        to_type=to_type, to_id=to_id, timestamp=timestamp, schema_version=schema_version,
    )
