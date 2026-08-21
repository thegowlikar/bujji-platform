"""Trade Construction Foundation engine — Series 90.

Reuses, never reimplements:
  - `bujji.intelligence.volatility_brain.solve_implied_volatility` /
    `compute_expected_move` (real Black-Scholes IV solver, same functions
    `msi_volatility_structure` already bridges in).
  - `bujji.intelligence.greeks_brain._bs_delta` (real Black-Scholes delta).
Both are PURE functions (no wall-clock, no I/O) -- safe to call directly,
unlike their `analyze()` wrapper classes (see `msi_volatility_structure`'s
own migration-note precedent).

Chain input: a tuple of real per-contract observation objects exactly as
produced by `bujji.options_observation.runner.ingest_all_option_series_from_bhavcopy`
(one row per contract: strike/expiry/option_type/settlement/open_interest/
underlying_price). This package never re-parses Bhavcopy itself.
"""
from __future__ import annotations

import hashlib
import math
from datetime import datetime
from typing import Dict, Iterable, Optional, Sequence, Tuple

from bujji.core.enums import OptionType
from bujji.intelligence.volatility_brain import solve_implied_volatility
from bujji.intelligence.greeks_brain import _bs_delta

from . import config as _config
from . import taxonomy
from .models import Explanation, ExpiryDecision, StrikeLeg, TradeConstructionAssessment


# ---------------------------------------------------------------------------
# Deliverable 2 -- Expiry selection
# ---------------------------------------------------------------------------

def _dte(as_of_date: str, expiry: str) -> int:
    return (datetime.fromisoformat(expiry).date() - datetime.fromisoformat(as_of_date).date()).days


def select_expiry(
    chain: Sequence, as_of_date: str,
    min_dte: int = _config.DEFAULT_MIN_DTE, max_dte: int = _config.DEFAULT_MAX_DTE,
) -> ExpiryDecision:
    """Nearest expiry (weekly or monthly, whichever is soonest -- no
    distinction is made between them at this stage) within [min_dte,
    max_dte]. Every candidate rejected outside that window is reported,
    never silently dropped."""
    all_expiries = sorted({row.expiry for row in chain if row.expiry is not None})
    rejected: list = []
    survivors: list = []
    for exp in all_expiries:
        dte = _dte(as_of_date, exp)
        if dte < min_dte:
            rejected.append((exp, taxonomy.EXPIRY_REJECTED_BELOW_MIN_DTE))
        elif dte > max_dte:
            rejected.append((exp, taxonomy.EXPIRY_REJECTED_ABOVE_MAX_DTE))
        else:
            survivors.append((exp, dte))

    if not survivors:
        return ExpiryDecision(
            chosen_expiry=None, dte=None, candidate_expiries=tuple(all_expiries),
            rejected_expiries=tuple(rejected),
            reasoning=(f"no expiry survived the [{min_dte},{max_dte}] DTE window "
                       f"out of {len(all_expiries)} available expiries",),
        )
    chosen_expiry, chosen_dte = min(survivors, key=lambda p: p[1])
    return ExpiryDecision(
        chosen_expiry=chosen_expiry, dte=chosen_dte, candidate_expiries=tuple(all_expiries),
        rejected_expiries=tuple(rejected),
        reasoning=(f"chose {chosen_expiry} (DTE={chosen_dte}), the nearest expiry within "
                   f"the configured [{min_dte},{max_dte}] DTE window",),
    )


def select_calendar_expiries(
    chain: Sequence, as_of_date: str,
    min_dte: int = _config.DEFAULT_MIN_DTE, max_dte: int = _config.DEFAULT_MAX_DTE,
) -> Tuple[ExpiryDecision, Optional[str]]:
    """CALENDAR needs a near AND a far leg. Near = `select_expiry`'s normal
    choice; far = the next later expiry present in the chain, itself
    within max_dte (a far leg with genuinely stale evidence is refused,
    not guessed at)."""
    near_decision = select_expiry(chain, as_of_date, min_dte, max_dte)
    if near_decision.chosen_expiry is None:
        return near_decision, None
    later = [e for e in near_decision.candidate_expiries if e > near_decision.chosen_expiry]
    for exp in sorted(later):
        if _dte(as_of_date, exp) <= max_dte:
            return near_decision, exp
    return near_decision, None


# ---------------------------------------------------------------------------
# Chain analytics -- IV / delta per strike, real Black-Scholes reuse only
# ---------------------------------------------------------------------------

