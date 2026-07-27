"""Evidence Interpreter mapping tables — BUJJI Options OS v3, Engineering
Series 32, Sprint 1.

This module contains ONLY finite, deterministic translation tables from
MIC v2 classification strings to Trading Ontology vocabulary values
(see bujji/trading_brain/ontology/taxonomy.py, frozen as of Engineering
Series 31). There is no reasoning here, no scoring, no fusion of
multiple sources into one ontology field -- exactly one MIC v2 layer
drives exactly one ontology field, per the Engineering Series 32
specification's own recommended mapping table.

If a supplied classification string is not a recognized member of its
source layer's own domain, engine.py raises ValueError rather than
silently defaulting anything -- the same "closed vocabulary" discipline
established in the ontology package. If a source layer's value is
altogether absent (None), the mapped ontology field becomes UNKNOWN,
with provenance recording exactly why -- never fabricated, never
inferred.

An honest note on unreachable states: because each ontology field is
driven by exactly one MIC v2 layer, not every value in a given ontology
vocabulary is reachable through this mapping alone. Each table below
documents which ontology values are structurally unreachable via this
sprint's translation rules -- exactly the same "disclosed limitation"
discipline used throughout MIC v2 (e.g. Governance's REVERSAL,
Lifecycle's SUPERSEDED). Reaching those values is the job of a later
Trading Brain module that fuses multiple sources -- never this one.
"""
from __future__ import annotations

INTERPRETER_VERSION = "1.0.0"

# ---------------------------------------------------------------------------
# Market State <- Market Context (trend dimension)
# Unreachable via this mapping alone: BREAKOUT, VOLATILE, QUIET,
# EVENT_DRIVEN. Market Context publishes a Trend dimension only; the
# other Market Context dimensions (Volatility, Liquidity, Regime) are
# not consulted by this sprint's single-source-per-field rule.
# ---------------------------------------------------------------------------
MARKET_STATE_MAPPING_VERSION = "1.0.0"
MARKET_STATE_SOURCE_LAYER = "market_context"

MARKET_STATE_MAPPING = {
    "TRENDING_UP": "TREND",
    "TRENDING_DOWN": "TREND",
    "SIDEWAYS": "RANGE",
    "TRANSITION": "REVERSAL",
    "UNKNOWN": "UNKNOWN",
}

# ---------------------------------------------------------------------------
# Strategy Intent <- Market Opinion
# Unreachable via this mapping alone: SELL_PREMIUM, BUY_PREMIUM,
# VOLATILITY_EXPANSION, VOLATILITY_CONTRACTION. Market Opinion carries
# no volatility-premium view -- that is Calibration/Governance's remit,
# not Opinion's.
# ---------------------------------------------------------------------------
STRATEGY_INTENT_MAPPING_VERSION = "1.0.0"
STRATEGY_INTENT_SOURCE_LAYER = "market_opinion"

STRATEGY_INTENT_MAPPING = {
    "BULLISH": "DIRECTIONAL_BULLISH",
    "BEARISH": "DIRECTIONAL_BEARISH",
    "NEUTRAL": "DELTA_NEUTRAL",
    "MIXED": "UNKNOWN",
    "INSUFFICIENT_EVIDENCE": "UNKNOWN",
    "UNKNOWN": "UNKNOWN",
}

# ---------------------------------------------------------------------------
# Confidence <- Context Stability
# Unreachable: none -- Context Stability's five real states plus
# UNKNOWN cover the full Confidence scale exactly.
# ---------------------------------------------------------------------------
CONFIDENCE_MAPPING_VERSION = "1.0.0"
CONFIDENCE_SOURCE_LAYER = "context_stability"

CONFIDENCE_MAPPING = {
    "STABLE": "VERY_HIGH",
    "MOSTLY_STABLE": "HIGH",
    "TRANSITIONING": "MODERATE",
    "HIGHLY_VARIABLE": "LOW",
    "INSUFFICIENT_HISTORY": "UNKNOWN",
    "UNKNOWN": "UNKNOWN",
}

# ---------------------------------------------------------------------------
# Opportunity State <- Calibration
# Unreachable via this mapping alone: WATCH. Calibration's three real
# states map onto AVOID / MEDIUM_EDGE / HIGH_EDGE; WATCH requires a
# finer signal than Calibration alone provides.
# ---------------------------------------------------------------------------
OPPORTUNITY_STATE_MAPPING_VERSION = "1.0.0"
OPPORTUNITY_STATE_SOURCE_LAYER = "calibration"

OPPORTUNITY_STATE_MAPPING = {
    "CALIBRATED": "HIGH_EDGE",
    "MOSTLY_CALIBRATED": "MEDIUM_EDGE",
    "UNCALIBRATED": "AVOID",
    "INSUFFICIENT_HISTORY": "UNKNOWN",
    "UNKNOWN": "UNKNOWN",
}

