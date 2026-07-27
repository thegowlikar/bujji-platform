"""bujji.msi_strategy_optimization.engine — Series 108.

Consumes FROZEN, unmodified modules only:
  - `bujji.msi_trade_construction.engine._build_strike_evidence` /
    `_candidates_for_type` (Series 90's own real per-strike IV/delta
    evidence builder -- reused directly rather than re-solving IV a
    second time with a second implementation).
  - `bujji.intelligence.volatility_brain.compute_expected_move`,
    `bujji.intelligence.greeks_brain._bs_gamma`/`_bs_theta` (real
    Black-Scholes greeks, same functions Series 88/90 already reuse).
  - `bujji.msi_trade_construction.config` (FAMILY_DELTA_TARGETS,
    WING_WIDTH_* policy, DEFAULT_RISK_FREE_RATE) -- reused BY IDENTITY,
    never re-declared with a second, possibly-diverging value.
  - `bujji.msi_position_lifecycle.taxonomy` (ALL_POSITION_STATES) --
    this package reads a real, already-computed `PositionLifecycleAssessment`,
    never re-derives lifecycle state itself.

This module adds ZERO new market-data parsing, ZERO new IV/Greeks
formulas, and never modifies MSI, Strategy Selection, Position
Construction, Portfolio Construction, Margin Bridge, Lifecycle, or
Execution Planning -- it only reads their real outputs and the real
option chain, and produces optimisation *guidance* (strike/expiry/roll/
adjustment/conversion assessments), which nothing downstream is wired
to consume automatically (no frozen call site is edited).
"""
from __future__ import annotations

import hashlib
import math
from datetime import datetime
from typing import Any, Optional, Sequence, Tuple

from bujji.core.enums import OptionType
from bujji.intelligence.volatility_brain import compute_expected_move
from bujji.intelligence.greeks_brain import _bs_gamma, _bs_theta

from bujji.msi_trade_construction import config as tc_config
from bujji.msi_trade_construction.engine import _build_strike_evidence, _candidates_for_type

from . import config as _config
from . import taxonomy
from .models import (
    Explanation, StrategyOptimizationAssessment, StrikeCandidate, StrikeOptimizationAssessment,
    ExpiryCandidate, ExpiryOptimizationAssessment, RollAssessment, AdjustmentPlanAssessment,
    ConversionAssessment,
)

_R = tc_config.DEFAULT_RISK_FREE_RATE


def _explain(prefix: str, seed: str, why, evidence, dominant) -> Explanation:
    aid = hashlib.md5(f"{prefix}|{seed}|{taxonomy.MSI_STRATEGY_OPTIMIZATION_VERSION}".encode()).hexdigest()
    return Explanation(
        assessment_id=aid, why=tuple(why), evidence_used=tuple(evidence),
        dominant_constraints=tuple(dominant), schema_version=taxonomy.MSI_STRATEGY_OPTIMIZATION_VERSION,
    )


# ---------------------------------------------------------------------------
# Deliverable 3 -- per-family optimisation objectives
# ---------------------------------------------------------------------------
def optimize_strategy(strategy_family: str, *, timestamp: str) -> StrategyOptimizationAssessment:
    objectives = taxonomy.FAMILY_OBJECTIVES.get(strategy_family, ())
    target_delta = tc_config.FAMILY_DELTA_TARGETS.get(strategy_family)
    explanation = _explain(
        "STRATEGY_OPT", f"{strategy_family}|{timestamp}",
        why=[f"family={strategy_family}: objectives={objectives or taxonomy.UNKNOWN}",
             (f"target_delta={target_delta} (Series 90's own FAMILY_DELTA_TARGETS, reused by identity)"
              if target_delta is not None else "no target_delta declared for this family")],
        evidence=[f"FAMILY_OBJECTIVES[{strategy_family}]", f"tc_config.FAMILY_DELTA_TARGETS[{strategy_family}]"],
        dominant=["family_objective_lookup"],
    )
    aid = hashlib.md5(f"SOA|{strategy_family}|{objectives}|{target_delta}|{timestamp}".encode()).hexdigest()
    return StrategyOptimizationAssessment(
        assessment_id=aid, strategy_family=strategy_family, objectives=objectives,
        target_delta=target_delta, timestamp=timestamp, explanation=explanation,
        provenance=_config.DEFAULT_PROVENANCE, schema_version=_config.SCHEMA_VERSION,
    )