class _StrikeEvidence:
    __slots__ = ("strike", "option_type", "premium", "premium_basis",
                 "open_interest", "iv", "delta")

    def __init__(self, strike, option_type, premium, open_interest, iv, delta,
                 premium_basis=None):
        self.strike = strike
        self.option_type = option_type
        self.premium = premium
        # WHICH observed number the IV was inverted from. Recorded because an
        # IV is only as current as the price behind it: a settlement, a
        # two-sided mid and a possibly-stale last trade are three different
        # claims about "the price", and a reader of the reasoning should be
        # able to tell which one a strike choice rests on.
        self.premium_basis = premium_basis
        self.open_interest = open_interest
        self.iv = iv
        self.delta = delta


# Premium sources, in priority order. Recorded on the evidence.
PREMIUM_SETTLEMENT = "SETTLEMENT"
PREMIUM_MID = "MID"
PREMIUM_LAST_TRADE = "LAST_TRADE"


def _premium_for(row):
    """The real observed premium for one chain row, and where it came from.

    WHY THIS EXISTS (2026-08-19). This engine read `row.settlement` and
    nothing else. The bhavcopy replay provider populates settlement; the LIVE
    chain provider explicitly does NOT -- it sets settlement=None and puts the
    traded price in `close`. So on live data every strike resolved to
    premium=None, therefore iv=None, therefore delta=None, therefore ZERO
    candidates, and every live entry attempt died at strike selection with
    REJECT_STRIKE_UNAVAILABLE.

    Nothing caught it because the entire test suite drives the bhavcopy path.
    Bujji could not construct a trade on live data at all -- a second, unknown
    gate sitting behind the regime stability gate.

    ORDER IS DELIBERATE AND NON-REGRESSIVE. Settlement stays FIRST, so every
    bhavcopy-sourced decision, replay and test is bit-for-bit unchanged. The
    new sources are reached only where settlement is absent, which is exactly
    the live case. Mid is preferred over the last trade because a print on a
    far strike can be minutes old while the book has moved, and a stale price
    implies a stale volatility.

    Returns (premium, basis), or (None, None) when the row carries no usable
    price -- absent, never defaulted to zero.
    """
    settlement = getattr(row, "settlement", None)
    try:
        if settlement is not None and float(settlement) > 0:
            return float(settlement), PREMIUM_SETTLEMENT
    except (TypeError, ValueError):
        pass

    bid, ask = getattr(row, "bid", None), getattr(row, "ask", None)
    try:
        if bid is not None and ask is not None and float(bid) > 0 and float(ask) > 0:
            return (float(bid) + float(ask)) / 2.0, PREMIUM_MID
    except (TypeError, ValueError):
        pass

    close = getattr(row, "close", None)
    try:
        if close is not None and float(close) > 0:
            return float(close), PREMIUM_LAST_TRADE
    except (TypeError, ValueError):
        pass

    return None, None


def _build_strike_evidence(
    chain: Sequence, expiry: str, spot: float, t_years: float,
    r: float = _config.DEFAULT_RISK_FREE_RATE,
) -> Dict[Tuple[float, str], _StrikeEvidence]:
    evidence: Dict[Tuple[float, str], _StrikeEvidence] = {}
    for row in chain:
        if row.expiry != expiry or row.strike is None or row.option_type not in ("CE", "PE"):
            continue
        premium, premium_basis = _premium_for(row)
        iv, delta = None, None
        if premium is not None and premium > 0 and t_years > 0:
            opt = OptionType.CE if row.option_type == "CE" else OptionType.PE
            iv = solve_implied_volatility(premium, spot, row.strike, t_years, r, opt)
            if iv is not None:
                delta = _bs_delta(spot, row.strike, t_years, r, iv, opt)
        evidence[(row.strike, row.option_type)] = _StrikeEvidence(
            strike=row.strike, option_type=row.option_type, premium=premium,
            open_interest=row.open_interest, iv=iv, delta=delta,
            premium_basis=premium_basis,
        )
    return evidence


def _candidates_for_type(evidence: Dict, option_type: str) -> list:
    return sorted((e for e in evidence.values() if e.option_type == option_type and e.delta is not None),
                  key=lambda e: e.strike)


def _nearest_by_delta(evidence: Dict, option_type: str, target_delta: float):
    """Returns (chosen, neighbours) where `chosen` is the candidate whose
    |delta| is closest to `target_delta`, and `neighbours` are the next-2
    closest -- used to answer "why not neighbouring strikes"."""
    candidates = _candidates_for_type(evidence, option_type)
    if not candidates:
        return None, []
    ranked = sorted(candidates, key=lambda e: abs(abs(e.delta) - target_delta))
    return ranked[0], ranked[1:3]


