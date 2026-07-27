"""Market Structure Intelligence vocabulary — BUJJI Engineering Series 79
(MSI Brain 2 / MSSI v1).

Implements the Support & Resistance Intelligence domain slice of
`docs/MSI_V1_FOUNDATION.md` Deliverable 2 (domain 2) and Deliverable 3,
reasoned against Deliverable 1's first-principles Acceptance/Rejection/
Auction concepts. Answers WHERE price is located relative to structure
(support/resistance/breakout/breakdown/retest/rejection/structural
balance) — orthogonal to Series 78 (`bujji.msi_price_structure`), which
answers HOW price is behaving (trend/swing/compression/expansion).

Following this project's established convention (see
`bujji/msi_price_structure/taxonomy.py`), closed vocabularies here are
plain string constants collected into `ALL_*` tuples, not `enum.Enum`
classes.

---------------------------------------------------------------------
Step 0.6 overlap-check finding (disclosed, per this sprint's explicit
instruction to confirm honestly rather than silently duplicate or
silently omit):
---------------------------------------------------------------------
`bujji/msi_price_structure/engine.py` was read in full. It tracks only
delta SIGN runs (trend/swing), delta MAGNITUDE runs (compression/
expansion), and a net-displacement ratio (balance) — it never
identifies a price LEVEL, never counts a "test"/"touch" of a level,
and never asks whether price broke/retested a prior level. There is no
support/resistance/breakout/retest concept anywhere in Series 78. This
package's `identify_structural_levels`/level-registry machinery is
therefore genuinely new, not a re-implementation of anything 78
already does.

`StructuralBalance` (this package) vs. `BalanceState` (78) is the one
place a naming collision risk exists: 78's `balance_state` asks "is
directional price MOVEMENT two-sided" (a ratio over deltas, trend-scale,
no notion of a level at all). `StructuralBalance` here asks a
different, level-scale question — "is current price currently
contained INSIDE a recognized support/resistance range" — which
requires the level registry 78 does not build. Scoped explicitly this
way to avoid the collision; see this module's `StructuralBalance`
section below.
"""
from __future__ import annotations

MSI_MARKET_STRUCTURE_VERSION = "1.0.0"

RECOGNIZED_SCHEMA_VERSIONS = ("1.0.0",)

# ---------------------------------------------------------------------------
# SupportState / ResistanceState — Deliverable 2 fields. Independent
# dimensions: a level's support/resistance read is a function of test
# count + age + recency of that specific level only, never coupled to
# any other dimension's value.
# ---------------------------------------------------------------------------
SUPPORT_NONE = "NONE"
SUPPORT_WEAK = "WEAK"
SUPPORT_DEVELOPING = "DEVELOPING"
SUPPORT_ESTABLISHED = "ESTABLISHED"

ALL_SUPPORT_STATES = (SUPPORT_NONE, SUPPORT_WEAK, SUPPORT_DEVELOPING, SUPPORT_ESTABLISHED)

RESISTANCE_NONE = "NONE"
RESISTANCE_WEAK = "WEAK"
RESISTANCE_DEVELOPING = "DEVELOPING"
RESISTANCE_ESTABLISHED = "ESTABLISHED"

ALL_RESISTANCE_STATES = (RESISTANCE_NONE, RESISTANCE_WEAK, RESISTANCE_DEVELOPING, RESISTANCE_ESTABLISHED)

# ---------------------------------------------------------------------------
# BreakoutState / BreakdownState — Deliverable 2 fields, mirror-image
# shapes. Breakout = a decisive upward break of a resistance level;
# Breakdown = a decisive downward break of a support level. Kept as
# two separate fields (not one signed field) because a single evidence
# window can, in principle, carry evidence of BOTH an old breakout and
# a newer breakdown of a different level — collapsing them would force
# a false choice.
# ---------------------------------------------------------------------------
BREAKOUT_NONE = "NONE"
BREAKOUT_DEVELOPING = "DEVELOPING"
BREAKOUT_CONFIRMED = "CONFIRMED"
BREAKOUT_FAILED = "FAILED"

ALL_BREAKOUT_STATES = (BREAKOUT_NONE, BREAKOUT_DEVELOPING, BREAKOUT_CONFIRMED, BREAKOUT_FAILED)

