"""Evidence Packet System (EPS) taxonomy — Series 101. Plain string
constants (house convention, never enum.Enum).

EPS stores FACTS, never interpretations -- there is deliberately very
little vocabulary here compared to other MSI taxonomy.py modules: no
confidence scale, no suitability states, no scoring. The only real
classification this module owns is the traceability graph's node-type
vocabulary (an Evidence Packet is a different kind of object than a
Knowledge Candidate, an Engineering Proposal, or a Production Rule, and
the graph needs to say which is which)."""
from __future__ import annotations

MSI_EVIDENCE_PACKET_VERSION = "1.0.0"
RECOGNIZED_SCHEMA_VERSIONS = ("1.0.0",)

# --- Traceability graph node types (Deliverable 6) -------------------------
NODE_EVIDENCE_PACKET = "EVIDENCE_PACKET"
NODE_KNOWLEDGE_CANDIDATE = "KNOWLEDGE_CANDIDATE"
NODE_ENGINEERING_PROPOSAL = "ENGINEERING_PROPOSAL"
NODE_PRODUCTION_RULE = "PRODUCTION_RULE"

ALL_NODE_TYPES = (NODE_EVIDENCE_PACKET, NODE_KNOWLEDGE_CANDIDATE, NODE_ENGINEERING_PROPOSAL, NODE_PRODUCTION_RULE)

# --- The one real, disclosed direction every edge must point (mission's
# own diagram: Evidence -> Knowledge -> Engineering -> Production). A
# reverse edge (e.g. PRODUCTION_RULE -> EVIDENCE_PACKET) is never
# recorded -- ancestry is always queried by walking edges backward from
# their real, forward-recorded direction (see query.trace_ancestry). -----
_ALLOWED_EDGE_DIRECTIONS = (
    (NODE_EVIDENCE_PACKET, NODE_KNOWLEDGE_CANDIDATE),
    (NODE_KNOWLEDGE_CANDIDATE, NODE_ENGINEERING_PROPOSAL),
    (NODE_EVIDENCE_PACKET, NODE_ENGINEERING_PROPOSAL),   # a proposal may cite evidence directly too, per the mission's own linking description
    (NODE_ENGINEERING_PROPOSAL, NODE_PRODUCTION_RULE),
)


def is_allowed_edge(from_type: str, to_type: str) -> bool:
    return (from_type, to_type) in _ALLOWED_EDGE_DIRECTIONS
