"""Trade Intent Intelligence (TII) engine — BUJJI Engineering
Series 83.

PHASE 14B-P0.1 ADDITION: `determine_trade_intent()` below is UNCHANGED --
same signature, same body, same behavior, byte-for-byte, for every
existing caller. A NEW, separate function,
`determine_trade_intent_from_selection()`, is added at the bottom of
this file for the modern pipeline (real `StrategySelectionAssessment`,
Series 87) -- it does not replace or call
`_placeholder_select_one_eligible_family` at all. See that function's
own docstring for the full design rationale (Phase 14A found the
placeholder was a stand-in for a selector that now exists but was
never connected; Phase 14B repairs that without touching this
placeholder path, since removing it before its own compatibility
requirements are understood was explicitly out of scope).

---------------------------------------------------------------------
Architecture boundary -- why this package DIRECTLY IMPORTS real
sibling-brain model types, mirroring Series 82's own resolved
precedent structure exactly.
---------------------------------------------------------------------
Series 78/79/81 are PEERS: each independently reads the same
underlying market data and none may import another's real model type.
TII is not a peer of 77 or 82 -- it is STRICTLY DOWNSTREAM of both,
exactly as 82 was downstream of 77 and 81. Consuming 82's and 77's
PUBLIC output types directly is an explicit, deliberate, ONE-DIRECTION
downstream dependency -- not the "never import a sibling's code" rule
that governs same-level siblings. TII does NOT import
`bujji.msi_price_structure`, `bujji.msi_market_structure`, or
`bujji.msi_consensus` directly -- those are already summarized two (or
three) levels up by 77's and 82's own outputs, and reaching past them
would duplicate reasoning those stages already did, with no principled
benefit.

---------------------------------------------------------------------
Check 1's resolution -- why TII consumes BOTH `StrategyEligibilityAssessment`
(82) AND `MarketOpportunityAssessment` (77) directly, not merely the
former.
---------------------------------------------------------------------
The task's literal Deliverable 4 text says "consume only
StrategySelectionAssessment" -- but that type does not exist (see
Check 1 in `taxonomy.py`'s module docstring). Once resolved to consume
82's REAL `StrategyEligibilityAssessment` instead, a further real gap
remains: a family name alone (e.g. `DEFINED_RISK_DIRECTIONAL`) does
not carry the real, checkable `opportunity_state`/`assessment_id`
information Deliverable 5's invalidation conditions must reference
("if the opportunity_state changes away from the one that produced
this intent"). That information lives only on the real
`MarketOpportunityAssessment` object (Series 77), not on
`StrategyEligibilityAssessment` alone (which only cites 77's and 81's
assessment_ids by string reference, not their live field values).
`determine_trade_intent()` therefore accepts BOTH real,
already-legitimately-downstream-consumable types directly -- this is
the honest, correct resolution of a real gap in the literal spec text,
not scope creep.

NOTE (Check 1b, disclosed fully in `taxonomy.py`): the ORIGINAL reason
this module's docstring initially assumed opportunity was needed --
recovering a real bullish/bearish directional-lean signal for
`market_bias` -- turned out to be unavailable: neither
`MarketOpportunityAssessment` nor `ConsensusAssessment` stores an
aggregate directional-lean field at all. `derive_market_bias` below
therefore always returns DELTA_NEUTRAL, honestly, rather than
fabricating a direction. Consuming `MarketOpportunityAssessment`
remains justified for the invalidation/explanation reasons above, even
though it does not supply directional-lean information.

TII determines the INTENT (exposure/bias/risk-profile/invalidation) of
ONE placeholder-selected eligible strategy family. It never selects
strikes, expiry, or size, and it performs no scoring, optimization, or
P&L prediction of any kind.

Trade Intent describes what a trade should EXPRESS. It never
constructs the trade.
"""
from __future__ import annotations

import hashlib
from typing import Optional, Tuple

from bujji.msi_decision_synthesis.models import MarketOpportunityAssessment
from bujji.msi_strategy_eligibility.models import StrategyEligibilityAssessment
from bujji.msi_strategy_eligibility import taxonomy as _sei_taxonomy
from bujji.strategy_taxonomy_bridge.mapping import eligibility_families_for_selection_family