# ---------------------------------------------------------------------------
# Deliverable 4 -- Strike Optimiser
# ---------------------------------------------------------------------------
def optimize_strikes(
    strategy_family: str, chain: Sequence, spot: float, expiry: str, t_years: float,
    target_delta: Optional[float], *, timestamp: str, r: float = _R,
) -> StrikeOptimizationAssessment:
    """Real per-strike IV/delta evidence (reused from Series 90's own
    builder), never a hard-coded strike. Fails closed (UNKNOWN/None) when
    the real chain lacks resolvable evidence -- never guesses a strike."""
    if target_delta is None or spot is None:
        explanation = _explain(
            "STRIKE_OPT", f"{strategy_family}|{timestamp}",
            why=["no target_delta or spot available -- cannot optimise strikes"],
            evidence=[], dominant=["missing_target_delta_or_spot"],
        )
        aid = hashlib.md5(f"SKO|{strategy_family}|NONE|{timestamp}".encode()).hexdigest()
        return StrikeOptimizationAssessment(
            assessment_id=aid, strategy_family=strategy_family, short_strike=None, long_strike=None,
            wing_width=None, strike_spacing=None, skew_adjustment=taxonomy.UNKNOWN,
            delta_targets=(), achieved_deltas=(), candidates_considered=(), timestamp=timestamp,
            explanation=explanation, provenance=_config.DEFAULT_PROVENANCE, schema_version=_config.SCHEMA_VERSION,
        )

    evidence = _build_strike_evidence(chain, expiry, spot, t_years, r=r)
    ce_candidates = _candidates_for_type(evidence, "CE")
    pe_candidates = _candidates_for_type(evidence, "PE")

    def _closest_to_delta(candidates, wanted_abs_delta, sign):
        best, best_diff = None, None
        for c in candidates:
            d = c.delta
            if d is None or (sign > 0 and d < 0) or (sign < 0 and d > 0):
                continue
            diff = abs(abs(d) - wanted_abs_delta)
            if best_diff is None or diff < best_diff:
                best, best_diff = c, diff
        return best

    short_ce = _closest_to_delta(ce_candidates, target_delta, +1)
    short_pe = _closest_to_delta(pe_candidates, target_delta, -1)

    is_defined_risk = strategy_family in taxonomy.FAMILY_OBJECTIVES and strategy_family in (
        "IRON_CONDOR", "IRON_FLY", "BUTTERFLY", "CALENDAR", "LONG_DIRECTIONAL",
        "NEUTRAL_PREMIUM_BUYING", "VOLATILITY_EXPANSION",
    )  # matches tc_taxonomy.DEFINED_RISK_FAMILIES by value, reused via that module in query.py

    # Wing width: reuse Series 90's OWN policy (expected-move-based,
    # falling back to a fixed point width) -- never a second, diverging
    # wing-width rule.
    wing_width = None
    if is_defined_risk:
        expected_move = None
        atm_strike = min((c.strike for c in ce_candidates), key=lambda s: abs(s - spot)) if ce_candidates else None
        atm = evidence.get((atm_strike, "CE")) if atm_strike is not None else None
        if atm is not None and atm.iv is not None:
            expected_move = compute_expected_move(spot, atm.iv, t_years)
        wing_width = (
            expected_move * tc_config.WING_WIDTH_EXPECTED_MOVE_MULTIPLIER
            if expected_move is not None else tc_config.WING_WIDTH_FALLBACK_POINTS
        )

    short_strike = short_ce.strike if short_ce is not None else (short_pe.strike if short_pe is not None else None)
    long_strike = None
    if is_defined_risk and short_strike is not None and wing_width is not None:
        long_strike = short_strike + wing_width  # away from spot; sign convention matches Series 90's own butterfly/condor legs

    strike_spacing = None
    if ce_candidates and len(ce_candidates) > 1:
        strikes_sorted = sorted({c.strike for c in ce_candidates})
        spacings = [b - a for a, b in zip(strikes_sorted, strikes_sorted[1:])]
        strike_spacing = min(spacings) if spacings else None

    # Skew (Deliverable 1: "skew-aware positioning") -- compare solved
    # IV at the CE vs PE short strikes, real evidence only.
    skew_adjustment = taxonomy.UNKNOWN
    if short_ce is not None and short_pe is not None and short_ce.iv is not None and short_pe.iv is not None:
        diff = short_ce.iv - short_pe.iv
        if abs(diff) < _config.SKEW_IV_DIFFERENCE_THRESHOLD:
            skew_adjustment = "NEUTRAL"
        elif diff > 0:
            skew_adjustment = "CALL_SKEW"
        else:
            skew_adjustment = "PUT_SKEW"

    candidates_considered = tuple(
        StrikeCandidate(strike=c.strike, option_type=c.option_type, delta=c.delta, implied_volatility=c.iv)
        for c in (ce_candidates + pe_candidates)
    )
    delta_targets = (("SHORT_CE", target_delta), ("SHORT_PE", -target_delta))
    achieved_deltas = (
        ("SHORT_CE", short_ce.delta if short_ce else None),
        ("SHORT_PE", short_pe.delta if short_pe else None),
    )

    why = [f"target_delta={target_delta}"]
    if short_ce is not None:
        why.append(f"short_ce strike={short_ce.strike} achieved_delta={short_ce.delta:.4f}" if short_ce.delta is not None else f"short_ce strike={short_ce.strike}")
    if short_pe is not None:
        why.append(f"short_pe strike={short_pe.strike} achieved_delta={short_pe.delta:.4f}" if short_pe.delta is not None else f"short_pe strike={short_pe.strike}")
    if is_defined_risk:
        why.append(f"wing_width={wing_width} ({'expected-move-derived' if expected_move is not None else 'fallback fixed points -- no resolvable ATM IV this day'})")
    why.append(f"skew_adjustment={skew_adjustment} (SKEW_IV_DIFFERENCE_THRESHOLD={_config.SKEW_IV_DIFFERENCE_THRESHOLD})")

    explanation = _explain(
        "STRIKE_OPT", f"{strategy_family}|{short_strike}|{long_strike}|{timestamp}",
        why=why,
        evidence=[f"real chain, expiry={expiry}", "Series 90's _build_strike_evidence (real IV/delta solve)"],
        dominant=["delta_target", "wing_width_policy" if is_defined_risk else "single_sided"],
    )
    aid = hashlib.md5(f"SKO|{strategy_family}|{short_strike}|{long_strike}|{timestamp}".encode()).hexdigest()
    return StrikeOptimizationAssessment(
        assessment_id=aid, strategy_family=strategy_family, short_strike=short_strike, long_strike=long_strike,
        wing_width=wing_width, strike_spacing=strike_spacing, skew_adjustment=skew_adjustment,
        delta_targets=delta_targets, achieved_deltas=achieved_deltas,
        candidates_considered=candidates_considered, timestamp=timestamp, explanation=explanation,
        provenance=_config.DEFAULT_PROVENANCE, schema_version=_config.SCHEMA_VERSION,
    )


