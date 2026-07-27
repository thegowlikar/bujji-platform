"""Strategy Selector models — frozen, immutable records.

Implements Deliverable 3 (`StrategySelectionAssessment`) plus the
typed `Explanation` pattern established since Series 78.
`assessment_id` is a deterministic hashlib.md5 content hash over the
active market states + selected family + candidate scores + schema_version
— NEVER timestamp, NEVER uuid4().
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class CandidateScore:
    """One SUITABLE (per Series 87/88), non-disqualified-or-disqualified
    candidate's real, disclosed match score against the active market
    states -- never a numeric optimisation target, a categorical match
    count."""
    strategy_family: str
    disqualified: bool
    disqualifying_states: Tuple[str, ...]   # Forbidden states that were active, if disqualified.
    matched_preferred_states: Tuple[str, ...]
    matched_acceptable_states: Tuple[str, ...]
    match_score: Optional[int]               # None if disqualified.


@dataclass(frozen=True)
class Explanation:
    assessment_id: str
    why_this_strategy: Tuple[str, ...]
    why_not_alternatives: Tuple[str, ...]
    evidence_that_mattered_most: Tuple[str, ...]
    evidence_that_prevented_alternatives: Tuple[str, ...]
    active_market_states: Tuple[str, ...]
    schema_version: str


@dataclass(frozen=True)
class StrategySelectionAssessment:
    assessment_id: str
    timestamp: str
    selected_strategy_family: Optional[str]      # None if no suitable, non-disqualified candidate exists.
    alternative_candidates: Tuple[CandidateScore, ...]   # Every OTHER suitable candidate considered, full detail.
    rejection_reasons: Tuple[str, ...]           # Per-alternative rejection reasons, human-readable.
    supporting_evidence: Tuple[str, ...]         # Real input assessment ids.
    confidence: str                              # One of taxonomy.ALL_CONFIDENCE_LEVELS.
    explanation: Explanation
    provenance: str
    schema_version: str
