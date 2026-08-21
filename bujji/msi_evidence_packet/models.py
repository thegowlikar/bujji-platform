"""Evidence Packet System (EPS) models — Series 101. Frozen dataclasses
throughout (house convention) -- `frozen=True` is the primary,
structural enforcement of this package's Immutability requirement
(Deliverable 5): a packet cannot be edited after construction, full
stop, not merely by convention."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class Metric:
    """One real, disclosed measurement -- never an opinion. `value` is
    stored as its real string representation (the source of truth for
    hashing/serialization); numeric callers pass str(value)."""
    name: str
    value: str
    unit: Optional[str] = None


@dataclass(frozen=True)
class EvidencePacket:
    """The permanent laboratory notebook for a future engineering
    decision. Contains ONLY facts, measurements, and references -- no
    recommendation, suggestion, optimisation, threshold change, or
    strategy preference may ever be stored here (enforced by
    engine.py's construction function refusing fields that don't exist
    on this dataclass; there is structurally nowhere to put an opinion)."""
    # --- Identity ---
    packet_id: str                         # md5 content hash -- IS the identity; same real inputs always
                                            # produce the same id (Reproducibility, Deliverable 8), and a
                                            # genuine correction necessarily produces a DIFFERENT id (Immutability).
    created_timestamp: str
    mle_version: str
    production_version: str                # real git commit hash, caller-supplied -- EPS never inspects git itself.
    replay_version: str                    # real replay script/tag identifier, caller-supplied.

    # --- Source Evidence ---
    decision_record_ids: Tuple[str, ...]   # real bujji.msi_decision_auditor.DecisionRecord.decision_id values.
    outcome_record_ids: Tuple[str, ...]    # real bujji.msi_decision_auditor.OutcomeRecord.outcome_id values.
    journal_references: Tuple[str, ...]    # real journal file paths / line references.
    market_recorder_session: str           # real SessionDriver session identifier.
    replay_session: str                    # real replay run identifier, if this packet originated from a replay.

    # --- Context ---
    market_classification: str             # caller-supplied real classification (e.g. from MLE) -- EPS does not classify.
    trading_day_classification: str
    regime: str
    expiry_context: str
    volatility_context: str

    # --- Supporting Material ---
    statistics: Tuple[Metric, ...]
    charts: Tuple[str, ...]                # REFERENCES (paths/URIs/descriptions), never embedded binary data.
    derived_measurements: Tuple[Metric, ...]

    schema_version: str


@dataclass(frozen=True)
class EngineeringProposal:
    """Links Knowledge Candidate(s) and Evidence Packet(s) to a proposed
    production change. NOT implementation -- `proposed_change` is a
    disclosed, plain-language description only; nothing in this package
    ever writes code or touches Production. Explicitly outside
    Production per the mission's own statement."""
    proposal_id: str
    created_timestamp: str
    knowledge_candidate_ids: Tuple[str, ...]
    evidence_packet_ids: Tuple[str, ...]
    proposed_change: str
    reasoning: Tuple[str, ...]
    schema_version: str


@dataclass(frozen=True)
class TraceabilityEdge:
    """One real, immutable, append-only edge in the traceability graph
    (Deliverable 6). Edges are never deleted or rewritten -- the graph
    at any point in time is exactly the set of edges recorded so far."""
    edge_id: str
    from_type: str                         # taxonomy.ALL_NODE_TYPES
    from_id: str
    to_type: str
    to_id: str
    timestamp: str
    schema_version: str
