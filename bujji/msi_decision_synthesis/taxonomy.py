"""MSI Decision Synthesis Engine vocabulary — BUJJI Engineering Series 77
(DSE v1).

Lives at `bujji/msi_decision_synthesis/`, outside `mic_v2`,
`bujji.mic_replay`, `bujji.production_runtime`, `bujji.trading_brain`,
and `bujji.strategy_selector` -- same isolation discipline as Series
73A/73B/73C/74/75/76. This package implements the fusion layer
described by `docs/MSI_V1_FOUNDATION.md` (Series 71) as architecturally
analogous to (but NOT identical to) Domain 9 "Regime Intelligence":
Regime Intelligence synthesizes structural regime from the 8 read
domains; the Decision Synthesis Engine (DSE) synthesizes a
`MarketOpportunityAssessment` from ALL 9 domains' published
intelligence (including Regime Intelligence's own output) AND folds in
strategy-family compatibility -- something Regime Intelligence
explicitly does not do (MSI_V1_FOUNDATION.md Deliverable 2, Domain 9).

No real MSI brain exists yet (Series 71 Deliverable 8/9's Gap
Analysis: only architecture, no implementation). This package is
therefore deliberately domain-agnostic: it never hardcodes which
brains exist, never imports `bujji.trading_brain`, and consumes only
the generic `DomainSignal` input contract defined in `models.py`.

Following this project's established convention (see
`bujji/market_observation/taxonomy.py`, `bujji/live_market_events/taxonomy.py`,
`bujji/market_episode/taxonomy.py`), closed vocabularies here are plain
string constants collected into `ALL_*` tuples, not `enum.Enum`
classes.

---------------------------------------------------------------------
Design note -- the three-way opportunity_state / confidence_level /
opportunity_quality split (this sprint's central taxonomy ambiguity):
---------------------------------------------------------------------
The spec's Deliverable 2 gives ONE flat example list of 10 values
(NO_ACTION, WAIT, MONITOR, LOW_CONVICTION, HIGH_CONVICTION,
DIRECTIONAL_OPPORTUNITY, NEUTRAL_OPPORTUNITY, VOLATILITY_OPPORTUNITY,
MEAN_REVERSION_OPPORTUNITY, BREAKOUT_OPPORTUNITY) but ALSO requires the
`MarketOpportunityAssessment` model to carry `opportunity_state`,
`confidence_level`, AND `opportunity_quality` as three SEPARATE fields.
Reusing "HIGH_CONVICTION"/"LOW_CONVICTION" as `opportunity_state`
values while ALSO needing a `confidence_level` field would be a real
naming collision -- two fields answering overlapping questions with
overlapping vocabulary is exactly the kind of ambiguity a synthesis
layer must resolve explicitly, not paper over. Resolution, reasoned
from first principles:

  * `opportunity_state` answers "WHAT KIND of opportunity, if any, is
    this." NO_ACTION / WAIT / MONITOR are the non-opportunity states
    (no edge, or not yet enough evidence to call one); DIRECTIONAL /
    NEUTRAL / VOLATILITY / MEAN_REVERSION / BREAKOUT are the five real
    opportunity TYPES. This is a TYPE taxonomy, never a confidence
    taxonomy -- it says nothing about how sure DSE is.

  * `confidence_level` answers "HOW SURE is DSE that this
    `opportunity_state` read is correct" -- i.e. how much the
    contributing domains agree vs. conflict (Deliverable 5's
    "confidence decreases with contradiction" requirement is a
    statement about THIS field, not about opportunity_state). Clean,
    non-overlapping names were chosen precisely to avoid the
    LOW_CONVICTION/HIGH_CONVICTION collision: `NONE, LOW, MODERATE,
    HIGH`.

  * `opportunity_quality` answers a THIRD, genuinely distinct
    question: "if this opportunity is real, how GOOD/well-evidenced is
    it" -- a function of evidence richness/domain coverage, not of
    agreement. A single domain reporting with total conviction is not
    the same as many independent domains reporting consistently with
    only moderate confidence each; the former is arguably higher
    "confidence" (little disagreement to speak of) but lower "quality"
    (thin evidentiary base). Conflating quality with confidence would
    silently discard exactly this distinction, which is why the spec
    lists them as separate fields in the first place. Resolved here as
    `POOR, FAIR, GOOD, EXCELLENT`, computed from domain coverage
    (how many of the 9 MSI domains contributed at all) combined with
    the contributing domains' own self-reported confidence floats --
    deliberately NOT from agreement/conflict counts, which belong
    entirely to `confidence_level`.

The spec's flat 10-value list is thus split as:
  opportunity_state:  NO_ACTION, WAIT, MONITOR, DIRECTIONAL_OPPORTUNITY,
                       NEUTRAL_OPPORTUNITY, VOLATILITY_OPPORTUNITY,
                       MEAN_REVERSION_OPPORTUNITY, BREAKOUT_OPPORTUNITY
  confidence_level:    NONE, LOW, MODERATE, HIGH   (replaces the spec's
                       LOW_CONVICTION/HIGH_CONVICTION with non-colliding
                       names, per the reasoning above)
  opportunity_quality: POOR, FAIR, GOOD, EXCELLENT (a new, third axis,
                       required by the model but not enumerated by the
                       spec's flat list at all)

---------------------------------------------------------------------
Relationship to `bujji.trading_brain` vocabulary (Step 0.4 finding --
reused BY NAME/CONCEPT ONLY, ZERO CODE IMPORTED, verified by this
package's AST isolation test):
---------------------------------------------------------------------
`bujji/trading_brain/strategy_selector/registry.py`'s
`StrategyDefinition.risk_profile` uses exactly two values,
`DEFINED_RISK` / `UNDEFINED_RISK` (confirmed by reading that file).
DSE's `STRATEGY_FAMILY_*` constants below reuse that same
DEFINED_RISK/UNDEFINED_RISK vocabulary as a NAMING PREFIX
(`DEFINED_RISK_DIRECTIONAL`, `UNDEFINED_RISK_PREMIUM`, ...) because it
is the right, already-proven distinction for this domain -- but DSE
never imports `bujji.trading_brain.strategy_selector.registry` and
never references any of that registry's named, concrete option
structures -- all such named-structure identifiers are explicitly
absent here, per this sprint's constraint that DSE recommends
opportunity CLASSES, never concrete trades.

`bujji/trading_brain/ontology/models.py`'s `TradingOntologySnapshot`
already has an `opportunity_state` field, and
`bujji/trading_brain/ontology/taxonomy.py` defines its own
`ALL_OPPORTUNITY_STATES = (UNKNOWN, AVOID, WATCH, LOW_EDGE, MEDIUM_EDGE,
HIGH_EDGE)` (confirmed by reading that file). That vocabulary answers a
different question than this package's `opportunity_state` -- it is an
EDGE-STRENGTH scale (is there an edge, and how strong), which is much
closer to what THIS package calls `confidence_level`/`opportunity_quality`
than to what this package calls `opportunity_state` (a TYPE taxonomy:
directional vs. neutral vs. volatility vs. mean-reversion vs.
breakout). Deliberately NOT reused verbatim, to avoid exactly the
naming collision the module docstring above works through: reusing
`HIGH_EDGE`/`LOW_EDGE` as `opportunity_state` values here would
recreate the same collision problem this file resolves for
LOW_CONVICTION/HIGH_CONVICTION. `risk_state`'s vocabulary
(`UNKNOWN, LOW, NORMAL, HIGH, EXTREME`) was considered and deliberately
NOT reused for `confidence_level` either -- it describes portfolio/
market RISK level, a different concept than "how much do domains
agree," even though both happen to use LOW/HIGH-shaped words.
"""
from __future__ import annotations

