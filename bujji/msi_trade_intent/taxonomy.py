"""Trade Intent Intelligence (TII) vocabulary — BUJJI Engineering
Series 83.

Lives at `bujji/msi_trade_intent/`, outside `mic_v2`, `bujji.mic_replay`,
`bujji.production_runtime`, `bujji.trading_brain`,
`bujji.strategy_selector`, and `fyers_apiv3` — same isolation
discipline as every prior series in this arc. Like Series 82
(`bujji.msi_strategy_eligibility`), TII is a DELIBERATE, DOWNSTREAM
consumer of real sibling model types, not a peer performing an
independent domain read. See `engine.py`'s module docstring for the
full "Architecture boundary" reasoning.

---------------------------------------------------------------------
Check 1 finding (STEP 0, reconfirmed here) — "Strategy Selection" does
NOT exist as a built stage, and `StrategySelectionAssessment` does not
exist anywhere in this codebase.
---------------------------------------------------------------------
Confirmed by direct commands against the live repo at
`/opt/bujji/app`:
    ls bujji/ | grep -iE 'strategy_selection|strategy.selector'
    find bujji -iname '*StrategySelectionAssessment*' -o -iname '*msi_strategy_selection*'
The first found nothing named "strategy_selection"; the second found
nothing at all. `bujji/trading_brain/strategy_selector/` DOES exist,
but it is a dormant, unrelated legacy module from an earlier, different
arc of this project -- not part of the MSI 77-83 series (confirmed:
nothing in `bujji.trading_brain` is imported by the live app or by any
MSI-series package). The most recently completed REAL series is
Series 82 -- Strategy Eligibility (`bujji.msi_strategy_eligibility`),
which produces `StrategyEligibilityAssessment`
(`eligible_strategy_families`/`ineligible_strategy_families` -- a SET
of eligible families, not a single selection).

Resolution: Series 83 consumes Series 82's REAL
`StrategyEligibilityAssessment` directly. Since Deliverable 4 requires
converting "a selected strategy family" (singular) into intent, and no
real selector exists yet to produce one, `engine.py` implements a
MINIMAL, clearly-disclosed, deterministic placeholder --
`_placeholder_select_one_eligible_family` -- that is NOT a real
strategy selector (no scoring, no optimization): it applies a fixed,
disclosed priority order (see `config.FAMILY_SELECTION_PRIORITY_ORDER`)
over the eligible set to pick exactly one family, purely as a
tie-breaking rule so Trade Intent has something concrete to consume.
A real Strategy Selector remains future work (Series 84+).

---------------------------------------------------------------------
Check 1b finding (discovered during this sprint's own Step 0 study,
same rigor as Check 1) — neither of Trade Intent's two real,
legitimately-consumable upstream types carries an aggregate directional
("bullish"/"bearish") lean field.
---------------------------------------------------------------------
Confirmed by reading `bujji/msi_decision_synthesis/models.py`
(`MarketOpportunityAssessment` has `opportunity_state` [a TYPE of
opportunity, e.g. "DIRECTIONAL_OPPORTUNITY", not a DIRECTION],
`confidence_level`, `opportunity_quality`, `supporting_domains`/
`conflicting_domains` [domain NAMES, not directions],
`compatible_strategy_families`/`incompatible_strategy_families`
[family NAMES] -- no bullish/bearish field anywhere) and
`bujji/msi_consensus/models.py` (`ConsensusAssessment` has
`agreeing_domains`/`conflicting_domains` [domain NAMES] and no stored
aggregate `lean` value at all -- the per-domain `lean` values that feed
`compute_consensus()` are transient CALLER-supplied inputs
(`DomainAssessmentView.lean`) and are never retained on the output
`ConsensusAssessment` object).

Resolution: `market_bias` (Deliverable 3) CANNOT be honestly derived as
LONG_DELTA/SHORT_DELTA from real upstream evidence today -- doing so
would fabricate a directional read neither real upstream type
provides. `engine.derive_market_bias()` therefore deterministically
returns `MARKET_BIAS_DELTA_NEUTRAL` for every family until a future
series publishes a real aggregate directional-lean field on either
`MarketOpportunityAssessment` or `ConsensusAssessment`. This is a
genuine, disclosed limitation of Series 83 -- not a hardcoded
shortcut hidden from the reader. See `docs/MSI_TRADE_INTENT.md` "Known
limitations" and `tests/test_msi_trade_intent.py`'s
`test_market_bias_defaults_deterministically_absent_real_directional_signal`
for the honest test that replaces the originally-specified (but
unbuildable, given real data) "opposite-direction opportunity" test.

Trade Intent still legitimately consumes `MarketOpportunityAssessment`
directly (in addition to `StrategyEligibilityAssessment`) -- not for a
directional-lean field that doesn't exist, but because
`opportunity_state`, `confidence_level`, and `opportunity.assessment_id`
are real, checkable fields used by `derive_invalidation_conditions` and
`build_explanation`, and because Deliverable 5's invalidation
conditions must be able to reference "the opportunity_state changing
away from the one that produced this intent" -- something only the
real `MarketOpportunityAssessment` object, not `StrategyEligibilityAssessment`
alone, can supply.

---------------------------------------------------------------------
Naming-collision disclosure — `volatility_bias`'s LONG_VOLATILITY /
SHORT_VOLATILITY values vs. Series 82's STRATEGY FAMILY names of the
same spelling.
---------------------------------------------------------------------
Series 82's taxonomy (`bujji.msi_strategy_eligibility.taxonomy`) has
`FAMILY_LONG_VOLATILITY = "LONG_VOLATILITY"` and
`FAMILY_SHORT_VOLATILITY = "SHORT_VOLATILITY"` -- STRATEGY FAMILY
names. This package's `VOLATILITY_BIAS_LONG = "LONG_VOLATILITY"` and
`VOLATILITY_BIAS_SHORT = "SHORT_VOLATILITY"` are an INTENT DIMENSION
(this trade's net vega posture), a genuinely different concept: a
`DEFINED_RISK_DIRECTIONAL`-family trade intent could in principle carry
either volatility_bias depending on structure, and this package's own
`derive_volatility_bias()` maps the `CALENDAR` family (not a
"volatility" family in 82's taxonomy at all) to `VOLATILITY_BIAS_LONG`.
The string spelling overlap across the two packages is a REAL,
disclosed risk worth naming plainly -- it is acceptable here only
because the two packages' fields (`StrategyEligibilityAssessment.
eligible_strategy_families` vs. `TradeIntentAssessment.volatility_bias`)
are never directly compared or unioned by any caller in this codebase;
a future caller must not assume equality of these strings implies
equality of meaning.

---------------------------------------------------------------------
`market_bias` vs. `directional_exposure` — Deliverable 2 lists BOTH as
separate fields; resolved as two genuinely different dimensions.
---------------------------------------------------------------------
`market_bias` is the qualitative delta LEAN (LONG_DELTA/SHORT_DELTA/
DELTA_NEUTRAL) -- see the Check 1b limitation above (currently always
DELTA_NEUTRAL, honestly disclosed). `directional_exposure` is a
different, STRUCTURAL characterization of the position's exposure
SHAPE (DEFINED/UNDEFINED/HEDGED), deliberately mirroring the
DEFINED_RISK/UNDEFINED_RISK/HEDGED_DIRECTIONAL family concepts from
Series 77/82 but reframed as an exposure-SHAPE property of the
resulting trade intent, not a family label. Forcing these two fields
to mean the same thing would collapse a real distinction: two
`DEFINED_RISK_DIRECTIONAL`-family intents could differ in market_bias
(if a real directional-lean field existed) while always sharing
directional_exposure=DEFINED (a property of the FAMILY's risk
structure, not of which way the market is expected to move).
"""
from __future__ import annotations

