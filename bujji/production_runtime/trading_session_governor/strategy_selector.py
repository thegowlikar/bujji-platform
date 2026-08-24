"""Regime-Based Strategy Selector -- BUJJI Options OS v3, Gate V1.1
Component 2.

PURPOSE: a pure lookup table, not an intelligence engine. Consumes
already-normalized regime labels from the EXISTING `market_regime_
adapter.py` (Gate D.5) -- ALL_TREND_REGIMES / ALL_VOLATILITY_REGIMES --
never recomputes a regime reading of its own. Chooses at most one
strategy family, always from Gate B's own already-approved, already-
tested defined-risk formula set -- never invents a new strategy shape.

STEP 1 FINDING THAT SHAPES THIS TABLE: Bujji's mandate is options
SELLING. Of Gate B's 7 defined-risk-APPROVED families
(msi_trade_construction.taxonomy.DEFINED_RISK_FAMILIES), only
IRON_CONDOR and IRON_FLY are genuine net-credit/premium-selling
shapes -- CALENDAR/BUTTERFLY/LONG_DIRECTIONAL/NEUTRAL_PREMIUM_BUYING/
VOLATILITY_EXPANSION are net-debit/buying shapes, and the real naked-
selling families (SHORT_DIRECTIONAL, NEUTRAL_PREMIUM_SELLING) are
Gate B's own PERMANENT VETOES for unbounded risk. This selector's
candidate universe is therefore honestly just {IRON_CONDOR, IRON_FLY}
-- never forcing a buying shape into a selling mandate, and never
reaching for a vetoed naked-selling family.

Fails closed on any missing/unrecognized input -- NO_TRADE, never a
guessed strategy.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional, Tuple

from bujji.trading_brain.risk_governor.market_regime_adapter import (
    TREND_SIDEWAYS, TREND_TRENDING_DOWN, TREND_TRENDING_UP, TREND_UNKNOWN,
    VOL_CONTRACTION, VOL_EXPANSION, VOL_HIGH, VOL_LOW, VOL_UNKNOWN,
)

Clock = Callable[[], datetime]

# The only two Gate-B-approved, genuinely premium-SELLING defined-risk
# families -- see module docstring. Never extended without a
# corresponding Gate B formula already existing and already approved.
# Every shape this selector may name. Widened 2026-08-19 with the three-part
# regime rewrite; IRON_CONDOR/IRON_FLY remain constructible families but are
# no longer reachable from this table, so they are not in the universe it can
# select. Kept as a single declared tuple so "what may Bujji sell?" has one
# answer rather than being inferred from branch bodies.
SELLING_UNIVERSE = (
    "VOLATILITY_COMPRESSION",    # short straddle  -- sideways, high vol
    "NEUTRAL_PREMIUM_SELLING",   # short strangle  -- sideways, thin premium
    "BULL_PUT_SPREAD",           # credit spread   -- up-trend
    "BEAR_CALL_SPREAD",          # credit spread   -- down-trend
)

NO_TRADE = "NO_TRADE"


@dataclass(frozen=True)
class StrategySelectionResult:
    selected_strategy: Optional[str]   # None means NO_TRADE
    trend_regime: str
    volatility_regime: str
    reasoning: str
    confidence: str                     # "HIGH" | "NONE" -- deterministic, from data completeness only, never a fabricated probability
    evaluated_at: datetime
    # Every declared shape with the reason it was or was not available for
    # this regime. Defaulted so that every existing construction site --
    # including SessionGovernor rebuilding a locked decision -- stays valid;
    # an empty tuple therefore means "not evaluated", never "none eligible".
    candidates: Tuple["StrategyCandidate", ...] = ()


# Deterministic mapping table -- the ENTIRE decision surface of this
# module. Every branch is documented; nothing outside this table
# influences the outcome.
# ---------------------------------------------------------------------------
# THREE-PART REGIME SELECTION (operator directive, 2026-08-19)
# ---------------------------------------------------------------------------
# The market does one of three things, and a volatility seller should have a
# sellable shape for each:
#
#   UP        -> BULL_PUT_SPREAD    credit spread leaning with the trend
#   DOWN      -> BEAR_CALL_SPREAD   its mirror
#   SIDEWAYS  -> SHORT_STRADDLE or SHORT_STRANGLE, by volatility
#
# WHAT THIS REPLACED, and why. The previous table had exactly two tradeable
# outcomes -- IRON_CONDOR and IRON_FLY, both neutral -- so a TRENDING market
# always resolved to no-trade. Its own reasoning said a directional regime had
# "no Gate-B-approved defined-risk SELLING strategy", which was true only
# because no directional credit spread existed in the construction engine.
# Two were added (bujji/msi_trade_construction), so the refusal no longer
# describes reality and the trending third of the market becomes tradeable.
#
# STRADDLE vs STRANGLE is a judgement, so it is CONFIG, not a hardcoded rule:
#   HIGH_VOL              -> straddle. Premium at the money is rich enough to
#                            pay for sitting on the gamma.
#   LOW_VOL / CONTRACTION -> strangle. Premium is thin, so distance from the
#                            money is worth more than the extra credit.
# Invert SIDEWAYS_SHAPE_BY_VOLATILITY to trade the opposite opinion; plenty of
# sellers argue it the other way, and neither is a fact about the market.
#
# RISK POSTURE CHANGED HERE, deliberately and with operator approval on
# 2026-08-19. Every previously selectable family (IRON_CONDOR, IRON_FLY) was
# structurally DEFINED-risk. A short straddle and a short strangle carry naked
# short legs with UNBOUNDED loss -- taxonomy.UNDEFINED_RISK_FAMILIES says so.
# The two credit spreads remain defined-risk. What still protects the book:
# the real SPAN margin veto at Gate B, the capital check, portfolio limits,
# the daily loss limit, the emergency brake and the mandatory 15:15 exit.
# What no longer protects it: the SHAPE. On a gap, a naked strangle's loss is
# bounded by the exit policy firing, not by the position's own structure.
#
# UNCHANGED, on purpose: every no-trade path. Missing or unknown regime,
# volatility expansion, and any unmapped combination still fail closed. This
# widened what Bujji can sell; it did not widen when Bujji is willing to sell.

# The family names below MUST exist in
# msi_trade_construction.taxonomy.SUPPORTED_FAMILIES -- a selector that picks
# a shape the engine cannot build produces a guaranteed rejection instead of a
# trade. tests/test_three_part_strategy_selection.py asserts the linkage.
FAMILY_SHORT_STRADDLE = "VOLATILITY_COMPRESSION"   # sell CE+PE, both ATM
FAMILY_SHORT_STRANGLE = "NEUTRAL_PREMIUM_SELLING"  # sell CE+PE, delta-targeted
FAMILY_BULL_PUT_SPREAD = "BULL_PUT_SPREAD"
FAMILY_BEAR_CALL_SPREAD = "BEAR_CALL_SPREAD"

SIDEWAYS_SHAPE_BY_VOLATILITY = {
    VOL_HIGH: FAMILY_SHORT_STRADDLE,
    VOL_LOW: FAMILY_SHORT_STRANGLE,
    VOL_CONTRACTION: FAMILY_SHORT_STRANGLE,
}

# ---------------------------------------------------------------------------
# DEFINED-RISK MODE (operator decision, 2026-08-20)
# ---------------------------------------------------------------------------
# Measured over 168,194 real 5-minute NIFTY bars (2017-2026): the stop-loss,
# the daily loss limit and the emergency brake are all evaluated on ONE
# 300-second management heartbeat, and the worst single 5-minute bar in nine
# years ranged 611.8 points. Against the real ATM straddle credit measured on
# 2026-08-19 (240.95 pts, 1 lot), that is ~Rs 39,764 of adverse move inside a
# single unchecked interval -- 2.5x the stop and 1.6x the entire daily loss
# limit. Bars of 200+ points occur ~5 times a year; 300+ about once.
#
# A naked short strangle has no structural floor: its loss is bounded only by
# a control that runs every five minutes. A wing is a floor that holds
# regardless of how slowly the loop runs -- a structural guarantee rather
# than a procedural one, and only the structural kind survives a gap.
#
# So for REAL MONEY the sideways branch takes the defined-risk twin. The
# substitution is unusually clean because the twins share the naked shapes'
# short strikes exactly (FAMILY_DELTA_TARGETS: NEUTRAL_PREMIUM_SELLING and
# IRON_CONDOR are both 0.20; VOLATILITY_COMPRESSION and IRON_FLY are both
# ATM/0.50). The trade thesis is unchanged -- same view, same strikes, wings
# added. It costs some premium and nothing else.
#
# The trending branches need no twin: BULL_PUT_SPREAD and BEAR_CALL_SPREAD
# already pair every short leg with a protective long.
DEFINED_RISK_TWIN = {
    FAMILY_SHORT_STRADDLE: "IRON_FLY",       # same ATM shorts, plus wings
    FAMILY_SHORT_STRANGLE: "IRON_CONDOR",    # same 0.20-delta shorts, plus wings
}




# ---------------------------------------------------------------------------
# DECLARATIVE STRATEGY RULES (Priority A, 2026-08-23)
# ---------------------------------------------------------------------------
# WHY THIS IS NOT A NEW REGISTRY. Two strategy registries already exist in
# this repository:
#   bujji/trading_brain/strategy_selector/registry.py  -- 11 StrategyDefinitions
#   bujji/msi_strategy_selector/engine.py              -- its own family model
# A reachability closure from the real trading entrypoint
# (bujji_options_os_runner.py) proves NEITHER is reachable from the trade
# DECISION: trading_brain.strategy_selector.registry is not on the import
# closure at all, and msi_strategy_selector is reached only by observation
# paths (intelligence_cycle_recorder, live_shadow_validation) that record what
# a selector WOULD say and never place anything. The one authority deciding
# what Bujji sells is select_strategy() in THIS module.
#
# So the fix for "selection is a conditional table returning a bare string" is
# NOT a third registry -- that would be the duplicate the architecture
# forbids. It is to make THIS authority declarative: every family Bujji may
# sell declares its own eligibility, refusal conditions, required data
# capabilities, risk shape and downstream obligations here, and the decision
# is checked against those declarations rather than merely described by them.
#
# THE TABLE BELOW DOES NOT DECIDE ANYTHING YET, AND THAT IS DELIBERATE.
# The branch bodies of select_strategy() are unchanged and still produce the
# outcome; the rules produce an INDEPENDENT candidate record for the same
# inputs. tests/test_strategy_rules_match_decision.py asserts the two agree on
# every reachable regime pair in both risk modes. If a later edit changes one
# and not the other, that test fails -- which is the entire point. Rewriting
# the branches to be DRIVEN by the table is a behaviour-preserving refactor
# that can only be proven safe once the equivalence test exists, so it is a
# later commit, not this one.

CANDIDATE_ELIGIBLE = "ELIGIBLE"
CANDIDATE_REJECTED = "REJECTED"

# Reason codes. Every refusal Bujji can make about a shape has exactly one
# code, so "why was nothing traded today?" is answerable from the record
# rather than by reading prose.
REASON_REGIME_UNKNOWN = "REGIME_UNKNOWN"
REASON_VOL_EXPANSION = "VOL_EXPANSION_VETO"
REASON_TREND_MISMATCH = "TREND_NOT_ELIGIBLE"
REASON_VOL_MISMATCH = "VOL_NOT_ELIGIBLE"
REASON_NAKED_IN_DEFINED_RISK = "NAKED_SHAPE_SUPPRESSED_BY_DEFINED_RISK_MODE"
REASON_DEFINED_RISK_TWIN_ONLY = "REACHABLE_ONLY_AS_DEFINED_RISK_TWIN"
REASON_CAPABILITY_MISSING = "REQUIRED_DATA_CAPABILITY_UNPROVEN"
REASON_ELIGIBLE = "ELIGIBLE"

# Generic data capabilities. Deliberately NOT FYERS field names and NOT
# thresholds -- no live payload has been measured yet (Gate 1, Monday). They
# name what a decision NEEDS, so that when Monday proves which of them the
# feed can actually satisfy, the mapping is a config change and not a rewrite.
# CAP_LAST_PRICE is required by every shape because a premium seller that
# cannot price its own legs cannot value, stop or exit them.
CAP_LAST_PRICE = "QUOTE_LAST_PRICE"
CAP_TWO_SIDED = "QUOTE_TWO_SIDED_MARKET"
CAP_OPEN_INTEREST = "QUOTE_OPEN_INTEREST"


@dataclass(frozen=True)
class StrategyRule:
    """One declaration per shape Bujji may sell. Pure metadata: no branch in
    this class body, no threshold, no broker call."""
    family: str
    shape: str
    eligible_trends: Tuple[str, ...]
    eligible_volatilities: Tuple[str, ...]
    # Conditions refusing the shape regardless of the eligibility above.
    vetoing_volatilities: Tuple[str, ...]
    structurally_defined_risk: bool
    hedged: bool
    # Required capabilities are ASPIRATIONAL until Gate 1. They are enforced
    # only when a capability policy is supplied -- see _evaluate_candidates.
    # Unconfigured means unenforced, never "satisfied": an empty policy does
    # not assert the feed is capable of anything.
    required_capabilities: Tuple[str, ...]
    # Downstream obligations this shape imposes. Declared here so the
    # obligation is visible at selection time; each is ENFORCED by its own
    # named owner, recorded so a reader can go and check that it runs.
    requires_margin_check_by: str
    requires_lot_size_check_by: str
    exit_policy_owner: str
    eod_behaviour: str
    # Set only on a shape reachable when defined_risk_only substitutes it.
    twin_of: Optional[str] = None


_COMMON_CAPS = (CAP_LAST_PRICE,)
_MARGIN_OWNER = "bujji.trading_brain.risk_governor.capital_safety_governor"
_LOT_OWNER = "bujji.instruments.lot_size_for"
_EXIT_OWNER = "bujji.production_runtime.exit_lifecycle"
_EOD = "mandatory flatten at the configured market-close cutoff; no overnight exposure"

STRATEGY_RULES: Tuple[StrategyRule, ...] = (
    StrategyRule(
        family=FAMILY_SHORT_STRADDLE, shape="short straddle (ATM both legs)",
        eligible_trends=(TREND_SIDEWAYS,), eligible_volatilities=(VOL_HIGH,),
        vetoing_volatilities=(VOL_EXPANSION,),
        structurally_defined_risk=False, hedged=False,
        required_capabilities=_COMMON_CAPS,
        requires_margin_check_by=_MARGIN_OWNER, requires_lot_size_check_by=_LOT_OWNER,
        exit_policy_owner=_EXIT_OWNER, eod_behaviour=_EOD,
    ),
    StrategyRule(
        family=FAMILY_SHORT_STRANGLE, shape="short strangle (delta-targeted both legs)",
        eligible_trends=(TREND_SIDEWAYS,), eligible_volatilities=(VOL_LOW, VOL_CONTRACTION),
        vetoing_volatilities=(VOL_EXPANSION,),
        structurally_defined_risk=False, hedged=False,
        required_capabilities=_COMMON_CAPS,
        requires_margin_check_by=_MARGIN_OWNER, requires_lot_size_check_by=_LOT_OWNER,
        exit_policy_owner=_EXIT_OWNER, eod_behaviour=_EOD,
    ),
    StrategyRule(
        family=FAMILY_BULL_PUT_SPREAD, shape="bull put spread (sell put, buy lower put)",
        eligible_trends=(TREND_TRENDING_UP,),
        eligible_volatilities=(VOL_HIGH, VOL_LOW, VOL_CONTRACTION),
        vetoing_volatilities=(VOL_EXPANSION,),
        structurally_defined_risk=True, hedged=True,
        required_capabilities=_COMMON_CAPS,
        requires_margin_check_by=_MARGIN_OWNER, requires_lot_size_check_by=_LOT_OWNER,
        exit_policy_owner=_EXIT_OWNER, eod_behaviour=_EOD,
    ),
    StrategyRule(
        family=FAMILY_BEAR_CALL_SPREAD, shape="bear call spread (sell call, buy higher call)",
        eligible_trends=(TREND_TRENDING_DOWN,),
        eligible_volatilities=(VOL_HIGH, VOL_LOW, VOL_CONTRACTION),
        vetoing_volatilities=(VOL_EXPANSION,),
        structurally_defined_risk=True, hedged=True,
        required_capabilities=_COMMON_CAPS,
        requires_margin_check_by=_MARGIN_OWNER, requires_lot_size_check_by=_LOT_OWNER,
        exit_policy_owner=_EXIT_OWNER, eod_behaviour=_EOD,
    ),
    StrategyRule(
        family="IRON_FLY", shape="iron fly (ATM shorts plus wings)",
        eligible_trends=(TREND_SIDEWAYS,), eligible_volatilities=(VOL_HIGH,),
        vetoing_volatilities=(VOL_EXPANSION,),
        structurally_defined_risk=True, hedged=True,
        required_capabilities=_COMMON_CAPS,
        requires_margin_check_by=_MARGIN_OWNER, requires_lot_size_check_by=_LOT_OWNER,
        exit_policy_owner=_EXIT_OWNER, eod_behaviour=_EOD,
        twin_of=FAMILY_SHORT_STRADDLE,
    ),
    StrategyRule(
        family="IRON_CONDOR", shape="iron condor (0.20-delta shorts plus wings)",
        eligible_trends=(TREND_SIDEWAYS,), eligible_volatilities=(VOL_LOW, VOL_CONTRACTION),
        vetoing_volatilities=(VOL_EXPANSION,),
        structurally_defined_risk=True, hedged=True,
        required_capabilities=_COMMON_CAPS,
        requires_margin_check_by=_MARGIN_OWNER, requires_lot_size_check_by=_LOT_OWNER,
        exit_policy_owner=_EXIT_OWNER, eod_behaviour=_EOD,
        twin_of=FAMILY_SHORT_STRANGLE,
    ),
)

RULES_BY_FAMILY = {r.family: r for r in STRATEGY_RULES}


@dataclass(frozen=True)
class StrategyCandidate:
    """Why one shape was or was not available for THIS regime. Produced for
    every declared rule on every evaluation -- a rejected candidate is
    evidence, not silence."""
    family: str
    status: str          # CANDIDATE_ELIGIBLE | CANDIDATE_REJECTED
    reason_code: str
    detail: str


def _evaluate_candidates(trend_regime, volatility_regime, defined_risk_only,
                         available_capabilities=None):
    """Score every declared rule against one regime pair. Pure: no clock, no
    IO, and no ordering opinion -- ranking is the caller's job, and today at
    most one family is eligible by construction.

    `available_capabilities` is None whenever no capability policy is
    configured, which is the state until Gate 1 measures the live payload.
    None means the requirement is NOT EVALUATED -- it does not mean satisfied,
    and nothing here records that the feed is capable. Once a policy exists,
    passing a set makes a missing capability a refusal."""
    out = []
    for rule in STRATEGY_RULES:
        # 1. An unknown regime refuses everything, before any per-shape opinion.
        if (trend_regime is None or volatility_regime is None
                or trend_regime == TREND_UNKNOWN or volatility_regime == VOL_UNKNOWN):
            out.append(StrategyCandidate(
                rule.family, CANDIDATE_REJECTED, REASON_REGIME_UNKNOWN,
                "regime unavailable or unrecognized; an unknown market is not a neutral one"))
            continue
        # 2. Per-shape veto, checked BEFORE eligibility -- a veto must not be
        #    reachable around by an otherwise-eligible shape.
        if volatility_regime in rule.vetoing_volatilities:
            out.append(StrategyCandidate(
                rule.family, CANDIDATE_REJECTED, REASON_VOL_EXPANSION,
                f"{volatility_regime} vetoes this shape regardless of trend"))
            continue
        # 3. Availability under the active risk mode.
        if defined_risk_only and not rule.structurally_defined_risk:
            out.append(StrategyCandidate(
                rule.family, CANDIDATE_REJECTED, REASON_NAKED_IN_DEFINED_RISK,
                "defined-risk mode is active and this shape has no structural floor"))
            continue
        if not defined_risk_only and rule.twin_of is not None:
            out.append(StrategyCandidate(
                rule.family, CANDIDATE_REJECTED, REASON_DEFINED_RISK_TWIN_ONLY,
                f"only reachable as the defined-risk twin of {rule.twin_of}"))
            continue
        # 4. Regime eligibility.
        if trend_regime not in rule.eligible_trends:
            out.append(StrategyCandidate(
                rule.family, CANDIDATE_REJECTED, REASON_TREND_MISMATCH,
                f"declared for {list(rule.eligible_trends)}, not {trend_regime}"))
            continue
        if volatility_regime not in rule.eligible_volatilities:
            out.append(StrategyCandidate(
                rule.family, CANDIDATE_REJECTED, REASON_VOL_MISMATCH,
                f"declared for {list(rule.eligible_volatilities)}, not {volatility_regime}"))
            continue
        # 5. Capability policy -- unconfigured means unevaluated, not passed.
        if available_capabilities is not None:
            missing = [c for c in rule.required_capabilities
                       if c not in available_capabilities]
            if missing:
                out.append(StrategyCandidate(
                    rule.family, CANDIDATE_REJECTED, REASON_CAPABILITY_MISSING,
                    f"required data capabilities not proven available: {missing}"))
                continue
        out.append(StrategyCandidate(
            rule.family, CANDIDATE_ELIGIBLE, REASON_ELIGIBLE,
            f"{rule.shape}: eligible for ({trend_regime}, {volatility_regime})"))
    return tuple(out)


def eligible_families(trend_regime, volatility_regime, *, defined_risk_only=False,
                      available_capabilities=None):
    """The families the DECLARATIONS say are available for this regime. The
    equivalence test compares this against what select_strategy() actually
    returns; nothing in the runtime decides from it yet."""
    return tuple(c.family for c in _evaluate_candidates(
        trend_regime, volatility_regime, defined_risk_only, available_capabilities)
        if c.status == CANDIDATE_ELIGIBLE)


def select_strategy(trend_regime: Optional[str], volatility_regime: Optional[str],
                    clock: Clock, *, defined_risk_only: bool = False) -> StrategySelectionResult:
    """`defined_risk_only` substitutes each naked sideways shape for its
    defined-risk twin. It is DERIVED from the execution mode by the caller,
    never configured independently -- see OptionsOSRunner, which fails closed:
    anything other than a literal `shadow_mode: true` selects defined-risk
    only, so a missing key, a typo or a real-money switch all land safe."""
    now = clock()
    candidates = _evaluate_candidates(trend_regime, volatility_regime, defined_risk_only)

    # 1. NO OPINION -> NO TRADE. Unchanged: an absent or unrecognised regime
    #    is not a neutral market, it is an unknown one.
    if (trend_regime is None or volatility_regime is None
            or trend_regime == TREND_UNKNOWN or volatility_regime == VOL_UNKNOWN):
        return StrategySelectionResult(
            selected_strategy=None, trend_regime=trend_regime or "MISSING",
            volatility_regime=volatility_regime or "MISSING",
            reasoning="Market regime unavailable or unrecognized -- fail closed, no trade today.",
            confidence="NONE", evaluated_at=now, candidates=candidates,
        )

    # 2. VOLATILITY EXPANSION VETOES EVERYTHING. Checked BEFORE direction:
    #    selling into rising volatility is a bad trade in a trend exactly as
    #    it is in a range, and this ordering makes that non-negotiable rather
    #    than something a directional branch could reach around.
    if volatility_regime == VOL_EXPANSION:
        return StrategySelectionResult(
            selected_strategy=None, trend_regime=trend_regime, volatility_regime=volatility_regime,
            reasoning="Volatility expanding -- premium-selling risk/reward is unfavorable while vol "
                      "is still rising; wait for stabilization rather than sell into expansion. "
                      "Applies in every regime, including a trending one.",
            confidence="NONE", evaluated_at=now, candidates=candidates,
        )

    # 3. SIDEWAYS -- the most common state, and the one premium selling is for.
    if trend_regime == TREND_SIDEWAYS:
        family = SIDEWAYS_SHAPE_BY_VOLATILITY.get(volatility_regime)
        if family is None:
            return StrategySelectionResult(
                selected_strategy=None, trend_regime=trend_regime, volatility_regime=volatility_regime,
                reasoning=f"Range-bound market with unmapped volatility regime "
                          f"({volatility_regime}) -- fail closed rather than guess a shape.",
                confidence="NONE", evaluated_at=now, candidates=candidates,
            )
        shape = "short straddle (ATM both legs)" if family == FAMILY_SHORT_STRADDLE \
            else "short strangle (delta-targeted both legs)"
        rationale = ("premium at the money is rich enough to pay for the gamma"
                     if family == FAMILY_SHORT_STRADDLE
                     else "premium is thin, so distance from the money is worth more than the credit")

        if defined_risk_only:
            naked, family = family, DEFINED_RISK_TWIN[family]
            return StrategySelectionResult(
                selected_strategy=family, trend_regime=trend_regime,
                volatility_regime=volatility_regime,
                reasoning=f"Range-bound market ({trend_regime}) with {volatility_regime} "
                          f"volatility -- {shape}: {rationale}. DEFINED-RISK MODE: {naked} "
                          f"substituted for {family}, same short strikes with protective wings, "
                          f"because loss on a naked shape is bounded only by a control that runs "
                          f"every 300s and a 5-minute bar can exceed the stop.",
                confidence="HIGH", evaluated_at=now, candidates=candidates,
            )

        return StrategySelectionResult(
            selected_strategy=family, trend_regime=trend_regime, volatility_regime=volatility_regime,
            reasoning=f"Range-bound market ({trend_regime}) with {volatility_regime} volatility -- "
                      f"{shape}: {rationale}. NAKED short legs: loss is not bounded by the shape, "
                      f"only by the exit policy and the risk gates.",
            confidence="HIGH", evaluated_at=now, candidates=candidates,
        )

    # 4. TRENDING -- sell against the trend's own direction, with protection.
    if trend_regime == TREND_TRENDING_UP:
        return StrategySelectionResult(
            selected_strategy=FAMILY_BULL_PUT_SPREAD, trend_regime=trend_regime,
            volatility_regime=volatility_regime,
            reasoning=f"Up-trending market ({trend_regime}) with {volatility_regime} volatility -- "
                      f"bull put spread: sell the put and buy a lower put, collecting a credit that "
                      f"profits if price rises, stalls, or falls less than the short strike. "
                      f"Defined risk: max loss capped by the long leg.",
            confidence="HIGH", evaluated_at=now, candidates=candidates,
        )

    if trend_regime == TREND_TRENDING_DOWN:
        return StrategySelectionResult(
            selected_strategy=FAMILY_BEAR_CALL_SPREAD, trend_regime=trend_regime,
            volatility_regime=volatility_regime,
            reasoning=f"Down-trending market ({trend_regime}) with {volatility_regime} volatility -- "
                      f"bear call spread: sell the call and buy a higher call, collecting a credit "
                      f"that profits if price falls, stalls, or rises less than the short strike. "
                      f"Defined risk: max loss capped by the long leg.",
            confidence="HIGH", evaluated_at=now, candidates=candidates,
        )

    # 5. ANYTHING ELSE -> NO TRADE. Unchanged.
    return StrategySelectionResult(
        selected_strategy=None, trend_regime=trend_regime, volatility_regime=volatility_regime,
        reasoning=f"Regime combination ({trend_regime}, {volatility_regime}) has no mapped selling "
                  f"strategy in this table -- fail closed rather than guess.",
        confidence="NONE", evaluated_at=now, candidates=candidates,
    )