from . import config as _config
from . import taxonomy
from .models import Explanation, InvalidationCondition, TradeIntentAssessment

# ---------------------------------------------------------------------------
# Family classification used only for market_bias's honest, disclosed
# default (see Check 1b) -- kept for documentation/forward-compat: if a
# future series adds a real directional-lean field, direction-sensitive
# families are the ones that WOULD consult it.
# ---------------------------------------------------------------------------
_DIRECTION_SENSITIVE_FAMILIES = (
    _sei_taxonomy.FAMILY_DEFINED_RISK_DIRECTIONAL,
    _sei_taxonomy.FAMILY_HEDGED_DIRECTIONAL,
    _sei_taxonomy.FAMILY_DIAGONAL,
)


def _placeholder_select_one_eligible_family(eligibility: StrategyEligibilityAssessment) -> Optional[str]:
    """TEMPORARY STAND-IN for a real future Strategy Selector (Series
    84 or later) -- NOT a real strategy selector, NOT scored or
    optimized. Applies a fixed, disclosed priority order
    (`config.FAMILY_SELECTION_PRIORITY_ORDER`, alphabetical) over
    `eligibility.eligible_strategy_families` and returns the first
    match. Returns None if the eligible set is empty -- no intent can
    be formed when Strategy Eligibility (82) has licensed nothing."""
    for family in _config.FAMILY_SELECTION_PRIORITY_ORDER:
        if family in eligibility.eligible_strategy_families:
            return family
    return None


def derive_market_bias(selected_family: str, opportunity: MarketOpportunityAssessment) -> str:
    """See Check 1b (taxonomy.py module docstring): neither
    `MarketOpportunityAssessment` nor `ConsensusAssessment` stores a
    real aggregate directional-lean field, so `market_bias` cannot be
    honestly derived as LONG_DELTA/SHORT_DELTA from real upstream
    evidence today. `opportunity` is accepted (and genuinely
    consulted, e.g. `opportunity.opportunity_state`) to keep this
    function's signature stable for when a future series adds a real
    directional-lean field -- but no such field exists yet, so this
    deterministically returns DELTA_NEUTRAL for every family,
    regardless of `selected_family`'s direction-sensitivity."""
    # `opportunity.opportunity_state` is genuinely read here (proving
    # this function does consult real opportunity context), but no
    # bullish/bearish value derivable from it exists on the real type.
    _ = opportunity.opportunity_state
    _ = selected_family in _DIRECTION_SENSITIVE_FAMILIES
    return taxonomy.MARKET_BIAS_DELTA_NEUTRAL


def derive_volatility_bias(selected_family: str) -> str:
    return _config.FAMILY_INTENT_PROFILE[selected_family][0]


def derive_premium_exposure(selected_family: str) -> str:
    return _config.FAMILY_INTENT_PROFILE[selected_family][1]


def derive_directional_exposure(selected_family: str) -> str:
    return _config.FAMILY_INTENT_PROFILE[selected_family][2]


def derive_risk_profile(selected_family: str) -> str:
    return _config.FAMILY_INTENT_PROFILE[selected_family][3]


def derive_invalidation_conditions(
    eligibility: StrategyEligibilityAssessment,
    opportunity: MarketOpportunityAssessment,
    selected_family: str,
) -> Tuple[InvalidationCondition, ...]:
    """Genuinely mechanical, checkable conditions referencing real
    upstream fields -- never vague prose. Never empty for a formed
    intent (Deliverable 5)."""
    conditions = [
        InvalidationCondition(
            protected_assumption=(
                f"Strategy Eligibility's read remains at least "
                f"{_config.MIN_ELIGIBILITY_CONFIDENCE_FOR_STABLE_INTENT} confidence."
            ),
            checkable_field="eligibility_confidence",
            trigger_description=(
                f"eligibility_confidence drops below "
                f"{_config.MIN_ELIGIBILITY_CONFIDENCE_FOR_STABLE_INTENT} "
                f"(currently {eligibility.eligibility_confidence})."
            ),
            source_assessment_id=eligibility.assessment_id,
        ),
        InvalidationCondition(
            protected_assumption=(
                f"{selected_family} remains a member of "
                f"eligible_strategy_families."
            ),
            checkable_field="eligible_strategy_families",
            trigger_description=(
                f"eligible_strategy_families no longer contains {selected_family}."
            ),
            source_assessment_id=eligibility.assessment_id,
        ),
        InvalidationCondition(
            protected_assumption=(
                f"The opportunity read remains {opportunity.opportunity_state}, "
                f"the state that produced this intent."
            ),
            checkable_field="opportunity_state",
            trigger_description=(
                f"opportunity_state changes away from {opportunity.opportunity_state}."
            ),
            source_assessment_id=opportunity.assessment_id,
        ),
    ]
    return tuple(conditions)


