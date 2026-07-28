"""KVE config — Series 105. Declarative only, never tuned against replay
outcomes."""
from __future__ import annotations

from . import taxonomy as t

DEFAULT_PROVENANCE = "bujji.msi_knowledge_validation.engine"
SCHEMA_VERSION = t.MSI_KNOWLEDGE_VALIDATION_VERSION

# Real, disclosed trend-detection thresholds -- same declarative pattern
# as Series 100's own decay assessment (DECAY_IMPROVING_RATIO/
# DECAY_WEAKENING_RATIO), reimplemented locally per KVE's own zero-
# sibling-import isolation.
TREND_GROWING_RATIO = 1.25
TREND_WEAKENING_RATIO = 0.75