BREAKDOWN_NONE = "NONE"
BREAKDOWN_DEVELOPING = "DEVELOPING"
BREAKDOWN_CONFIRMED = "CONFIRMED"
BREAKDOWN_FAILED = "FAILED"

ALL_BREAKDOWN_STATES = (BREAKDOWN_NONE, BREAKDOWN_DEVELOPING, BREAKDOWN_CONFIRMED, BREAKDOWN_FAILED)

# ---------------------------------------------------------------------------
# RetestState — Deliverable 2 field: has price returned to a recently
# broken level (from the far side) to confirm it now holds as the
# opposite structural role (former resistance acting as new support, or
# vice versa)?
# ---------------------------------------------------------------------------
RETEST_NONE = "NONE"
RETEST_ACTIVE = "ACTIVE"
RETEST_CONFIRMED = "CONFIRMED"
RETEST_FAILED = "FAILED"

ALL_RETEST_STATES = (RETEST_NONE, RETEST_ACTIVE, RETEST_CONFIRMED, RETEST_FAILED)

# ---------------------------------------------------------------------------
# RejectionState — Deliverable 2 field, not given an explicit value set
# by Deliverable 4's examples. Designed here directly from Deliverable
# 1's Rejection concept ("price visiting a level and being quickly,
# forcefully returned from it ... indicating disagreement rather than
# acceptance"): NONE (no rejection evidence), WEAK (one forceful
# reversal off a level), STRONG (repeated forceful reversals off the
# same level, i.e. the level has never once been accepted through).
# ---------------------------------------------------------------------------
REJECTION_NONE = "NONE"
REJECTION_WEAK = "WEAK"
REJECTION_STRONG = "STRONG"

ALL_REJECTION_STATES = (REJECTION_NONE, REJECTION_WEAK, REJECTION_STRONG)

# ---------------------------------------------------------------------------
# StructuralBalance — Deliverable 2 field. See module docstring's
# Step 0.6 disclosure for why this is scoped differently from 78's
# balance_state: this asks only "is price currently inside a
# recognized (both-sides-established) support/resistance range,"
# never anything about directional-movement two-sidedness.
# ---------------------------------------------------------------------------
STRUCTURAL_BALANCE_UNKNOWN = "UNKNOWN"
STRUCTURAL_BALANCE_RANGE_BOUND = "RANGE_BOUND"
STRUCTURAL_BALANCE_UNBOUNDED = "UNBOUNDED"

ALL_STRUCTURAL_BALANCE_STATES = (
    STRUCTURAL_BALANCE_UNKNOWN,
    STRUCTURAL_BALANCE_RANGE_BOUND,
    STRUCTURAL_BALANCE_UNBOUNDED,
)

# ---------------------------------------------------------------------------
# StructureLocation — Deliverable 2's composite/overall read, analogous
# in spirit to 78's structure_state: deterministically DERIVED from the
# independent dimensions above plus current price's position relative
# to the level registry (see engine.derive_structure_location).
# ---------------------------------------------------------------------------
LOCATION_UNKNOWN = "UNKNOWN"
LOCATION_INSIDE_RANGE = "INSIDE_RANGE"
LOCATION_NEAR_SUPPORT = "NEAR_SUPPORT"
LOCATION_NEAR_RESISTANCE = "NEAR_RESISTANCE"
LOCATION_ABOVE_RESISTANCE = "ABOVE_RESISTANCE"
LOCATION_BELOW_SUPPORT = "BELOW_SUPPORT"
LOCATION_AT_RETEST = "AT_RETEST"

ALL_STRUCTURE_LOCATIONS = (
    LOCATION_UNKNOWN,
    LOCATION_INSIDE_RANGE,
    LOCATION_NEAR_SUPPORT,
    LOCATION_NEAR_RESISTANCE,
    LOCATION_ABOVE_RESISTANCE,
    LOCATION_BELOW_SUPPORT,
    LOCATION_AT_RETEST,
)

