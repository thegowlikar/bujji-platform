"""bujji.msi_dynamic_management.engine — Series 109.

Consumes, read-only, never modifies:
  - `bujji.msi_position_lifecycle` (Series 96, frozen) -- real
    `PositionLifecycleAssessment` (position_state, thesis_invalidation,
    adjustment_policy.fired) and its own real WATCH_ABS_PORTFOLIO_DELTA/
    VEGA thresholds, reused BY IDENTITY.
  - `bujji.msi_strategy_optimization` (Series 108, frozen) -- real
    `RollAssessment` (five independent signals) and `ConversionAssessment`,
    and `TESTED_STRIKE_DELTA_THRESHOLD`/`ROLL_EXPIRY_DTE_THRESHOLD`,
    reused BY IDENTITY as the RECOMMENDED-priority boundary for the
    corresponding decision type here.
  - `bujji.msi_volatility_structure` (Series 88, frozen) -- real
    `volatility_regime` taxonomy, for wing-adjustment evidence.

This module adds ZERO new market-data parsing and ZERO new IV/Greeks
formulas -- it only classifies existing, real evidence into six
INDEPENDENT decision assessments plus a priority level for each,
computed from that evidence (never a fixed lookup table).
"""
from __future__ import annotations

import hashlib
from typing import Any, Optional, Tuple

from bujji.msi_position_lifecycle import config as pli_config
from bujji.msi_strategy_optimization import config as mso_config
from bujji.msi_strategy_optimization import taxonomy as mso_taxonomy
from bujji.msi_strategy_optimization import engine as mso_engine

from . import config as _config
from . import taxonomy
from .models import Explanation, DecisionAssessment, TransitionAssessment, DynamicManagementBoard


def _explain(prefix: str, seed: str, *, why, why_now, why_not_later, why_not_another_roll, why_not_exit, evidence) -> Explanation:
    aid = hashlib.md5(f"{prefix}|{seed}|{taxonomy.MSI_DYNAMIC_MANAGEMENT_VERSION}".encode()).hexdigest()
    return Explanation(
        assessment_id=aid, why=tuple(why), why_now=tuple(why_now), why_not_later=tuple(why_not_later),
        why_not_another_roll=tuple(why_not_another_roll), why_not_exit=tuple(why_not_exit),
        evidence_used=tuple(evidence), schema_version=taxonomy.MSI_DYNAMIC_MANAGEMENT_VERSION,
    )


def _decision(decision_type: str, recommended: bool, priority: str, priority_evidence, lifecycle_id: str,
             timestamp: str, explanation: Explanation) -> DecisionAssessment:
    aid = hashlib.md5(f"{decision_type}|{lifecycle_id}|{priority}|{recommended}|{timestamp}".encode()).hexdigest()
    return DecisionAssessment(
        assessment_id=aid, decision_type=decision_type, recommended=recommended, priority=priority,
        priority_evidence=tuple(priority_evidence), lifecycle_id=lifecycle_id, timestamp=timestamp,
        explanation=explanation, provenance=_config.DEFAULT_PROVENANCE, schema_version=_config.SCHEMA_VERSION,
    )


def _thesis_broken(lifecycle) -> bool:
    return lifecycle.position_state in ("THESIS_BROKEN", "EXIT_CANDIDATE") or not lifecycle.thesis_invalidation.compatible


