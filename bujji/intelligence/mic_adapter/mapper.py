"""Intelligence Snapshot Mapper — BUJJI Options OS, Integration Series 1,
Sprint 1.

A PURE mapper: converts one MIC v2 `ConsumerRecord` (the published
Consumer API contract, Sprint 29) into a BUJJI-native, immutable
`IntelligenceSnapshot`. No calculations. No inference. No enrichment.
No transformation beyond copying a field's own value across. Duck-typed
on purpose (reads attributes by name rather than importing
`mic_v2.models.consumer.ConsumerRecord`) so this module has zero import
dependency on MIC v2 at all -- it can be unit tested with any
object exposing the same attribute names.
"""
from __future__ import annotations

from .models import IntelligenceSnapshot, IntelligenceSnapshotProvenance

MAPPER_VERSION = "1.0.0"

# The mic_v2.consumer.serialization.SCHEMA_VERSION this adapter was built
# against (Sprint 29 = "1.0.0"). Not read dynamically from any file --
# a fixed constant documenting what schema shape this mapper expects,
# consistent with MIC v2's own read-time-adaptation philosophy (Sprint 19):
# a future schema change on MIC v2's side would bump this constant here,
# never require guessing from file contents.
CONSUMER_SCHEMA_VERSION = "1.0.0"


def map_consumer_record(consumer_record) -> IntelligenceSnapshot:
    """Pure field-for-field mapping. `consumer_record` is expected to
    expose the same attributes as `mic_v2.models.consumer.ConsumerRecord`
    -- every value below is copied verbatim, never recomputed.
    """
    return IntelligenceSnapshot(
        snapshot_id=consumer_record.consumer_id,
        timestamp=consumer_record.timestamp,
        consumer_status=consumer_record.consumer_status,
        replay_id=consumer_record.replay_id,
        publication_id=consumer_record.publication_id,
        certification_id=consumer_record.certification_id,
        archive_id=consumer_record.archive_id,
        lineage_id=consumer_record.lineage_id,
        compatibility_id=consumer_record.compatibility_id,
        environment_id=consumer_record.environment_id,
        artifact_id=consumer_record.artifact_id,
        # getattr, not a plain attribute access: a ConsumerRecord
        # produced before MIC v2 Addendum 8 has no market_opinion_id
        # attribute at all -- this must not raise for old data.
        market_opinion_id=getattr(consumer_record, "market_opinion_id", None),
        provenance=IntelligenceSnapshotProvenance(
            source="mic_v2_consumer_api",
            ruleset_version=MAPPER_VERSION,
            mic_schema_version=CONSUMER_SCHEMA_VERSION,
        ),
    )
