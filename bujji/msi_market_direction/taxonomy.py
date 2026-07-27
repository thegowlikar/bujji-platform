"""Market Direction Intelligence vocabulary — BUJJI Engineering Series 85
(MDI v1).

Follows this project's established convention (plain string constants
collected into ALL_* tuples, not enum.Enum — see
bujji.msi_price_structure.taxonomy / bujji.msi_consensus.taxonomy).

---------------------------------------------------------------------
Design note — three genuinely distinct "no clean bullish/bearish"
outcomes (per Series 84's investigation and this sprint's own
Deliverable 3):
---------------------------------------------------------------------
NEUTRAL = participating lenses genuinely AGREE there is no directional
bias (e.g. price is range-bound with no breakout evidence either way).
MIXED   = participating lenses genuinely DISAGREE — some lean bullish,
some lean bearish. This is a real, distinct market state per this
sprint's explicit instruction: "never collapse disagreement into
UNKNOWN." MIXED must never be silently averaged into NEUTRAL either —
"the lenses agree on nothing" and "the lenses agree there's nothing"
are different facts.
UNKNOWN = insufficient evidence existed for ANY lens to form an
opinion at all (absence of signal, not disagreement, not agreement).
These three states are proven distinct by
tests/test_msi_market_direction_intelligence.py.
"""
from __future__ import annotations

MSI_MARKET_DIRECTION_VERSION = "1.0.0"

RECOGNIZED_SCHEMA_VERSIONS = ("1.0.0",)

# ---------------------------------------------------------------------------
# DirectionalLean — Deliverable 3.
# ---------------------------------------------------------------------------
STRONG_BULLISH = "STRONG_BULLISH"
BULLISH = "BULLISH"
WEAK_BULLISH = "WEAK_BULLISH"
NEUTRAL = "NEUTRAL"
WEAK_BEARISH = "WEAK_BEARISH"
BEARISH = "BEARISH"
STRONG_BEARISH = "STRONG_BEARISH"
MIXED = "MIXED"
UNKNOWN = "UNKNOWN"

# Ordered bearish(-3) .. bullish(+3), used to band overall_direction by
# a signed average lean-rank across agreeing lenses. NEUTRAL/MIXED/
# UNKNOWN are not part of this ordered band scale — they are distinct
# outcomes assigned directly by reconcile_lenses's own logic, never by
# interpolating this scale.
_BULLISH_BAND_SEQUENCE = (
    STRONG_BEARISH, BEARISH, WEAK_BEARISH, NEUTRAL, WEAK_BULLISH, BULLISH, STRONG_BULLISH,
)
_BAND_RANK = {lean: i - 3 for i, lean in enumerate(_BULLISH_BAND_SEQUENCE)}  # -3 .. +3


def band_rank(lean: str) -> int:
    """Signed rank (-3 strong-bearish .. +3 strong-bullish) for a
    per-lens directional_lean value. Callers must never call this on
    MIXED/UNKNOWN — those are not band-scale values."""
    return _BAND_RANK[lean]


def band_from_rank(rank: int) -> str:
    rank = max(-3, min(3, rank))
    return _BULLISH_BAND_SEQUENCE[rank + 3]


ALL_DIRECTIONAL_LEANS = (
    STRONG_BULLISH, BULLISH, WEAK_BULLISH, NEUTRAL,
    WEAK_BEARISH, BEARISH, STRONG_BEARISH, MIXED, UNKNOWN,
)

# ---------------------------------------------------------------------------
# LensName — an OPEN, extensible registry (Deliverable 2's own explicit
# design mandate: "future lenses can be added without redesign").
# reconcile_lenses() accepts an arbitrary-length tuple of LensOpinion —
# it never branches on lens count or a hardcoded pair.
# ---------------------------------------------------------------------------
PRICE_STRUCTURE_DIRECTION = "PRICE_STRUCTURE_DIRECTION"
MARKET_STRUCTURE_DIRECTION = "MARKET_STRUCTURE_DIRECTION"

# Future lenses (Deliverable 2's own examples) — named here only so a
# future caller has a canonical string to register against; nothing in
# this package computes them yet.
VOLATILITY_DIRECTION = "VOLATILITY_DIRECTION"
OPTIONS_POSITIONING_DIRECTION = "OPTIONS_POSITIONING_DIRECTION"
FUTURES_POSITIONING_DIRECTION = "FUTURES_POSITIONING_DIRECTION"
LIQUIDITY_DIRECTION = "LIQUIDITY_DIRECTION"
CROSS_ASSET_DIRECTION = "CROSS_ASSET_DIRECTION"

KNOWN_LENS_NAMES = (
    PRICE_STRUCTURE_DIRECTION,
    MARKET_STRUCTURE_DIRECTION,
    VOLATILITY_DIRECTION,
    OPTIONS_POSITIONING_DIRECTION,
    FUTURES_POSITIONING_DIRECTION,
    LIQUIDITY_DIRECTION,
    CROSS_ASSET_DIRECTION,
)

# ---------------------------------------------------------------------------
# ConfidenceLevel — established NONE/LOW/MODERATE/HIGH convention.
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
