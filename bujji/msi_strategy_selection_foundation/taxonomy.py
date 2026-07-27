"""Strategy Selection Foundation vocabulary — BUJJI Engineering Series
87 (SSF v1), updated by the Series 88 follow-up ("Wire SSF to consume
Volatility Structure evidence").

Follows this project's established convention (plain string constants,
not enum.Enum).

---------------------------------------------------------------------
Deliverable 1 — Strategy Family Taxonomy, with real, disclosed
capability gating (Deliverable 2/7):
---------------------------------------------------------------------
Every family below lists: objective, required_market_conditions,
forbidden_conditions, required_confidence, required_evidence,
consumes (Direction/Consensus/Positioning/Volatility/Liquidity/
MarketStructure/RiskProfile), and readiness (per Deliverable 7).

Readiness is determined by real, verified data availability, not
assumption:
- Direction (MDI, Series 85), Consensus (Series 81), Positioning
  (MPPI, Series 86, options-only), Market Structure (Series 79), Risk
  Profile (Trade Intent, Series 83): real, available (unchanged from
  Series 87).
- Volatility (Series 88, Volatility Structure Bridge): **NOW REAL AND
  AVAILABLE** — `bujji.msi_volatility_structure` bridges the legacy
  Black-Scholes IV/Greeks engine into MSI, providing real
  `volatility_regime`/`iv_state`/`expected_move_state`/
  `expansion_state`/`compression_state`. This taxonomy update moves
  `DOMAIN_VOLATILITY` from `UNAVAILABLE_DOMAINS` to `AVAILABLE_DOMAINS`.
- Volatility Term Structure: introduced as its OWN, more granular
  domain (`DOMAIN_VOLATILITY_TERM_STRUCTURE`), DELIBERATELY KEPT
  SEPARATE from plain `DOMAIN_VOLATILITY` and left in
  `UNAVAILABLE_DOMAINS` — Series 88's own Deliverable 1 audit found
  `skew_state`/`term_structure_state` are ALWAYS `UNKNOWN` on
  `VolatilityStructureAssessment` (no cross-strike IV curve or
  multi-expiry comparison exists anywhere). Conflating "plain IV/regime
  available" with "term structure available" would have been a real,
  disclosed mistake — CALENDAR genuinely needs the latter, not the
  former, and must stay gated.
- Liquidity, Futures Positioning: still NOT AVAILABLE, unchanged from
  Series 87.

A strategy family whose REQUIRED evidence includes Liquidity, Futures
Positioning, or Volatility Term Structure still cannot be assessed as
SUITABLE — `assess_suitability` honestly returns INSUFFICIENT_EVIDENCE
for it. Families requiring only Direction/Consensus/Positioning/Market
Structure/Risk Profile/Volatility now receive a real SUITABLE/
UNSUITABLE read, gated by real, disclosed volatility-condition rules
in engine.py (`_VOLATILITY_RULES`), never a numeric score.
"""
from __future__ import annotations

MSI_STRATEGY_SELECTION_FOUNDATION_VERSION = "1.1.0"
# 1.1.0: Volatility wiring (this follow-up). 1.0.0 remains recognized
# for backward-compatible reads of assessments produced before this change.
RECOGNIZED_SCHEMA_VERSIONS = ("1.0.0", "1.1.0")

# ---------------------------------------------------------------------------
# Suitability — Deliverable 3's core taxonomy. Mirrors the established
# NEUTRAL/MIXED/UNKNOWN-style discipline: SUITABLE and UNSUITABLE are
# real, evidence-backed conclusions; INSUFFICIENT_EVIDENCE means the
# strategy's own required evidence is genuinely unavailable (never
# forced to SUITABLE/UNSUITABLE on partial data).
# ---------------------------------------------------------------------------
SUITABLE = "SUITABLE"
UNSUITABLE = "UNSUITABLE"
INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"

ALL_SUITABILITY_VALUES = (SUITABLE, UNSUITABLE, INSUFFICIENT_EVIDENCE)

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


