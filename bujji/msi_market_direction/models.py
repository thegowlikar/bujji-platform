"""Market Direction Intelligence models — frozen, immutable records.

Implements Deliverable 4 (`MarketDirectionAssessment`) plus the
LensOpinion/Explanation records Deliverable 2/5/6 require. Every
dataclass is frozen=True, no logic — construction lives in engine.py.

`assessment_id` is a deterministic hashlib.md5 content hash over the
input assessment ids + every lens opinion + resulting overall_direction
+ schema_version — NEVER timestamp, NEVER uuid4(). Same precedent as
every prior MSI series (75/76/77/78/79/81/82/83).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class LensOpinion:
    """One participating lens's opinion — ALWAYS preserved in full
    inside the final MarketDirectionAssessment, never discarded or
    summarized away (Deliverable 5: "preserve every lens opinion")."""
    lens_name: str                          # One of taxonomy.KNOWN_LENS_NAMES.
    directional_lean: str                   # One of taxonomy.ALL_DIRECTIONAL_LEANS (never MIXED at the lens level — MIXED is an overall-reconciliation-only outcome).
    confidence: str                         # One of taxonomy.ALL_CONFIDENCE_LEVELS.
    supporting_evidence_ids: Tuple[str, ...]  # Real episode/event/observation ids this lens's opinion cites.
    reasoning: str                          # Deterministic, human-readable statement of how this lens reached its lean.


@dataclass(frozen=True)
class Explanation:
    assessment_id: str
    which_lenses_participated: Tuple[str, ...]
    which_bullish: Tuple[str, ...]
    which_bearish: Tuple[str, ...]
    which_neutral_or_unknown: Tuple[str, ...]
    per_lens_evidence: Tuple[str, ...]          # One human-readable entry per lens: "<lens_name>: <reasoning>".
    why_not_a_simple_vote: str                  # Genuinely computed explanation of the reconciliation rule actually applied.
    schema_version: str


@dataclass(frozen=True)
class MarketDirectionAssessment:
    assessment_id: str
    timestamp: str
    overall_direction: str                      # One of taxonomy.ALL_DIRECTIONAL_LEANS.
    overall_confidence: str                     # One of taxonomy.ALL_CONFIDENCE_LEVELS.
    participating_lenses: Tuple[LensOpinion, ...]   # ALL lens opinions, full detail, never a summary/count.
    conflicting_lenses: Tuple[str, ...]         # lens_names whose directional_lean disagrees with overall_direction.
    supporting_assessment_ids: Tuple[str, ...]  # Real PriceStructureAssessment.assessment_id / MarketStructureAssessment.assessment_id values.
    explanation: Explanation
    provenance: str
    schema_version: str
