"""Volatility Structure Bridge models — frozen, immutable records.

Implements Deliverable 4 (`VolatilityStructureAssessment`) plus the
typed `Explanation` pattern established since Series 78.
`assessment_id` is a deterministic hashlib.md5 content hash over every
dimension state + schema_version — NEVER timestamp, NEVER uuid4().
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class Explanation:
    assessment_id: str
    why: Tuple[str, ...]
    missing_evidence: Tuple[str, ...]
    would_increase_confidence: Tuple[str, ...]
    schema_version: str


@dataclass(frozen=True)
class VolatilityStructureAssessment:
    assessment_id: str
    timestamp: str
    volatility_regime: str          # One of taxonomy.ALL_VOLATILITY_REGIMES.
    iv_state: str                   # One of taxonomy.ALL_IV_STATES.
    expected_move_state: str        # One of taxonomy.ALL_EXPECTED_MOVE_STATES.
    skew_state: str                 # Always taxonomy.SKEW_UNKNOWN (Deliverable 1: missing).
    term_structure_state: str       # Always taxonomy.TERM_STRUCTURE_UNKNOWN (Deliverable 1: missing).
    expansion_state: str            # taxonomy.EXPANSION_*.
    compression_state: str          # taxonomy.COMPRESSION_*.
    confidence: str                 # One of taxonomy.ALL_CONFIDENCE_LEVELS.
    iv_average: Optional[float]     # Real solved IV (average of CE/PE legs), None if unsolvable -- raw evidence value alongside the classified state.
    realized_vol: Optional[float]   # Real annualized realized vol, None if insufficient candles.
    expected_move_pct: Optional[float]  # Real expected-move percentage, None if unsolvable.
    explanation: Explanation
    provenance: str
    schema_version: str