# ---------------------------------------------------------------------------
# Deliverable 5 -- Expiry Optimiser
# ---------------------------------------------------------------------------
def _dte(as_of_date: str, expiry: str) -> int:
    return (datetime.fromisoformat(expiry).date() - datetime.fromisoformat(as_of_date).date()).days


def _bucket_for_dte(dte: int) -> str:
    if dte < _config.NEXT_WEEKLY_MIN_DTE:
        return taxonomy.EXPIRY_WEEKLY
    if dte < _config.MONTHLY_MIN_DTE:
        return taxonomy.EXPIRY_NEXT_WEEKLY
    if dte < _config.FAR_MONTHLY_MIN_DTE:
        return taxonomy.EXPIRY_MONTHLY
    return taxonomy.EXPIRY_FAR_MONTHLY


def optimize_expiry(chain: Sequence, spot: Optional[float], as_of_date: str, *, timestamp: str, r: float = _R) -> ExpiryOptimizationAssessment:
    """Deliverable 5: evaluates every REAL expiry present in today's
    chain (never a synthetic/assumed expiry), buckets each by DTE, and
    picks the dominant one by real theta/gamma trade-off. Event calendar
    is explicitly reported as unavailable -- this codebase has no real
    event-calendar source anywhere (confirmed by the Deliverable 1
    audit), never fabricated."""
    real_expiries = sorted({row.expiry for row in chain if row.expiry is not None}) if spot is not None else []
    candidates = []
    per_bucket_best = {}
    for expiry in real_expiries:
        dte = _dte(as_of_date, expiry)
        if dte <= 0:
            continue
        bucket = _bucket_for_dte(dte)
        t_years = dte / 365.0
        strikes = sorted({row.strike for row in chain if row.strike is not None and row.expiry == expiry})
        if not strikes:
            candidates.append(ExpiryCandidate(bucket=bucket, expiry=expiry, dte=dte, theta_estimate=None,
                                              gamma_estimate=None, expected_move=None,
                                              why_considered_or_rejected="rejected: no real strikes for this expiry"))
            continue
        atm_strike = min(strikes, key=lambda s: abs(s - spot))
        evidence = _build_strike_evidence(chain, expiry, spot, t_years, r=r)
        atm_ce = evidence.get((atm_strike, "CE"))
        if atm_ce is None or atm_ce.iv is None:
            candidates.append(ExpiryCandidate(bucket=bucket, expiry=expiry, dte=dte, theta_estimate=None,
                                              gamma_estimate=None, expected_move=None,
                                              why_considered_or_rejected="rejected: ATM IV not resolvable from real premiums this day"))
            continue
        theta = _bs_theta(spot, atm_strike, t_years, r, atm_ce.iv, OptionType.CE)
        gamma = _bs_gamma(spot, atm_strike, t_years, r, atm_ce.iv)
        expected_move = compute_expected_move(spot, atm_ce.iv, t_years)
        cand = ExpiryCandidate(
            bucket=bucket, expiry=expiry, dte=dte, theta_estimate=theta, gamma_estimate=gamma,
            expected_move=expected_move, why_considered_or_rejected=(
                f"real ATM(strike={atm_strike}) theta={theta:.4f} gamma={gamma:.6f} expected_move={expected_move:.2f}"
            ),
        )
        candidates.append(cand)
        if bucket not in per_bucket_best or bucket not in per_bucket_best:
            per_bucket_best.setdefault(bucket, cand)

    # Dominant expiry: among candidates with a resolvable theta/gamma
    # reading AND within EXPIRY_DOMINANCE_MAX_DTE (real, practically-
    # tradable expiries only -- see config.py), the one with the greatest
    # absolute real theta -- directly reflecting MAXIMIZE_THETA
    # (Deliverable 3), with gamma exposure already bounded by the DTE
    # cap itself (gamma is monotonically higher for near-term expiries,
    # so this is the real, disclosed theta/gamma trade-off point, not a
    # ratio that degenerates toward the most distant real expiry).
    resolvable = [c for c in candidates if c.theta_estimate is not None and c.gamma_estimate not in (None, 0.0)]
    practical = [c for c in resolvable if c.dte is not None and c.dte <= _config.EXPIRY_DOMINANCE_MAX_DTE]
    dominant = max(practical, key=lambda c: abs(c.theta_estimate)) if practical else None

    why = [f"{len(real_expiries)} real expiries present in today's chain, {len(resolvable)} with resolvable "
           f"theta/gamma, {len(practical)} within EXPIRY_DOMINANCE_MAX_DTE={_config.EXPIRY_DOMINANCE_MAX_DTE}"]
    if dominant is not None:
        why.append(f"dominant={dominant.expiry} (bucket={dominant.bucket}): {dominant.why_considered_or_rejected}, "
                   f"chosen by max(|theta|) among practical (DTE<={_config.EXPIRY_DOMINANCE_MAX_DTE}) candidates")
    elif resolvable:
        why.append(f"all {len(resolvable)} resolvable candidates exceed EXPIRY_DOMINANCE_MAX_DTE="
                   f"{_config.EXPIRY_DOMINANCE_MAX_DTE} -- dominant_expiry is honestly UNKNOWN rather than picking "
                   "an impractically far-dated real expiry")
    else:
        why.append("no candidate had resolvable real theta/gamma -- dominant_expiry is honestly UNKNOWN, not guessed")
    why.append("event_calendar: UNAVAILABLE -- no real event-calendar source exists anywhere in this codebase (Deliverable 1 audit)")

    explanation = _explain(
        "EXPIRY_OPT", f"{as_of_date}|{[c.expiry for c in candidates]}|{timestamp}",
        why=why, evidence=[f"real chain expiries: {real_expiries}"], dominant=["theta_gamma_tradeoff"],
    )
    aid = hashlib.md5(f"EXO|{as_of_date}|{dominant.expiry if dominant else None}|{timestamp}".encode()).hexdigest()
    return ExpiryOptimizationAssessment(
        assessment_id=aid, dominant_expiry=(dominant.expiry if dominant else None),
        dominant_bucket=(dominant.bucket if dominant else None), candidates=tuple(candidates),
        event_calendar_available=False, timestamp=timestamp, explanation=explanation,
        provenance=_config.DEFAULT_PROVENANCE, schema_version=_config.SCHEMA_VERSION,
    )


