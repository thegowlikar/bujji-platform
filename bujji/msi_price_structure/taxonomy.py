"""Price Structure Intelligence vocabulary — BUJJI Engineering Series 78
(MSI Brain 1 / PSI v1).

Implements the Price Structure slice of `docs/MSI_V1_FOUNDATION.md`
Deliverable 3 (`StructureState`, `TrendStrength`->`TrendState`,
`SwingQuality`->`SwingState`, ...), reasoned against Deliverable 1's
first-principles concept table (Trend, Swing, Impulse, Correction,
Compression, Expansion, Balance, Imbalance, Momentum, Exhaustion).

Following this project's established convention (see
`bujji/market_episode/taxonomy.py`, `bujji/msi_decision_synthesis/taxonomy.py`),
closed vocabularies here are plain string constants collected into
`ALL_*` tuples, not `enum.Enum` classes.

---------------------------------------------------------------------
DEVIATION FROM THE LITERAL SPEC — single-vs-multi-dimension
structure_state (disclosed per this sprint's explicit license to
deviate where real reasoning reveals a design issue):
---------------------------------------------------------------------
`docs/MSI_V1_FOUNDATION.md` Deliverable 3 gives ONE flat `StructureState`
enum: {IMPULSE, CORRECTION, COMPRESSION, EXPANSION, BALANCE,
TRANSITIONING, UNKNOWN} — implying a single linear state machine.
Deliverable 2's own Price Structure Intelligence field list, however,
calls for `structure_state, trend_state, swing_state, compression_state,
expansion_state, balance_state` as SEPARATE fields on the same
assessment. Building the real reasoning against the flat enum first
revealed the actual problem the field list already anticipates:
Compression and Expansion (Deliverable 1) are described as sibling,
opposite conditions of "potential energy" — a market losing range while
still extending a trend (a trend "compressing" before its next leg) is
a genuine, common real pattern. Forcing COMPRESSION and TRENDING to be
mutually exclusive values of one state machine cannot represent that
pattern at all; one of the two conclusions would have to be silently
discarded every time it occurred.

Resolution adopted here: `StructureState` is kept as a real, top-level,
composite/overall READ (this is what a single trader-facing headline
classification needs), but it is DERIVED from the independent finer
dimensions below, and it deliberately does NOT include COMPRESSION or
EXPANSION as values — those live only in their own independent
`CompressionState`/`ExpansionState` fields, which can co-occur with any
`structure_state` value. This keeps Deliverable 2's field list intact
exactly as written (all six fields exist, independently populated) while
fixing the literal Deliverable 3 diagram's inability to represent
trend+compression coexisting. See `engine.derive_structure_state` for
the deterministic composite-derivation logic and its own docstring for
per-case reasoning.
"""
from __future__ import annotations

MSI_PRICE_STRUCTURE_VERSION = "1.1.0"

# Series 85 addendum: 1.1.0 adds the purely-additive
# `trend_direction_signal` field to PriceStructureAssessment (see
# taxonomy's DirectionSignal section and engine.py's
# `derive_trend_direction_signal`). 1.0.0 remains recognized for
# backward-compatible reads of assessments produced before this change.
RECOGNIZED_SCHEMA_VERSIONS = ("1.0.0", "1.1.0")

# ---------------------------------------------------------------------------
# StructureState — the composite/overall read (see module docstring for
# the disclosed deviation from the literal Deliverable 3 enum).
# Deliberately excludes COMPRESSION/EXPANSION — those are independent
# dimensions (CompressionState/ExpansionState below), not top-level
# structure_state values, because they can coexist with any of these.
# ---------------------------------------------------------------------------
STRUCTURE_UNKNOWN = "UNKNOWN"
STRUCTURE_BALANCE = "BALANCE"
STRUCTURE_TRENDING = "TRENDING"
STRUCTURE_CORRECTING = "CORRECTING"
STRUCTURE_TRANSITIONING = "TRANSITIONING"

ALL_STRUCTURE_STATES = (
    STRUCTURE_UNKNOWN,
    STRUCTURE_BALANCE,
    STRUCTURE_TRENDING,
    STRUCTURE_CORRECTING,
    STRUCTURE_TRANSITIONING,
)

# Deliverable 4 — explicit transition table for structure_state.
# "Transitions must be evidence-driven, never time-driven": this table
# is consulted ONLY inside engine.derive_structure_state, which is
# itself only ever invoked from runner functions that require a new
# Episode/MarketEvent input to run at all. There is deliberately NO
# time-advance entrypoint anywhere in this package (unlike
# bujji.market_episode's advance_time) — the absence of such a function
# is itself the structural enforcement of "never time-driven", proven
# by tests.test_evidence_driven_not_time_driven.
VALID_STRUCTURE_TRANSITIONS = {
    STRUCTURE_UNKNOWN: (STRUCTURE_UNKNOWN, STRUCTURE_BALANCE, STRUCTURE_TRENDING, STRUCTURE_CORRECTING, STRUCTURE_TRANSITIONING),
    STRUCTURE_BALANCE: (STRUCTURE_BALANCE, STRUCTURE_TRENDING, STRUCTURE_TRANSITIONING),
    STRUCTURE_TRENDING: (STRUCTURE_TRENDING, STRUCTURE_CORRECTING, STRUCTURE_TRANSITIONING, STRUCTURE_BALANCE),
    STRUCTURE_CORRECTING: (STRUCTURE_CORRECTING, STRUCTURE_TRENDING, STRUCTURE_BALANCE, STRUCTURE_TRANSITIONING),
    STRUCTURE_TRANSITIONING: (STRUCTURE_TRANSITIONING, STRUCTURE_TRENDING, STRUCTURE_CORRECTING, STRUCTURE_BALANCE, STRUCTURE_UNKNOWN),
}


