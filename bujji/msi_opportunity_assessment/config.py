"""OAE config — Series 104. Declarative only. No tunable thresholds --
the classification logic is a fixed decision tree over real evidence
presence/absence, never a score fit to outcomes."""
from __future__ import annotations

from . import taxonomy as t

DEFAULT_PROVENANCE = "bujji.msi_opportunity_assessment.engine"
SCHEMA_VERSION = t.MSI_OPPORTUNITY_ASSESSMENT_VERSION
