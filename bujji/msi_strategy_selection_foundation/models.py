"""Strategy Selection Foundation models — frozen, immutable records.

Implements Deliverable 3 (`StrategySuitabilityAssessment`) plus the
typed `Explanation` pattern established since Series 78.
`assessment_id` is a deterministic hashlib.md5 content hash over the
strategy_family + input assessment ids + resulting suitability +
schema_version — NEVER timestamp, NEVER uuid4().
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class Explanation:
    assessment_id: str
    why_suitable: Tuple[str, ...]
    why_unsuitable: Tuple[str, ...]
    supporting_evidence: Tuple[str, ...]
    rejecting_evidence: Tuple[str, ...]
    missing_evidence: Tuple[str, ...]
    schema_version: str


@dataclass(frozen=True)
class StrategySuitabilityAssessment:
    assessment_id: str
    timestamp: str
    strategy_family: str                        # One of taxonomy.ALL_STRATEGY_FAMILIES.
    suitability: str                             # One of taxonomy.ALL_SUITABILITY_VALUES.
    supporting_reasons: Tuple[str, ...]
    rejecting_reasons: Tuple[str, ...]
    required_missing_evidence: Tuple[str, ...]   # Domain names (taxonomy.ALL_EVIDENCE_DOMAINS) genuinely unavailable.
    confidence: str                              # One of taxonomy.ALL_CONFIDENCE_LEVELS.
    supporting_assessment_ids: Tuple[str, ...]   # Real input assessment ids (MDI/MPPI/Consensus), referenced not copied.
    explanation: Explanation
    provenance: str
    schema_version: str
