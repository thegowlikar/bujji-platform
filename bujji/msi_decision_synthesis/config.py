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