DSE_VERSION = "1.0.0"

RECOGNIZED_SCHEMA_VERSIONS = ("1.0.0",)

# ---------------------------------------------------------------------------
# opportunity_state — WHAT KIND of opportunity, if any (Deliverable 3).
# See module docstring for the three-way split reasoning.
# ---------------------------------------------------------------------------
OPPORTUNITY_STATE_NO_ACTION = "NO_ACTION"
OPPORTUNITY_STATE_WAIT = "WAIT"
OPPORTUNITY_STATE_MONITOR = "MONITOR"
OPPORTUNITY_STATE_DIRECTIONAL = "DIRECTIONAL_OPPORTUNITY"
OPPORTUNITY_STATE_NEUTRAL = "NEUTRAL_OPPORTUNITY"
OPPORTUNITY_STATE_VOLATILITY = "VOLATILITY_OPPORTUNITY"
OPPORTUNITY_STATE_MEAN_REVERSION = "MEAN_REVERSION_OPPORTUNITY"
OPPORTUNITY_STATE_BREAKOUT = "BREAKOUT_OPPORTUNITY"

# Non-opportunity states (no real edge, or not enough evidence to type one).
NON_OPPORTUNITY_STATES = (
    OPPORTUNITY_STATE_NO_ACTION,
    OPPORTUNITY_STATE_WAIT,
    OPPORTUNITY_STATE_MONITOR,
)

