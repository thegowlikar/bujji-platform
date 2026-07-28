"""EPS journal — Series 101. Three append-only JSONL stores (packets,
proposals, traceability edges), mirroring
bujji.live_shadow_operator.journal.OperatorJournal's own persistence
discipline (failed_writes tracked, never silently swallowed).

Immutability enforcement (Deliverable 5) lives here, concretely: because
`packet_id`/`proposal_id`/`edge_id` are content hashes (engine.py), the
SAME real id can only ever arise from the SAME real content. This module
adds one more real, structural guarantee on top of that: if a caller ever
attempts to record a DIFFERENT payload under an id that already exists in
the journal, that is refused with a hard error rather than silently
appended as a second, conflicting entry for the same id -- 'corrections
create a NEW Evidence Packet' is enforced, not merely documented.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import List, Optional

from .models import EngineeringProposal, EvidencePacket, TraceabilityEdge
from .serialization import (
    edge_from_dict, edge_to_dict, packet_from_dict, packet_to_dict, proposal_from_dict, proposal_to_dict,
)


class ImmutabilityViolation(Exception):
    """Raised when a caller attempts to record different real content
    under an id that has already been journaled -- the concrete,
    enforced meaning of 'Evidence Packets are immutable: never edit,
    never overwrite, never delete'."""


class _AppendOnlyStore:
    """Shared real implementation behind PacketJournal/ProposalJournal/
    TraceabilityJournal -- same file-per-store, same failed_writes
    tracking, same immutability check, parameterised by id field name
    and to_dict/from_dict."""

    def __init__(self, path: Path, id_field: str, to_dict, from_dict, logger: logging.Logger) -> None:
        self._path = path
        self._id_field = id_field
        self._to_dict = to_dict
        self._from_dict = from_dict
        self._log = logger
        self.failed_writes: int = 0

    def _iter_entries(self):
        if not self._path.exists():
            return
        with open(self._path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue

    def record(self, obj) -> None:
        entry = self._to_dict(obj)
        real_id = entry[self._id_field]
        for existing in self._iter_entries():
            if existing[self._id_field] == real_id and existing != entry:
                raise ImmutabilityViolation(
                    f"refusing to overwrite {self._id_field}={real_id}: existing content differs from the new "
                    f"content -- a genuine correction must produce a new id (it always will, since the id is a "
                    f"content hash); this indicates a real bug, not a legitimate correction."
                )
        try:
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, default=str) + "\n")
        except Exception as exc:  # noqa: BLE001 -- persistence must never crash the caller
            self.failed_writes += 1
            self._log.error("eps_journal_write_failed %s=%s failed_writes=%d: %s",
                            self._id_field, real_id, self.failed_writes, exc, exc_info=True)

    def by_id(self, real_id: str):
        for entry in self._iter_entries():
            if entry[self._id_field] == real_id:
                return self._from_dict(entry)
        return None

    def all(self) -> List:
        return [self._from_dict(e) for e in self._iter_entries()]


class EvidencePacketJournal:
    def __init__(self, directory: Path, logger: Optional[logging.Logger] = None) -> None:
        d = Path(directory)
        d.mkdir(parents=True, exist_ok=True)
        log = logger or logging.getLogger("bujji.msi_evidence_packet.journal")
        self._packets = _AppendOnlyStore(d / "evidence_packets.jsonl", "packet_id", packet_to_dict, packet_from_dict, log)
        self._proposals = _AppendOnlyStore(d / "engineering_proposals.jsonl", "proposal_id", proposal_to_dict, proposal_from_dict, log)
        self._edges = _AppendOnlyStore(d / "traceability_edges.jsonl", "edge_id", edge_to_dict, edge_from_dict, log)

    def record_packet(self, packet: EvidencePacket) -> None:
        self._packets.record(packet)

    def record_proposal(self, proposal: EngineeringProposal) -> None:
        self._proposals.record(proposal)

    def record_edge(self, edge: TraceabilityEdge) -> None:
        self._edges.record(edge)

    def packet_by_id(self, packet_id: str) -> Optional[EvidencePacket]:
        return self._packets.by_id(packet_id)

    def proposal_by_id(self, proposal_id: str) -> Optional[EngineeringProposal]:
        return self._proposals.by_id(proposal_id)

    def all_packets(self) -> List[EvidencePacket]:
        return self._packets.all()

    def all_proposals(self) -> List[EngineeringProposal]:
        return self._proposals.all()

    def all_edges(self) -> List[TraceabilityEdge]:
        return self._edges.all()

    @property
    def failed_writes(self) -> int:
        return self._packets.failed_writes + self._proposals.failed_writes + self._edges.failed_writes
