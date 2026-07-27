"""Static configuration for the Strategy Eligibility Intelligence
engine.

Every threshold below is fixed, disclosed configuration -- never fit
or tuned against outcomes, mirroring `bujji.msi_decision_synthesis.
config`/`bujji.msi_consensus.config`'s exact posture. Changing a
threshold is a deliberate, reviewed edit to this file, never a
runtime-learned value.
"""
from __future__ import annotations

from . import taxonomy

SCHEMA_VERSION = taxonomy.SEI_VERSION

DEFAULT_PROVENANCE = "msi_strategy_eligibility.engine.determine_eligibility"

# ---------------------------------------------------------------------------
# Gating thresholds -- deterministic bands used by engine.py's rules.
# consensus_level and evidence_sufficiency below this rank are treated
# as "low coherence" -- eligibility must not be confidently granted
# regardless of how clean the opportunity read looks (this is the
# central rule that proves SEI does something 77 alone cannot, since
# 77's synthesize() never receives a ConsensusAssessment at all).
# ---------------------------------------------------------------------------
MIN_CONSENSUS_RANK_FOR_NORMAL_ELIGIBILITY = 3   # STRONG_CONSENSUS or above (see msi_consensus.taxonomy.CONSENSUS_LEVEL_RANK).
MIN_SUFFICIENCY_RANK_FOR_NORMAL_ELIGIBILITY = 2  # ADEQUATE or above (see msi_consensus.taxonomy.SUFFICIENCY_RANK).

# Below these ranks, eligibility_confidence is capped at LOW, and only
# the most conservative, defined-risk families remain eligible.
MIN_CONSENSUS_RANK_FOR_ANY_ELIGIBILITY = 1   # WEAK_CONSENSUS or above; NO_CONSENSUS (rank 0) collapses to zero eligible families.
MIN_SUFFICIENCY_RANK_FOR_ANY_ELIGIBILITY = 1  # LIMITED or above; INSUFFICIENT (rank 0) collapses to zero eligible families.

# opportunity confidence_level rank floor (see msi_decision_synthesis.
# taxonomy.CONFIDENCE_RANK) below which the opportunity read itself is
# too thin to license any family selection, independent of consensus.
MIN_OPPORTUNITY_CONFIDENCE_RANK_FOR_ANY_ELIGIBILITY = 1  # LOW or above; NONE (rank 0) collapses to zero eligible families.

# The conservative "fallback" family set retained even under
# low-coherence conditions where SOME (but not full) eligibility still
# holds -- always defined-risk, always the most conservative postures.
CONSERVATIVE_FALLBACK_FAMILIES = (
    taxonomy.FAMILY_DEFINED_RISK_NEUTRAL,
)