# ---------------------------------------------------------------------------
# Risk State <- Governance
# Unreachable via this mapping alone: LOW. A clean governance approval
# only confirms the absence of known issues, not a positively low-risk
# environment -- so APPROVED maps to NORMAL, never LOW. Reaching LOW
# requires more evidence than Governance alone supplies.
#
# The APPROVED_WITH_WARNINGS -> HIGH mapping is the specification's own
# worked example and must not be altered without an explicit spec
# change.
# ---------------------------------------------------------------------------
RISK_STATE_MAPPING_VERSION = "1.0.0"
RISK_STATE_SOURCE_LAYER = "governance"

RISK_STATE_MAPPING = {
    "APPROVED": "NORMAL",
    "APPROVED_WITH_WARNINGS": "HIGH",
    "REVIEW_REQUIRED": "HIGH",
    "REJECTED": "EXTREME",
    "UNKNOWN": "UNKNOWN",
}

# ---------------------------------------------------------------------------
# Execution Intent <- Lifecycle
# Unreachable via this mapping alone: ENTER, ADD, REDUCE, ROLL, HEDGE,
# EXIT. Lifecycle only tells us whether the underlying intelligence
# itself is fresh or stale -- it cannot tell us to actually act on a
# position. ACTIVE intelligence only ever earns PREPARE; anything
# beyond that is a future Strategy Selector / Position Manager's job.
# ---------------------------------------------------------------------------
EXECUTION_INTENT_MAPPING_VERSION = "1.0.0"
EXECUTION_INTENT_SOURCE_LAYER = "lifecycle"

EXECUTION_INTENT_MAPPING = {
    "ACTIVE": "PREPARE",
    "STALE": "NO_TRADE",
    "ARCHIVED": "NO_TRADE",
    "SUPERSEDED": "NO_TRADE",
    "UNKNOWN": "UNKNOWN",
}

# ---------------------------------------------------------------------------
# Capital Intent <- Intelligence Contract
# Unreachable via this mapping alone: LARGE, MAXIMUM. Contract
# completeness alone can never justify scaling capital up beyond
# NORMAL -- that requires the future Capital Allocation module.
# ---------------------------------------------------------------------------
CAPITAL_INTENT_MAPPING_VERSION = "1.0.0"
CAPITAL_INTENT_SOURCE_LAYER = "intelligence_contract"

CAPITAL_INTENT_MAPPING = {
    "COMPLETE": "NORMAL",
    "PARTIAL": "SMALL",
    "LEGACY": "SMALL",
    "EXPERIMENTAL": "NO_ALLOCATION",
    "UNKNOWN": "UNKNOWN",
}

# ---------------------------------------------------------------------------
# Registry -- (ontology_field_name, source_layer_name, mapping_table,
# mapping_version) for each of the seven translations. Used by engine.py
# to iterate generically rather than a chain of if/elif branches.
# ---------------------------------------------------------------------------
MAPPINGS = {
    "market_state": (MARKET_STATE_SOURCE_LAYER, MARKET_STATE_MAPPING, MARKET_STATE_MAPPING_VERSION),
    "strategy_intent": (STRATEGY_INTENT_SOURCE_LAYER, STRATEGY_INTENT_MAPPING, STRATEGY_INTENT_MAPPING_VERSION),
    "confidence": (CONFIDENCE_SOURCE_LAYER, CONFIDENCE_MAPPING, CONFIDENCE_MAPPING_VERSION),
    "opportunity_state": (OPPORTUNITY_STATE_SOURCE_LAYER, OPPORTUNITY_STATE_MAPPING, OPPORTUNITY_STATE_MAPPING_VERSION),
    "risk_state": (RISK_STATE_SOURCE_LAYER, RISK_STATE_MAPPING, RISK_STATE_MAPPING_VERSION),
    "execution_intent": (EXECUTION_INTENT_SOURCE_LAYER, EXECUTION_INTENT_MAPPING, EXECUTION_INTENT_MAPPING_VERSION),
    "capital_intent": (CAPITAL_INTENT_SOURCE_LAYER, CAPITAL_INTENT_MAPPING, CAPITAL_INTENT_MAPPING_VERSION),
}

# Ordered field -> the runner kwarg name used to pass the raw MIC v2
# classification string into engine.interpret().
FIELD_INPUT_KWARGS = {
    "market_state": "market_context",
    "strategy_intent": "market_opinion",
    "confidence": "context_stability",
    "opportunity_state": "calibration",
    "risk_state": "governance",
    "execution_intent": "lifecycle",
    "capital_intent": "contract",
}