def _nearest_atm(evidence: Dict, option_type: str, spot: float):
    candidates = _candidates_for_type(evidence, option_type)
    if not candidates:
        return None, []
    ranked = sorted(candidates, key=lambda e: abs(e.strike - spot))
    return ranked[0], ranked[1:3]


def _at_strike(evidence: Dict, option_type: str, strike: float):
    return evidence.get((strike, option_type))


# ---------------------------------------------------------------------------
# Liquidity validation (Deliverable 1 finding: OI proxy, not spread)
# ---------------------------------------------------------------------------

def _liquidity_ok(evidences: Iterable[_StrikeEvidence]) -> Tuple[bool, Tuple[str, ...]]:
    failing = [e for e in evidences if e.open_interest is None or e.open_interest < _config.MIN_OPEN_INTEREST]
    if failing:
        return False, tuple(
            f"{e.option_type}{int(e.strike)} open_interest="
            f"{e.open_interest} < MIN_OPEN_INTEREST={_config.MIN_OPEN_INTEREST} "
            f"(liquidity proxy -- real bid/ask unavailable in this data source)"
            for e in failing
        )
    return True, ()


# ---------------------------------------------------------------------------
# Deliverable 3/4 -- per-family leg construction
# ---------------------------------------------------------------------------

def _leg(role, evidence: _StrikeEvidence, side: str, expiry: str, ratio: int, why: str) -> StrikeLeg:
    return StrikeLeg(
        role=role, option_type=evidence.option_type, strike=evidence.strike, expiry=expiry,
        delta=round(evidence.delta, 4) if evidence.delta is not None else None,
        premium=evidence.premium, open_interest=evidence.open_interest, side=side, ratio=ratio,
        reasoning=(why,),
    )


def _direction_option_type(direction: Optional[str]) -> Optional[str]:
    bullish = ("STRONG_BULLISH", "BULLISH", "WEAK_BULLISH")
    bearish = ("STRONG_BEARISH", "BEARISH", "WEAK_BEARISH")
    if direction in bullish:
        return "CE"
    if direction in bearish:
        return "PE"
    return None  # NEUTRAL / MIXED / UNKNOWN / None -- no deterministic side.


def _wing_width(expected_move_pct: Optional[float], spot: Optional[float]) -> float:
    if expected_move_pct is not None and spot is not None:
        return round(spot * (expected_move_pct / 100.0) * _config.WING_WIDTH_EXPECTED_MOVE_MULTIPLIER, -1) or _config.WING_WIDTH_FALLBACK_POINTS
    return _config.WING_WIDTH_FALLBACK_POINTS


