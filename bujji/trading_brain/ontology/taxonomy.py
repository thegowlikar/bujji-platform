"""Trading Ontology — BUJJI Options OS v3, Engineering Series 31, Sprint 1.

This module defines the finite, versioned vocabulary that every future
Trading Brain module (Evidence Interpreter, Market State Builder,
Strategy Selector, Strategy Constructor, Risk Brain, Position Manager,
Execution Planner, Trade Manager, Learning Engine, Strategy Evolution,
Meta Brain, Portfolio Brain) must reuse rather than reinvent.

This is language, not logic. Nothing in this module decides anything.
There is no engine.py in this package: no strategy selection, no risk
scoring, no position sizing, no execution planning. Only the finite
set of words the Trading Brain is allowed to use to describe the
world, and one shared confidence scale for expressing how sure any
future module is about any of these words.

Each vocabulary below is independently versioned so that extending one
(e.g. adding a new MarketState value in a future series) never forces
a version bump on any other vocabulary. Extension is additive-only:
existing constant names and existing meanings must never change or be
removed once published, ensuring every already-journaled record stays
interpretable under whatever version of the ontology reads it later.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Overall package version. Bumped only if the *shape* of the ontology
# package itself changes (e.g. a new vocabulary is added). Each
# individual vocabulary also carries its own version below.
# ---------------------------------------------------------------------------
ONTOLOGY_PACKAGE_VERSION = "1.0.0"


# ---------------------------------------------------------------------------
# 1. Market State — what kind of market is this, structurally?
# ---------------------------------------------------------------------------
MARKET_STATE_VERSION = "1.0.0"

MARKET_STATE_UNKNOWN = "UNKNOWN"
MARKET_STATE_TREND = "TREND"
MARKET_STATE_RANGE = "RANGE"
MARKET_STATE_REVERSAL = "REVERSAL"
MARKET_STATE_BREAKOUT = "BREAKOUT"
MARKET_STATE_VOLATILE = "VOLATILE"
MARKET_STATE_QUIET = "QUIET"
MARKET_STATE_EVENT_DRIVEN = "EVENT_DRIVEN"

ALL_MARKET_STATES = (
    MARKET_STATE_UNKNOWN,
    MARKET_STATE_TREND,
    MARKET_STATE_RANGE,
    MARKET_STATE_REVERSAL,
    MARKET_STATE_BREAKOUT,
    MARKET_STATE_VOLATILE,
    MARKET_STATE_QUIET,
    MARKET_STATE_EVENT_DRIVEN,
)

MARKET_STATE_DESCRIPTIONS = {
    MARKET_STATE_UNKNOWN: "Insufficient or absent evidence to classify the market's structural state.",
    MARKET_STATE_TREND: "Market is moving persistently in one direction.",
    MARKET_STATE_RANGE: "Market is oscillating between bounds without persistent direction.",
    MARKET_STATE_REVERSAL: "Market is transitioning from one directional bias to its opposite.",
    MARKET_STATE_BREAKOUT: "Market is exiting a prior range or consolidation with momentum.",
    MARKET_STATE_VOLATILE: "Market is moving with unusually large magnitude, direction unresolved.",
    MARKET_STATE_QUIET: "Market is moving with unusually small magnitude, low activity.",
    MARKET_STATE_EVENT_DRIVEN: "Market behavior is dominated by a scheduled or unscheduled event.",
}


# ---------------------------------------------------------------------------
# 2. Opportunity State — is there an edge worth pursuing today?
# ---------------------------------------------------------------------------
OPPORTUNITY_STATE_VERSION = "1.0.0"

OPPORTUNITY_STATE_UNKNOWN = "UNKNOWN"
OPPORTUNITY_STATE_AVOID = "AVOID"
OPPORTUNITY_STATE_WATCH = "WATCH"
OPPORTUNITY_STATE_LOW_EDGE = "LOW_EDGE"
OPPORTUNITY_STATE_MEDIUM_EDGE = "MEDIUM_EDGE"
OPPORTUNITY_STATE_HIGH_EDGE = "HIGH_EDGE"

ALL_OPPORTUNITY_STATES = (
    OPPORTUNITY_STATE_UNKNOWN,
    OPPORTUNITY_STATE_AVOID,
    OPPORTUNITY_STATE_WATCH,
    OPPORTUNITY_STATE_LOW_EDGE,
    OPPORTUNITY_STATE_MEDIUM_EDGE,
    OPPORTUNITY_STATE_HIGH_EDGE,
)

OPPORTUNITY_STATE_DESCRIPTIONS = {
    OPPORTUNITY_STATE_UNKNOWN: "Insufficient evidence to judge whether an edge exists today.",
    OPPORTUNITY_STATE_AVOID: "No edge; conditions argue against taking any position.",
    OPPORTUNITY_STATE_WATCH: "No actionable edge yet; conditions worth monitoring.",
    OPPORTUNITY_STATE_LOW_EDGE: "A marginal edge exists.",
    OPPORTUNITY_STATE_MEDIUM_EDGE: "A moderate edge exists.",
    OPPORTUNITY_STATE_HIGH_EDGE: "A strong edge exists.",
}


# ---------------------------------------------------------------------------
# 3. Risk State — how much danger is in the environment right now?
# ---------------------------------------------------------------------------
RISK_STATE_VERSION = "1.0.0"

RISK_STATE_UNKNOWN = "UNKNOWN"
RISK_STATE_LOW = "LOW"
RISK_STATE_NORMAL = "NORMAL"
RISK_STATE_HIGH = "HIGH"
RISK_STATE_EXTREME = "EXTREME"

ALL_RISK_STATES = (
    RISK_STATE_UNKNOWN,
    RISK_STATE_LOW,
    RISK_STATE_NORMAL,
    RISK_STATE_HIGH,
    RISK_STATE_EXTREME,
)

RISK_STATE_DESCRIPTIONS = {
    RISK_STATE_UNKNOWN: "Insufficient evidence to judge the risk environment.",
    RISK_STATE_LOW: "Risk factors are below their normal range.",
    RISK_STATE_NORMAL: "Risk factors are within their normal range.",
    RISK_STATE_HIGH: "Risk factors are elevated above their normal range.",
    RISK_STATE_EXTREME: "Risk factors are severely elevated; caution is paramount.",
}


# ---------------------------------------------------------------------------
# 4. Execution Intent — what action, if any, is intended right now?
# ---------------------------------------------------------------------------
EXECUTION_INTENT_VERSION = "1.0.0"

EXECUTION_INTENT_UNKNOWN = "UNKNOWN"
EXECUTION_INTENT_NO_TRADE = "NO_TRADE"
EXECUTION_INTENT_PREPARE = "PREPARE"
EXECUTION_INTENT_ENTER = "ENTER"
EXECUTION_INTENT_ADD = "ADD"
EXECUTION_INTENT_REDUCE = "REDUCE"
EXECUTION_INTENT_ROLL = "ROLL"
EXECUTION_INTENT_HEDGE = "HEDGE"
EXECUTION_INTENT_EXIT = "EXIT"

ALL_EXECUTION_INTENTS = (
    EXECUTION_INTENT_UNKNOWN,
    EXECUTION_INTENT_NO_TRADE,
    EXECUTION_INTENT_PREPARE,
    EXECUTION_INTENT_ENTER,
    EXECUTION_INTENT_ADD,
    EXECUTION_INTENT_REDUCE,
    EXECUTION_INTENT_ROLL,
    EXECUTION_INTENT_HEDGE,
    EXECUTION_INTENT_EXIT,
)

EXECUTION_INTENT_DESCRIPTIONS = {
    EXECUTION_INTENT_UNKNOWN: "Insufficient evidence to state an execution intent.",
    EXECUTION_INTENT_NO_TRADE: "No action intended.",
    EXECUTION_INTENT_PREPARE: "Preparing to act, not yet acting.",
    EXECUTION_INTENT_ENTER: "Intent to open a new position.",
    EXECUTION_INTENT_ADD: "Intent to add to an existing position.",
    EXECUTION_INTENT_REDUCE: "Intent to partially reduce an existing position.",
    EXECUTION_INTENT_ROLL: "Intent to roll an existing position to a new expiry or strike.",
    EXECUTION_INTENT_HEDGE: "Intent to hedge an existing position's risk.",
    EXECUTION_INTENT_EXIT: "Intent to fully close an existing position.",
}


# ---------------------------------------------------------------------------
# 5. Strategy Intent — what kind of options exposure is desired?
# ---------------------------------------------------------------------------
STRATEGY_INTENT_VERSION = "1.0.0"

STRATEGY_INTENT_UNKNOWN = "UNKNOWN"
STRATEGY_INTENT_SELL_PREMIUM = "SELL_PREMIUM"
STRATEGY_INTENT_BUY_PREMIUM = "BUY_PREMIUM"
STRATEGY_INTENT_DELTA_NEUTRAL = "DELTA_NEUTRAL"
STRATEGY_INTENT_DIRECTIONAL_BULLISH = "DIRECTIONAL_BULLISH"
STRATEGY_INTENT_DIRECTIONAL_BEARISH = "DIRECTIONAL_BEARISH"
STRATEGY_INTENT_VOLATILITY_EXPANSION = "VOLATILITY_EXPANSION"
STRATEGY_INTENT_VOLATILITY_CONTRACTION = "VOLATILITY_CONTRACTION"

ALL_STRATEGY_INTENTS = (
    STRATEGY_INTENT_UNKNOWN,
    STRATEGY_INTENT_SELL_PREMIUM,
    STRATEGY_INTENT_BUY_PREMIUM,
    STRATEGY_INTENT_DELTA_NEUTRAL,
    STRATEGY_INTENT_DIRECTIONAL_BULLISH,
    STRATEGY_INTENT_DIRECTIONAL_BEARISH,
    STRATEGY_INTENT_VOLATILITY_EXPANSION,
    STRATEGY_INTENT_VOLATILITY_CONTRACTION,
)

STRATEGY_INTENT_DESCRIPTIONS = {
    STRATEGY_INTENT_UNKNOWN: "Insufficient evidence to state a strategy intent.",
    STRATEGY_INTENT_SELL_PREMIUM: "Intent to be a net seller of options premium.",
    STRATEGY_INTENT_BUY_PREMIUM: "Intent to be a net buyer of options premium.",
    STRATEGY_INTENT_DELTA_NEUTRAL: "Intent to hold negligible directional exposure.",
    STRATEGY_INTENT_DIRECTIONAL_BULLISH: "Intent to hold net positive directional exposure.",
    STRATEGY_INTENT_DIRECTIONAL_BEARISH: "Intent to hold net negative directional exposure.",
    STRATEGY_INTENT_VOLATILITY_EXPANSION: "Intent to benefit from rising implied volatility.",
    STRATEGY_INTENT_VOLATILITY_CONTRACTION: "Intent to benefit from falling implied volatility.",
}


# ---------------------------------------------------------------------------
# 6. Capital Intent — how much capital should be committed?
# ---------------------------------------------------------------------------
CAPITAL_INTENT_VERSION = "1.0.0"

CAPITAL_INTENT_UNKNOWN = "UNKNOWN"
CAPITAL_INTENT_NO_ALLOCATION = "NO_ALLOCATION"
CAPITAL_INTENT_SMALL = "SMALL"
CAPITAL_INTENT_NORMAL = "NORMAL"
CAPITAL_INTENT_LARGE = "LARGE"
CAPITAL_INTENT_MAXIMUM = "MAXIMUM"

ALL_CAPITAL_INTENTS = (
    CAPITAL_INTENT_UNKNOWN,
    CAPITAL_INTENT_NO_ALLOCATION,
    CAPITAL_INTENT_SMALL,
    CAPITAL_INTENT_NORMAL,
    CAPITAL_INTENT_LARGE,
    CAPITAL_INTENT_MAXIMUM,
)

CAPITAL_INTENT_DESCRIPTIONS = {
    CAPITAL_INTENT_UNKNOWN: "Insufficient evidence to state a capital intent.",
    CAPITAL_INTENT_NO_ALLOCATION: "No capital should be committed.",
    CAPITAL_INTENT_SMALL: "A below-normal amount of capital should be committed.",
    CAPITAL_INTENT_NORMAL: "A standard amount of capital should be committed.",
    CAPITAL_INTENT_LARGE: "An above-normal amount of capital should be committed.",
    CAPITAL_INTENT_MAXIMUM: "The maximum permitted amount of capital should be committed.",
}


# ---------------------------------------------------------------------------
# 7. Confidence — the ONE shared scale. Every future module that needs
# to express "how sure am I" must import and reuse this vocabulary
# rather than inventing its own scale or a raw float score.
# ---------------------------------------------------------------------------
CONFIDENCE_VERSION = "1.0.0"

CONFIDENCE_UNKNOWN = "UNKNOWN"
CONFIDENCE_VERY_LOW = "VERY_LOW"
CONFIDENCE_LOW = "LOW"
CONFIDENCE_MODERATE = "MODERATE"
CONFIDENCE_HIGH = "HIGH"
CONFIDENCE_VERY_HIGH = "VERY_HIGH"

ALL_CONFIDENCE_LEVELS = (
    CONFIDENCE_UNKNOWN,
    CONFIDENCE_VERY_LOW,
    CONFIDENCE_LOW,
    CONFIDENCE_MODERATE,
    CONFIDENCE_HIGH,
    CONFIDENCE_VERY_HIGH,
)

CONFIDENCE_DESCRIPTIONS = {
    CONFIDENCE_UNKNOWN: "Confidence cannot be assessed.",
    CONFIDENCE_VERY_LOW: "Very low confidence.",
    CONFIDENCE_LOW: "Low confidence.",
    CONFIDENCE_MODERATE: "Moderate confidence.",
    CONFIDENCE_HIGH: "High confidence.",
    CONFIDENCE_VERY_HIGH: "Very high confidence.",
}


# ---------------------------------------------------------------------------
# Vocabulary registry — used by runner.py to validate a field against
# its own taxonomy generically, without a chain of if/elif branches
# that would need editing every time a vocabulary is added.
# ---------------------------------------------------------------------------
VOCABULARIES = {
    "market_state": (ALL_MARKET_STATES, MARKET_STATE_VERSION),
    "opportunity_state": (ALL_OPPORTUNITY_STATES, OPPORTUNITY_STATE_VERSION),
    "risk_state": (ALL_RISK_STATES, RISK_STATE_VERSION),
    "execution_intent": (ALL_EXECUTION_INTENTS, EXECUTION_INTENT_VERSION),
    "strategy_intent": (ALL_STRATEGY_INTENTS, STRATEGY_INTENT_VERSION),
    "capital_intent": (ALL_CAPITAL_INTENTS, CAPITAL_INTENT_VERSION),
    "confidence": (ALL_CONFIDENCE_LEVELS, CONFIDENCE_VERSION),
}
