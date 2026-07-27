"""Strategy Eligibility Intelligence (SEI) vocabulary — BUJJI
Engineering Series 82.

Lives at `bujji/msi_strategy_eligibility/`, outside `mic_v2`,
`bujji.mic_replay`, `bujji.production_runtime`, `bujji.trading_brain`,
`bujji.strategy_selector`, and `fyers_apiv3` — same isolation
discipline as every prior series in this arc. UNLIKE Series 81 (MDCI),
which stays domain-count/domain-type agnostic and imports nothing from
any sibling MSI brain, SEI is a DELIBERATE, DOWNSTREAM consumer of two
specific real model types: `bujji.msi_decision_synthesis.models.
MarketOpportunityAssessment` (Series 77) and
`bujji.msi_consensus.models.ConsensusAssessment` (Series 81). See
`engine.py`'s module docstring and `docs/MSI_STRATEGY_ELIGIBILITY.md`
Section "Architecture boundary" for the full reasoning on why this is
NOT the same "never import a sibling brain's code" rule that governs
peers like 78/79/81 -- 82 sits one level further down the pipeline as
a strict consumer of 77+81's PUBLIC output types, not a peer performing
its own independent domain read. SEI never imports
`bujji.msi_price_structure` or `bujji.msi_market_structure` directly --
those are already summarized inside 77's and 81's outputs and SEI has
no principled need to reach two levels upstream.

---------------------------------------------------------------------
Design note -- strategy-family taxonomy: SEI's OWN list, not 77's,
with an explicit mapping table (Check 2/Deliverable 3 resolution).
---------------------------------------------------------------------
Series 77's `ALL_STRATEGY_FAMILIES` (confirmed by reading
`bujji/msi_decision_synthesis/taxonomy.py`) is:
    DEFINED_RISK_DIRECTIONAL, DEFINED_RISK_NEUTRAL,
    DEFINED_RISK_VOLATILITY, UNDEFINED_RISK_PREMIUM,
    CALENDAR, DIAGONAL, HEDGED_DIRECTIONAL
This sprint's Deliverable 3 spec gives a DIFFERENT, more granular list
that explicitly SPLITS 77's single `DEFINED_RISK_VOLATILITY` bucket
into two directionally-opposite volatility postures:
    DEFINED_RISK_DIRECTIONAL, DEFINED_RISK_NEUTRAL,
    UNDEFINED_RISK_PREMIUM, LONG_VOLATILITY, SHORT_VOLATILITY,
    CALENDAR, DIAGONAL, HEDGED_DIRECTIONAL

Resolution: SEI uses ITS OWN taxonomy, exactly as Deliverable 3
specifies it, rather than silently reusing 77's coarser list or
silently diverging without documentation. This is deliberate, not an
oversight -- eligibility and compatibility are related-but-distinct
questions (see Check 2's full resolution in `docs/
MSI_STRATEGY_ELIGIBILITY.md`), and a downstream, dedicated eligibility
layer is the right place to draw a finer-grained distinction (long vs.
short volatility exposure) that the synthesis layer's single
compatibility byproduct field never needed to draw. The explicit
mapping below documents the relationship rather than leaving it
implicit:

    SEI family              <->  77 family (closest correspondence)
    ----------------------------------------------------------------
    DEFINED_RISK_DIRECTIONAL <-> DEFINED_RISK_DIRECTIONAL   (identical concept)
    DEFINED_RISK_NEUTRAL     <-> DEFINED_RISK_NEUTRAL        (identical concept)
    UNDEFINED_RISK_PREMIUM   <-> UNDEFINED_RISK_PREMIUM      (identical concept)
    LONG_VOLATILITY          <-> DEFINED_RISK_VOLATILITY     (refinement: the "buy vol" half of 77's single bucket)
    SHORT_VOLATILITY         <-> DEFINED_RISK_VOLATILITY     (refinement: the "sell vol" half of 77's single bucket)
    CALENDAR                 <-> CALENDAR                    (identical concept)
    DIAGONAL                 <-> DIAGONAL                    (identical concept)
    HEDGED_DIRECTIONAL       <-> HEDGED_DIRECTIONAL          (identical concept)

`FAMILY_TO_DSE_FAMILY` below encodes this table programmatically for
any caller/documentation that needs the mapping at runtime; engine.py
never uses it in eligibility logic itself (eligibility is computed
from opportunity_state/confidence_level/consensus fields, never from
77's family fields, per Check 2's resolution that 82 does NOT simply
copy or re-derive 77's compatible/incompatible lists).
"""
from __future__ import annotations

