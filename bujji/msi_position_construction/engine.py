"""Position Construction Intelligence engine — Series 95.

Consumes ONLY the real outputs of Strategy Selection (Series 89),
Strategy Expression (Series 93), and Trade Thesis (Series 92) --
never re-derives any of their reasoning, never touches a real option
chain, never calls a broker, never solves an IV or computes a real
Greek (Deliverable 1: no real premium/chain data exists at this
planning layer -- that remains Series 90's job, downstream).

Deliverable 1 reuse: expiry DTE window, per-family delta targets, and
wing-width policy are the EXACT SAME constants Series 90 uses
(`bujji.msi_position_construction.config`, itself re-exporting
`bujji.msi_trade_construction.config` verbatim) -- a single shared
policy source, not two diverging ones.
"""
from __future__ import annotations

import hashlib
from typing import Optional, Tuple

from bujji.msi_strategy_expression.models import StrategyExpressionAssessment
from bujji.msi_strategy_selector.models import StrategySelectionAssessment
from bujji.msi_trade_thesis.models import TradeThesisAssessment

from . import config as _config
from . import taxonomy
from .models import Explanation, ExpiryPlan, PositionConstructionAssessment, StrikePlan, WingPlan


def _assessment_id(family: Optional[str], construction_type: str, expiry_rule: str,
                    risk_profile: str, schema_version: str) -> str:
    content = "|".join([family or "", construction_type, expiry_rule, risk_profile, schema_version])
    return hashlib.md5(content.encode("utf-8")).hexdigest()


def _decide_construction_type(family: str, conviction: str, expression: StrategyExpressionAssessment) -> Tuple[str, list]:
    """Deliverable 3/4: the ONE new decision Series 90 does not make --
    which concrete SHAPE within the family. Narrowly scoped to the two
    single-leg directional families (config.py's own disclosed scope
    boundary); every other family keeps its natural default shape."""
    default_type = _config.FAMILY_DEFAULT_CONSTRUCTION_TYPE.get(family, taxonomy.CONSTRUCTION_NONE)
    why: list = [f"{family}'s default construction shape is {default_type}"]

    if family not in _config.DIRECTIONAL_REFINEMENT_FAMILIES:
        return default_type, why

    if family == "LONG_DIRECTIONAL":
        if taxonomy.conviction_rank(conviction) < taxonomy.conviction_rank(_config.DEBIT_SPREAD_BELOW_CONVICTION):
            why.append(f"conviction={conviction} is below {_config.DEBIT_SPREAD_BELOW_CONVICTION} -- "
                       f"refined to a cheaper, lower-theta vertical debit spread rather than a naked single long option")
            return taxonomy.CONSTRUCTION_VERTICAL_DEBIT_SPREAD, why
        why.append(f"conviction={conviction} meets {_config.DEBIT_SPREAD_BELOW_CONVICTION} -- "
                   f"a naked single long option is preferred (maximum, uncapped upside for the highest-conviction case)")
        return default_type, why

    # SHORT_DIRECTIONAL: refine a naked short into a defined-risk credit
    # spread whenever the expression demands DEFINED_RISK (Series 90
    # classifies this family's default shape as UNDEFINED_RISK -- Series
    # 93/94's own real-corpus finding showed this family is frequently
    # rejected outright for exactly this reason; this refinement is a
    # real remedy, not a workaround).
    if expression.desired_risk_profile == "DEFINED_RISK":
        why.append("expression demands DEFINED_RISK and SHORT_DIRECTIONAL's naked default is UNDEFINED_RISK -- "
                   "refined to a defined-risk vertical credit spread (caps the naked leg with a further OTM long leg)")
        return taxonomy.CONSTRUCTION_VERTICAL_CREDIT_SPREAD, why
    why.append("expression does not require DEFINED_RISK -- naked single-leg premium sale is kept as-is")
    return default_type, why


def _risk_profile_for(family: str, construction_type: str) -> str:
    if construction_type in (taxonomy.CONSTRUCTION_VERTICAL_DEBIT_SPREAD, taxonomy.CONSTRUCTION_VERTICAL_CREDIT_SPREAD):
        return taxonomy.RISK_DEFINED
    from bujji.msi_trade_construction import taxonomy as tc_taxonomy
    if family in tc_taxonomy.DEFINED_RISK_FAMILIES:
        return taxonomy.RISK_DEFINED
    if family in tc_taxonomy.UNDEFINED_RISK_FAMILIES:
        return taxonomy.RISK_UNDEFINED
    return taxonomy.RISK_UNKNOWN


def _payoff_profile_for(family: str, construction_type: str) -> str:
    if construction_type == taxonomy.CONSTRUCTION_SINGLE_LEG and family == "SHORT_DIRECTIONAL":
        return taxonomy.PAYOFF_LIMITED_PROFIT_UNLIMITED_LOSS  # naked short option -- override the table's long-option default.
    return _config.PAYOFF_BY_CONSTRUCTION_TYPE.get(construction_type, taxonomy.PAYOFF_UNKNOWN)