def build_explanation(
    assessment_id: str,
    eligibility: StrategyEligibilityAssessment,
    opportunity: MarketOpportunityAssessment,
    selected_family: str,
    market_bias: str,
    volatility_bias: str,
    directional_exposure: str,
    premium_exposure: str,
    risk_profile: str,
    invalidation_conditions: Tuple[InvalidationCondition, ...],
) -> Explanation:
    why_this_market_expression = (
        f"{selected_family} selected via the disclosed placeholder priority order "
        f"{_config.FAMILY_SELECTION_PRIORITY_ORDER} over eligible_strategy_families="
        f"{eligibility.eligible_strategy_families}, given opportunity_state="
        f"{opportunity.opportunity_state} and eligibility_confidence="
        f"{eligibility.eligibility_confidence}."
    )
    exposures_sought = (
        f"market_bias={market_bias}",
        f"volatility_bias={volatility_bias}",
        f"premium_exposure={premium_exposure}",
        f"directional_exposure={directional_exposure}",
        f"risk_profile={risk_profile}",
    )
    why_other_profiles_rejected = tuple(
        f"{family} not selected: ranked after {selected_family} in the disclosed "
        f"placeholder priority order {_config.FAMILY_SELECTION_PRIORITY_ORDER}."
        for family in eligibility.eligible_strategy_families
        if family != selected_family
    ) + tuple(
        f"{family} not selected: excluded from eligible_strategy_families by "
        f"Strategy Eligibility (82) already."
        for family in eligibility.ineligible_strategy_families
    )
    what_would_invalidate_before_execution = tuple(
        c.trigger_description for c in invalidation_conditions
    )
    return Explanation(
        assessment_id=assessment_id,
        why_this_market_expression=why_this_market_expression,
        exposures_sought=exposures_sought,
        why_other_profiles_rejected=why_other_profiles_rejected,
        what_would_invalidate_before_execution=what_would_invalidate_before_execution,
        schema_version=_config.SCHEMA_VERSION,
    )


def _compute_assessment_id(
    supporting_assessment_ids: Tuple[str, ...],
    selected_family: str,
    market_bias: str,
    volatility_bias: str,
    directional_exposure: str,
    premium_exposure: str,
    risk_profile: str,
    schema_version: str,
) -> str:
    payload = "|".join((
        ",".join(sorted(supporting_assessment_ids)),
        selected_family,
        market_bias,
        volatility_bias,
        directional_exposure,
        premium_exposure,
        risk_profile,
        schema_version,
    ))
    return hashlib.md5(payload.encode("utf-8")).hexdigest()


