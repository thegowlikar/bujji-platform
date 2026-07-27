"""Market Structure Intelligence models — frozen, immutable records.

Implements Deliverable 5 (`Contradiction`), Deliverable 6 (`Explanation`),
and Deliverable 2 (`MarketStructureAssessment`). Every dataclass here is
`frozen=True` and carries no logic — construction lives in `engine.py`,
mirroring `bujji.msi_price_structure.models` exactly.

`assessment_id` is a deterministic `hashlib.md5` content hash over the
sorted input episode ids plus every dimension state plus
`schema_version` — NEVER `timestamp`, NEVER `uuid4()`. Same precedent
as Series 75/76/77/78.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class Contradiction:
    dimension_a: str
    value_a: str
    dimension_b: str
    value_b: str
    reason: str


@dataclass(frozen=True)
class Explanation:
    assessment_id: str
    what_changed: Optional[str]
    why: Tuple[str, ...]
    which_episodes_caused_it: Tuple[str, ...]
    which_observations_support_it: Tuple[str, ...]
    missing_evidence: Tuple[str, ...]
    would_increase_confidence: Tuple[str, ...]
    schema_version: str


@dataclass(frozen=True)
class MarketStructureAssessment:
    assessment_id: str
    timestamp: str
    structure_location: str                      # One of taxonomy.ALL_STRUCTURE_LOCATIONS.
    support_state: str                            # One of taxonomy.ALL_SUPPORT_STATES.
    resistance_state: str                         # One of taxonomy.ALL_RESISTANCE_STATES.
    breakout_state: str                           # One of taxonomy.ALL_BREAKOUT_STATES.
    breakdown_state: str                          # One of taxonomy.ALL_BREAKDOWN_STATES.
    retest_state: str                             # One of taxonomy.ALL_RETEST_STATES.
    rejection_state: str                          # One of taxonomy.ALL_REJECTION_STATES.
    structural_balance: str                       # One of taxonomy.ALL_STRUCTURAL_BALANCE_STATES.
    confidence: str                               # One of taxonomy.ALL_CONFIDENCE_LEVELS.
    supporting_episode_ids: Tuple[str, ...]
    supporting_event_ids: Tuple[str, ...]
    supporting_observation_ids: Tuple[str, ...]
    contradictions: Tuple[Contradiction, ...]
    explanation: Explanation
    provenance: str
    schema_version: str
