"""Strategy Selector vocabulary — BUJJI Engineering Series 89 (MSS v1).

Follows this project's established convention (plain string constants,
not enum.Enum).

---------------------------------------------------------------------
Deliverable 1 — Market State Ontology. These are MARKET STATES, never
strategies. Deliberately INDEPENDENT dimensions (a cycle can carry
multiple simultaneously active states, e.g. BALANCE + VOLATILITY_CONTRACTION
together are a coherent, common real condition) — mirrors this
project's established compression/expansion-coexistence pattern
(Series 78) rather than forcing one linear state machine.

Note on naming: `STATE_VOLATILITY_EXPANSION`/`STATE_VOLATILITY_CONTRACTION`
(market states, this package) share text with
`bujji.msi_strategy_selection_foundation.taxonomy.VOLATILITY_EXPANSION`/
`VOLATILITY_COMPRESSION` (STRATEGY FAMILY names, Series 87) — a real,
disclosed naming coincidence, not a collision in code (distinct Python
identifiers in distinct taxonomy modules) — a strategy family named
"Volatility Expansion" and a market state named "Volatility Expansion"
are two different concepts (an approach vs. a condition) that happen
to share an English name, exactly as a discretionary trader would use
the phrase both ways.
---------------------------------------------------------------------
"""
from __future__ import annotations

MSI_STRATEGY_SELECTOR_VERSION = "1.0.0"
RECOGNIZED_SCHEMA_VERSIONS = ("1.0.0",)

# ---------------------------------------------------------------------------
# MarketState — Deliverable 1. Independent boolean tags, not mutually
# exclusive states.
# ---------------------------------------------------------------------------
STATE_TREND_EXPANSION = "TREND_EXPANSION"
STATE_TREND_EXHAUSTION = "TREND_EXHAUSTION"
STATE_BALANCE = "BALANCE"
STATE_COMPRESSION = "COMPRESSION"                      # Price-structure compression (PSI), distinct evidence source from volatility.
STATE_VOLATILITY_EXPANSION = "VOLATILITY_EXPANSION"    # Volatility-domain expansion (VSB), distinct from STATE_COMPRESSION.
STATE_VOLATILITY_CONTRACTION = "VOLATILITY_CONTRACTION"
STATE_ROTATIONAL_MARKET = "ROTATIONAL_MARKET"
STATE_UNCERTAIN_MARKET = "UNCERTAIN_MARKET"
STATE_STRONG_PARTICIPATION = "STRONG_PARTICIPATION"
STATE_WEAK_PARTICIPATION = "WEAK_PARTICIPATION"

ALL_MARKET_STATES = (
    STATE_TREND_EXPANSION, STATE_TREND_EXHAUSTION, STATE_BALANCE, STATE_COMPRESSION,
    STATE_VOLATILITY_EXPANSION, STATE_VOLATILITY_CONTRACTION, STATE_ROTATIONAL_MARKET,
    STATE_UNCERTAIN_MARKET, STATE_STRONG_PARTICIPATION, STATE_WEAK_PARTICIPATION,
)