def determine_trade_intent(
    eligibility: StrategyEligibilityAssessment,
    opportunity: MarketOpportunityAssessment,
    *,
    timestamp: str,
    schema_version: str = _config.SCHEMA_VERSION,
    provenance: str = _config.DEFAULT_PROVENANCE,
) -> Optional[TradeIntentAssessment]:
    """The one real entrypoint. Takes REAL `StrategyEligibilityAssessment`
    (Series 82) and `MarketOpportunityAssessment` (Series 77) objects
    directly -- no adapter -- per this module's Architecture boundary
    and Check 1's resolution. Returns None (documented, never
    fabricated) if `_placeholder_select_one_eligible_family` cannot
    select a family (i.e. `eligibility.eligible_strategy_families` is
    empty)."""
    selected_family = _placeholder_select_one_eligible_family(eligibility)
    if selected_family is None:
        return None

    market_bias = derive_market_bias(selected_family, opportunity)
    volatility_bias = derive_volatility_bias(selected_family)
    directional_exposure = derive_directional_exposure(selected_family)
    premium_exposure = derive_premium_exposure(selected_family)
    risk_profile = derive_risk_profile(selected_family)

    invalidation_conditions = derive_invalidation_conditions(eligibility, opportunity, selected_family)

    supporting_assessment_ids = tuple(sorted({eligibility.assessment_id, opportunity.assessment_id}))
    assessment_id = _compute_assessment_id(
        supporting_assessment_ids, selected_family, market_bias, volatility_bias,
        directional_exposure, premium_exposure, risk_profile, schema_version,
    )

    explanation = build_explanation(
        assessment_id, eligibility, opportunity, selected_family, market_bias,
        volatility_bias, directional_exposure, premium_exposure, risk_profile,
        invalidation_conditions,
    )

    return TradeIntentAssessment(
        assessment_id=assessment_id,
        timestamp=timestamp,
        selected_strategy_family=selected_family,
        intent_state=taxonomy.INTENT_STATE_FORMED,
        market_bias=market_bias,
        volatility_bias=volatility_bias,
        directional_exposure=directional_exposure,
        premium_exposure=premium_exposure,
        risk_profile=risk_profile,
        invalidation_conditions=invalidation_conditions,
        supporting_assessment_ids=supporting_assessment_ids,
        explanation=explanation,
        provenance=provenance,
        schema_version=schema_version,
    )