def _build_legs(
    family: str, evidence: Dict, expiry: str, far_expiry_evidence: Optional[Dict], far_expiry: Optional[str],
    spot: float, direction: Optional[str], expected_move_pct: Optional[float],
):
    """Returns (legs, why_these_strikes, why_not_neighbouring, dominant_constraints, rejection_reason)."""
    target_delta = _config.FAMILY_DELTA_TARGETS[family]
    why_strikes: list = []
    why_not: list = []
    dominant: list = []

    def not_neighbour_note(chosen, neighbours, label):
        if neighbours:
            why_not.append(
                f"{label}: rejected neighbouring strikes "
                f"{[int(n.strike) for n in neighbours]} (delta {[round(abs(n.delta),3) for n in neighbours]}) "
                f"further from target delta {target_delta} than chosen {int(chosen.strike)} "
                f"(delta {round(abs(chosen.delta),3)})"
            )

    if family in ("LONG_DIRECTIONAL", "SHORT_DIRECTIONAL", "COVERED", "SYNTHETIC"):
        opt_type = _direction_option_type(direction) if family != "COVERED" else "CE"
        if family == "SYNTHETIC" and opt_type is None:
            return None, why_strikes, why_not, dominant, taxonomy.REJECT_STRIKE_UNAVAILABLE
        if family in ("LONG_DIRECTIONAL", "SHORT_DIRECTIONAL") and opt_type is None:
            return None, why_strikes, why_not, dominant, taxonomy.REJECT_STRIKE_UNAVAILABLE

        if family == "LONG_DIRECTIONAL":
            chosen, nbrs = _nearest_by_delta(evidence, opt_type, target_delta)
            if chosen is None:
                return None, why_strikes, why_not, dominant, taxonomy.REJECT_STRIKE_UNAVAILABLE
            why_strikes.append(f"bought {opt_type}{int(chosen.strike)}: delta {round(abs(chosen.delta),3)} "
                                f"nearest to target {target_delta} for a {direction} lean (long directional exposure)")
            not_neighbour_note(chosen, nbrs, opt_type)
            dominant.append("delta_target")
            return [_leg(taxonomy.ROLE_LONG, chosen, "BUY", expiry, 1, why_strikes[-1])], why_strikes, why_not, dominant, None

        if family == "SHORT_DIRECTIONAL":
            # Directional premium sale: bullish lean -> sell PUT, bearish lean -> sell CALL.
            sell_type = "PE" if direction in ("STRONG_BULLISH", "BULLISH", "WEAK_BULLISH") else "CE"
            chosen, nbrs = _nearest_by_delta(evidence, sell_type, target_delta)
            if chosen is None:
                return None, why_strikes, why_not, dominant, taxonomy.REJECT_STRIKE_UNAVAILABLE
            why_strikes.append(f"sold {sell_type}{int(chosen.strike)}: delta {round(abs(chosen.delta),3)} "
                                f"nearest to target {target_delta}, directional premium sale against a {direction} lean")
            not_neighbour_note(chosen, nbrs, sell_type)
            dominant.append("delta_target")
            return [_leg(taxonomy.ROLE_SHORT, chosen, "SELL", expiry, 1, why_strikes[-1])], why_strikes, why_not, dominant, None

        if family == "COVERED":
            chosen, nbrs = _nearest_by_delta(evidence, "CE", target_delta)
            if chosen is None:
                return None, why_strikes, why_not, dominant, taxonomy.REJECT_STRIKE_UNAVAILABLE
            why_strikes.append(f"sold covered CE{int(chosen.strike)}: delta {round(abs(chosen.delta),3)} "
                                f"nearest to configured covered-call target {target_delta}")
            not_neighbour_note(chosen, nbrs, "CE")
            dominant.append("delta_target")
            return [_leg(taxonomy.ROLE_COVERED_SHORT_CALL, chosen, "SELL", expiry, 1, why_strikes[-1])], why_strikes, why_not, dominant, None

        # SYNTHETIC
        long_type = opt_type
        short_type = "PE" if long_type == "CE" else "CE"
        long_leg, long_nbrs = _nearest_atm(evidence, long_type, spot)
        short_leg, short_nbrs = (_at_strike(evidence, short_type, long_leg.strike), []) if long_leg else (None, [])
        if long_leg is None or short_leg is None:
            return None, why_strikes, why_not, dominant, taxonomy.REJECT_STRIKE_UNAVAILABLE
        why_strikes.append(f"synthetic position at ATM strike {int(long_leg.strike)} "
                            f"({direction} lean): long {long_type}, short {short_type}, same strike/expiry")
        not_neighbour_note(long_leg, long_nbrs, long_type)
        dominant.append("atm_anchor")
        return [
            _leg(taxonomy.ROLE_SYNTHETIC_LONG_CALL if long_type == "CE" else taxonomy.ROLE_SYNTHETIC_SHORT_PUT,
                 long_leg, "BUY", expiry, 1, why_strikes[-1]),
            _leg(taxonomy.ROLE_SYNTHETIC_SHORT_PUT if short_type == "PE" else taxonomy.ROLE_SYNTHETIC_LONG_CALL,
                 short_leg, "SELL", expiry, 1, why_strikes[-1]),
        ], why_strikes, why_not, dominant, None

    if family in ("NEUTRAL_PREMIUM_SELLING", "NEUTRAL_PREMIUM_BUYING", "VOLATILITY_EXPANSION", "VOLATILITY_COMPRESSION"):
        side = "SELL" if family in ("NEUTRAL_PREMIUM_SELLING", "VOLATILITY_COMPRESSION") else "BUY"
        role = taxonomy.ROLE_SHORT if side == "SELL" else taxonomy.ROLE_LONG
        if family in ("VOLATILITY_EXPANSION", "VOLATILITY_COMPRESSION"):
            call_leg, call_nbrs = _nearest_atm(evidence, "CE", spot)
            put_leg, put_nbrs = (_at_strike(evidence, "PE", call_leg.strike), []) if call_leg else (None, [])
        else:
            call_leg, call_nbrs = _nearest_by_delta(evidence, "CE", target_delta)
            put_leg, put_nbrs = _nearest_by_delta(evidence, "PE", target_delta)
        if call_leg is None or put_leg is None:
            return None, why_strikes, why_not, dominant, taxonomy.REJECT_STRIKE_UNAVAILABLE
        shape = "straddle (ATM both legs)" if family in ("VOLATILITY_EXPANSION", "VOLATILITY_COMPRESSION") else "strangle (delta-targeted both legs)"
        why_strikes.append(f"{side.lower()} CE{int(call_leg.strike)} + {side.lower()} PE{int(put_leg.strike)}: {shape}, "
                            f"target delta {target_delta}")
        not_neighbour_note(call_leg, call_nbrs, "CE")
        not_neighbour_note(put_leg, put_nbrs, "PE")
        dominant.append("delta_target" if "strangle" in shape else "atm_anchor")
        return [
            _leg(role, call_leg, side, expiry, 1, why_strikes[-1]),
            _leg(role, put_leg, side, expiry, 1, why_strikes[-1]),
        ], why_strikes, why_not, dominant, None

    if family in ("IRON_CONDOR", "IRON_FLY"):
        if family == "IRON_CONDOR":
            short_call, cc_nbrs = _nearest_by_delta(evidence, "CE", target_delta)
            short_put, pp_nbrs = _nearest_by_delta(evidence, "PE", target_delta)
        else:
            short_call, cc_nbrs = _nearest_atm(evidence, "CE", spot)
            short_put, pp_nbrs = (_at_strike(evidence, "PE", short_call.strike), []) if short_call else (None, [])
        if short_call is None or short_put is None:
            return None, why_strikes, why_not, dominant, taxonomy.REJECT_STRIKE_UNAVAILABLE
        width = _wing_width(expected_move_pct, spot)
        dominant.append("expected_move" if expected_move_pct is not None else "wing_width_fallback")
        long_call = _at_strike(evidence, "CE", _nearest_grid(short_call.strike + width, evidence, "CE"))
        long_put = _at_strike(evidence, "PE", _nearest_grid(short_put.strike - width, evidence, "PE"))
        if long_call is None or long_put is None:
            return None, why_strikes, why_not, dominant, taxonomy.REJECT_IMPOSSIBLE_WING_WIDTH
        why_strikes.append(f"short CE{int(short_call.strike)}/PE{int(short_put.strike)} (target delta {target_delta}), "
                            f"wings at CE{int(long_call.strike)}/PE{int(long_put.strike)} "
                            f"(width={width} pts, source={'VSB expected move' if expected_move_pct is not None else 'configured fallback'})")
        not_neighbour_note(short_call, cc_nbrs, "CE")
        not_neighbour_note(short_put, pp_nbrs, "PE")
        return [
            _leg(taxonomy.ROLE_SHORT, short_call, "SELL", expiry, 1, why_strikes[-1]),
            _leg(taxonomy.ROLE_SHORT, short_put, "SELL", expiry, 1, why_strikes[-1]),
            _leg(taxonomy.ROLE_WING_UPPER, long_call, "BUY", expiry, 1, why_strikes[-1]),
            _leg(taxonomy.ROLE_WING_LOWER, long_put, "BUY", expiry, 1, why_strikes[-1]),
        ], why_strikes, why_not, dominant, None

    if family in ("BULL_PUT_SPREAD", "BEAR_CALL_SPREAD"):
        # DIRECTIONAL CREDIT SPREADS (2026-08-19, operator directive).
        # Structurally one half of an IRON_CONDOR: a short leg at the
        # premium-selling delta, protected by a long leg one wing-width
        # further out-of-the-money. Deliberately built from the SAME
        # helpers the condor uses -- `_nearest_by_delta`, `_wing_width`
        # (driven by VSB's real expected move when available),
        # `_nearest_grid` -- so a change to the distance policy moves every
        # premium-selling shape together instead of leaving these two behind.
        #
        # BULL_PUT_SPREAD leans WITH an up-trend: sell the put, buy a lower
        # put. It profits if price rises, stalls, or falls less than the
        # short strike. BEAR_CALL_SPREAD is its mirror for a down-trend.
        # Both collect a credit and both are defined-risk: max loss is
        # (wing width - credit), capped by the long leg.
        opt_type = "PE" if family == "BULL_PUT_SPREAD" else "CE"
        short_leg, short_nbrs = _nearest_by_delta(evidence, opt_type, target_delta)
        if short_leg is None:
            return None, why_strikes, why_not, dominant, taxonomy.REJECT_STRIKE_UNAVAILABLE
        width = _wing_width(expected_move_pct, spot)
        dominant.append("expected_move" if expected_move_pct is not None else "wing_width_fallback")
        # The protection sits FURTHER out of the money than the short leg:
        # below it for a put spread, above it for a call spread.
        protect_strike = (short_leg.strike - width if family == "BULL_PUT_SPREAD"
                          else short_leg.strike + width)
        long_leg = _at_strike(evidence, opt_type,
                              _nearest_grid(protect_strike, evidence, opt_type))
        if long_leg is None:
            return None, why_strikes, why_not, dominant, taxonomy.REJECT_IMPOSSIBLE_WING_WIDTH
        lean = "up-trend" if family == "BULL_PUT_SPREAD" else "down-trend"
        why_strikes.append(
            f"short {opt_type}{int(short_leg.strike)} (target delta {target_delta}) protected by "
            f"long {opt_type}{int(long_leg.strike)} (width={width} pts, "
            f"source={'VSB expected move' if expected_move_pct is not None else 'configured fallback'}) "
            f"-- credit spread leaning with the {lean}, max loss capped by the long leg")
        not_neighbour_note(short_leg, short_nbrs, opt_type)
        wing_role = (taxonomy.ROLE_WING_LOWER if family == "BULL_PUT_SPREAD"
                     else taxonomy.ROLE_WING_UPPER)
        return [
            _leg(taxonomy.ROLE_SHORT, short_leg, "SELL", expiry, 1, why_strikes[-1]),
            _leg(wing_role, long_leg, "BUY", expiry, 1, why_strikes[-1]),
        ], why_strikes, why_not, dominant, None

    if family == "BUTTERFLY":
        body, body_nbrs = _nearest_atm(evidence, "CE", spot)
        if body is None:
            return None, why_strikes, why_not, dominant, taxonomy.REJECT_STRIKE_UNAVAILABLE
        width = _wing_width(expected_move_pct, spot)
        dominant.append("expected_move" if expected_move_pct is not None else "wing_width_fallback")
        lower = _at_strike(evidence, "CE", _nearest_grid(body.strike - width, evidence, "CE"))
        upper = _at_strike(evidence, "CE", _nearest_grid(body.strike + width, evidence, "CE"))
        if lower is None or upper is None:
            return None, why_strikes, why_not, dominant, taxonomy.REJECT_IMPOSSIBLE_WING_WIDTH
        why_strikes.append(f"CALL butterfly body CE{int(body.strike)} (ATM, 2x short), "
                            f"wings CE{int(lower.strike)}/CE{int(upper.strike)} (width={width} pts, "
                            f"source={'VSB expected move' if expected_move_pct is not None else 'configured fallback'})")
        not_neighbour_note(body, body_nbrs, "CE")
        return [
            _leg(taxonomy.ROLE_WING_LOWER, lower, "BUY", expiry, 1, why_strikes[-1]),
            _leg(taxonomy.ROLE_BODY, body, "SELL", expiry, 2, why_strikes[-1]),
            _leg(taxonomy.ROLE_WING_UPPER, upper, "BUY", expiry, 1, why_strikes[-1]),
        ], why_strikes, why_not, dominant, None

    if family == "RATIO":
        long_leg, long_nbrs = _nearest_atm(evidence, "CE", spot)
        short_leg, short_nbrs = _nearest_by_delta(evidence, "CE", target_delta)
        if long_leg is None or short_leg is None:
            return None, why_strikes, why_not, dominant, taxonomy.REJECT_STRIKE_UNAVAILABLE
        if short_leg.strike == long_leg.strike:
            return None, why_strikes, why_not, dominant, taxonomy.REJECT_STRIKE_UNAVAILABLE
        why_strikes.append(f"1x long ATM CE{int(long_leg.strike)}, 2x short OTM CE{int(short_leg.strike)} "
                            f"(target delta {target_delta}) -- call ratio spread")
        not_neighbour_note(short_leg, short_nbrs, "CE")
        dominant.append("delta_target")
        return [
            _leg(taxonomy.ROLE_LONG, long_leg, "BUY", expiry, 1, why_strikes[-1]),
            _leg(taxonomy.ROLE_SHORT, short_leg, "SELL", expiry, 2, why_strikes[-1]),
        ], why_strikes, why_not, dominant, None

    if family == "CALENDAR":
        if far_expiry_evidence is None or far_expiry is None:
            return None, why_strikes, why_not, dominant, taxonomy.REJECT_NO_SUITABLE_EXPIRY
        near_body, near_nbrs = _nearest_atm(evidence, "CE", spot)
        far_body = _at_strike(far_expiry_evidence, "CE", near_body.strike) if near_body else None
        if near_body is None or far_body is None:
            return None, why_strikes, why_not, dominant, taxonomy.REJECT_STRIKE_UNAVAILABLE
        why_strikes.append(f"ATM strike CE{int(near_body.strike)}: sell near expiry {expiry}, "
                            f"buy far expiry {far_expiry}, same strike (calendar)")
        not_neighbour_note(near_body, near_nbrs, "CE")
        dominant.append("atm_anchor")
        return [
            _leg(taxonomy.ROLE_NEAR_EXPIRY_SHORT, near_body, "SELL", expiry, 1, why_strikes[-1]),
            _leg(taxonomy.ROLE_FAR_EXPIRY_LONG, far_body, "BUY", far_expiry, 1, why_strikes[-1]),
        ], why_strikes, why_not, dominant, None

    return None, why_strikes, why_not, dominant, taxonomy.REJECT_UNSUPPORTED_FAMILY