# ---------------------------------------------------------------------------
# StrategyFamily — Deliverable 1's 13 families, plain string constants.
# ---------------------------------------------------------------------------
LONG_DIRECTIONAL = "LONG_DIRECTIONAL"
SHORT_DIRECTIONAL = "SHORT_DIRECTIONAL"
NEUTRAL_PREMIUM_SELLING = "NEUTRAL_PREMIUM_SELLING"
NEUTRAL_PREMIUM_BUYING = "NEUTRAL_PREMIUM_BUYING"
VOLATILITY_EXPANSION = "VOLATILITY_EXPANSION"
VOLATILITY_COMPRESSION = "VOLATILITY_COMPRESSION"
CALENDAR = "CALENDAR"
RATIO = "RATIO"
BUTTERFLY = "BUTTERFLY"
IRON_CONDOR = "IRON_CONDOR"
IRON_FLY = "IRON_FLY"
COVERED = "COVERED"
SYNTHETIC = "SYNTHETIC"

ALL_STRATEGY_FAMILIES = (
    LONG_DIRECTIONAL, SHORT_DIRECTIONAL, NEUTRAL_PREMIUM_SELLING, NEUTRAL_PREMIUM_BUYING,
    VOLATILITY_EXPANSION, VOLATILITY_COMPRESSION, CALENDAR, RATIO, BUTTERFLY,
    IRON_CONDOR, IRON_FLY, COVERED, SYNTHETIC,
)

# ---------------------------------------------------------------------------
# EvidenceDomain — Deliverable 2's capability-matrix dimensions.
# ---------------------------------------------------------------------------
DOMAIN_DIRECTION = "DIRECTION"
DOMAIN_CONSENSUS = "CONSENSUS"
DOMAIN_POSITIONING = "POSITIONING"
DOMAIN_VOLATILITY = "VOLATILITY"
DOMAIN_VOLATILITY_TERM_STRUCTURE = "VOLATILITY_TERM_STRUCTURE"  # NEW: deliberately distinct from DOMAIN_VOLATILITY.
DOMAIN_LIQUIDITY = "LIQUIDITY"
DOMAIN_MARKET_STRUCTURE = "MARKET_STRUCTURE"
DOMAIN_RISK_PROFILE = "RISK_PROFILE"
DOMAIN_FUTURES_POSITIONING = "FUTURES_POSITIONING"

ALL_EVIDENCE_DOMAINS = (
    DOMAIN_DIRECTION, DOMAIN_CONSENSUS, DOMAIN_POSITIONING, DOMAIN_VOLATILITY,
    DOMAIN_VOLATILITY_TERM_STRUCTURE, DOMAIN_LIQUIDITY, DOMAIN_MARKET_STRUCTURE,
    DOMAIN_RISK_PROFILE, DOMAIN_FUTURES_POSITIONING,
)

# Domains this codebase can genuinely supply real evidence for today.
# DOMAIN_VOLATILITY moved here from UNAVAILABLE_DOMAINS (Series 88
# Volatility Structure Bridge, now wired). DOMAIN_VOLATILITY_TERM_STRUCTURE
# stays unavailable -- confirmed absent by Series 88's own audit.
AVAILABLE_DOMAINS = (
    DOMAIN_DIRECTION, DOMAIN_CONSENSUS, DOMAIN_POSITIONING,
    DOMAIN_MARKET_STRUCTURE, DOMAIN_RISK_PROFILE, DOMAIN_VOLATILITY,
)
UNAVAILABLE_DOMAINS = (DOMAIN_VOLATILITY_TERM_STRUCTURE, DOMAIN_LIQUIDITY, DOMAIN_FUTURES_POSITIONING)

# ---------------------------------------------------------------------------
# Readiness — Deliverable 7. READY_AFTER_VOLATILITY_BRIDGE is now
# obsolete as a FUTURE state (the bridge exists) but the constant is
# kept, unused by any current family, for backward-compat with any
# stored 1.0.0-schema assessment that referenced it.
# ---------------------------------------------------------------------------
READY_TODAY = "READY_TODAY"
READY_AFTER_VOLATILITY_BRIDGE = "READY_AFTER_VOLATILITY_BRIDGE"  # No longer used by any family as of 1.1.0.
REQUIRES_FUTURES_POSITIONING = "REQUIRES_FUTURES_POSITIONING"
REQUIRES_LIQUIDITY = "REQUIRES_LIQUIDITY"
REQUIRES_DATA_NOT_YET_AVAILABLE = "REQUIRES_DATA_NOT_YET_AVAILABLE"  # multiple/richer gaps (e.g. term structure).

ALL_READINESS_STATES = (
    READY_TODAY, READY_AFTER_VOLATILITY_BRIDGE, REQUIRES_FUTURES_POSITIONING,
    REQUIRES_LIQUIDITY, REQUIRES_DATA_NOT_YET_AVAILABLE,
)

