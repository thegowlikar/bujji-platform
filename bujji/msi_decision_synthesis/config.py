"""Static configuration for the MSI Decision Synthesis Engine.

Every threshold below is fixed, disclosed configuration -- never fit
or adjusted against outcomes (per this project's "measure before
tuning" discipline, MSI_V1_FOUNDATION.md Deliverable 10 Guiding
Principle 9, and this sprint's own explicit no-tuning-against-results
constraint). Changing a threshold is a deliberate, reviewed edit to
this file, never a runtime-learned value.
"""
from __future__ import annotations

from . import taxonomy

SCHEMA_VERSION = taxonomy.DSE_VERSION

DEFAULT_PROVENANCE = "msi_decision_synthesis.engine.synthesize"

# ---------------------------------------------------------------------------
# Deliverable 5 — state -> lean/type mapping. Deliberately a SMALL,
# illustrative seed vocabulary, not exhaustive: since no real MSI brain
# exists yet (MSI_V1_FOUNDATION.md Deliverable 8/9), DSE cannot know
# every future domain's real published-state vocabulary. Any `state`
# string not present here maps to `LEAN_AMBIGUOUS` -- an unmapped state
# is NEVER silently coerced into agreement or conflict; it is simply
# excluded from the agreement/conflict vote (see engine.py's
# `_lean_for_signal`). Extending this table for a newly-built real
# brain is expected and safe: it never changes engine.py's logic,
# exactly like taxonomy extension elsewhere in this project.
#
# PHASE 14B-P1 (forensic finding): the placeholder keys below (Title
# Case: "Trending", "Range", ...) were written before any real MSI
# brain existed and NEVER matched any real domain's actual output --
# `psi.structure_state`/`mssi.structure_location`/`mdi.overall_direction`/
# `mppi.positioning_bias`/`vsb.volatility_regime` all use ALL_CAPS_WITH_
# UNDERSCORES values that share zero keys with this table (confirmed by
# exhaustive comparison against a real live session: 174/174 cycles
# resolved to MONITOR purely from this string mismatch, never from
# genuine evidence absence). The block below ADDS the real domains'
# actual taxonomy values as new keys -- purely additive, old placeholder
# keys kept unchanged for anything still relying on them, engine.py
# untouched. Semantics for each new mapping are derived from each
# domain's own taxonomy docstring (msi_price_structure/msi_market_structure/
# msi_market_direction/msi_participant_positioning/msi_volatility_structure),
# not guessed:
#   PSI structure_state: TRENDING -> directional edge; BALANCE -> neutral
#     (range-bound); CORRECTING -> a pullback within a structure, i.e. a
#     reversion read; TRANSITIONING/UNKNOWN -> genuinely ambiguous, never
#     forced either way.
#   MSSI structure_location: INSIDE_RANGE/NEAR_SUPPORT/NEAR_RESISTANCE ->
#     classic mean-reversion setups (price still bounded); ABOVE_RESISTANCE/
#     BELOW_SUPPORT -> a real structural break, i.e. breakout; AT_RETEST/
#     UNKNOWN -> ambiguous (a retest is inherently undecided).
#   MDI overall_direction: any real bullish/bearish band -> directional;
#     NEUTRAL -> neutral; MIXED/UNKNOWN -> ambiguous (MIXED is genuine
#     cross-lens disagreement, never averaged into NEUTRAL -- see
#     msi_market_direction.taxonomy's own docstring).
#   MPPI positioning_bias: BULLISH/BEARISH_POSITIONING -> directional;
#     NEUTRAL_POSITIONING -> neutral; MIXED/UNKNOWN_POSITIONING -> ambiguous.
#   VSB volatility_regime: COMPRESSED/HIGH_VOLATILITY -> both real
#     volatility-type opportunities (compression anticipates expansion,
#     already-elevated vol supports a volatility read -- mirrors Phase 9's
#     own VOLATILITY_EXPANSION/COMPRESSION suitability rules); STABLE ->
#     neutral; TRANSITIONING/UNKNOWN -> ambiguous.
# ---------------------------------------------------------------------------
LEAN_OPPORTUNITY_FORMING = "OPPORTUNITY_FORMING"
LEAN_NEUTRAL_FORMING = "NEUTRAL_FORMING"
LEAN_AMBIGUOUS = "AMBIGUOUS"

