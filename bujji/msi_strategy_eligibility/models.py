"""Strategy Eligibility Intelligence models — frozen, immutable
records.

Implements Deliverable 2 (`StrategyEligibilityAssessment`), the
`Contradiction` record, and the mandatory `Explanation` (Deliverable
5's 5 questions). Every dataclass here is `frozen=True` and carries no
logic -- construction lives in `engine.py`, mirroring
`bujji.msi_decision_synthesis.models`/`bujji.msi_consensus.models`
exactly.

---------------------------------------------------------------------
Design decision — assessment_id: deterministic content hash, over WHAT.
---------------------------------------------------------------------
`assessment_id` is a `hashlib.md5` hash over:
  * `supporting_assessment_ids` (the input MarketOpportunityAssessment's
    and ConsensusAssessment's real assessment_ids, sorted);
  * the resulting `eligible_strategy_families`/`ineligible_strategy_families`
    tuples (sorted);
  * `schema_version`.
NEVER over `timestamp`, NEVER `uuid4()`. Two identical (opportunity,
consensus) pairs fed through `engine.determine_eligibility()` twice, at
two different wall-clock times, always produce the identical
`assessment_id` -- mirrors Series 77/81's exact precedent, proven by
`tests/test_msi_strategy_eligibility.py::test_assessment_id_deterministic`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


# ---------------------------------------------------------------------------
# Contradiction — a genuine tension between the opportunity read's own
# confidence and the underlying multi-domain consensus backing it
# (e.g. a confident DIRECTIONAL_OPPORTUNITY read sitting on top of
# WEAK_CONSENSUS/conflicting domains). Never resolved -- only surfaced.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Contradiction:
    dimension_a: str    # e.g. "OPPORTUNITY_CONFIDENCE"
    value_a: str         # e.g. the opportunity's confidence_level, "HIGH"
    dimension_b: str     # e.g. "CONSENSUS_LEVEL"
    value_b: str         # e.g. the consensus's consensus_level, "WEAK_CONSENSUS"
    reason: str           # Deterministic, human-readable explanation of why these two reads conflict.


# ---------------------------------------------------------------------------
# Explanation — mandatory. Answers Deliverable 5's 5 questions as real
# computed content, genuinely per-family, never templated global prose.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Explanation:
    assessment_id: str
    why_eligible: Tuple[str, ...]                 # One entry per eligible family, citing the specific rule/evidence that admitted it.
    why_ineligible: Tuple[str, ...]                # One entry per ineligible family, citing the specific rule/evidence that excluded it.
    supporting_evidence: Tuple[str, ...]            # Which upstream fields (opportunity_state, confidence_level, consensus_level, evidence_sufficiency, ...) supported the read, stated concretely.
    weakening_evidence: Tuple[str, ...]             # Which upstream fields weakened it (e.g. conflicting_domains non-empty, evidence_sufficiency INSUFFICIENT).
    what_would_change_it: Tuple[str, ...]            # Deterministic, mechanical statements only (e.g. "consensus_level rising to STRONG_CONSENSUS or above").
    schema_version: str


# ---------------------------------------------------------------------------
# StrategyEligibilityAssessment — Deliverable 2, immutable.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class StrategyEligibilityAssessment:
    assessment_id: str
    timestamp: str
    eligible_strategy_families: Tuple[str, ...]     # Subset of taxonomy.ALL_STRATEGY_FAMILIES.
    ineligible_strategy_families: Tuple[str, ...]    # Disjoint complement subset of taxonomy.ALL_STRATEGY_FAMILIES.
    eligibility_confidence: str                      # One of taxonomy.ALL_ELIGIBILITY_CONFIDENCE_LEVELS.
    supporting_assessment_ids: Tuple[str, ...]        # The real input MarketOpportunityAssessment.assessment_id and ConsensusAssessment.assessment_id -- referenced, never copied.
    contradictions: Tuple[Contradiction, ...]
    explanation: Explanation
    provenance: str                                   # Free-text description of what produced this assessment.
    schema_version: str