# ---------------------------------------------------------------------------
# Deliverable 2 — per-family market-state fit rules. Pure declarative
# data (a tuple of preferred/acceptable/forbidden state sets), read by
# engine.py. No numeric optimisation anywhere in this data -- match
# counting happens in engine.py as a disclosed, fixed-weight, ranking
# rule (preferred > acceptable, forbidden always disqualifies), never
# fit against historical outcomes.
# ---------------------------------------------------------------------------
STRATEGY_STATE_FIT = {
    "LONG_DIRECTIONAL": {
        "preferred": (STATE_TREND_EXPANSION, STATE_STRONG_PARTICIPATION),
        "acceptable": (),
        "forbidden": (STATE_BALANCE, STATE_ROTATIONAL_MARKET, STATE_UNCERTAIN_MARKET),
    },
    "SHORT_DIRECTIONAL": {
        "preferred": (STATE_TREND_EXPANSION, STATE_STRONG_PARTICIPATION),
        "acceptable": (),
        "forbidden": (STATE_BALANCE, STATE_ROTATIONAL_MARKET, STATE_UNCERTAIN_MARKET),
    },
    "COVERED": {
        "preferred": (STATE_TREND_EXPANSION,),
        "acceptable": (STATE_BALANCE,),
        "forbidden": (STATE_UNCERTAIN_MARKET,),
    },
    "SYNTHETIC": {
        "preferred": (STATE_TREND_EXPANSION,),
        "acceptable": (),
        "forbidden": (STATE_UNCERTAIN_MARKET, STATE_ROTATIONAL_MARKET),
    },
    "NEUTRAL_PREMIUM_SELLING": {
        "preferred": (STATE_BALANCE,),
        "acceptable": (STATE_VOLATILITY_CONTRACTION,),
        "forbidden": (STATE_TREND_EXPANSION, STATE_VOLATILITY_EXPANSION),
    },
    "NEUTRAL_PREMIUM_BUYING": {
        "preferred": (STATE_COMPRESSION, STATE_VOLATILITY_CONTRACTION),
        "acceptable": (STATE_UNCERTAIN_MARKET,),
        "forbidden": (STATE_TREND_EXPANSION,),
    },
    "VOLATILITY_EXPANSION": {
        "preferred": (STATE_COMPRESSION, STATE_VOLATILITY_CONTRACTION),
        "acceptable": (),
        "forbidden": (STATE_VOLATILITY_EXPANSION,),  # Already expanding -- this strategy's own edge would be spent.
    },
    "VOLATILITY_COMPRESSION": {
        "preferred": (STATE_VOLATILITY_EXPANSION, STATE_TREND_EXHAUSTION),
        "acceptable": (),
        "forbidden": (STATE_COMPRESSION,),
    },
    "RATIO": {
        "preferred": (STATE_TREND_EXPANSION,),
        "acceptable": (STATE_ROTATIONAL_MARKET,),
        "forbidden": (STATE_UNCERTAIN_MARKET,),
    },
    "BUTTERFLY": {
        "preferred": (STATE_BALANCE, STATE_COMPRESSION),
        "acceptable": (),
        "forbidden": (STATE_TREND_EXPANSION, STATE_VOLATILITY_EXPANSION),
    },
    "IRON_CONDOR": {
        "preferred": (STATE_BALANCE,),
        "acceptable": (STATE_VOLATILITY_CONTRACTION,),
        "forbidden": (STATE_TREND_EXPANSION,),
    },
    "IRON_FLY": {
        "preferred": (STATE_BALANCE, STATE_COMPRESSION),
        "acceptable": (),
        "forbidden": (STATE_TREND_EXPANSION, STATE_VOLATILITY_EXPANSION),
    },
    "CALENDAR": {
        "preferred": (STATE_COMPRESSION,),
        "acceptable": (),
        "forbidden": (STATE_TREND_EXPANSION,),
    },
}

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


# Deliverable 3's disclosed, fixed match-weighting (never fit to
# outcomes): a preferred-state match is worth more than an acceptable
# one; a forbidden-state match disqualifies the family outright,
# regardless of other matches.
PREFERRED_MATCH_WEIGHT = 2
ACCEPTABLE_MATCH_WEIGHT = 1

# Fixed, disclosed tie-break order when two or more SUITABLE,
# non-disqualified families have an identical match score -- purely
# alphabetical, never a preference ranking of "better" strategies.
TIE_BREAK_ORDER = (
    "LONG_DIRECTIONAL", "SHORT_DIRECTIONAL", "COVERED", "SYNTHETIC",
    "NEUTRAL_PREMIUM_SELLING", "NEUTRAL_PREMIUM_BUYING", "VOLATILITY_EXPANSION",
    "VOLATILITY_COMPRESSION", "RATIO", "BUTTERFLY", "IRON_CONDOR", "IRON_FLY", "CALENDAR",
)