def _expiry_plan(family: str, reasoning_prefix: str) -> ExpiryPlan:
    if family == "CALENDAR":
        return ExpiryPlan(
            rule=taxonomy.EXPIRY_RULE_CALENDAR_NEAR_FAR, min_dte=_config.DEFAULT_MIN_DTE, max_dte=_config.DEFAULT_MAX_DTE,
            reasoning=(f"{reasoning_prefix}: CALENDAR needs a near+far expiry pair, reusing Series 90's own "
                       f"CALENDAR_NEAR_FAR rule and [{_config.DEFAULT_MIN_DTE},{_config.DEFAULT_MAX_DTE}] DTE window verbatim",),
        )
    return ExpiryPlan(
        rule=taxonomy.EXPIRY_RULE_NEAREST_WEEKLY, min_dte=_config.DEFAULT_MIN_DTE, max_dte=_config.DEFAULT_MAX_DTE,
        reasoning=(f"{reasoning_prefix}: nearest weekly expiry within Series 90's own "
                   f"[{_config.DEFAULT_MIN_DTE},{_config.DEFAULT_MAX_DTE}] DTE window, reused verbatim, not re-derived",),
    )


def _strike_plan(family: str) -> StrikePlan:
    target = _config.FAMILY_DELTA_TARGETS.get(family)
    return StrikePlan(
        target_delta=target,
        reasoning=(f"target delta {target} reused verbatim from Series 90's own FAMILY_DELTA_TARGETS for {family}",)
        if target is not None else ("no delta target configured for this family",),
    )


def _wing_plan(construction_type: str, thesis: TradeThesisAssessment) -> WingPlan:
    if construction_type not in _config.WING_BEARING_CONSTRUCTION_TYPES:
        return WingPlan(plan=taxonomy.WING_PLAN_NOT_APPLICABLE, width_source=None,
                        reasoning=("this construction type has no wings",))
    if thesis.expected_move is not None:
        return WingPlan(
            plan=taxonomy.WING_PLAN_EXPECTED_MOVE_BASED, width_source="expected_move",
            reasoning=(f"wing width will be based on the day's real expected move ({thesis.expected_move}%), "
                       f"reusing Series 90's own expected-move-based wing policy, not re-derived",),
        )
    return WingPlan(
        plan=taxonomy.WING_PLAN_EXPECTED_MOVE_BASED, width_source="configured_fallback",
        reasoning=(f"expected move unavailable today -- Series 90's own configured fallback "
                   f"({_config.WING_WIDTH_FALLBACK_POINTS} pts) will apply",),
    )


def _sign_from_direction(direction: str) -> str:
    bullish = ("STRONG_BULLISH", "BULLISH", "WEAK_BULLISH")
    bearish = ("STRONG_BEARISH", "BEARISH", "WEAK_BEARISH")
    if direction in bullish:
        return taxonomy.SIGN_POSITIVE
    if direction in bearish:
        return taxonomy.SIGN_NEGATIVE
    if direction in ("NEUTRAL",):
        return taxonomy.SIGN_NEUTRAL
    return taxonomy.SIGN_UNKNOWN


def _sign_from_volatility_exposure(volatility_exposure: str) -> str:
    if volatility_exposure == "LONG_VOLATILITY":
        return taxonomy.SIGN_POSITIVE
    if volatility_exposure == "SHORT_VOLATILITY":
        return taxonomy.SIGN_NEGATIVE
    return taxonomy.SIGN_UNKNOWN


def _sign_from_time_decay(time_decay: str) -> str:
    if time_decay == "POSITIVE_THETA":
        return taxonomy.SIGN_POSITIVE
    if time_decay == "NEGATIVE_THETA":
        return taxonomy.SIGN_NEGATIVE
    return taxonomy.SIGN_UNKNOWN


def _sign_from_convexity(convexity: str) -> str:
    if convexity == "POSITIVE_CONVEXITY":
        return taxonomy.SIGN_POSITIVE
    if convexity == "NEGATIVE_CONVEXITY":
        return taxonomy.SIGN_NEGATIVE
    return taxonomy.SIGN_UNKNOWN