# ---------------------------------------------------------------------------
# Deliverable 3.1 -- Strike Roll
# ---------------------------------------------------------------------------
def assess_strike_roll(lifecycle, short_strike_delta: Optional[float], *, timestamp: str) -> DecisionAssessment:
    broken = _thesis_broken(lifecycle)
    threshold = mso_config.TESTED_STRIKE_DELTA_THRESHOLD
    mandatory_line = _config.STRIKE_ROLL_MANDATORY_DELTA
    optional_line = threshold - _config.STRIKE_ROLL_OPTIONAL_BAND

    if short_strike_delta is None:
        priority, recommended = taxonomy.PRIORITY_AVOID, False
        priority_evidence = ("short_strike_delta is unavailable -- cannot evaluate, fails closed to AVOID",)
    elif broken:
        priority, recommended = taxonomy.PRIORITY_AVOID, False
        priority_evidence = (f"thesis_compatible={lifecycle.thesis_invalidation.compatible}, "
                             f"position_state={lifecycle.position_state} -- rolling a strike on a broken thesis "
                             "just re-tests the same failed idea; exit is the correct decision type, not this one",)
    else:
        d = abs(short_strike_delta)
        if d >= mandatory_line:
            priority, recommended = taxonomy.PRIORITY_MANDATORY, True
            priority_evidence = (f"|delta|={d:.4f} >= STRIKE_ROLL_MANDATORY_DELTA={mandatory_line} "
                                 "(short leg now more likely ITM than OTM)",)
        elif d >= threshold:
            priority, recommended = taxonomy.PRIORITY_RECOMMENDED, True
            priority_evidence = (f"|delta|={d:.4f} >= TESTED_STRIKE_DELTA_THRESHOLD={threshold} "
                                 "(Series 108, reused by identity)",)
        elif d >= optional_line:
            priority, recommended = taxonomy.PRIORITY_OPTIONAL, False
            priority_evidence = (f"|delta|={d:.4f} within STRIKE_ROLL_OPTIONAL_BAND="
                                 f"{_config.STRIKE_ROLL_OPTIONAL_BAND} below the tested threshold -- approaching, not yet tested",)
        else:
            priority, recommended = taxonomy.PRIORITY_AVOID, False
            priority_evidence = (f"|delta|={d:.4f} well within a safe range -- no roll evidence",)

    explanation = _explain(
        "STRIKE_ROLL", f"{lifecycle.lifecycle_id}|{priority}|{timestamp}",
        why=[f"priority={priority}"] + list(priority_evidence),
        why_now=[priority_evidence[0]] if recommended else ["not applicable -- priority is not MANDATORY/RECOMMENDED"],
        why_not_later=(["a MANDATORY/RECOMMENDED strike breach compounds gamma risk the longer it is left untouched"]
                       if priority in (taxonomy.PRIORITY_MANDATORY, taxonomy.PRIORITY_RECOMMENDED)
                       else ["priority is not yet at a level where timing pressure applies"]),
        why_not_another_roll=["evaluated independently of expiry_roll/delta_rebalance/wing_adjustment -- see their own assessments"],
        why_not_exit=(["thesis remains compatible; the STRIKE, not the thesis, is under pressure"] if not broken
                      else ["thesis is broken -- see full_exit assessment instead, this decision type is AVOID"]),
        evidence=[f"lifecycle_id={lifecycle.lifecycle_id}", f"short_strike_delta={short_strike_delta}",
                 f"thesis_compatible={lifecycle.thesis_invalidation.compatible}"],
    )
    return _decision(taxonomy.DECISION_STRIKE_ROLL, recommended, priority, priority_evidence,
                     lifecycle.lifecycle_id, timestamp, explanation)


