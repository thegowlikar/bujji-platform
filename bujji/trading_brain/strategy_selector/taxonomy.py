"""Strategy Selector vocabulary — BUJJI Options OS v3, Engineering Series
34, Sprint 1.

Two finite vocabularies belong to this module:

- `eligibility`: what this module concludes about ONE strategy against
  today's MarketStateAssessment.
- `selection_status`: what this module concludes about the OVERALL
  decision.

Both are closed, finite, and never extended with an ad-hoc string.
Two internal-only ordinal orderings (never exposed as a public
vocabulary) are also defined here, used purely to compare a
MarketStateAssessment's `market_character`/`confidence` against a
strategy's declared minimums -- the same disclosed,
calculation-only-ordinal pattern used since MIC v2's Context Stability
sprint (Engineering Series 21) and again in the Market State Builder
(Engineering Series 33).
"""
from __future__ import annotations

STRATEGY_SELECTOR_VERSION = "1.0.0"

# ---------------------------------------------------------------------------
# Eligibility -- per-strategy verdict.
# ---------------------------------------------------------------------------
ELIGIBILITY_VERSION = "1.0.0"

ELIGIBILITY_ELIGIBLE = "ELIGIBLE"
ELIGIBILITY_NOT_ELIGIBLE = "NOT_ELIGIBLE"
ELIGIBILITY_UNKNOWN = "UNKNOWN"

ALL_ELIGIBILITY_STATES = (
    ELIGIBILITY_ELIGIBLE,
    ELIGIBILITY_NOT_ELIGIBLE,
    ELIGIBILITY_UNKNOWN,
)

ELIGIBILITY_DESCRIPTIONS = {
    ELIGIBILITY_ELIGIBLE: "This strategy can honestly operate under today's market state.",
    ELIGIBILITY_NOT_ELIGIBLE: "This strategy cannot honestly operate under today's market state.",
    ELIGIBILITY_UNKNOWN: "Today's market state is itself UNKNOWN; eligibility cannot be assessed.",
}

# ---------------------------------------------------------------------------
# Selection Status -- overall decision verdict.
# ---------------------------------------------------------------------------
SELECTION_STATUS_VERSION = "1.0.0"

SELECTION_STATUS_SELECTED = "SELECTED"
SELECTION_STATUS_NO_STRATEGY = "NO_STRATEGY"
SELECTION_STATUS_UNKNOWN = "UNKNOWN"

ALL_SELECTION_STATUSES = (
    SELECTION_STATUS_SELECTED,
    SELECTION_STATUS_NO_STRATEGY,
    SELECTION_STATUS_UNKNOWN,
)

SELECTION_STATUS_DESCRIPTIONS = {
    SELECTION_STATUS_SELECTED: "Exactly one strategy was chosen.",
    SELECTION_STATUS_NO_STRATEGY: "Every registered strategy was evaluated and none honestly qualified, or the market's evidence was too contradictory to trust any of them.",
    SELECTION_STATUS_UNKNOWN: "No MarketStateAssessment was supplied at all; nothing could be evaluated.",
}

# ---------------------------------------------------------------------------
# Internal-only ordinal orderings, used solely by engine.py to compare
# a MarketStateAssessment's own fields against a strategy's declared
# minimums. Never exposed as a public vocabulary of this module's own;
# both source vocabularies (`market_character`, `confidence`) remain
# owned by the Market State Builder (Series 33) and the frozen Trading
# Ontology (Series 31) respectively.
# ---------------------------------------------------------------------------
CHARACTER_TOLERANCE_ORDER = (
    "UNKNOWN",
    "INSUFFICIENT_EVIDENCE",
    "UNCERTAIN",
    "MIXED",
    "CONTESTED",
    "CLEAR",
)

CONFIDENCE_ORDER = ("UNKNOWN", "VERY_LOW", "LOW", "MODERATE", "HIGH", "VERY_HIGH")