# The five real opportunity TYPES. Declaration order here is also the
# deterministic tie-break preference order engine.py uses when more
# than one type is equally, plurality-supported (see engine.py's
# `_TYPE_TIEBREAK_ORDER`, which is defined as exactly this tuple).
OPPORTUNITY_TYPES = (
    OPPORTUNITY_STATE_DIRECTIONAL,
    OPPORTUNITY_STATE_BREAKOUT,
    OPPORTUNITY_STATE_VOLATILITY,
    OPPORTUNITY_STATE_NEUTRAL,
    OPPORTUNITY_STATE_MEAN_REVERSION,
)

ALL_OPPORTUNITY_STATES = NON_OPPORTUNITY_STATES + OPPORTUNITY_TYPES

# ---------------------------------------------------------------------------
# confidence_level — HOW SURE DSE is that opportunity_state is correct,
# a function of domain agreement/conflict (Deliverable 5). Deliberately
# non-colliding with opportunity_state's vocabulary (see module docstring).
# ---------------------------------------------------------------------------
CONFIDENCE_NONE = "NONE"
CONFIDENCE_LOW = "LOW"
CONFIDENCE_MODERATE = "MODERATE"
CONFIDENCE_HIGH = "HIGH"

ALL_CONFIDENCE_LEVELS = (
    CONFIDENCE_NONE,
    CONFIDENCE_LOW,
    CONFIDENCE_MODERATE,
    CONFIDENCE_HIGH,
)

# Ranked order, used only for testing/comparing bands (e.g. the
# monotonic-decrease-with-conflict property test) — never used to
# compute a score itself.
CONFIDENCE_RANK = {
    CONFIDENCE_NONE: 0,
    CONFIDENCE_LOW: 1,
    CONFIDENCE_MODERATE: 2,
    CONFIDENCE_HIGH: 3,
}

# ---------------------------------------------------------------------------
# opportunity_quality — HOW GOOD/well-evidenced the opportunity is, if
# real -- a function of domain coverage/evidence richness, deliberately
# NOT of agreement/conflict (see module docstring for why conflating
# quality and confidence would be a design flaw).
# ---------------------------------------------------------------------------
QUALITY_POOR = "POOR"
QUALITY_FAIR = "FAIR"
QUALITY_GOOD = "GOOD"
QUALITY_EXCELLENT = "EXCELLENT"

ALL_OPPORTUNITY_QUALITIES = (
    QUALITY_POOR,
    QUALITY_FAIR,
    QUALITY_GOOD,
    QUALITY_EXCELLENT,
)

QUALITY_RANK = {
    QUALITY_POOR: 0,
    QUALITY_FAIR: 1,
    QUALITY_GOOD: 2,
    QUALITY_EXCELLENT: 3,
}

