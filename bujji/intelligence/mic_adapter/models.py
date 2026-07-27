"""Intelligence Snapshot models — BUJJI Options OS, Integration Series 1,
Sprint 1.

Frozen dataclasses only, no mutable fields. Every field is a 1:1 mapping
of a field already present on MIC v2's own published `ConsumerRecord`
(mic_v2.models.consumer, Sprint 29) -- nothing here is computed,
inferred, or enriched. No trading recommendation field, no execution
field, no broker field, no PnL field exists anywhere on this object,
structurally, not merely by convention.

Honesty note: MIC v2's Consumer API (Sprint 29) exposes REFERENCE IDS
only (certification_id, compatibility_id, lineage_id, ...) -- it does
not itself carry the underlying status STRINGS (e.g. "CERTIFIED"). This
snapshot therefore carries the ids it was actually given, plus the
Consumer record's own overall `consumer_status`
(AVAILABLE/NOT_AVAILABLE/UNKNOWN); it does not fabricate a
certification/compatibility/lineage status value that was never present
in the source object. A future reader wanting those detailed statuses
would follow these ids into MIC v2's own Certification/Compatibility/
Lineage Query APIs directly -- this adapter does not do that on their
behalf, since that would mean invoking MIC v2 modules beyond the
Consumer API boundary.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class IntelligenceSnapshotProvenance:
    source: str                  # "mic_v2_consumer_api" -- always this; never a broker or execution source.
    ruleset_version: str          # This adapter's own mapper version.
    mic_schema_version: str        # The schema_version stamped on the source ConsumerRecord's own serialization.


@dataclass(frozen=True)
class IntelligenceSnapshot:
    """A read-only, BUJJI-native view of one MIC v2 ConsumerRecord.
    Every id field is copied verbatim from the source record -- never
    recomputed, never validated against anything else, never enriched.
    """

    snapshot_id: str
    timestamp: datetime
    consumer_status: str            # MIC v2's own AVAILABLE/NOT_AVAILABLE/UNKNOWN, copied verbatim.
    replay_id: str
    publication_id: str
    certification_id: str
    archive_id: str
    lineage_id: str
    compatibility_id: str
    environment_id: str
    artifact_id: str
    provenance: IntelligenceSnapshotProvenance
    # Real Opinion Source Wiring (Integration Series 4, Sprint 1) --
    # additive only, copied verbatim from the source ConsumerRecord's
    # own market_opinion_id (MIC v2 Engineering Series 19, Sprint 1 /
    # Addendum 8). None for any ConsumerRecord predating that field, and
    # for any consumer record where MIC v2 published no opinion.
    market_opinion_id: Optional[str] = None