# ---------------------------------------------------------------------------
# Deliverable 3.2 -- Expiry Roll
# ---------------------------------------------------------------------------
def assess_expiry_roll(lifecycle, dte: Optional[int], *, timestamp: str) -> DecisionAssessment:
    broken = _thesis_broken(lifecycle)
    threshold = mso_config.ROLL_EXPIRY_DTE_THRESHOLD
    mandatory_line = _config.EXPIRY_ROLL_MANDATORY_DTE
    optional_line = _config.EXPIRY_ROLL_OPTIONAL_DTE

    if dte is None:
        priority, recommended = taxonomy.PRIORITY_AVOID, False
        priority_evidence = ("dte is unavailable -- cannot evaluate, fails closed to AVOID",)
    elif broken:
        priority, recommended = taxonomy.PRIORITY_AVOID, False
        priority_evidence = (f"thesis_compatible={lifecycle.thesis_invalidation.compatible} -- rolling expiry on "
                             "a broken thesis only extends a position that should be closed",)
    elif dte <= mandatory_line:
        priority, recommended = taxonomy.PRIORITY_MANDATORY, True
        priority_evidence = (f"dte={dte} <= EXPIRY_ROLL_MANDATORY_DTE={mandatory_line} "
                             "(near-universally cited gamma/pin-risk acceleration zone)",)
    elif dte <= threshold:
        priority, recommended = taxonomy.PRIORITY_RECOMMENDED, True
        priority_evidence = (f"dte={dte} <= ROLL_EXPIRY_DTE_THRESHOLD={threshold} (Series 108, style-specific, reused by identity)",)
    elif dte <= optional_line:
        priority, recommended = taxonomy.PRIORITY_OPTIONAL, False
        priority_evidence = (f"dte={dte} <= EXPIRY_ROLL_OPTIONAL_DTE={optional_line} -- worth watching, not yet actionable",)
    else:
        priority, recommended = taxonomy.PRIORITY_AVOID, False
        priority_evidence = (f"dte={dte} well outside any roll window",)

    explanation = _explain(
        "EXPIRY_ROLL", f"{lifecycle.lifecycle_id}|{priority}|{timestamp}",
        why=[f"priority={priority}"] + list(priority_evidence),
        why_now=[priority_evidence[0]] if recommended else ["not applicable"],
        why_not_later=(["gamma/pin risk only accelerates as DTE continues to fall"]
                       if priority in (taxonomy.PRIORITY_MANDATORY, taxonomy.PRIORITY_RECOMMENDED)
                       else ["sufficient time remains -- no timing pressure yet"]),
        why_not_another_roll=["evaluated independently of strike_roll/delta_rebalance/wing_adjustment -- see their own assessments"],
        why_not_exit=(["thesis remains compatible; TIME, not the thesis, is under pressure"] if not broken
                      else ["thesis is broken -- see full_exit assessment instead"]),
        evidence=[f"lifecycle_id={lifecycle.lifecycle_id}", f"dte={dte}", f"thesis_compatible={lifecycle.thesis_invalidation.compatible}"],
    )
    return _decision(taxonomy.DECISION_EXPIRY_ROLL, recommended, priority, priority_evidence,
                     lifecycle.lifecycle_id, timestamp, explanation)


# ---------------------------------------------------------------------------
# Deliverable 3.3 -- Delta Rebalance (reuses Series 91/96's own real
# portfolio-Greeks watch thresholds by identity, never a new number)
# ---------------------------------------------------------------------------
def assess_delta_rebalance(lifecycle, portfolio_delta_after: Optional[float], portfolio_vega_after: Optional[float], *, timestamp: str) -> DecisionAssessment:
    fired = lifecycle.adjustment_policy.fired
    watch_delta, watch_vega = pli_config.WATCH_ABS_PORTFOLIO_DELTA, pli_config.WATCH_ABS_PORTFOLIO_VEGA

    if fired:
        priority, recommended = taxonomy.PRIORITY_MANDATORY, True
        priority_evidence = (f"lifecycle.adjustment_policy.fired={fired} (Series 96's own real hard exposure-breach triggers)",)
    elif portfolio_delta_after is not None and abs(portfolio_delta_after) >= watch_delta:
        priority, recommended = taxonomy.PRIORITY_RECOMMENDED, True
        priority_evidence = (f"|portfolio_delta_after|={abs(portfolio_delta_after):.2f} >= WATCH_ABS_PORTFOLIO_DELTA={watch_delta} (Series 96, reused by identity)",)
    elif portfolio_vega_after is not None and abs(portfolio_vega_after) >= watch_vega:
        priority, recommended = taxonomy.PRIORITY_RECOMMENDED, True
        priority_evidence = (f"|portfolio_vega_after|={abs(portfolio_vega_after):.2f} >= WATCH_ABS_PORTFOLIO_VEGA={watch_vega} (Series 96, reused by identity)",)
    else:
        priority, recommended = taxonomy.PRIORITY_AVOID, False
        priority_evidence = ("no fired hard trigger, no watch-threshold breach -- portfolio Greeks within tolerance",)

    explanation = _explain(
        "DELTA_REBALANCE", f"{lifecycle.lifecycle_id}|{priority}|{timestamp}",
        why=[f"priority={priority}"] + list(priority_evidence),
        why_now=[priority_evidence[0]] if recommended else ["not applicable"],
        why_not_later=(["a fired hard trigger or watch-threshold breach compounds with every additional day of exposure"]
                       if recommended else ["no exposure pressure currently exists"]),
        why_not_another_roll=["this decision concerns PORTFOLIO exposure, independent of this position's own strike/expiry -- see those assessments separately"],
        why_not_exit=["delta/vega rebalancing addresses portfolio exposure, not this position's own thesis -- exit is evaluated separately"],
        evidence=[f"lifecycle_id={lifecycle.lifecycle_id}", f"fired={fired}",
                 f"portfolio_delta_after={portfolio_delta_after}", f"portfolio_vega_after={portfolio_vega_after}"],
    )
    return _decision(taxonomy.DECISION_DELTA_REBALANCE, recommended, priority, priority_evidence,
                     lifecycle.lifecycle_id, timestamp, explanation)