# ---------------------------------------------------------------------------
# Strategy families (Deliverable 4) — plain string constants, no
# named, concrete option structures, per this sprint's explicit
# constraint; those belong to a future Strategy Selector.
# `DEFINED_RISK_*`/`UNDEFINED_RISK_*` naming reuses
# bujji.trading_brain.strategy_selector.registry's `risk_profile`
# vocabulary BY NAME ONLY (see module docstring, Step 0.4) — zero code
# imported from that package.
# ---------------------------------------------------------------------------
STRATEGY_FAMILY_DEFINED_RISK_DIRECTIONAL = "DEFINED_RISK_DIRECTIONAL"
STRATEGY_FAMILY_DEFINED_RISK_NEUTRAL = "DEFINED_RISK_NEUTRAL"
STRATEGY_FAMILY_DEFINED_RISK_VOLATILITY = "DEFINED_RISK_VOLATILITY"
STRATEGY_FAMILY_UNDEFINED_RISK_PREMIUM = "UNDEFINED_RISK_PREMIUM"
STRATEGY_FAMILY_CALENDAR = "CALENDAR"
STRATEGY_FAMILY_DIAGONAL = "DIAGONAL"
STRATEGY_FAMILY_HEDGED_DIRECTIONAL = "HEDGED_DIRECTIONAL"

ALL_STRATEGY_FAMILIES = (
    STRATEGY_FAMILY_DEFINED_RISK_DIRECTIONAL,
    STRATEGY_FAMILY_DEFINED_RISK_NEUTRAL,
    STRATEGY_FAMILY_DEFINED_RISK_VOLATILITY,
    STRATEGY_FAMILY_UNDEFINED_RISK_PREMIUM,
    STRATEGY_FAMILY_CALENDAR,
    STRATEGY_FAMILY_DIAGONAL,
    STRATEGY_FAMILY_HEDGED_DIRECTIONAL,
)

# ---------------------------------------------------------------------------
# MSI domain names (Deliverable 2, Series 71) — reused generically so
# DSE can reference "which domain said what" without hardcoding which
# brains actually exist yet (none do, per Series 71 Deliverable 8/9).
# ---------------------------------------------------------------------------
DOMAIN_PRICE_STRUCTURE = "PRICE_STRUCTURE"
DOMAIN_SUPPORT_RESISTANCE = "SUPPORT_RESISTANCE"
DOMAIN_OPTIONS_MARKET_STRUCTURE = "OPTIONS_MARKET_STRUCTURE"
DOMAIN_FUTURES_STRUCTURE = "FUTURES_STRUCTURE"
DOMAIN_VOLATILITY_STRUCTURE = "VOLATILITY_STRUCTURE"
DOMAIN_LIQUIDITY = "LIQUIDITY"
DOMAIN_TIME_STRUCTURE = "TIME_STRUCTURE"
DOMAIN_CROSS_ASSET = "CROSS_ASSET"
DOMAIN_REGIME_INTELLIGENCE = "REGIME_INTELLIGENCE"

ALL_MSI_DOMAINS = (
    DOMAIN_PRICE_STRUCTURE,
    DOMAIN_SUPPORT_RESISTANCE,
    DOMAIN_OPTIONS_MARKET_STRUCTURE,
    DOMAIN_FUTURES_STRUCTURE,
    DOMAIN_VOLATILITY_STRUCTURE,
    DOMAIN_LIQUIDITY,
    DOMAIN_TIME_STRUCTURE,
    DOMAIN_CROSS_ASSET,
    DOMAIN_REGIME_INTELLIGENCE,
)

# Domains whose reasoning responsibility (per MSI Deliverable 2) is a
# structural PRECONDITION (can this be executed / how should evidence
# be weighted) rather than a directional/typed opportunity signal.
# Signals from these domains are never forced into the
# agreement/conflict vote (see engine.py's `_lean_for_signal`) because
# doing so would fabricate a directional read these domains are not
# designed to produce (Liquidity: "not a signal about direction",
# Time Structure: "changes weighting... without producing directional
# conclusions" — both direct quotes from MSI_V1_FOUNDATION.md Deliverable 2).
PRECONDITION_DOMAINS = (
    DOMAIN_LIQUIDITY,
    DOMAIN_TIME_STRUCTURE,
)