# ---------------------------------------------------------------------------
# Deliverable 6 -- Rolling Intelligence. FIVE independent decisions,
# never collapsed. Consumes a real, already-computed
# `PositionLifecycleAssessment` (Series 96, frozen) -- never re-derives
# lifecycle state itself.
# ---------------------------------------------------------------------------
def assess_roll(
    lifecycle, *, short_strike_delta: Optional[float], dte: Optional[int], timestamp: str,
) -> RollAssessment:
    thesis_compatible = lifecycle.thesis_invalidation.compatible
    state = lifecycle.position_state

    exit_ = state in ("EXIT_CANDIDATE", "THESIS_BROKEN")
    exit_reasoning = ((f"lifecycle.position_state={state}",) if exit_ else ())

    roll_whole = (not exit_) and state == "ADJUSTMENT_CANDIDATE" and not thesis_compatible
    roll_whole_reasoning = ((f"position_state=ADJUSTMENT_CANDIDATE and thesis no longer compatible "
                            f"(entry thesis invalidated) -- the STRUCTURE itself, not just its strikes/expiry, "
                            f"no longer fits current evidence",) if roll_whole else ())

    tested = short_strike_delta is not None and abs(short_strike_delta) >= _config.TESTED_STRIKE_DELTA_THRESHOLD
    roll_strike = (not exit_) and (not roll_whole) and tested and thesis_compatible
    roll_strike_reasoning = ((f"short strike delta={short_strike_delta:.4f} >= "
                              f"TESTED_STRIKE_DELTA_THRESHOLD={_config.TESTED_STRIKE_DELTA_THRESHOLD}, thesis "
                              f"still compatible -- the strike is tested but the underlying thesis is not broken",)
                             if roll_strike else ())

    dte_low = dte is not None and dte <= _config.ROLL_EXPIRY_DTE_THRESHOLD
    roll_expiry = (not exit_) and (not roll_whole) and dte_low and thesis_compatible
    roll_expiry_reasoning = ((f"dte={dte} <= ROLL_EXPIRY_DTE_THRESHOLD={_config.ROLL_EXPIRY_DTE_THRESHOLD} "
                              f"(style-specific convention, see docs), thesis still compatible",) if roll_expiry else ())

    hold = not (exit_ or roll_whole or roll_strike or roll_expiry) and state in ("HEALTHY", "IMPROVING", "NEWLY_OPENED", "PROFIT_HARVEST")
    hold_reasoning = ((f"position_state={state}, none of exit/roll_whole/roll_strike/roll_expiry triggered",) if hold else ())

    # Priority-resolved single label for readability only -- the five
    # booleans above remain the real, independent signals (Deliverable
    # 6's own explicit instruction: "never collapse them").
    recommended_action = taxonomy.HOLD
    for action, flag in ((taxonomy.EXIT, exit_), (taxonomy.ROLL_WHOLE_STRATEGY, roll_whole),
                         (taxonomy.ROLL_EXPIRY, roll_expiry), (taxonomy.ROLL_STRIKE, roll_strike)):
        if flag:
            recommended_action = action
            break

    explanation = _explain(
        "ROLL", f"{lifecycle.lifecycle_id}|{timestamp}",
        why=[f"exit={exit_}", f"roll_whole_strategy={roll_whole}", f"roll_strike={roll_strike}",
             f"roll_expiry={roll_expiry}", f"hold={hold}", f"recommended_action={recommended_action}"],
        evidence=[f"lifecycle_id={lifecycle.lifecycle_id}", f"position_state={state}",
                 f"thesis_compatible={thesis_compatible}", f"short_strike_delta={short_strike_delta}", f"dte={dte}"],
        dominant=["lifecycle_state", "tested_strike_threshold", "dte_threshold"],
    )
    aid = hashlib.md5(f"ROLL|{lifecycle.lifecycle_id}|{recommended_action}|{timestamp}".encode()).hexdigest()
    return RollAssessment(
        assessment_id=aid, roll_strike=roll_strike, roll_strike_reasoning=roll_strike_reasoning,
        roll_expiry=roll_expiry, roll_expiry_reasoning=roll_expiry_reasoning,
        roll_whole_strategy=roll_whole, roll_whole_strategy_reasoning=roll_whole_reasoning,
        hold=hold, hold_reasoning=hold_reasoning, exit=exit_, exit_reasoning=exit_reasoning,
        recommended_action=recommended_action, timestamp=timestamp, explanation=explanation,
        provenance=_config.DEFAULT_PROVENANCE, schema_version=_config.SCHEMA_VERSION,
    )