# ---------------------------------------------------------------------------
# Deliverable 1 — per-family definition. Pure declarative data; engine.py
# interprets it, never hardcodes per-family branches beyond the small,
# named `_VOLATILITY_RULES` dict (one predicate per family that
# requires DOMAIN_VOLATILITY -- see engine.py for the real conditions
# and their reasoning).
# ---------------------------------------------------------------------------
STRATEGY_DEFINITIONS = {
    LONG_DIRECTIONAL: {
        "objective": "Capture a sustained upward price move with defined directional exposure.",
        "consumes": (DOMAIN_DIRECTION, DOMAIN_CONSENSUS, DOMAIN_POSITIONING, DOMAIN_MARKET_STRUCTURE, DOMAIN_RISK_PROFILE),
        "required_evidence": (DOMAIN_DIRECTION, DOMAIN_CONSENSUS),
        "required_direction_leans": ("STRONG_BULLISH", "BULLISH", "WEAK_BULLISH"),
        "forbidden_direction_leans": ("STRONG_BEARISH", "BEARISH", "WEAK_BEARISH", "MIXED"),
        "required_min_consensus_rank": 1,
        "required_confidence": CONFIDENCE_MODERATE,
        "readiness": READY_TODAY,
    },
    SHORT_DIRECTIONAL: {
        "objective": "Capture a sustained downward price move with defined directional exposure.",
        "consumes": (DOMAIN_DIRECTION, DOMAIN_CONSENSUS, DOMAIN_POSITIONING, DOMAIN_MARKET_STRUCTURE, DOMAIN_RISK_PROFILE),
        "required_evidence": (DOMAIN_DIRECTION, DOMAIN_CONSENSUS),
        "required_direction_leans": ("STRONG_BEARISH", "BEARISH", "WEAK_BEARISH"),
        "forbidden_direction_leans": ("STRONG_BULLISH", "BULLISH", "WEAK_BULLISH", "MIXED"),
        "required_min_consensus_rank": 1,
        "required_confidence": CONFIDENCE_MODERATE,
        "readiness": READY_TODAY,
    },
    COVERED: {
        "objective": "Yield-enhance an assumed underlying position via a mildly directional overlay.",
        "consumes": (DOMAIN_DIRECTION, DOMAIN_CONSENSUS, DOMAIN_RISK_PROFILE),
        "required_evidence": (DOMAIN_DIRECTION,),
        "required_direction_leans": ("STRONG_BULLISH", "BULLISH", "WEAK_BULLISH", "NEUTRAL"),
        "forbidden_direction_leans": ("STRONG_BEARISH", "BEARISH", "MIXED"),
        "required_min_consensus_rank": 0,
        "required_confidence": CONFIDENCE_LOW,
        "readiness": READY_TODAY,
    },
    SYNTHETIC: {
        "objective": "Replicate underlying directional exposure via a combined options structure.",
        "consumes": (DOMAIN_DIRECTION, DOMAIN_CONSENSUS, DOMAIN_LIQUIDITY),
        "required_evidence": (DOMAIN_DIRECTION, DOMAIN_LIQUIDITY),
        "required_direction_leans": ("STRONG_BULLISH", "BULLISH", "STRONG_BEARISH", "BEARISH"),
        "forbidden_direction_leans": ("MIXED",),
        "required_min_consensus_rank": 1,
        "required_confidence": CONFIDENCE_MODERATE,
        "readiness": REQUIRES_LIQUIDITY,
    },
    NEUTRAL_PREMIUM_SELLING: {
        "objective": "Collect option premium in a range-bound market with elevated-but-stable volatility.",
        "consumes": (DOMAIN_DIRECTION, DOMAIN_CONSENSUS, DOMAIN_POSITIONING, DOMAIN_VOLATILITY),
        "required_evidence": (DOMAIN_DIRECTION, DOMAIN_VOLATILITY),
        "required_direction_leans": ("NEUTRAL",),
        "forbidden_direction_leans": ("MIXED",),
        "required_min_consensus_rank": 1,
        "required_confidence": CONFIDENCE_MODERATE,
        "readiness": READY_TODAY,
    },
    NEUTRAL_PREMIUM_BUYING: {
        "objective": "Profit from a large move of uncertain direction via long premium exposure.",
        "consumes": (DOMAIN_DIRECTION, DOMAIN_VOLATILITY),
        "required_evidence": (DOMAIN_VOLATILITY,),
        "required_direction_leans": ("NEUTRAL", "MIXED"),
        "forbidden_direction_leans": (),
        "required_min_consensus_rank": 0,
        "required_confidence": CONFIDENCE_MODERATE,
        "readiness": READY_TODAY,
    },
    VOLATILITY_EXPANSION: {
        "objective": "Profit from a rising volatility regime independent of direction.",
        "consumes": (DOMAIN_VOLATILITY,),
        "required_evidence": (DOMAIN_VOLATILITY,),
        "required_direction_leans": (),
        "forbidden_direction_leans": (),
        "required_min_consensus_rank": 0,
        "required_confidence": CONFIDENCE_MODERATE,
        "readiness": READY_TODAY,
    },
    VOLATILITY_COMPRESSION: {
        "objective": "Profit from a falling/compressed volatility regime independent of direction.",
        "consumes": (DOMAIN_VOLATILITY,),
        "required_evidence": (DOMAIN_VOLATILITY,),
        "required_direction_leans": (),
        "forbidden_direction_leans": (),
        "required_min_consensus_rank": 0,
        "required_confidence": CONFIDENCE_MODERATE,
        "readiness": READY_TODAY,
    },
    RATIO: {
        "objective": "Asymmetric directional exposure funded by skewed premium.",
        "consumes": (DOMAIN_DIRECTION, DOMAIN_VOLATILITY),
        "required_evidence": (DOMAIN_DIRECTION, DOMAIN_VOLATILITY),
        "required_direction_leans": ("BULLISH", "BEARISH", "STRONG_BULLISH", "STRONG_BEARISH"),
        "forbidden_direction_leans": ("MIXED",),
        "required_min_consensus_rank": 1,
        "required_confidence": CONFIDENCE_MODERATE,
        "readiness": READY_TODAY,
    },
    BUTTERFLY: {
        "objective": "Precision, low-movement play pinned around a specific structural level.",
        "consumes": (DOMAIN_MARKET_STRUCTURE, DOMAIN_VOLATILITY),
        "required_evidence": (DOMAIN_MARKET_STRUCTURE, DOMAIN_VOLATILITY),
        "required_direction_leans": (),
        "forbidden_direction_leans": (),
        "required_min_consensus_rank": 0,
        "required_confidence": CONFIDENCE_MODERATE,
        "readiness": READY_TODAY,
    },
    IRON_CONDOR: {
        "objective": "Range-bound premium collection with defined risk across a wide wing structure.",
        "consumes": (DOMAIN_DIRECTION, DOMAIN_MARKET_STRUCTURE, DOMAIN_VOLATILITY, DOMAIN_LIQUIDITY),
        "required_evidence": (DOMAIN_DIRECTION, DOMAIN_VOLATILITY, DOMAIN_LIQUIDITY),
        "required_direction_leans": ("NEUTRAL",),
        "forbidden_direction_leans": ("MIXED",),
        "required_min_consensus_rank": 1,
        "required_confidence": CONFIDENCE_MODERATE,
        "readiness": REQUIRES_LIQUIDITY,  # Volatility now bridged; only Liquidity remains missing.
    },
    IRON_FLY: {
        "objective": "Tighter-range premium collection with defined risk, higher theta concentration.",
        "consumes": (DOMAIN_DIRECTION, DOMAIN_MARKET_STRUCTURE, DOMAIN_VOLATILITY, DOMAIN_LIQUIDITY),
        "required_evidence": (DOMAIN_DIRECTION, DOMAIN_VOLATILITY, DOMAIN_LIQUIDITY),
        "required_direction_leans": ("NEUTRAL",),
        "forbidden_direction_leans": ("MIXED",),
        "required_min_consensus_rank": 1,
        "required_confidence": CONFIDENCE_MODERATE,
        "readiness": REQUIRES_LIQUIDITY,
    },
    CALENDAR: {
        "objective": "Exploit differential time decay / IV term structure across two expiries.",
        "consumes": (DOMAIN_VOLATILITY_TERM_STRUCTURE,),
        "required_evidence": (DOMAIN_VOLATILITY_TERM_STRUCTURE,),  # NOT plain DOMAIN_VOLATILITY -- genuinely needs term structure, still absent.
        "required_direction_leans": (),
        "forbidden_direction_leans": (),
        "required_min_consensus_rank": 0,
        "required_confidence": CONFIDENCE_MODERATE,
        "readiness": REQUIRES_DATA_NOT_YET_AVAILABLE,
    },
}