# ---------------------------------------------------------------------------
# Deliverable 3.4 -- Wing Adjustment (IV expansion/contraction -- reuses
# VSB's own real volatility_regime taxonomy, Series 88, frozen)
# ---------------------------------------------------------------------------
def assess_wing_adjustment(lifecycle, entry_volatility_regime: Optional[str], current_volatility_regime: Optional[str], *, timestamp: str) -> DecisionAssessment:
    broken = _thesis_broken(lifecycle)
    if entry_volatility_regime is None or current_volatility_regime is None:
        priority, recommended = taxonomy.PRIORITY_AVOID, False
        priority_evidence = ("entry or current volatility_regime is unavailable -- cannot evaluate, fails closed to AVOID",)
    elif broken:
        priority, recommended = taxonomy.PRIORITY_AVOID, False
        priority_evidence = ("thesis is broken -- wing width is moot if the position is exiting",)
    elif entry_volatility_regime != current_volatility_regime:
        priority, recommended = taxonomy.PRIORITY_RECOMMENDED, True
        priority_evidence = (f"volatility_regime changed: entry={entry_volatility_regime} -> current={current_volatility_regime} "
                             "(Series 88's own real regime taxonomy, reused by identity)",)
    else:
        priority, recommended = taxonomy.PRIORITY_AVOID, False
        priority_evidence = (f"volatility_regime unchanged since entry ({current_volatility_regime}) -- no evidence for a wing adjustment",)

    explanation = _explain(
        "WING_ADJ", f"{lifecycle.lifecycle_id}|{priority}|{timestamp}",
        why=[f"priority={priority}"] + list(priority_evidence),
        why_now=[priority_evidence[0]] if recommended else ["not applicable"],
        why_not_later=(["a regime shift already priced into the market compounds the longer wings stay at entry width"]
                       if recommended else ["no regime shift has occurred -- entry wings remain evidence-appropriate"]),
        why_not_another_roll=["evaluated independently of strike_roll/expiry_roll/delta_rebalance -- see their own assessments"],
        why_not_exit=["a regime shift changes wing SIZING, not whether the thesis itself survives"],
        evidence=[f"lifecycle_id={lifecycle.lifecycle_id}", f"entry_regime={entry_volatility_regime}", f"current_regime={current_volatility_regime}"],
    )
    return _decision(taxonomy.DECISION_WING_ADJUSTMENT, recommended, priority, priority_evidence,
                     lifecycle.lifecycle_id, timestamp, explanation)


# ---------------------------------------------------------------------------
# Deliverable 3.5 -- Strategy Conversion (Deliverable 5's transition
# engine folded in here as one of the six independent decision types)
# ---------------------------------------------------------------------------
def _transition_rule_for(from_family: str):
    for rule in taxonomy.TRANSITION_RULES:
        if rule["from_family"] == from_family:
            return rule
    for rule in mso_taxonomy.CONVERSION_RULES:  # Series 108, frozen, reused by identity
        if rule["from_family"] == from_family:
            return rule
    return None