# ---------------------------------------------------------------------------
# Deliverable 7 -- Adjustment Planner
# ---------------------------------------------------------------------------
def plan_adjustment(roll: RollAssessment, lifecycle, *, timestamp: str) -> AdjustmentPlanAssessment:
    """Maps the real Roll Assessment + real lifecycle fired-triggers
    into ONE of the 8 spec-named actions, every recommendation citing
    the specific real evidence that drove it."""
    fired = lifecycle.adjustment_policy.fired if hasattr(lifecycle, "adjustment_policy") else ()

    if roll.exit:
        action, evidence = taxonomy.ADJUSTMENT_CLOSE_COMPLETE, (f"roll.exit=True: {roll.exit_reasoning}",)
    elif roll.roll_whole_strategy:
        action, evidence = taxonomy.ADJUSTMENT_CONVERT_STRATEGY, (f"roll.roll_whole_strategy=True: {roll.roll_whole_strategy_reasoning}",)
    elif fired:
        # Series 96's own hard exposure-breach triggers, reused by
        # identity -- widen/narrow/reduce-delta framed off the SAME
        # real fired-trigger evidence, never re-derived.
        action, evidence = taxonomy.ADJUSTMENT_REDUCE_DELTA, (f"lifecycle.adjustment_policy.fired={fired}",)
    elif roll.roll_strike or roll.roll_expiry:
        action, evidence = taxonomy.ADJUSTMENT_WIDEN_WINGS, (
            f"roll_strike={roll.roll_strike} roll_expiry={roll.roll_expiry}: strike/expiry pressure without a "
            "hard exposure breach -- widening room before a full roll is warranted",
        )
    elif lifecycle.position_state == "PROFIT_HARVEST":
        action, evidence = taxonomy.ADJUSTMENT_CLOSE_PARTIAL, ("lifecycle.position_state=PROFIT_HARVEST",)
    else:
        action, evidence = taxonomy.ADJUSTMENT_NONE, ("no roll signal, no fired trigger, position healthy",)

    explanation = _explain(
        "ADJ", f"{lifecycle.lifecycle_id}|{action}|{timestamp}",
        why=[f"action={action}"], evidence=list(evidence), dominant=["roll_assessment", "lifecycle_fired_triggers"],
    )
    aid = hashlib.md5(f"ADJ|{lifecycle.lifecycle_id}|{action}|{timestamp}".encode()).hexdigest()
    return AdjustmentPlanAssessment(
        assessment_id=aid, action=action, evidence_cited=evidence, timestamp=timestamp,
        explanation=explanation, provenance=_config.DEFAULT_PROVENANCE, schema_version=_config.SCHEMA_VERSION,
    )