def _nearest_grid(target: float, evidence: Dict, option_type: str) -> Optional[float]:
    candidates = [e.strike for e in evidence.values() if e.option_type == option_type]
    if not candidates:
        return None
    return min(candidates, key=lambda s: abs(s - target))


# ---------------------------------------------------------------------------
# Deliverable 4 -- Assessment assembly
# ---------------------------------------------------------------------------

def _assessment_id(family: str, expiry: Optional[str], legs: Tuple[StrikeLeg, ...], rejection_reason: Optional[str], schema_version: str) -> str:
    parts = [family, expiry or "", rejection_reason or ""]
    for leg in legs:
        parts.append(f"{leg.role}:{leg.option_type}:{leg.strike}:{leg.side}:{leg.ratio}:{leg.expiry}")
    content = "|".join(parts) + f"|{schema_version}"
    return hashlib.md5(content.encode("utf-8")).hexdigest()


def construct_trade(
    strategy_family: str, chain: Sequence, spot: Optional[float], as_of_date: str, *,
    direction: Optional[str] = None, expected_move_pct: Optional[float] = None,
    supporting_assessment_ids: Tuple[str, ...] = (), timestamp: str,
    min_dte: int = _config.DEFAULT_MIN_DTE, max_dte: int = _config.DEFAULT_MAX_DTE,
) -> TradeConstructionAssessment:
    schema_version = taxonomy.MSI_TRADE_CONSTRUCTION_VERSION

    def _rejected(reason: str, expiry_decision: ExpiryDecision, legs=()) -> TradeConstructionAssessment:
        aid = _assessment_id(strategy_family, expiry_decision.chosen_expiry, tuple(legs), reason, schema_version)
        explanation = Explanation(
            assessment_id=aid, why_this_expiry=expiry_decision.reasoning,
            why_these_strikes=(), why_not_neighbouring_strikes=(),
            dominant_constraints=(reason,), schema_version=schema_version,
        )
        return TradeConstructionAssessment(
            assessment_id=aid, timestamp=timestamp, strategy_family=strategy_family,
            constructed=False, rejection_reason=reason,
            expiry=expiry_decision.chosen_expiry, expiry_decision=expiry_decision, legs=(),
            entry_reference_prices={}, expected_credit_debit=None,
            risk_profile=taxonomy.RISK_UNKNOWN, required_margin=None,
            margin_unavailable_reason="construction was rejected before margin would apply",
            supporting_assessment_ids=supporting_assessment_ids, explanation=explanation,
            provenance="bujji.msi_trade_construction.engine.construct_trade", schema_version=schema_version,
        )

    if strategy_family not in taxonomy.SUPPORTED_FAMILIES:
        empty_decision = ExpiryDecision(None, None, (), (), ("family not supported by this package",))
        return _rejected(taxonomy.REJECT_UNSUPPORTED_FAMILY, empty_decision)

    if not chain or spot is None or spot <= 0:
        empty_decision = ExpiryDecision(None, None, (), (), ("no usable chain or spot for this day",))
        return _rejected(taxonomy.REJECT_INCONSISTENT_CHAIN, empty_decision)

    far_expiry = None
    far_evidence = None
    if strategy_family == "CALENDAR":
        expiry_decision, far_expiry = select_calendar_expiries(chain, as_of_date, min_dte, max_dte)
    else:
        expiry_decision = select_expiry(chain, as_of_date, min_dte, max_dte)

    if expiry_decision.chosen_expiry is None:
        return _rejected(taxonomy.REJECT_NO_SUITABLE_EXPIRY, expiry_decision)

    t_years = max(expiry_decision.dte, 1) / 365.0
    evidence = _build_strike_evidence(chain, expiry_decision.chosen_expiry, spot, t_years)
    if not evidence:
        return _rejected(taxonomy.REJECT_INCONSISTENT_CHAIN, expiry_decision)

    if strategy_family == "CALENDAR":
        if far_expiry is None:
            return _rejected(taxonomy.REJECT_NO_SUITABLE_EXPIRY, expiry_decision)
        far_t_years = max(_dte(as_of_date, far_expiry), 1) / 365.0
        far_evidence = _build_strike_evidence(chain, far_expiry, spot, far_t_years)
        if not far_evidence:
            return _rejected(taxonomy.REJECT_INCONSISTENT_CHAIN, expiry_decision)

    legs, why_strikes, why_not, dominant, rejection_reason = _build_legs(
        strategy_family, evidence, expiry_decision.chosen_expiry, far_evidence, far_expiry,
        spot, direction, expected_move_pct,
    )
    if rejection_reason is not None:
        return _rejected(rejection_reason, expiry_decision)

    leg_evidences = [
        _at_strike(evidence if leg.expiry == expiry_decision.chosen_expiry else far_evidence, leg.option_type, leg.strike)
        for leg in legs
    ]
    leg_evidences = [e for e in leg_evidences if e is not None]
    liquidity_ok, liquidity_notes = _liquidity_ok(leg_evidences)
    if not liquidity_ok:
        aid = _assessment_id(strategy_family, expiry_decision.chosen_expiry, tuple(legs), taxonomy.REJECT_LIQUIDITY_INSUFFICIENT, schema_version)
        explanation = Explanation(
            assessment_id=aid, why_this_expiry=expiry_decision.reasoning, why_these_strikes=tuple(why_strikes),
            why_not_neighbouring_strikes=tuple(why_not), dominant_constraints=("liquidity",) + tuple(liquidity_notes),
            schema_version=schema_version,
        )
        return TradeConstructionAssessment(
            assessment_id=aid, timestamp=timestamp, strategy_family=strategy_family,
            constructed=False, rejection_reason=taxonomy.REJECT_LIQUIDITY_INSUFFICIENT,
            expiry=expiry_decision.chosen_expiry, expiry_decision=expiry_decision, legs=tuple(legs),
            entry_reference_prices={}, expected_credit_debit=None, risk_profile=taxonomy.RISK_UNKNOWN,
            required_margin=None, margin_unavailable_reason="construction was rejected before margin would apply",
            supporting_assessment_ids=supporting_assessment_ids, explanation=explanation,
            provenance="bujji.msi_trade_construction.engine.construct_trade", schema_version=schema_version,
        )

    entry_prices = {f"{leg.option_type}_{int(leg.strike)}_{leg.expiry}": leg.premium for leg in legs if leg.premium is not None}
    credit_debit = 0.0
    any_price_missing = False
    for leg in legs:
        if leg.premium is None:
            any_price_missing = True
            continue
        sign = 1.0 if leg.side == "SELL" else -1.0
        credit_debit += sign * leg.premium * leg.ratio
    expected_credit_debit = None if any_price_missing else round(credit_debit, 2)

    risk_profile = (
        taxonomy.RISK_DEFINED if strategy_family in taxonomy.DEFINED_RISK_FAMILIES
        else taxonomy.RISK_UNDEFINED if strategy_family in taxonomy.UNDEFINED_RISK_FAMILIES
        else taxonomy.RISK_UNKNOWN
    )

    aid = _assessment_id(strategy_family, expiry_decision.chosen_expiry, tuple(legs), None, schema_version)
    explanation = Explanation(
        assessment_id=aid, why_this_expiry=expiry_decision.reasoning, why_these_strikes=tuple(why_strikes),
        why_not_neighbouring_strikes=tuple(why_not), dominant_constraints=tuple(dominant), schema_version=schema_version,
    )
    return TradeConstructionAssessment(
        assessment_id=aid, timestamp=timestamp, strategy_family=strategy_family,
        constructed=True, rejection_reason=None,
        expiry=expiry_decision.chosen_expiry, expiry_decision=expiry_decision, legs=tuple(legs),
        entry_reference_prices=entry_prices, expected_credit_debit=expected_credit_debit,
        risk_profile=risk_profile, required_margin=None,
        margin_unavailable_reason=(
            "no deterministic/replay-safe margin source exists in this codebase -- only a LIVE "
            "FYERS SPAN-margin broker call (bujji.capital.providers.FyersSpanMarginProvider), which "
            "requires network access and is not wall-clock/replay-safe; see docs/TRADE_CONSTRUCTION_FOUNDATION.md"
        ),
        supporting_assessment_ids=supporting_assessment_ids, explanation=explanation,
        provenance="bujji.msi_trade_construction.engine.construct_trade", schema_version=schema_version,
    )