def assess_strategy_transition(from_family: str, *, timestamp: str) -> TransitionAssessment:
    """Deliverable 5. Only recommends a transition representable in
    `ALL_STRATEGY_FAMILIES` -- if the requested/considered transition is
    not representable, this reports that explicitly rather than
    approximating it (verified: `test_transition_...not_representable`)."""
    rule = _transition_rule_for(from_family)
    representable = rule is not None
    to_family = rule["to_family"] if rule else None
    reasoning = (rule["condition"],) if rule else (f"no representable transition rule exists for from_family={from_family} in "
                                                    "the current frozen taxonomy -- honestly reporting this rather than inventing one",)
    explanation = _explain(
        "TRANSITION", f"{from_family}|{to_family}|{timestamp}",
        why=[f"from_family={from_family} representable={representable} to_family={to_family}"] + list(reasoning),
        why_now=list(reasoning) if representable else ["not applicable -- no representable transition"],
        why_not_later=["a representable transition's underlying condition (term-structure edge closing, tail-risk "
                       "tolerance tightening, thesis stalling) only strengthens the longer it persists unaddressed"]
                      if representable else ["not applicable"],
        why_not_another_roll=["strategy conversion changes the STRUCTURE itself -- evaluated independently of "
                              "strike/expiry/delta/wing adjustments, which operate within the existing structure"],
        why_not_exit=["a representable, evidenced transition preserves capital already committed to the thesis; "
                      "exit is reserved for when the thesis itself has failed, not merely when the expression should change"],
        evidence=[f"TRANSITION_RULES + msi_strategy_optimization.CONVERSION_RULES lookup for from_family={from_family}"],
    )
    aid = hashlib.md5(f"TRANSITION|{from_family}|{to_family}|{timestamp}".encode()).hexdigest()
    return TransitionAssessment(
        assessment_id=aid, from_family=from_family, representable=representable, to_family=to_family,
        reasoning=reasoning, timestamp=timestamp, explanation=explanation,
        provenance=_config.DEFAULT_PROVENANCE, schema_version=_config.SCHEMA_VERSION,
    )


def assess_strategy_conversion(lifecycle, from_family: str, roll108, *, timestamp: str) -> DecisionAssessment:
    transition = assess_strategy_transition(from_family, timestamp=timestamp)
    if not roll108.roll_whole_strategy:
        priority, recommended = taxonomy.PRIORITY_AVOID, False
        priority_evidence = ("roll.roll_whole_strategy=False (Series 108) -- no structural-mismatch evidence exists",)
    elif not transition.representable:
        priority, recommended = taxonomy.PRIORITY_OPTIONAL, False
        priority_evidence = (f"roll_whole_strategy=True but no representable transition exists for {from_family} -- "
                             "documented as a missing capability, not silently ignored",)
    else:
        priority, recommended = taxonomy.PRIORITY_RECOMMENDED, True
        priority_evidence = (f"roll_whole_strategy=True and a representable transition exists: "
                             f"{from_family} -> {transition.to_family}",)

    explanation = _explain(
        "STRAT_CONV", f"{lifecycle.lifecycle_id}|{priority}|{timestamp}",
        why=[f"priority={priority}"] + list(priority_evidence),
        why_now=[priority_evidence[0]] if recommended else ["not applicable"],
        why_not_later=(["the structural mismatch that triggered this only compounds while the current shape is held"]
                       if recommended else ["no structural-mismatch evidence currently exists"]),
        why_not_another_roll=["strategy conversion is evaluated independently of strike/expiry/delta/wing decisions"],
        why_not_exit=["a representable conversion is preferred over exit when the thesis itself is not broken -- see full_exit for that distinct evaluation"],
        evidence=[f"lifecycle_id={lifecycle.lifecycle_id}", f"roll_whole_strategy={roll108.roll_whole_strategy}",
                 f"transition_representable={transition.representable}", f"to_family={transition.to_family}"],
    )
    return _decision(taxonomy.DECISION_STRATEGY_CONVERSION, recommended, priority, priority_evidence,
                     lifecycle.lifecycle_id, timestamp, explanation)