# Deliverable 4 — explicit transition table for structure_location.
# Consulted ONLY inside engine.derive_structure_location, itself only
# ever invoked from runner functions requiring a new Episode/MarketEvent
# input — no time-advance entrypoint exists anywhere in this package
# (mirrors Series 78's precedent exactly). The absence of such a
# function is itself the structural enforcement of "never
# time-driven", proven by tests.test_evidence_driven_not_time_driven.
VALID_STRUCTURE_LOCATION_TRANSITIONS = {
    LOCATION_UNKNOWN: ALL_STRUCTURE_LOCATIONS,
    LOCATION_INSIDE_RANGE: (LOCATION_INSIDE_RANGE, LOCATION_NEAR_SUPPORT, LOCATION_NEAR_RESISTANCE, LOCATION_AT_RETEST, LOCATION_UNKNOWN),
    LOCATION_NEAR_SUPPORT: (LOCATION_NEAR_SUPPORT, LOCATION_INSIDE_RANGE, LOCATION_BELOW_SUPPORT, LOCATION_AT_RETEST, LOCATION_UNKNOWN),
    LOCATION_NEAR_RESISTANCE: (LOCATION_NEAR_RESISTANCE, LOCATION_INSIDE_RANGE, LOCATION_ABOVE_RESISTANCE, LOCATION_AT_RETEST, LOCATION_UNKNOWN),
    LOCATION_ABOVE_RESISTANCE: (LOCATION_ABOVE_RESISTANCE, LOCATION_AT_RETEST, LOCATION_NEAR_RESISTANCE, LOCATION_UNKNOWN),
    LOCATION_BELOW_SUPPORT: (LOCATION_BELOW_SUPPORT, LOCATION_AT_RETEST, LOCATION_NEAR_SUPPORT, LOCATION_UNKNOWN),
    LOCATION_AT_RETEST: (LOCATION_AT_RETEST, LOCATION_ABOVE_RESISTANCE, LOCATION_BELOW_SUPPORT, LOCATION_INSIDE_RANGE, LOCATION_UNKNOWN),
}


def is_valid_structure_location_transition(from_state: str, to_state: str) -> bool:
    return to_state in VALID_STRUCTURE_LOCATION_TRANSITIONS.get(from_state, ())


# ---------------------------------------------------------------------------
# LevelType — internal level-registry vocabulary (not a
# MarketStructureAssessment field itself; consumed by engine.py only).
# ---------------------------------------------------------------------------
LEVEL_SUPPORT = "SUPPORT"
LEVEL_RESISTANCE = "RESISTANCE"

ALL_LEVEL_TYPES = (LEVEL_SUPPORT, LEVEL_RESISTANCE)

# ---------------------------------------------------------------------------
# ConfidenceLevel — independently-defined (not imported), same clean
# NONE/LOW/MODERATE/HIGH scheme as Series 77/78.
# ---------------------------------------------------------------------------
CONFIDENCE_NONE = "NONE"
CONFIDENCE_LOW = "LOW"
CONFIDENCE_MODERATE = "MODERATE"
CONFIDENCE_HIGH = "HIGH"

ALL_CONFIDENCE_LEVELS = (CONFIDENCE_NONE, CONFIDENCE_LOW, CONFIDENCE_MODERATE, CONFIDENCE_HIGH)

_CONFIDENCE_RANK = {level: i for i, level in enumerate(ALL_CONFIDENCE_LEVELS)}


def confidence_rank(level: str) -> int:
    return _CONFIDENCE_RANK[level]


def confidence_at_rank(rank: int) -> str:
    rank = max(0, min(rank, len(ALL_CONFIDENCE_LEVELS) - 1))
    return ALL_CONFIDENCE_LEVELS[rank]


# ---------------------------------------------------------------------------
# Which Series-75 MarketEvent types carry a price-structure-relevant
# `detail.new_value`/`detail.delta` this engine may reason over —
# identical membership list to Series 78's PRICE_STRUCTURE_EVENT_TYPES
# (same underlying raw evidence, different reasoning purpose: building
# a level registry, not detecting trend/swing/compression/expansion
# behavior).
# ---------------------------------------------------------------------------
PRICE_STRUCTURE_EVENT_TYPES = (
    "PRICE_CHANGED",
    "PRICE_GAP_DETECTED",
    "NEW_SESSION_HIGH",
    "NEW_SESSION_LOW",
)
