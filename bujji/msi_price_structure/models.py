"""Price Structure Intelligence models — frozen, immutable records.

Implements Deliverable 5 (`Contradiction`), Deliverable 6 (`Explanation`),
and Deliverable 2 (`PriceStructureAssessment`). Every dataclass here is
`frozen=True` and carries no logic — construction lives in `engine.py`.

---------------------------------------------------------------------
Design decision — assessment_id: deterministic content hash, over WHAT.
---------------------------------------------------------------------
`assessment_id` is a `hashlib.md5` hash over the sorted tuple of input
episode ids, plus every dimension state value, plus `schema_version` —
NEVER over `timestamp` and NEVER `uuid4()`. Two identical episode
sequences fed through `engine`/`runner` twice, at two different
wall-clock times, always produce the identical `assessment_id` —
mirroring Series 75/76/77's precedent exactly (`event_id`/`episode_id`/
`assessment_id` are all content hashes over immutable, meaning-bearing
inputs only).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


# ---------------------------------------------------------------------------
# Contradiction — Deliverable 5. A small typed record: which
# dimension/evidence conflicts, with what, and why. Never a free-text
# string standing in for structured content.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Contradiction:
    dimension_a: str        # e.g. "trend_state"
    value_a: str             # e.g. taxonomy.TREND_ESTABLISHED
    dimension_b: str        # e.g. "balance_state"
    value_b: str             # e.g. taxonomy.BALANCE_IN_BALANCE
    reason: str              # Deterministic, human-readable explanation of why these two reads conflict.


# ---------------------------------------------------------------------------
# Explanation — Deliverable 6, mandatory, mirroring Series 77's
# mandatory-explanation pattern. Every field is GENUINELY COMPUTED from
# the real reasoning trace, never templated prose with no substance.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Explanation:
    assessment_id: str
    what_changed: Optional[str]                          # vs. the previous assessment (by assessment_id); None if there was no previous assessment.
    why: Tuple[str, ...]                                  # Per-dimension reasoning trace (one entry per dimension that contributed a non-UNKNOWN/NO_*-style read).
    which_episodes_caused_it: Tuple[str, ...]             # episode_ids that produced this assessment.
    which_observations_support_it: Tuple[str, ...]        # observation_ids, traced transitively through episodes -> events -> observations.
    missing_evidence: Tuple[str, ...]                     # Honestly-disclosed gaps (e.g. insufficient price events, no volatility data for compression/expansion).
    would_increase_confidence: Tuple[str, ...]            # Deterministic, mechanical statements only — never speculative/ML-style suggestions.
    schema_version: str


# ---------------------------------------------------------------------------
# PriceStructureAssessment — Deliverable 2, immutable.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PriceStructureAssessment:
    assessment_id: str
    timestamp: str
    structure_state: str                       # One of taxonomy.ALL_STRUCTURE_STATES.
    trend_state: str                            # One of taxonomy.ALL_TREND_STATES.
    swing_state: str                            # One of taxonomy.ALL_SWING_STATES.
    compression_state: str                      # One of taxonomy.ALL_COMPRESSION_STATES.
    expansion_state: str                        # One of taxonomy.ALL_EXPANSION_STATES.
    balance_state: str                          # One of taxonomy.ALL_BALANCE_STATES.
    structure_integrity: str                    # One of taxonomy.ALL_STRUCTURE_INTEGRITY_STATES.
    confidence: str                              # One of taxonomy.ALL_CONFIDENCE_LEVELS.
    supporting_episode_ids: Tuple[str, ...]
    supporting_event_ids: Tuple[str, ...]
    supporting_observation_ids: Tuple[str, ...]
    contradictions: Tuple[Contradiction, ...]
    explanation: Explanation
    provenance: str                              # Free-text description of what produced this assessment.
    schema_version: str
    # Series 85 addendum (schema_version 1.1.0) — purely additive field.
    # Exposes the sign of the trailing price-delta run that
    # `derive_trend_state` already computes internally but never
    # returned. None when trend_state == taxonomy.TREND_NONE (no run
    # exists to have a sign); one of taxonomy.ALL_DIRECTION_SIGNALS
    # otherwise. Zero change to trend_state's own derivation/meaning.
    # Defaults to None so pre-Series-85 callers/tests remain valid.
    trend_direction_signal: Optional[str] = None