# ---------------------------------------------------------------------------
# Deliverable 3.6 -- Full Exit
# ---------------------------------------------------------------------------
def assess_full_exit(lifecycle, *, timestamp: str) -> DecisionAssessment:
    fired = lifecycle.adjustment_policy.fired
    if lifecycle.position_state == "THESIS_BROKEN" and fired:
        priority, recommended = taxonomy.PRIORITY_MANDATORY, True
        priority_evidence = (f"position_state=THESIS_BROKEN AND adjustment_policy.fired={fired} "
                             "(thesis failure compounded by a hard exposure breach)",)
    elif lifecycle.position_state in ("THESIS_BROKEN", "EXIT_CANDIDATE"):
        priority, recommended = taxonomy.PRIORITY_RECOMMENDED, True
        priority_evidence = (f"position_state={lifecycle.position_state}",)
    elif lifecycle.position_state == "PROFIT_HARVEST":
        priority, recommended = taxonomy.PRIORITY_OPTIONAL, False
        priority_evidence = ("position_state=PROFIT_HARVEST -- a partial/complete profit-take is worth considering, not mandatory",)
    else:
        priority, recommended = taxonomy.PRIORITY_AVOID, False
        priority_evidence = (f"position_state={lifecycle.position_state}, thesis_compatible={lifecycle.thesis_invalidation.compatible} -- no exit evidence",)

    explanation = _explain(
        "FULL_EXIT", f"{lifecycle.lifecycle_id}|{priority}|{timestamp}",
        why=[f"priority={priority}"] + list(priority_evidence),
        why_now=[priority_evidence[0]] if recommended else ["not applicable"],
        why_not_later=(["a broken thesis or hard exposure breach does not improve by waiting -- capital stays at risk against a failed premise"]
                       if recommended else ["no exit evidence currently exists"]),
        why_not_another_roll=["exit is evaluated independently and takes precedence in the priority board when it fires at MANDATORY"],
        why_not_exit=(["N/A -- this IS the exit assessment"] if recommended
                      else ["thesis remains compatible and no hard exposure breach exists -- the position is still supported by evidence"]),
        evidence=[f"lifecycle_id={lifecycle.lifecycle_id}", f"position_state={lifecycle.position_state}",
                 f"thesis_compatible={lifecycle.thesis_invalidation.compatible}", f"fired={fired}"],
    )
    return _decision(taxonomy.DECISION_FULL_EXIT, recommended, priority, priority_evidence,
                     lifecycle.lifecycle_id, timestamp, explanation)


# ---------------------------------------------------------------------------
# Board assembly -- bundles the six independent assessments + transition
# for one position on one real day. Never collapses them into one decision.
# ---------------------------------------------------------------------------
def build_dynamic_management_board(
    lifecycle, from_family: str, *, short_strike_delta: Optional[float], dte: Optional[int],
    portfolio_delta_after: Optional[float], portfolio_vega_after: Optional[float],
    entry_volatility_regime: Optional[str], current_volatility_regime: Optional[str],
    roll108, timestamp: str,
) -> DynamicManagementBoard:
    return DynamicManagementBoard(
        lifecycle_id=lifecycle.lifecycle_id, timestamp=timestamp,
        strike_roll=assess_strike_roll(lifecycle, short_strike_delta, timestamp=timestamp),
        expiry_roll=assess_expiry_roll(lifecycle, dte, timestamp=timestamp),
        delta_rebalance=assess_delta_rebalance(lifecycle, portfolio_delta_after, portfolio_vega_after, timestamp=timestamp),
        wing_adjustment=assess_wing_adjustment(lifecycle, entry_volatility_regime, current_volatility_regime, timestamp=timestamp),
        strategy_conversion=assess_strategy_conversion(lifecycle, from_family, roll108, timestamp=timestamp),
        full_exit=assess_full_exit(lifecycle, timestamp=timestamp),
        transition=assess_strategy_transition(from_family, timestamp=timestamp),
    )
