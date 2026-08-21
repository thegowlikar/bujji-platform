"""EPS config — Series 101. Declarative only; there are no tunable
thresholds in this package (it stores facts, not judgments)."""
from __future__ import annotations

from . import taxonomy as t

DEFAULT_PROVENANCE = "bujji.msi_evidence_packet.engine"
SCHEMA_VERSION = t.MSI_EVIDENCE_PACKET_VERSION
