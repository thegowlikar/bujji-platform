"""Volatility Intelligence vocabulary — BUJJI Options OS v3, Trading
Brain Intelligence Upgrade, Phase 2.

Answers ONE question: "what is volatility telling us?" It never
answers "should we sell premium?" -- that decision belongs entirely
to `strategy_evaluator`. No field here is a trade recommendation.

Plain string constants (house convention). Every value is a named,
disclosed category with a stated reason -- never a numeric weight, a
probability, or a machine-learned value. No new volatility math is
computed anywhere in this package -- every field is either a direct
translation of an already-real value from `msi_volatility_structure`
(VSB) or `mic_v0.volatility_classifier`, or a small, disclosed
synthesis rule over those two real reads (see engine.py).
"""
from __future__ import annotations

VOLATILITY_INTELLIGENCE_VERSION = "1.0.0"

# ---------------------------------------------------------------------------
# IVState -- direct translation of VSB's real iv_state
# (IV_RICH/IV_CHEAP/IV_FAIR/UNKNOWN), stripped of the IV_ prefix per
# this package's own vocabulary. Never recomputed.
# ---------------------------------------------------------------------------
IV_CHEAP = "CHEAP"
IV_FAIR = "FAIR"
IV_RICH = "RICH"
IV_UNKNOWN = "UNKNOWN"

ALL_IV_STATES = (IV_CHEAP, IV_FAIR, IV_RICH, IV_UNKNOWN)

# ---------------------------------------------------------------------------
# IVRankState -- direct pass-through of mic_v0.volatility_classifier.
# classify_volatility_state()'s real LOW/NORMAL/HIGH output (already
# these exact string values -- VOLATILITY_LOW="LOW" etc. in
# bujji.mic_v0.models), UNKNOWN when the classifier returns None
# (insufficient trailing history, per its own MIN_WINDOW=60 floor).
#
# Honesty note carried through into every reason string that uses this
# field: this is an INDEX-level (India VIX) percentile read, not a
# per-instrument option-chain-derived IV rank -- option-derived IV
# rank remains genuinely missing (see engine.py's own disclosed gap
# list).
# ---------------------------------------------------------------------------
IV_RANK_LOW = "LOW"
IV_RANK_NORMAL = "NORMAL"
IV_RANK_HIGH = "HIGH"
IV_RANK_UNKNOWN = "UNKNOWN"

ALL_IV_RANK_STATES = (IV_RANK_LOW, IV_RANK_NORMAL, IV_RANK_HIGH, IV_RANK_UNKNOWN)

# ---------------------------------------------------------------------------
# VolatilityState -- a NEW, disclosed 3-way bucketing of VSB's real
# volatility_regime field (REGIME_HIGH_VOLATILITY/REGIME_COMPRESSED/
# REGIME_TRANSITIONING/REGIME_STABLE/REGIME_UNKNOWN) -- classification
# of an already-real value, never new math. See engine.py for the
# exact mapping.
# ---------------------------------------------------------------------------
VOLATILITY_LOW = "LOW"
VOLATILITY_NORMAL = "NORMAL"
VOLATILITY_ELEVATED = "ELEVATED"
VOLATILITY_UNKNOWN = "UNKNOWN"

ALL_VOLATILITY_STATES = (VOLATILITY_LOW, VOLATILITY_NORMAL, VOLATILITY_ELEVATED, VOLATILITY_UNKNOWN)

# ---------------------------------------------------------------------------
# VolatilityRegime -- a NEW, disclosed translation of VSB's real,
# independent expansion_state/compression_state dimensions
# (EXPANSION_CONFIRMED/COMPRESSION_CONFIRMED/NOT_DETECTED/UNKNOWN)
# into one combined read. See engine.py for the exact mapping,
# including the defensive UNKNOWN fallback if both were somehow
# CONFIRMED at once (should not happen given VSB's own single-ratio
# derivation, but never silently picked one over the other).
# ---------------------------------------------------------------------------
REGIME_EXPANDING = "EXPANDING"
REGIME_CONTRACTING = "CONTRACTING"
REGIME_STABLE = "STABLE"
REGIME_UNKNOWN = "UNKNOWN"

ALL_VOLATILITY_REGIMES = (REGIME_EXPANDING, REGIME_CONTRACTING, REGIME_STABLE, REGIME_UNKNOWN)

# ---------------------------------------------------------------------------
# ExpectedMoveState -- direct translation of VSB's real
# expected_move_state (NARROW/MODERATE/WIDE/UNKNOWN), renamed to this
# package's own LOW/NORMAL/HIGH vocabulary. Never recomputed.
# ---------------------------------------------------------------------------
EXPECTED_MOVE_LOW = "LOW"
EXPECTED_MOVE_NORMAL = "NORMAL"
EXPECTED_MOVE_HIGH = "HIGH"
EXPECTED_MOVE_UNKNOWN = "UNKNOWN"

ALL_EXPECTED_MOVE_STATES = (EXPECTED_MOVE_LOW, EXPECTED_MOVE_NORMAL, EXPECTED_MOVE_HIGH, EXPECTED_MOVE_UNKNOWN)

# ---------------------------------------------------------------------------
# SkewState / TermStructureState -- always UNKNOWN. Genuinely missing
# codebase-wide (confirmed by direct trace, not assumption): no
# cross-strike IV curve, no multi-expiry IV comparison exists
# anywhere. Explicitly out of scope for Phase 2 per instruction.
# ---------------------------------------------------------------------------
SKEW_UNKNOWN = "UNKNOWN"
TERM_STRUCTURE_UNKNOWN = "UNKNOWN"

# ---------------------------------------------------------------------------
# VolatilityQuality -- a NEW synthesis field: how much real,
# corroborated evidence backs this assessment across its two real
# sources (VSB's instrument-level iv_state, the classifier's
# index-level iv_rank_state). Deterministic rule table (engine.py):
#   UNKNOWN  = neither source produced real (non-UNKNOWN) evidence.
#   MODERATE = exactly one source produced real evidence.
#   STRONG   = both sources produced real evidence AND they agree
#              (RICH<->HIGH, CHEAP<->LOW, FAIR<->NORMAL).
#   WEAK     = both sources produced real evidence but DISAGREE --
#              deliberately the lowest tier, not a middle ground, so a
#              real conflict is never softened into an average.
# ---------------------------------------------------------------------------
QUALITY_STRONG = "STRONG"
QUALITY_MODERATE = "MODERATE"
QUALITY_WEAK = "WEAK"
QUALITY_UNKNOWN = "UNKNOWN"

ALL_VOLATILITY_QUALITIES = (QUALITY_STRONG, QUALITY_MODERATE, QUALITY_WEAK, QUALITY_UNKNOWN)

# ---------------------------------------------------------------------------
# ConfidenceLevel -- established NONE/LOW/MODERATE/HIGH convention,
# directly derived from volatility_quality (see engine.py).
# ---------------------------------------------------------------------------
CONFIDENCE_NONE = "NONE"
CONFIDENCE_LOW = "LOW"
CONFIDENCE_MODERATE = "MODERATE"
CONFIDENCE_HIGH = "HIGH"

ALL_CONFIDENCE_LEVELS = (CONFIDENCE_NONE, CONFIDENCE_LOW, CONFIDENCE_MODERATE, CONFIDENCE_HIGH)
_CONFIDENCE_RANK = {level: i for i, level in enumerate(ALL_CONFIDENCE_LEVELS)}


def confidence_rank(level: str) -> int:
    return _CONFIDENCE_RANK[level]
