"""Market State Builder vocabulary — BUJJI Options OS v3, Engineering
Series 33, Sprint 1.

This module reuses two FROZEN vocabularies from the Trading Ontology
(Engineering Series 31) without modification: `market_state` and
`confidence` (also used here for `market_conviction`). It defines TWO
new vocabularies of its own, scoped entirely to this module's job of
describing the *quality* of a market-state read rather than the
market's structure itself:

- `market_character` -- how trustworthy/coherent is this assessment?
- `market_phase` -- how mature/settled is the market's current regime?

Neither new vocabulary could have been expressed by the frozen
ontology alone: the ontology's `market_state` UNKNOWN collapses
"no evidence" and "evidence disagrees violently" into one value, and
the constitution requires the Builder to expose that distinction
honestly rather than erase it.
"""
from __future__ import annotations

from ..ontology.taxonomy import ALL_CONFIDENCE_LEVELS, ALL_MARKET_STATES

MARKET_STATE_BUILDER_VERSION = "1.0.0"

# ---------------------------------------------------------------------------
# market_state -- reused verbatim from the frozen Trading Ontology.
# Not redefined here. BREAKOUT, VOLATILE, QUIET, and EVENT_DRIVEN remain
# unreachable this sprint for the same reason they were unreachable to
# the Evidence Interpreter (Series 32): this Builder's only input,
# EvidenceInterpretation, carries just the Market Context Trend
# dimension -- Volatility, Liquidity, and Regime are not available
# through the Series-32 firewall. A future series that extends the
# Evidence Interpreter to expose those additional MIC v2 dimensions
# would be the correct place to make those states reachable, never a
# re-derivation performed here from data this module cannot see.
REUSED_MARKET_STATES = ALL_MARKET_STATES

# ---------------------------------------------------------------------------
# confidence / market_conviction -- reused verbatim from the frozen
# Trading Ontology. In this sprint `market_conviction` is computed
# identically to `confidence`; they are kept as two distinct fields
# because a future series may compute conviction about the specific
# state claim separately from confidence in the overall assessment.
# That is a disclosed, deliberate simplification, not an oversight.
REUSED_CONFIDENCE_LEVELS = ALL_CONFIDENCE_LEVELS


# ---------------------------------------------------------------------------
# Market Character -- new vocabulary. Describes how the available
# evidence relates to itself: agreeing, silent, mildly contested,
# violently contested, or simply too sparse to reason about at all.
# ---------------------------------------------------------------------------
MARKET_CHARACTER_VERSION = "1.0.0"

MARKET_CHARACTER_UNKNOWN = "UNKNOWN"
MARKET_CHARACTER_CLEAR = "CLEAR"
MARKET_CHARACTER_MIXED = "MIXED"
MARKET_CHARACTER_CONTESTED = "CONTESTED"
MARKET_CHARACTER_UNCERTAIN = "UNCERTAIN"
MARKET_CHARACTER_INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"

ALL_MARKET_CHARACTERS = (
    MARKET_CHARACTER_UNKNOWN,
    MARKET_CHARACTER_CLEAR,
    MARKET_CHARACTER_MIXED,
    MARKET_CHARACTER_CONTESTED,
    MARKET_CHARACTER_UNCERTAIN,
    MARKET_CHARACTER_INSUFFICIENT_EVIDENCE,
)

MARKET_CHARACTER_DESCRIPTIONS = {
    MARKET_CHARACTER_UNKNOWN: "Character could not be assessed.",
    MARKET_CHARACTER_CLEAR: "A defined market state exists and no available signal contradicts it.",
    MARKET_CHARACTER_MIXED: "No defined market state and no active contradiction -- evidence is simply silent.",
    MARKET_CHARACTER_CONTESTED: "A defined market state exists but exactly one available signal contradicts it.",
    MARKET_CHARACTER_UNCERTAIN: "Two or more available signals contradict each other; the market state is withheld rather than trusted.",
    MARKET_CHARACTER_INSUFFICIENT_EVIDENCE: "Most of the four input layers were not supplied at all; there is not enough evidence to reason from.",
}


# ---------------------------------------------------------------------------
# Market Phase -- new vocabulary. Describes how settled the current
# regime is, derived purely from the stability signal.
# ---------------------------------------------------------------------------
MARKET_PHASE_VERSION = "1.0.0"

MARKET_PHASE_UNKNOWN = "UNKNOWN"
MARKET_PHASE_EMERGING = "EMERGING"
MARKET_PHASE_ESTABLISHED = "ESTABLISHED"
MARKET_PHASE_UNSTABLE = "UNSTABLE"

ALL_MARKET_PHASES = (
    MARKET_PHASE_UNKNOWN,
    MARKET_PHASE_EMERGING,
    MARKET_PHASE_ESTABLISHED,
    MARKET_PHASE_UNSTABLE,
)

MARKET_PHASE_DESCRIPTIONS = {
    MARKET_PHASE_UNKNOWN: "Phase could not be assessed.",
    MARKET_PHASE_EMERGING: "The current regime is still forming.",
    MARKET_PHASE_ESTABLISHED: "The current regime is settled and persistent.",
    MARKET_PHASE_UNSTABLE: "The current regime is breaking down or highly variable.",
}

# Confidence level -> Market Phase. Purely a function of the stability
# signal (the ontology `confidence` field, which the Evidence
# Interpreter derives solely from Context Stability -- see Series 32).
PHASE_BY_CONFIDENCE = {
    "VERY_HIGH": MARKET_PHASE_ESTABLISHED,
    "HIGH": MARKET_PHASE_ESTABLISHED,
    "MODERATE": MARKET_PHASE_EMERGING,
    "LOW": MARKET_PHASE_UNSTABLE,
    "VERY_LOW": MARKET_PHASE_UNSTABLE,
    "UNKNOWN": MARKET_PHASE_UNKNOWN,
}

# Ordinal order for confidence, used only internally by engine.py to
# downgrade confidence by one step when evidence is contested. Never
# exposed outside this package -- the same disclosed, calculation-only
# ordinal-mapping discipline MIC v2 used for conviction in Series 21.
CONFIDENCE_ORDER = ("UNKNOWN", "VERY_LOW", "LOW", "MODERATE", "HIGH", "VERY_HIGH")