TII_VERSION = "1.0.0"

RECOGNIZED_SCHEMA_VERSIONS = ("1.0.0",)

# ---------------------------------------------------------------------------
# market_bias (Deliverable 3) -- qualitative delta lean. See Check 1b
# above: honestly always DELTA_NEUTRAL today, given real upstream data.
# ---------------------------------------------------------------------------
MARKET_BIAS_LONG_DELTA = "LONG_DELTA"
MARKET_BIAS_SHORT_DELTA = "SHORT_DELTA"
MARKET_BIAS_DELTA_NEUTRAL = "DELTA_NEUTRAL"

ALL_MARKET_BIASES = (
    MARKET_BIAS_LONG_DELTA,
    MARKET_BIAS_SHORT_DELTA,
    MARKET_BIAS_DELTA_NEUTRAL,
)

# ---------------------------------------------------------------------------
# volatility_bias (Deliverable 3) -- net vega posture. See the naming-
# collision disclosure above re: Series 82's family names of the same
# spelling -- a different dimension, never directly compared.
# ---------------------------------------------------------------------------
VOLATILITY_BIAS_LONG = "LONG_VOLATILITY"
VOLATILITY_BIAS_SHORT = "SHORT_VOLATILITY"
VOLATILITY_BIAS_NEUTRAL = "VOLATILITY_NEUTRAL"

