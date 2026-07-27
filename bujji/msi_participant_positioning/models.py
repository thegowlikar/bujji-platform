"""Market Participant Positioning Intelligence models — frozen,
immutable records. Mirrors bujji.msi_market_direction.models exactly.

`assessment_id` is a deterministic hashlib.md5 content hash over the
input observation ids + every lens opinion + resulting
positioning_bias + schema_version — NEVER timestamp, NEVER uuid4().
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class LensOpinion:
    """One participating lens's opinion — ALWAYS preserved in full
    inside the final assessment, never discarded or summarized away."""
    lens_name: str                          # One of taxonomy.ALL_LENS_NAMES.
    positioning_lean: str                   # One of taxonomy.ALL_POSITIONING_BIASES (never MIXED_POSITIONING at the lens level — that is an overall-reconciliation-only outcome).
    confidence: str                         # One of taxonomy.ALL_CONFIDENCE_LEVELS.
    supporting_evidence_ids: Tuple[str, ...]  # Real observation_ids this lens's opinion cites.
    reasoning: str                          # Deterministic, human-readable statement of how this lens reached its lean.


@dataclass(frozen=True)
class Explanation:
    assessment_id: str
    which_lenses_participated: Tuple[str, ...]
    which_bullish: Tuple[str, ...]
    which_bearish: Tuple[str, ...]
    which_neutral_or_unknown: Tuple[str, ...]
    per_lens_evidence: Tuple[str, ...]          # One human-readable entry per lens: "<lens_name>: <reasoning>".
    missing_evidence: Tuple[str, ...]           # Honestly-disclosed gaps (e.g. no previous snapshot for migration, no bid/ask).
    why_positioning_was_chosen: str             # Genuinely computed explanation of the reconciliation rule actually applied.
    schema_version: str


@dataclass(frozen=True)
class MarketParticipantPositioningAssessment:
    assessment_id: str
    timestamp: str
    positioning_bias: str                       # One of taxonomy.ALL_POSITIONING_BIASES.
    positioning_strength: str                   # One of taxonomy.ALL_POSITIONING_STRENGTHS.
    participating_lenses: Tuple[LensOpinion, ...]   # ALL lens opinions, full detail, never a summary/count.
    conflicting_lenses: Tuple[str, ...]         # lens_names whose positioning_lean disagrees with positioning_bias.
    supporting_observation_ids: Tuple[str, ...]  # Real OptionObservation.observation_id values (current + previous snapshot, where used).
    explanation: Explanation
    provenance: str
    schema_version: str