def is_valid_structure_transition(from_state: str, to_state: str) -> bool:
    return to_state in VALID_STRUCTURE_TRANSITIONS.get(from_state, ())


# ---------------------------------------------------------------------------
# TrendState — per Deliverable 1's Trend concept ("a directional
# sequence of higher/lower swing structure the market is currently
# extending"), derived from the price-event delta-sign run.
# ---------------------------------------------------------------------------
TREND_NONE = "NO_TREND"
TREND_EMERGING = "EMERGING_TREND"
TREND_ESTABLISHED = "ESTABLISHED_TREND"
TREND_WEAKENING = "WEAKENING_TREND"

ALL_TREND_STATES = (TREND_NONE, TREND_EMERGING, TREND_ESTABLISHED, TREND_WEAKENING)

# ---------------------------------------------------------------------------
# Series 85 addendum — DirectionSignal. `derive_trend_state` already
# computes a signed per-delta value internally (the trailing run's
# sign) but never exposed it publicly, per Series 84's investigation
# (docs/DIRECTIONAL_OWNERSHIP_INVESTIGATION.md). This is a PURELY
# ADDITIVE exposure of that already-computed internal value — it does
# NOT change trend_state's derivation, meaning, or values in any way.
# Populated only when a real trailing run exists (trend_state in
# EMERGING/ESTABLISHED/WEAKENING); None when trend_state is NO_TREND
# (no run exists to have a sign at all — honestly absent, not 0/UNKNOWN).
# ---------------------------------------------------------------------------
DIRECTION_UP = "UP"
DIRECTION_DOWN = "DOWN"

ALL_DIRECTION_SIGNALS = (DIRECTION_UP, DIRECTION_DOWN)

# ---------------------------------------------------------------------------
# SwingState — per Deliverable 1's Swing concept ("a local extreme
# CONFIRMED by subsequent opposing structure").
# ---------------------------------------------------------------------------
SWING_NO_DATA = "NO_SWING_DATA"
SWING_FORMING = "FORMING"
SWING_CONFIRMED = "CONFIRMED"

ALL_SWING_STATES = (SWING_NO_DATA, SWING_FORMING, SWING_CONFIRMED)

# ---------------------------------------------------------------------------
# CompressionState / ExpansionState — independent dimensions (see
# module docstring). Necessarily APPROXIMATE: real range/volatility
# data (the Volatility Structure brain, per Deliverable 8's Brain 3)
# does not exist yet, so these are derived only from the magnitude
# sequence of price-change deltas available through the Episode/
# MarketEvent chain — a partial, price-only proxy for true range
# contraction/expansion. Disclosed explicitly in docs and in every
# assessment's Explanation.missing_evidence when relevant.
# ---------------------------------------------------------------------------
COMPRESSION_NOT_DETECTED = "NOT_DETECTED"
COMPRESSION_EARLY = "EARLY"
COMPRESSION_CONFIRMED = "CONFIRMED"

ALL_COMPRESSION_STATES = (COMPRESSION_NOT_DETECTED, COMPRESSION_EARLY, COMPRESSION_CONFIRMED)

EXPANSION_NOT_DETECTED = "NOT_DETECTED"
EXPANSION_EARLY = "EARLY"
EXPANSION_CONFIRMED = "CONFIRMED"

ALL_EXPANSION_STATES = (EXPANSION_NOT_DETECTED, EXPANSION_EARLY, EXPANSION_CONFIRMED)

# ---------------------------------------------------------------------------
# BalanceState — per Deliverable 1's Balance/Imbalance/Auction concepts.
# ---------------------------------------------------------------------------
BALANCE_UNKNOWN = "UNKNOWN"
BALANCE_IN_BALANCE = "IN_BALANCE"
BALANCE_IMBALANCED = "IMBALANCED"
BALANCE_TRANSITIONING = "TRANSITIONING"

ALL_BALANCE_STATES = (BALANCE_UNKNOWN, BALANCE_IN_BALANCE, BALANCE_IMBALANCED, BALANCE_TRANSITIONING)

# ---------------------------------------------------------------------------
# StructureIntegrity — how internally consistent the dimension reads
# are with each other (Deliverable 2 field). Purely a function of
# contradiction count (see engine.derive_structure_integrity).
# ---------------------------------------------------------------------------
INTEGRITY_COHERENT = "COHERENT"
INTEGRITY_PARTIALLY_COHERENT = "PARTIALLY_COHERENT"
INTEGRITY_CONFLICTED = "CONFLICTED"

ALL_STRUCTURE_INTEGRITY_STATES = (INTEGRITY_COHERENT, INTEGRITY_PARTIALLY_COHERENT, INTEGRITY_CONFLICTED)

# ---------------------------------------------------------------------------
# ConfidenceLevel — independently-defined (not imported), but
# deliberately using the same clean NONE/LOW/MODERATE/HIGH scheme
# Series 77's msi_decision_synthesis uses, per this sprint's explicit
# instruction to reuse vocabulary without importing implementation.
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
# `detail.delta` this engine may reason over (mirrors
# `bujji.market_episode.taxonomy._EVENT_TYPE_TO_EPISODE_TYPE`'s
# per-family membership discipline: structural membership only, never
# a meaning judgment).
# ---------------------------------------------------------------------------
PRICE_STRUCTURE_EVENT_TYPES = (
    "PRICE_CHANGED",
    "PRICE_GAP_DETECTED",
    "NEW_SESSION_HIGH",
    "NEW_SESSION_LOW",
)
