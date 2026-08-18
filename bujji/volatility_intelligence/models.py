"""Volatility Intelligence models — frozen, immutable assessment
record. Answers "what is volatility telling us?" only -- no field
here is a trade recommendation, a strategy name, or a should-we-sell
verdict."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple


@dataclass(frozen=True)
class VolatilityIntelligenceAssessment:
    assessment_id: str
    timestamp: str

    iv_state: str                    # ALL_IV_STATES -- from VSB, real, direct translation.
    iv_rank_state: str                # ALL_IV_RANK_STATES -- from mic_v0 classifier, real, index-level.
    volatility_state: str             # ALL_VOLATILITY_STATES -- bucketed from VSB's real volatility_regime.
    volatility_regime: str            # ALL_VOLATILITY_REGIMES -- from VSB's real expansion_state/compression_state.
    expected_move_state: str          # ALL_EXPECTED_MOVE_STATES -- from VSB's real expected_move_state.
    skew_state: str                   # Always UNKNOWN -- genuinely missing codebase-wide.
    term_structure_state: str         # Always UNKNOWN -- genuinely missing codebase-wide.
    volatility_quality: str           # ALL_VOLATILITY_QUALITIES -- corroboration between the two real sources.

    confidence: str                   # ALL_CONFIDENCE_LEVELS.
    reasons: Tuple[str, ...]          # Disclosed, human-readable, one entry per dimension plus corroboration/conflict notes.
    supporting_assessment_ids: Tuple[str, ...]  # Real VolatilityStructureAssessment.assessment_id, when supplied.
    provenance: str
    schema_version: str