# ---------------------------------------------------------------------------
# Deliverable 8 -- Dynamic Strategy Conversion
# ---------------------------------------------------------------------------
def evaluate_conversion(
    strategy_family: str, roll: RollAssessment, *, timestamp: str,
) -> ConversionAssessment:
    """Only fires on `taxonomy.CONVERSION_RULES`'s explicit, disclosed
    conditions -- never explores/optimises for novelty (Deliverable 8's
    own explicit instruction)."""
    if not roll.roll_whole_strategy:
        explanation = _explain(
            "CONV", f"{strategy_family}|NONE|{timestamp}",
            why=["roll.roll_whole_strategy=False -- conversion is only evaluated when the whole-strategy roll signal fires"],
            evidence=[f"roll_whole_strategy={roll.roll_whole_strategy}"], dominant=["roll_whole_strategy_gate"],
        )
        aid = hashlib.md5(f"CONV|{strategy_family}|NONE|{timestamp}".encode()).hexdigest()
        return ConversionAssessment(
            assessment_id=aid, recommended=False, from_family=strategy_family, to_family=None,
            reasoning=("roll_whole_strategy not triggered",), timestamp=timestamp, explanation=explanation,
            provenance=_config.DEFAULT_PROVENANCE, schema_version=_config.SCHEMA_VERSION,
        )

    applicable = [rule for rule in taxonomy.CONVERSION_RULES if rule["from_family"] == strategy_family]
    if not applicable:
        why = [f"no CONVERSION_RULES entry exists for from_family={strategy_family} -- honestly reporting no "
               "representable conversion rather than inventing one"]
        to_family, recommended, reasoning = None, False, ("no applicable conversion rule",)
    else:
        rule = applicable[0]
        to_family, recommended = rule["to_family"], True
        reasoning = (rule["condition"],)
        why = [f"{strategy_family} -> {to_family}: {rule['condition']}"]

    explanation = _explain(
        "CONV", f"{strategy_family}|{to_family}|{timestamp}", why=why,
        evidence=[f"roll.roll_whole_strategy_reasoning={roll.roll_whole_strategy_reasoning}"],
        dominant=["conversion_rule_table"],
    )
    aid = hashlib.md5(f"CONV|{strategy_family}|{to_family}|{timestamp}".encode()).hexdigest()
    return ConversionAssessment(
        assessment_id=aid, recommended=recommended, from_family=strategy_family, to_family=to_family,
        reasoning=reasoning, timestamp=timestamp, explanation=explanation,
        provenance=_config.DEFAULT_PROVENANCE, schema_version=_config.SCHEMA_VERSION,
    )