# state -> (lean, preferred opportunity_state if this lean wins the vote)
STATE_LEAN_MAP = {
    "Trending": (LEAN_OPPORTUNITY_FORMING, taxonomy.OPPORTUNITY_STATE_DIRECTIONAL),
    "Breakout": (LEAN_OPPORTUNITY_FORMING, taxonomy.OPPORTUNITY_STATE_BREAKOUT),
    "Expanding": (LEAN_OPPORTUNITY_FORMING, taxonomy.OPPORTUNITY_STATE_VOLATILITY),
    "Range": (LEAN_NEUTRAL_FORMING, taxonomy.OPPORTUNITY_STATE_MEAN_REVERSION),
    "Balanced": (LEAN_NEUTRAL_FORMING, taxonomy.OPPORTUNITY_STATE_NEUTRAL),
    "Neutral": (LEAN_NEUTRAL_FORMING, taxonomy.OPPORTUNITY_STATE_NEUTRAL),
    "Contracting": (LEAN_NEUTRAL_FORMING, taxonomy.OPPORTUNITY_STATE_NEUTRAL),
    # Precondition / negating reads -- ambiguous with respect to TYPE,
    # never forced into the agreement/conflict vote (see
    # taxonomy.PRECONDITION_DOMAINS and engine.py's `_lean_for_signal`,
    # which also always treats PRECONDITION_DOMAINS as AMBIGUOUS
    # regardless of this table).
    "Strong": (LEAN_AMBIGUOUS, None),
    "Adequate": (LEAN_AMBIGUOUS, None),
    "Thin": (LEAN_AMBIGUOUS, None),
    # --- Phase 14B-P1 additions: real MSI brain vocabulary ------------
    # PSI structure_state (msi_price_structure.taxonomy.ALL_STRUCTURE_STATES).
    "TRENDING": (LEAN_OPPORTUNITY_FORMING, taxonomy.OPPORTUNITY_STATE_DIRECTIONAL),
    "BALANCE": (LEAN_NEUTRAL_FORMING, taxonomy.OPPORTUNITY_STATE_NEUTRAL),
    "CORRECTING": (LEAN_NEUTRAL_FORMING, taxonomy.OPPORTUNITY_STATE_MEAN_REVERSION),
    "TRANSITIONING": (LEAN_AMBIGUOUS, None),
    # MSSI structure_location (msi_market_structure.taxonomy).
    "INSIDE_RANGE": (LEAN_NEUTRAL_FORMING, taxonomy.OPPORTUNITY_STATE_MEAN_REVERSION),
    "NEAR_SUPPORT": (LEAN_NEUTRAL_FORMING, taxonomy.OPPORTUNITY_STATE_MEAN_REVERSION),
    "NEAR_RESISTANCE": (LEAN_NEUTRAL_FORMING, taxonomy.OPPORTUNITY_STATE_MEAN_REVERSION),
    "ABOVE_RESISTANCE": (LEAN_OPPORTUNITY_FORMING, taxonomy.OPPORTUNITY_STATE_BREAKOUT),
    "BELOW_SUPPORT": (LEAN_OPPORTUNITY_FORMING, taxonomy.OPPORTUNITY_STATE_BREAKOUT),
    "AT_RETEST": (LEAN_AMBIGUOUS, None),
    # MDI overall_direction (msi_market_direction.taxonomy).
    "STRONG_BULLISH": (LEAN_OPPORTUNITY_FORMING, taxonomy.OPPORTUNITY_STATE_DIRECTIONAL),
    "BULLISH": (LEAN_OPPORTUNITY_FORMING, taxonomy.OPPORTUNITY_STATE_DIRECTIONAL),
    "WEAK_BULLISH": (LEAN_OPPORTUNITY_FORMING, taxonomy.OPPORTUNITY_STATE_DIRECTIONAL),
    "WEAK_BEARISH": (LEAN_OPPORTUNITY_FORMING, taxonomy.OPPORTUNITY_STATE_DIRECTIONAL),
    "BEARISH": (LEAN_OPPORTUNITY_FORMING, taxonomy.OPPORTUNITY_STATE_DIRECTIONAL),
    "STRONG_BEARISH": (LEAN_OPPORTUNITY_FORMING, taxonomy.OPPORTUNITY_STATE_DIRECTIONAL),
    "MIXED": (LEAN_AMBIGUOUS, None),
    # MPPI positioning_bias (msi_participant_positioning.taxonomy).
    "BULLISH_POSITIONING": (LEAN_OPPORTUNITY_FORMING, taxonomy.OPPORTUNITY_STATE_DIRECTIONAL),
    "BEARISH_POSITIONING": (LEAN_OPPORTUNITY_FORMING, taxonomy.OPPORTUNITY_STATE_DIRECTIONAL),
    "NEUTRAL_POSITIONING": (LEAN_NEUTRAL_FORMING, taxonomy.OPPORTUNITY_STATE_NEUTRAL),
    "MIXED_POSITIONING": (LEAN_AMBIGUOUS, None),
    "UNKNOWN_POSITIONING": (LEAN_AMBIGUOUS, None),
    # VSB volatility_regime (msi_volatility_structure.taxonomy).
    "COMPRESSED": (LEAN_OPPORTUNITY_FORMING, taxonomy.OPPORTUNITY_STATE_VOLATILITY),
    "HIGH_VOLATILITY": (LEAN_OPPORTUNITY_FORMING, taxonomy.OPPORTUNITY_STATE_VOLATILITY),
    "STABLE": (LEAN_NEUTRAL_FORMING, taxonomy.OPPORTUNITY_STATE_NEUTRAL),
    # "UNKNOWN" is shared by multiple domains as their honest no-evidence
    # value -- correctly ambiguous for all of them, added once below.
    "UNKNOWN": (LEAN_AMBIGUOUS, None),
    "Illiquid": (LEAN_AMBIGUOUS, None),
    "Weak": (LEAN_AMBIGUOUS, None),
}