def construct_position(
    selection: StrategySelectionAssessment, expression: StrategyExpressionAssessment,
    thesis: TradeThesisAssessment, *, timestamp: str,
) -> PositionConstructionAssessment:
    """Produces exactly one PositionConstructionAssessment. No
    optimisation, no profitability ranking, no execution logic, no real
    strike/expiry/premium anywhere."""
    schema_version = taxonomy.MSI_POSITION_CONSTRUCTION_VERSION
    family = selection.selected_strategy_family
    supporting_ids = (selection.assessment_id, expression.assessment_id, thesis.assessment_id)

    if family is None:
        aid = _assessment_id(None, taxonomy.CONSTRUCTION_NONE, taxonomy.EXPIRY_PLAN_NONE, taxonomy.RISK_UNKNOWN, schema_version)
        explanation = Explanation(
            assessment_id=aid, why_this_construction_style=("no strategy family was selected today -- no position to construct",),
            why_this_expiry_philosophy=(), why_this_strike_philosophy=(), evidence_that_drove_the_design=(),
            schema_version=schema_version,
        )
        return PositionConstructionAssessment(
            assessment_id=aid, timestamp=timestamp, selected_strategy_family=None,
            construction_type=taxonomy.CONSTRUCTION_NONE,
            expiry_plan=ExpiryPlan(taxonomy.EXPIRY_PLAN_NONE, None, None, ()),
            strike_plan=StrikePlan(None, ()), wing_plan=WingPlan(taxonomy.WING_PLAN_NOT_APPLICABLE, None, ()),
            risk_profile=taxonomy.RISK_UNKNOWN, payoff_profile=taxonomy.PAYOFF_UNKNOWN,
            adjustment_readiness=taxonomy.ADJUSTMENT_NOT_APPLICABLE,
            expected_delta=taxonomy.SIGN_UNKNOWN, expected_gamma=taxonomy.SIGN_UNKNOWN,
            expected_theta=taxonomy.SIGN_UNKNOWN, expected_vega=taxonomy.SIGN_UNKNOWN,
            supporting_assessment_ids=supporting_ids, explanation=explanation,
            provenance="bujji.msi_position_construction.engine.construct_position", schema_version=schema_version,
        )

    construction_type, why_style = _decide_construction_type(family, thesis.conviction, expression)
    risk_profile = _risk_profile_for(family, construction_type)
    payoff_profile = _payoff_profile_for(family, construction_type)
    adjustment_readiness = _config.ADJUSTMENT_READINESS_BY_CONSTRUCTION_TYPE.get(construction_type, taxonomy.ADJUSTMENT_NOT_APPLICABLE)

    expiry_plan = _expiry_plan(family, f"family={family}")
    strike_plan = _strike_plan(family)
    wing_plan = _wing_plan(construction_type, thesis)

    # Delta sign: families with an inherently delta-neutral shape (short/
    # long strangle, straddle, iron condor/fly, butterfly, calendar) are
    # always SIGN_NEUTRAL regardless of the day's directional lean --
    # inherently directional families (LONG_DIRECTIONAL/SHORT_DIRECTIONAL/
    # RATIO/COVERED/SYNTHETIC) take the sign of the real thesis lean. For
    # SHORT_DIRECTIONAL specifically, Series 90's own `_build_legs` logic
    # sells the OPPOSITE option type from the lean (bullish -> sell PUT),
    # which reproduces the SAME sign as the lean itself (a short put is
    # positive-delta) -- no special-casing needed, one rule covers both.
    _DELTA_NEUTRAL_FAMILIES = (
        "NEUTRAL_PREMIUM_SELLING", "NEUTRAL_PREMIUM_BUYING", "VOLATILITY_EXPANSION",
        "VOLATILITY_COMPRESSION", "IRON_CONDOR", "IRON_FLY", "BUTTERFLY", "CALENDAR",
    )
    if family in _DELTA_NEUTRAL_FAMILIES:
        expected_delta = taxonomy.SIGN_NEUTRAL
    else:
        expected_delta = _sign_from_direction(thesis.directional_expectation)

    expected_gamma = _sign_from_convexity(expression.desired_convexity)
    expected_theta = _sign_from_time_decay(expression.desired_time_decay)
    expected_vega = _sign_from_volatility_exposure(expression.desired_volatility_exposure)

    aid = _assessment_id(family, construction_type, expiry_plan.rule, risk_profile, schema_version)

    why_expiry = list(expiry_plan.reasoning)
    why_strike = list(strike_plan.reasoning)
    evidence = [
        f"thesis={thesis.thesis_type} (conviction={thesis.conviction})",
        f"expression: direction={expression.desired_direction}, volatility={expression.desired_volatility_exposure}, "
        f"risk={expression.desired_risk_profile}, theta={expression.desired_time_decay}, convexity={expression.desired_convexity}",
        f"selected family={family}",
    ]

    explanation = Explanation(
        assessment_id=aid, why_this_construction_style=tuple(why_style), why_this_expiry_philosophy=tuple(why_expiry),
        why_this_strike_philosophy=tuple(why_strike), evidence_that_drove_the_design=tuple(evidence),
        schema_version=schema_version,
    )

    return PositionConstructionAssessment(
        assessment_id=aid, timestamp=timestamp, selected_strategy_family=family, construction_type=construction_type,
        expiry_plan=expiry_plan, strike_plan=strike_plan, wing_plan=wing_plan, risk_profile=risk_profile,
        payoff_profile=payoff_profile, adjustment_readiness=adjustment_readiness,
        expected_delta=expected_delta, expected_gamma=expected_gamma, expected_theta=expected_theta,
        expected_vega=expected_vega, supporting_assessment_ids=supporting_ids, explanation=explanation,
        provenance="bujji.msi_position_construction.engine.construct_position", schema_version=schema_version,
    )