ALL_VOLATILITY_BIASES = (
    VOLATILITY_BIAS_LONG,
    VOLATILITY_BIAS_SHORT,
    VOLATILITY_BIAS_NEUTRAL,
)

# ---------------------------------------------------------------------------
# premium_exposure (Deliverable 3) -- whether the intent is a net
# premium collector, payer, or neutral.
# ---------------------------------------------------------------------------
PREMIUM_EXPOSURE_COLLECTION = "PREMIUM_COLLECTION"
PREMIUM_EXPOSURE_PAYMENT = "PREMIUM_PAYMENT"
PREMIUM_EXPOSURE_NEUTRAL = "PREMIUM_NEUTRAL"

ALL_PREMIUM_EXPOSURES = (
    PREMIUM_EXPOSURE_COLLECTION,
    PREMIUM_EXPOSURE_PAYMENT,
    PREMIUM_EXPOSURE_NEUTRAL,
)

# ---------------------------------------------------------------------------
# directional_exposure (Deliverable 2/3) -- exposure SHAPE, distinct
# from market_bias. See the "market_bias vs. directional_exposure"
# resolution above.
# ---------------------------------------------------------------------------
DIRECTIONAL_EXPOSURE_DEFINED = "DEFINED"
DIRECTIONAL_EXPOSURE_UNDEFINED = "UNDEFINED"
DIRECTIONAL_EXPOSURE_HEDGED = "HEDGED"

ALL_DIRECTIONAL_EXPOSURES = (
    DIRECTIONAL_EXPOSURE_DEFINED,
    DIRECTIONAL_EXPOSURE_UNDEFINED,
    DIRECTIONAL_EXPOSURE_HEDGED,
)

# ---------------------------------------------------------------------------
# risk_profile (Deliverable 3).
# ---------------------------------------------------------------------------
RISK_PROFILE_DEFINED = "DEFINED_RISK"
RISK_PROFILE_UNDEFINED = "UNDEFINED_RISK"

ALL_RISK_PROFILES = (
    RISK_PROFILE_DEFINED,
    RISK_PROFILE_UNDEFINED,
)

# ---------------------------------------------------------------------------
# intent_state (Deliverable 2) -- lifecycle/status of a formed intent,
# BEFORE any strike/expiry/quantity construction work happens.
#
# Transition rule design (disclosed, not enforced by code in this
# sprint): FORMED is the state a real `determine_trade_intent()` call
# always returns for a non-None result. AWAITING_CONSTRUCTION describes
# an intent that has been handed to a (not-yet-built) Strike Selection
# stage but construction has not yet completed. INVALIDATED describes
# an intent whose invalidation_conditions have since been tripped by
# fresh evidence -- BEFORE any strike/expiry/execution work happened.
# This package defines the state VOCABULARY (Deliverable 2's
# requirement) and each state's meaning; it deliberately does NOT
# implement a revalidation/transition function, since that requires
# re-running against fresh upstream assessments over time -- genuinely
# future work (Series 84+), not part of this sprint's scope.
# `engine.determine_trade_intent()` itself only ever returns
# INTENT_STATE_FORMED (or None, if no family could be selected).
# ---------------------------------------------------------------------------
INTENT_STATE_FORMED = "FORMED"
INTENT_STATE_AWAITING_CONSTRUCTION = "AWAITING_CONSTRUCTION"
INTENT_STATE_INVALIDATED = "INVALIDATED"

ALL_INTENT_STATES = (
    INTENT_STATE_FORMED,
    INTENT_STATE_AWAITING_CONSTRUCTION,
    INTENT_STATE_INVALIDATED,
)

# ---------------------------------------------------------------------------
# IntentConfidence -- independently defined NONE/LOW/MODERATE/HIGH
# scheme, same established convention as 77/81/82's confidence-style
# fields, but its own separate constants (never imported from a
# sibling).
# ---------------------------------------------------------------------------
INTENT_CONFIDENCE_NONE = "NONE"
INTENT_CONFIDENCE_LOW = "LOW"
INTENT_CONFIDENCE_MODERATE = "MODERATE"
INTENT_CONFIDENCE_HIGH = "HIGH"

ALL_INTENT_CONFIDENCE_LEVELS = (
    INTENT_CONFIDENCE_NONE,
    INTENT_CONFIDENCE_LOW,
    INTENT_CONFIDENCE_MODERATE,
    INTENT_CONFIDENCE_HIGH,
)

INTENT_CONFIDENCE_RANK = {
    INTENT_CONFIDENCE_NONE: 0,
    INTENT_CONFIDENCE_LOW: 1,
    INTENT_CONFIDENCE_MODERATE: 2,
    INTENT_CONFIDENCE_HIGH: 3,
}