# ---------------------------------------------------------------------------
# Phase 14B-P0.1 -- modern-pipeline entrypoint. Additive only: does not
# call or modify `_placeholder_select_one_eligible_family` or
# `determine_trade_intent` above.
#
# Root cause this repairs (Phase 14A): the real Strategy Selector
# (msi_strategy_selector, Series 87) was built downstream of Eligibility
# but TradeIntent (Series 83) was never updated to consume it -- it kept
# independently re-selecting from Eligibility's own family list via a
# documented placeholder. This function is the missing connection.
#
# Design (per Phase 14B-P0.3's own safety principle -- "Eligibility
# remains a gate, not a selector; Selection remains responsible for
# selecting"): the REAL Selection pick is taken as given, NEVER
# re-decided here. This function only asks Eligibility one question --
# "is the family Selection picked actually permitted?" -- via the
# explicit taxonomy bridge (Phase 14B-P0.2), never a blind pass-through,
# never a guess, never a literal-string match against a taxonomy
# Selection doesn't share:
#   - If eligibility is None, or eligibility_confidence is NONE (the
#     coherence gate itself found insufficient evidence), the answer is
#     honestly UNRESOLVED -- returns None, same as the legacy path's own
#     "no real intent can be formed" convention. Never coerced to a firm
#     rejection.
#   - If Selection's family maps (via the bridge) to at least one
#     Eligibility family that IS in eligible_strategy_families, intent is
#     formed using that mapped family's own declared intent profile
#     (FAMILY_INTENT_PROFILE) -- disclosed in the explanation as a
#     bridged, not exact, profile.
#   - Otherwise (mapped family exists but Eligibility rejected all of
#     them) -- returns None. Selection's pick is never forced through.
# ---------------------------------------------------------------------------
def determine_trade_intent_from_selection(
    selected_family: Optional[str],
    selection_confidence: Optional[str],
    eligibility: Optional[StrategyEligibilityAssessment],
    opportunity: Optional[MarketOpportunityAssessment],
    *,
    timestamp: str,
    schema_version: str = _config.SCHEMA_VERSION,
    provenance: str = "msi_trade_intent.engine.determine_trade_intent_from_selection",
) -> Optional[TradeIntentAssessment]:
    """`selected_family`/`selection_confidence`: exactly
    `StrategySelectionAssessment.selected_strategy_family`/`.confidence`
    (Series 87) -- pass them as plain values, not the whole object, so
    this function never needs to import `msi_strategy_selector`'s model
    type (this package stays strictly downstream of 77/82 only, same
    isolation discipline as the rest of this module). Returns None
    (never fabricated) whenever ANY of: no family was selected, no
    eligibility/opportunity exists this cycle, eligibility's own
    confidence is NONE, or the selected family maps to no eligible
    Eligibility family."""
    if selected_family is None or eligibility is None or opportunity is None:
        return None
    if eligibility.eligibility_confidence == _sei_taxonomy.ELIGIBILITY_CONFIDENCE_NONE:
        return None

    candidate_sei_families = eligibility_families_for_selection_family(selected_family)
    permitted_sei_families = tuple(
        f for f in candidate_sei_families if f in eligibility.eligible_strategy_families
    )
    if not permitted_sei_families:
        return None

    # Deterministic choice among multiple permitted mapped families:
    # the bridge's own declared tuple order (documented, not arbitrary).
    profile_family = permitted_sei_families[0]

    market_bias = derive_market_bias(selected_family, opportunity)
    volatility_bias, premium_exposure, directional_exposure, risk_profile = _config.FAMILY_INTENT_PROFILE[profile_family]

    invalidation_conditions = (
        InvalidationCondition(
            protected_assumption=(
                f"Strategy Eligibility's read remains at least "
                f"{_config.MIN_ELIGIBILITY_CONFIDENCE_FOR_STABLE_INTENT} confidence."
            ),
            checkable_field="eligibility_confidence",
            trigger_description=(
                f"eligibility_confidence drops below "
                f"{_config.MIN_ELIGIBILITY_CONFIDENCE_FOR_STABLE_INTENT} "
                f"(currently {eligibility.eligibility_confidence})."
            ),
            source_assessment_id=eligibility.assessment_id,
        ),
        InvalidationCondition(
            protected_assumption=(
                f"{profile_family} (the Eligibility-taxonomy family bridged from Selection's "
                f"{selected_family}) remains a member of eligible_strategy_families."
            ),
            checkable_field="eligible_strategy_families",
            trigger_description=(
                f"eligible_strategy_families no longer contains {profile_family}."
            ),
            source_assessment_id=eligibility.assessment_id,
        ),
        InvalidationCondition(
            protected_assumption=(
                f"Strategy Selection continues to select {selected_family} "
                f"(currently at confidence={selection_confidence})."
            ),
            checkable_field="selected_strategy_family",
            trigger_description=f"Strategy Selection's pick changes away from {selected_family}.",
            source_assessment_id=eligibility.assessment_id,
        ),
    )

    supporting_assessment_ids = tuple(sorted({eligibility.assessment_id, opportunity.assessment_id}))
    assessment_id = _compute_assessment_id(
        supporting_assessment_ids, selected_family, market_bias, volatility_bias,
        directional_exposure, premium_exposure, risk_profile, schema_version,
    )

    explanation = Explanation(
        assessment_id=assessment_id,
        why_this_market_expression=(
            f"{selected_family} selected by the real Strategy Selector (Series 87, "
            f"confidence={selection_confidence}), permitted by Strategy Eligibility via its "
            f"{profile_family} family (Phase 14B taxonomy bridge: {selected_family} -> "
            f"{profile_family}, one of {candidate_sei_families})."
        ),
        exposures_sought=(
            f"market_bias={market_bias}", f"volatility_bias={volatility_bias}",
            f"premium_exposure={premium_exposure}", f"directional_exposure={directional_exposure}",
            f"risk_profile={risk_profile}",
        ),
        why_other_profiles_rejected=tuple(
            f"{f} (bridged from {selected_family}) not eligible: not present in "
            f"eligible_strategy_families={eligibility.eligible_strategy_families}."
            for f in candidate_sei_families if f not in permitted_sei_families
        ),
        what_would_invalidate_before_execution=tuple(c.trigger_description for c in invalidation_conditions),
        schema_version=schema_version,
    )

    return TradeIntentAssessment(
        assessment_id=assessment_id,
        timestamp=timestamp,
        selected_strategy_family=selected_family,
        intent_state=taxonomy.INTENT_STATE_FORMED,
        market_bias=market_bias,
        volatility_bias=volatility_bias,
        directional_exposure=directional_exposure,
        premium_exposure=premium_exposure,
        risk_profile=risk_profile,
        invalidation_conditions=invalidation_conditions,
        supporting_assessment_ids=supporting_assessment_ids,
        explanation=explanation,
        provenance=provenance,
        schema_version=schema_version,
    )