# Deterministic tie-break order among opportunity TYPES when more than
# one type has an equal number of votes among OPPORTUNITY_FORMING
# supporting domains. This is exactly `taxonomy.OPPORTUNITY_TYPES`'
# declaration order, referenced here (not redefined) so there is
# exactly one place the order lives.
TYPE_TIEBREAK_ORDER = taxonomy.OPPORTUNITY_TYPES

# ---------------------------------------------------------------------------
# Deliverable 5 — confidence_level banding. A deterministic function of
# (agreement_count, conflict_count) only -- see engine.py's
# `_confidence_level`. MUST be, and is, monotonic non-increasing in
# conflict_count for any fixed agreement_count (proven by
# tests/test_msi_decision_synthesis_engine.py::test_confidence_monotonic_with_conflict).
# ---------------------------------------------------------------------------
# net = agreement_count - conflict_count
CONFIDENCE_HIGH_MIN_NET = 2                 # net >= 2 -> HIGH outright.
CONFIDENCE_HIGH_MIN_NET_WITH_STRONG_AGREEMENT = 1   # net >= 1 AND agreement_count >= this many -> also HIGH.
CONFIDENCE_HIGH_MIN_AGREEMENT_FOR_NET1 = 2
CONFIDENCE_MODERATE_MIN_NET = 1             # net >= 1 (and not already HIGH) -> MODERATE.
CONFIDENCE_MODERATE_MIN_NET_AT_ZERO = 0     # net == 0 AND agreement_count >= 1 -> MODERATE.
# Everything else considered (net < 0, or net == 0 with zero agreement) -> LOW.
# Zero considered (non-ambiguous) domains at all -> NONE.

# ---------------------------------------------------------------------------
# Deliverable 2/opportunity_quality — a function of domain COVERAGE and
# the contributing domains' OWN self-reported confidence, deliberately
# independent of agreement/conflict (see models.py's module docstring
# and taxonomy.py's three-way split reasoning).
# ---------------------------------------------------------------------------
TOTAL_MSI_DOMAINS = len(taxonomy.ALL_MSI_DOMAINS)

QUALITY_EXCELLENT_MIN_SCORE = 0.60
QUALITY_GOOD_MIN_SCORE = 0.35
QUALITY_FAIR_MIN_SCORE = 0.15
# Below QUALITY_FAIR_MIN_SCORE -> POOR.
