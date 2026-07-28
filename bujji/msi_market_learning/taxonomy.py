"""Market Learning Engine (MLE) taxonomy — Series 100, Phase 1.0. Plain
string constants (house convention, never enum.Enum).

MLE is an EVIDENCE ENGINE, not a learning/adaptive system (per the Series
100 spec's own First Principle). This module declares only the vocabulary
Phase 1.0's infrastructure needs: the Knowledge Candidate lifecycle, the
evidence-strength ladder, and decay states. No thresholds here are ever
tuned against real trading outcomes -- they are declared once, disclosed,
and never fit to data (mirrors every other MSI taxonomy.py in this
project).
"""
from __future__ import annotations

MSI_MARKET_LEARNING_VERSION = "1.0.0"
RECOGNIZED_SCHEMA_VERSIONS = ("1.0.0",)

# --- Knowledge Candidate lifecycle. ORDER IS THE CONTRACT: a candidate may
# only ever advance to the NEXT stage in this tuple, never skip ahead, and
# never move backward except into DECAYING/ARCHIVED (see engine.py). ------
LIFECYCLE_OBSERVED = "OBSERVED"
LIFECYCLE_REPEATED = "REPEATED"
LIFECYCLE_EVIDENCE = "EVIDENCE"
LIFECYCLE_KNOWLEDGE = "KNOWLEDGE"
LIFECYCLE_ENGINEERING_CANDIDATE = "ENGINEERING_CANDIDATE"
LIFECYCLE_APPROVED = "APPROVED"
LIFECYCLE_IMPLEMENTED = "IMPLEMENTED"
LIFECYCLE_REPLAY_VALIDATED = "REPLAY_VALIDATED"
LIFECYCLE_PRODUCTION_VALIDATED = "PRODUCTION_VALIDATED"
LIFECYCLE_ACTIVE = "ACTIVE"
LIFECYCLE_DECAYING = "DECAYING"
LIFECYCLE_ARCHIVED = "ARCHIVED"

ALL_LIFECYCLE_STAGES = (
    LIFECYCLE_OBSERVED, LIFECYCLE_REPEATED, LIFECYCLE_EVIDENCE, LIFECYCLE_KNOWLEDGE,
    LIFECYCLE_ENGINEERING_CANDIDATE, LIFECYCLE_APPROVED, LIFECYCLE_IMPLEMENTED,
    LIFECYCLE_REPLAY_VALIDATED, LIFECYCLE_PRODUCTION_VALIDATED, LIFECYCLE_ACTIVE,
    LIFECYCLE_DECAYING, LIFECYCLE_ARCHIVED,
)
_LIFECYCLE_RANK = {stage: i for i, stage in enumerate(ALL_LIFECYCLE_STAGES)}

# ACTIVE may transition directly to DECAYING or ARCHIVED (real, disclosed
# exceptions to strict forward-only order -- knowledge decay is a real,
# expected end state, not a skipped stage).
_LIFECYCLE_EXTRA_ALLOWED_TRANSITIONS = {
    LIFECYCLE_ACTIVE: (LIFECYCLE_DECAYING, LIFECYCLE_ARCHIVED),
    LIFECYCLE_DECAYING: (LIFECYCLE_ARCHIVED, LIFECYCLE_ACTIVE),  # decaying knowledge can recover, disclosed
}


def lifecycle_rank(stage: str) -> int:
    return _LIFECYCLE_RANK[stage]


def is_allowed_transition(from_stage: str, to_stage: str) -> bool:
    """A transition is allowed iff it moves exactly one stage forward in
    ALL_LIFECYCLE_STAGES, or is one of the explicitly disclosed exceptions
    above. No skipping, ever."""
    if to_stage in _LIFECYCLE_EXTRA_ALLOWED_TRANSITIONS.get(from_stage, ()):
        return True
    return _LIFECYCLE_RANK.get(to_stage, -1) == _LIFECYCLE_RANK.get(from_stage, -2) + 1


# --- Evidence strength tiers. Declarative occurrence-count ladder, exactly
# as specified -- never tuned against outcomes. ----------------------------
TIER_NONE = "NONE"
TIER_WEAK = "WEAK"
TIER_MODERATE = "MODERATE"
TIER_STRONG = "STRONG"
TIER_ENGINEERING_CANDIDATE = "ENGINEERING_CANDIDATE"

ALL_EVIDENCE_TIERS = (TIER_NONE, TIER_WEAK, TIER_MODERATE, TIER_STRONG, TIER_ENGINEERING_CANDIDATE)

# --- Consistency verdict (Deliverable: Knowledge Candidate's own
# `consistency` field). Plain string, house convention. --------------------
CONSISTENCY_CONSISTENT = "CONSISTENT"
CONSISTENCY_INCONSISTENT = "INCONSISTENT"
CONSISTENCY_UNKNOWN = "UNKNOWN"  # not yet evaluated -- honest default, never guessed.

ALL_CONSISTENCY_STATES = (CONSISTENCY_CONSISTENT, CONSISTENCY_INCONSISTENT, CONSISTENCY_UNKNOWN)

# --- Decay states. Detected, never auto-acted-on (spec: "never
# automatically remove knowledge -- flag it for engineering review"). ------
DECAY_IMPROVING = "IMPROVING"
DECAY_STABLE = "STABLE"
DECAY_WEAKENING = "WEAKENING"
DECAY_UNKNOWN = "UNKNOWN"  # insufficient recent occurrences to judge -- honest default.

ALL_DECAY_STATES = (DECAY_IMPROVING, DECAY_STABLE, DECAY_WEAKENING, DECAY_UNKNOWN)

# --- Confidence. Same NONE/LOW/MODERATE/HIGH scale used project-wide
# (Sprint 121's own finding: never invent a new meaning of "confidence"
# without saying explicitly what it measures -- here, evidence strength on
# the SAME occurrence-count ladder as ALL_EVIDENCE_TIERS, not a new axis). -
CONFIDENCE_NONE = "NONE"
CONFIDENCE_LOW = "LOW"
CONFIDENCE_MODERATE = "MODERATE"
CONFIDENCE_HIGH = "HIGH"
ALL_CONFIDENCE_LEVELS = (CONFIDENCE_NONE, CONFIDENCE_LOW, CONFIDENCE_MODERATE, CONFIDENCE_HIGH)
