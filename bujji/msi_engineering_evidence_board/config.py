"""EEB config — Series 106. Declarative only. No tunable thresholds --
the review logic is a fixed decision tree over real evidence
presence/state, never a score fit to outcomes."""
from __future__ import annotations

from . import taxonomy as t

DEFAULT_PROVENANCE = "bujji.msi_engineering_evidence_board.engine"
SCHEMA_VERSION = t.MSI_ENGINEERING_EVIDENCE_BOARD_VERSION