SEI_VERSION = "1.0.0"

RECOGNIZED_SCHEMA_VERSIONS = ("1.0.0",)

# ---------------------------------------------------------------------------
# Strategy families (Deliverable 3) -- SEI's own taxonomy. See module
# docstring for the explicit mapping to Series 77's family taxonomy.
# ---------------------------------------------------------------------------
FAMILY_DEFINED_RISK_DIRECTIONAL = "DEFINED_RISK_DIRECTIONAL"
FAMILY_DEFINED_RISK_NEUTRAL = "DEFINED_RISK_NEUTRAL"
FAMILY_UNDEFINED_RISK_PREMIUM = "UNDEFINED_RISK_PREMIUM"
FAMILY_LONG_VOLATILITY = "LONG_VOLATILITY"
FAMILY_SHORT_VOLATILITY = "SHORT_VOLATILITY"
FAMILY_CALENDAR = "CALENDAR"
FAMILY_DIAGONAL = "DIAGONAL"
FAMILY_HEDGED_DIRECTIONAL = "HEDGED_DIRECTIONAL"

ALL_STRATEGY_FAMILIES = (
    FAMILY_DEFINED_RISK_DIRECTIONAL,
    FAMILY_DEFINED_RISK_NEUTRAL,
    FAMILY_UNDEFINED_RISK_PREMIUM,
    FAMILY_LONG_VOLATILITY,
    FAMILY_SHORT_VOLATILITY,
    FAMILY_CALENDAR,
    FAMILY_DIAGONAL,
    FAMILY_HEDGED_DIRECTIONAL,
)

# Explicit mapping table, SEI family -> nearest Series 77 family. Not
# used by engine.py's eligibility logic -- documentation/tooling only.
FAMILY_TO_DSE_FAMILY = {
    FAMILY_DEFINED_RISK_DIRECTIONAL: "DEFINED_RISK_DIRECTIONAL",
    FAMILY_DEFINED_RISK_NEUTRAL: "DEFINED_RISK_NEUTRAL",
    FAMILY_UNDEFINED_RISK_PREMIUM: "UNDEFINED_RISK_PREMIUM",
    FAMILY_LONG_VOLATILITY: "DEFINED_RISK_VOLATILITY",
    FAMILY_SHORT_VOLATILITY: "DEFINED_RISK_VOLATILITY",
    FAMILY_CALENDAR: "CALENDAR",
    FAMILY_DIAGONAL: "DIAGONAL",
    FAMILY_HEDGED_DIRECTIONAL: "HEDGED_DIRECTIONAL",
}

# ---------------------------------------------------------------------------
# EligibilityConfidence -- independently defined NONE/LOW/MODERATE/HIGH
# scheme (same established convention as 77's confidence_level and
# 81's confidence_calibration concept, but its own separate constants,
# never imported from either sibling package).
# ---------------------------------------------------------------------------
ELIGIBILITY_CONFIDENCE_NONE = "NONE"
ELIGIBILITY_CONFIDENCE_LOW = "LOW"
ELIGIBILITY_CONFIDENCE_MODERATE = "MODERATE"
ELIGIBILITY_CONFIDENCE_HIGH = "HIGH"

ALL_ELIGIBILITY_CONFIDENCE_LEVELS = (
    ELIGIBILITY_CONFIDENCE_NONE,
    ELIGIBILITY_CONFIDENCE_LOW,
    ELIGIBILITY_CONFIDENCE_MODERATE,
    ELIGIBILITY_CONFIDENCE_HIGH,
)

ELIGIBILITY_CONFIDENCE_RANK = {
    ELIGIBILITY_CONFIDENCE_NONE: 0,
    ELIGIBILITY_CONFIDENCE_LOW: 1,
    ELIGIBILITY_CONFIDENCE_MODERATE: 2,
    ELIGIBILITY_CONFIDENCE_HIGH: 3,
}
