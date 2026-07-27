"""Volatility Structure Bridge vocabulary — BUJJI Engineering Series 88
(VSB v1).

Follows this project's established convention (plain string constants,
not enum.Enum).

---------------------------------------------------------------------
Deliverable 1 capability audit (see docs/VOLATILITY_STRUCTURE_BRIDGE.md
for the full table with file:line citations) that shapes this module's
scope, restated briefly:
---------------------------------------------------------------------
ALREADY REUSABLE (imported directly, PERMANENT — this pure math stays
in bujji/intelligence/ long-term; this package imports it, never forks
it): solve_implied_volatility, _annualized_realized_vol,
compute_expected_move, VolatilityBrain._classify_richness (volatility_brain.py);
_bs_delta/_bs_gamma/_bs_theta/_bs_vega, GreeksBrain._classify_exposure
(greeks_brain.py).

ADAPTER REQUIRED, TEMPORARY BRIDGE (disclosed migration plan: this is a
PRICE-based proxy for expansion/compression, not a genuine IV-based
volatility-structure signal — it should be REPLACED once real
multi-strike/multi-expiry IV time-series data exists to compute
expansion/compression from IV itself, not from realized price
statistics): RegimeBrain._compression_ratio/_log_returns/_stdev and
its COMPRESSION_RATIO_THRESHOLD/EXPANSION_RATIO_THRESHOLD constants
(regime_brain.py).

MISSING (this sprint does NOT build these — always reported as
UNKNOWN/None, never fabricated): IV Rank, IV Percentile, Skew, Smile,
Term Structure, Vanna, Charm.

UNSUITABLE FOR DIRECT REUSE: VolatilityBrain.analyze()/GreeksBrain.analyze()/
RegimeBrain.analyze() all call now_ist() internally — this bridge NEVER
calls these wrapper methods, only their pure inner functions, with a
caller-supplied timestamp throughout.
"""
from __future__ import annotations

MSI_VOLATILITY_STRUCTURE_VERSION = "1.0.0"
RECOGNIZED_SCHEMA_VERSIONS = ("1.0.0",)

# ---------------------------------------------------------------------------
# IVState — reuses Richness's real IV_RICH/IV_CHEAP/IV_FAIR classification
# (volatility_brain.py) by NAME only (this package does not import the
# legacy Richness enum type directly, mirroring the established
# "downstream consumer translates, never imports a sibling's real type
# across domain boundaries it doesn't own" discipline) -- adds UNKNOWN
# for genuinely insufficient evidence.
# ---------------------------------------------------------------------------
IV_RICH = "IV_RICH"
IV_CHEAP = "IV_CHEAP"
IV_FAIR = "IV_FAIR"
IV_UNKNOWN = "UNKNOWN"

ALL_IV_STATES = (IV_RICH, IV_CHEAP, IV_FAIR, IV_UNKNOWN)

# ---------------------------------------------------------------------------
# VolatilityRegime -- scoped to the genuinely volatility-relevant subset
# of RegimeBrain's classification (VOLATILE/COMPRESSED/TRANSITIONING);
# RegimeBrain's own TRENDING/RANGING are PRICE-DIRECTION concepts,
# already Price Structure's (Series 78) domain, deliberately excluded
# here to avoid duplicating that brain's scope.
# ---------------------------------------------------------------------------
REGIME_HIGH_VOLATILITY = "HIGH_VOLATILITY"
REGIME_COMPRESSED = "COMPRESSED"
REGIME_TRANSITIONING = "TRANSITIONING"
# BUGFIX (post-Series-88, "calibration mismatch" follow-up): a real
# compression_ratio computed but landing in the ambiguous middle (not
# extreme enough to call COMPRESSED or TRANSITIONING, and realized_vol
# below VOL_HIGH_THRESHOLD) is REAL EVIDENCE of an unremarkable/stable
# reading -- it must never be conflated with UNKNOWN (genuinely
# insufficient candles to compute anything at all). Confirmed by direct
# measurement: on the real 41-day corpus, 100% (31/31) of prior UNKNOWN
# regime reads had a real, computed compression_ratio -- zero were from
# missing data. REGIME_STABLE is the honest label for that case.
REGIME_STABLE = "STABLE"
REGIME_UNKNOWN = "UNKNOWN"

ALL_VOLATILITY_REGIMES = (REGIME_HIGH_VOLATILITY, REGIME_COMPRESSED, REGIME_TRANSITIONING, REGIME_STABLE, REGIME_UNKNOWN)

# ---------------------------------------------------------------------------
# ExpectedMoveState -- a NEW, small, disclosed bucketing of the real
# expected_move_pct value (no such bucketing exists in legacy code;
# this is classification, not new math, over an already-real number).
# ---------------------------------------------------------------------------
EXPECTED_MOVE_NARROW = "NARROW"
EXPECTED_MOVE_MODERATE = "MODERATE"
EXPECTED_MOVE_WIDE = "WIDE"
EXPECTED_MOVE_UNKNOWN = "UNKNOWN"

ALL_EXPECTED_MOVE_STATES = (EXPECTED_MOVE_NARROW, EXPECTED_MOVE_MODERATE, EXPECTED_MOVE_WIDE, EXPECTED_MOVE_UNKNOWN)

# ---------------------------------------------------------------------------
# SkewState / TermStructureState -- always UNKNOWN (Deliverable 1: MISSING).
# ---------------------------------------------------------------------------
SKEW_UNKNOWN = "UNKNOWN"
TERM_STRUCTURE_UNKNOWN = "UNKNOWN"

# ---------------------------------------------------------------------------
# ExpansionState / CompressionState -- independent dimensions, mirroring
# Series 78's own established compression_state/expansion_state
# co-existence pattern (never mutually exclusive with each other or
# with volatility_regime).
# ---------------------------------------------------------------------------
EXPANSION_CONFIRMED = "CONFIRMED"
EXPANSION_NOT_DETECTED = "NOT_DETECTED"
EXPANSION_UNKNOWN = "UNKNOWN"

COMPRESSION_CONFIRMED = "CONFIRMED"
COMPRESSION_NOT_DETECTED = "NOT_DETECTED"
COMPRESSION_UNKNOWN = "UNKNOWN"

# ---------------------------------------------------------------------------
# ConfidenceLevel -- established NONE/LOW/MODERATE/HIGH convention.
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


# Disclosed, fixed bucketing thresholds for ExpectedMoveState (new
# classification, not new math -- see module docstring).
EXPECTED_MOVE_WIDE_MIN_PCT = 2.0
EXPECTED_MOVE_MODERATE_MIN_PCT = 0.8
