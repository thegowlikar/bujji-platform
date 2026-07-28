"""EPS query — Series 101. Pure, read-only lookups over a real, already-
journaled set of edges/packets/proposals -- mirrors every prior MSI
package's query.py convention. This is the real Traceability Graph
(Deliverable 6): built by walking TraceabilityEdge records, never stored
as a back-reference on the (immutable) EvidencePacket itself."""
from __future__ import annotations

from typing import Sequence, Tuple

from . import taxonomy
from .models import TraceabilityEdge


def referencing(edges: Sequence[TraceabilityEdge], to_type: str, to_id: str) -> Tuple[TraceabilityEdge, ...]:
    """Every real edge pointing AT (to_type, to_id) -- i.e. this node is
    the `to_*` side. Edge direction follows the mission's own ancestry
    diagram (Evidence -> Knowledge -> Engineering -> Production), so
    'which Knowledge Candidates reference this Evidence Packet' is
    `referenced_by(edges, NODE_EVIDENCE_PACKET, packet_id)` (edges
    ORIGINATING at the packet), not this function -- this function is for
    the opposite question: 'what points at this (to_type, to_id) node'."""
    return tuple(e for e in edges if e.to_type == to_type and e.to_id == to_id)


def referenced_by(edges: Sequence[TraceabilityEdge], from_type: str, from_id: str) -> Tuple[TraceabilityEdge, ...]:
    """Every real edge originating FROM (from_type, from_id) -- e.g.
    'which Knowledge Candidates reference this Evidence Packet' is
    `referenced_by(edges, NODE_EVIDENCE_PACKET, packet_id)`."""
    return tuple(e for e in edges if e.from_type == from_type and e.from_id == from_id)


def trace_ancestry(edges: Sequence[TraceabilityEdge], node_type: str, node_id: str) -> Tuple[TraceabilityEdge, ...]:
    """Walks the real, disclosed edge directions BACKWARD from
    (node_type, node_id) to its full real ancestry -- the concrete
    implementation of 'Production Rule <- Engineering Proposal <-
    Knowledge Candidate <- Evidence Packet <- Decision Auditor <- Market
    Recorder'. Returns every real edge on the path, in discovery order.
    A node with no incoming real edge (e.g. a raw EvidencePacket with
    nothing citing it yet) returns an empty tuple -- an honest, disclosed
    'no ancestry recorded yet', never fabricated."""
    frontier = [(node_type, node_id)]
    visited_nodes = set(frontier)
    path: list = []
    while frontier:
        current_type, current_id = frontier.pop()
        incoming = referencing(edges, current_type, current_id)
        for edge in incoming:
            path.append(edge)
            key = (edge.from_type, edge.from_id)
            if key not in visited_nodes:
                visited_nodes.add(key)
                frontier.append(key)
    return tuple(path)
