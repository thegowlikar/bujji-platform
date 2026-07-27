"""Portfolio & Risk Construction engine — Series 91.

Reuses, never reimplements:
  - `bujji.intelligence.volatility_brain.solve_implied_volatility` /
    `_bs_vega` (real Black-Scholes IV solver + vega).
  - `bujji.intelligence.greeks_brain._bs_delta` / `_bs_gamma` / `_bs_theta`
    (pure Black-Scholes Greeks) -- the SAME functions Series 90 already
    reuses, called again here (not duplicated) to recompute each
    proposed leg's per-contract Greeks for portfolio aggregation.
  - `bujji.capital.engine.compute_lot_sizing` -- the EXACT pure sizing
    arithmetic `CapitalManagementEngine.approve_trade` uses in
    production, extracted (Series 91, additive, zero behavior change)
    so this package never re-derives its own sizing formula. This is
    the "single capital/risk implementation shared between replay and
    production" the spec asks for.

Deliverable 1 classification summary (full detail in
docs/PORTFOLIO_RISK_CONSTRUCTION.md):
  - capital engine sizing arithmetic  -> REUSABLE DIRECTLY (extracted as
    `compute_lot_sizing`).
  - margin estimation                -> REPLAY-ONLY APPROXIMATION, but
    the approximation VALUE itself is reused verbatim from config's own
    existing SIMULATION-tier figure (`REPLAY_MARGIN_PER_LOT_ESTIMATE`).
  - available capital                -> UNAVAILABLE (no deterministic
    source anywhere in this codebase); a new, disclosed, temporary
    constant is used (`REPLAY_ASSUMED_TOTAL_CAPITAL`).
  - risk engine / portfolio exposure / Greeks aggregation / concentration
    limits / existing position handling -> UNAVAILABLE (none exist);
    built fresh in this package, reusing only the pure Black-Scholes
    primitives above for per-leg Greeks.
"""
from __future__ import annotations

import hashlib
from typing import Optional, Sequence, Tuple

from bujji.core.enums import OptionType
from bujji.intelligence.volatility_brain import solve_implied_volatility, _bs_vega
from bujji.intelligence.greeks_brain import _bs_delta, _bs_gamma, _bs_theta
from bujji.capital.engine import compute_lot_sizing
from bujji.msi_trade_construction.models import TradeConstructionAssessment
from bujji.msi_trade_construction import taxonomy as tc_taxonomy

from . import config as _config
from . import taxonomy
from .models import (
    AdmittedTrade, ConcentrationReading, Explanation, HeldLeg,
    PortfolioConstructionAssessment, PortfolioState,
)


def _dte_years(as_of_date: str, expiry: str) -> float:
    from datetime import datetime
    days = (datetime.fromisoformat(expiry).date() - datetime.fromisoformat(as_of_date).date()).days
    return max(days, 1) / 365.0


def _leg_greeks(leg, spot: float, as_of_date: str, r: float = _config.RISK_FREE_RATE) -> HeldLeg:
    """Recompute this leg's per-contract Greeks from its own real entry
    premium/strike/expiry (same reuse pattern as Series 90/VSB: calling
    the existing pure Black-Scholes functions again, not duplicating
    their formulas)."""
    delta = gamma = theta = vega = None
    if leg.premium is not None and leg.premium > 0:
        t_years = _dte_years(as_of_date, leg.expiry)
        opt = OptionType.CE if leg.option_type == "CE" else OptionType.PE
        iv = solve_implied_volatility(leg.premium, spot, leg.strike, t_years, r, opt)
        if iv is not None:
            delta = _bs_delta(spot, leg.strike, t_years, r, iv, opt)
            gamma = _bs_gamma(spot, leg.strike, t_years, r, iv)
            # Same unit convention as bujji.intelligence.greeks_brain.GreeksBrain.analyze:
            # theta per CALENDAR DAY (raw annualized theta / 365), vega per
            # 1% IV CHANGE (raw annualized vega / 100) -- the units a
            # trader actually reasons in, reported consistently across
            # this codebase's Greeks consumers.
            theta = _bs_theta(spot, leg.strike, t_years, r, iv, opt) / 365.0
            vega = _bs_vega(spot, leg.strike, t_years, r, iv) / 100.0
    return HeldLeg(
        option_type=leg.option_type, strike=leg.strike, expiry=leg.expiry, side=leg.side,
        ratio=leg.ratio, delta=delta, gamma=gamma, theta=theta, vega=vega,
    )


def _signed_scale(leg: HeldLeg, lots: int, lot_size: int) -> float:
    sign = 1.0 if leg.side == "BUY" else -1.0
    return sign * leg.ratio * lots * lot_size


def _aggregate(legs_with_lots: Sequence[Tuple[HeldLeg, int]], lot_size: int) -> Tuple[Optional[float], Optional[float], Optional[float], Optional[float]]:
    delta = gamma = theta = vega = 0.0
    any_missing = False
    for leg, lots in legs_with_lots:
        if leg.delta is None or leg.gamma is None or leg.theta is None or leg.vega is None:
            any_missing = True
            continue
        delta += _signed_scale(leg, lots, lot_size) * leg.delta
        gamma += _signed_scale(leg, lots, lot_size) * leg.gamma
        theta += _signed_scale(leg, lots, lot_size) * leg.theta
        vega += _signed_scale(leg, lots, lot_size) * leg.vega
    if any_missing and not legs_with_lots:
        return None, None, None, None
    return round(delta, 4), round(gamma, 6), round(theta, 4), round(vega, 4)


def _concentration(active: Tuple[AdmittedTrade, ...], new_family: str, new_expiry: str, new_underlying: str) -> Tuple[ConcentrationReading, ...]:
    fam_count = sum(1 for t in active if t.strategy_family == new_family) + 1
    exp_count = sum(1 for t in active if t.position_close_date == new_expiry) + 1
    und_count = sum(1 for t in active if t.underlying == new_underlying) + 1
    return (
        ConcentrationReading(taxonomy.DIMENSION_STRATEGY_FAMILY, new_family, fam_count, _config.MAX_POSITIONS_PER_STRATEGY_FAMILY),
        ConcentrationReading(taxonomy.DIMENSION_EXPIRY, new_expiry, exp_count, _config.MAX_POSITIONS_PER_EXPIRY),
        ConcentrationReading(taxonomy.DIMENSION_UNDERLYING, new_underlying, und_count, _config.MAX_POSITIONS_PER_UNDERLYING),
    )


def _assessment_id(proposed_id: str, approval_state: str, reasons: Tuple[str, ...], size, schema_version: str) -> str:
    content = "|".join([proposed_id, approval_state, ",".join(reasons), str(size), schema_version])
    return hashlib.md5(content.encode("utf-8")).hexdigest()


def evaluate_trade(
    trade: TradeConstructionAssessment, portfolio: PortfolioState, spot: Optional[float], as_of_date: str,
    *, selection_confidence: str, underlying: str = "NIFTY", timestamp: str,
    margin_per_lot_estimate: Optional[float] = _config.REPLAY_MARGIN_PER_LOT_ESTIMATE,
    total_capital: float = _config.REPLAY_ASSUMED_TOTAL_CAPITAL,
) -> PortfolioConstructionAssessment:
    schema_version = taxonomy.MSI_PORTFOLIO_CONSTRUCTION_VERSION

    def _decide(state, reasons, *, position_size_lots="__NA__", capital_required=None,
                capital_available=None, risk_budget_used=None, portfolio_greeks=(None, None, None, None),
                concentration=(), confidence=taxonomy.CONFIDENCE_NONE,
                why_approved=(), why_rejected=(), dominant=None, what_would_change=()):
        size = taxonomy.SIZE_UNKNOWN if position_size_lots == "__NA__" else position_size_lots
        aid = _assessment_id(trade.assessment_id, state, reasons, size, schema_version)
        explanation = Explanation(
            assessment_id=aid, why_approved=why_approved, why_rejected=why_rejected,
            dominant_constraint=dominant, what_would_change_for_approval=what_would_change,
            schema_version=schema_version,
        )
        return PortfolioConstructionAssessment(
            assessment_id=aid, timestamp=timestamp, proposed_trade_assessment_id=trade.assessment_id,
            strategy_family=trade.strategy_family, approval_state=state, rejection_reasons=reasons,
            required_margin=None, estimated_margin=capital_required, portfolio_delta_after=portfolio_greeks[0],
            portfolio_gamma_after=portfolio_greeks[1], portfolio_theta_after=portfolio_greeks[2],
            portfolio_vega_after=portfolio_greeks[3], concentration_after=concentration,
            capital_required=capital_required, capital_available=capital_available,
            risk_budget_used=risk_budget_used, position_size_lots=size, confidence=confidence,
            explanation=explanation, provenance="bujji.msi_portfolio_construction.engine.evaluate_trade",
            schema_version=schema_version,
        )

    active = portfolio.active(as_of_date)

    if not trade.constructed:
        return _decide(taxonomy.REJECTED, (taxonomy.REJECT_CONSTRUCTION_NOT_SUCCESSFUL,),
                        why_rejected=(f"proposed trade was never constructed: {trade.rejection_reason}",),
                        dominant=taxonomy.REJECT_CONSTRUCTION_NOT_SUCCESSFUL)

    if trade.risk_profile == tc_taxonomy.RISK_UNDEFINED and not _config.ALLOW_UNDEFINED_RISK:
        return _decide(taxonomy.REJECTED, (taxonomy.REJECT_UNDEFINED_RISK_POLICY,),
                        why_rejected=(f"{trade.strategy_family} is UNDEFINED_RISK; "
                                      f"config.ALLOW_UNDEFINED_RISK=False (structural desk policy)",),
                        dominant=taxonomy.REJECT_UNDEFINED_RISK_POLICY,
                        what_would_change=("enable ALLOW_UNDEFINED_RISK, or select a DEFINED_RISK family instead",))

    if taxonomy.confidence_rank(selection_confidence) < taxonomy.confidence_rank(_config.MIN_CONFIDENCE_FOR_ADMISSION):
        return _decide(taxonomy.REJECTED, (taxonomy.REJECT_INSUFFICIENT_CONFIDENCE,),
                        why_rejected=(f"strategy-selection confidence {selection_confidence} below "
                                      f"configured floor {_config.MIN_CONFIDENCE_FOR_ADMISSION}",),
                        dominant=taxonomy.REJECT_INSUFFICIENT_CONFIDENCE,
                        what_would_change=(f"selection confidence would need to reach {_config.MIN_CONFIDENCE_FOR_ADMISSION}",))

    if spot is None or spot <= 0:
        return _decide(taxonomy.DEFERRED, (taxonomy.DEFER_GREEKS_UNAVAILABLE,),
                        why_rejected=("no usable spot to price this trade's Greeks",),
                        dominant=taxonomy.DEFER_GREEKS_UNAVAILABLE)

    new_legs = [_leg_greeks(leg, spot, as_of_date) for leg in trade.legs]
    if any(l.delta is None for l in new_legs):
        return _decide(taxonomy.DEFERRED, (taxonomy.DEFER_GREEKS_UNAVAILABLE,),
                        why_rejected=("one or more legs' IV could not be solved from real premiums -- "
                                      "cannot verify portfolio exposure limits without real Greeks",),
                        dominant=taxonomy.DEFER_GREEKS_UNAVAILABLE,
                        what_would_change=("would need a solvable IV for every leg (a genuine, non-stale real premium)",))

    if margin_per_lot_estimate is None:
        return _decide(taxonomy.REJECTED, (taxonomy.REJECT_MISSING_MARGIN_INFORMATION,),
                        why_rejected=("no margin_per_lot estimate configured for this deployment -- "
                                      "never guessing a figure (fail closed, per Series 91 mandate)",),
                        dominant=taxonomy.REJECT_MISSING_MARGIN_INFORMATION,
                        what_would_change=("configure REPLAY_MARGIN_PER_LOT_ESTIMATE (replay) or wire a live CERTIFIED margin provider (production)",))

    active_capital_used = sum(t.capital_required for t in active)
    capital_available = total_capital - active_capital_used
    if capital_available <= 0:
        return _decide(taxonomy.REJECTED, (taxonomy.REJECT_INSUFFICIENT_CAPITAL,),
                        position_size_lots=0, capital_available=round(capital_available, 2),
                        why_rejected=(f"capital_available={capital_available:.2f} <= 0 "
                                      f"(active_capital_used={active_capital_used:.2f} of total {total_capital:.2f})",),
                        dominant=taxonomy.REJECT_INSUFFICIENT_CAPITAL,
                        what_would_change=("free up capital by closing/expiring existing positions, or raise total_capital",))

    _usable, _max_safe, approved_lots = compute_lot_sizing(
        capital_available, margin_per_lot_estimate, _config.SAFETY_BUFFER, _config.CONFIGURED_MAX_LOTS_PER_TRADE,
    )
    if approved_lots <= 0:
        return _decide(taxonomy.REJECTED, (taxonomy.REJECT_INSUFFICIENT_CAPITAL,),
                        position_size_lots=0, capital_available=round(capital_available, 2),
                        why_rejected=(f"maximum_safe_lots=0 at margin_per_lot_estimate={margin_per_lot_estimate:.2f}, "
                                      f"safety_buffer={_config.SAFETY_BUFFER}",),
                        dominant=taxonomy.REJECT_INSUFFICIENT_CAPITAL,
                        what_would_change=("more available capital or a lower per-lot margin estimate would be needed",))

    capital_required = round(approved_lots * margin_per_lot_estimate, 2)

    new_legs_with_lots = [(leg, approved_lots) for leg in new_legs]
    active_legs_with_lots = [(leg, t.approved_lots) for t in active for leg in t.legs]
    delta_after, gamma_after, theta_after, vega_after = _aggregate(active_legs_with_lots + new_legs_with_lots, _config.DEFAULT_LOT_SIZE)

    position_close_date = max(leg.expiry for leg in trade.legs)
    concentration = _concentration(active, trade.strategy_family, position_close_date, underlying)
    breached = [c for c in concentration if c.count_after > c.limit]
    if breached:
        return _decide(taxonomy.REJECTED, (taxonomy.REJECT_EXCESSIVE_CONCENTRATION,),
                        concentration=concentration, capital_available=round(capital_available, 2),
                        why_rejected=tuple(f"{c.dimension}={c.key}: {c.count_after} > limit {c.limit}" for c in breached),
                        dominant=taxonomy.REJECT_EXCESSIVE_CONCENTRATION,
                        what_would_change=("reduce existing positions in the same dimension, or raise the configured limit",))

    if delta_after is not None and abs(delta_after) > _config.MAX_ABS_PORTFOLIO_DELTA:
        return _decide(taxonomy.REJECTED, (taxonomy.REJECT_EXCESSIVE_DIRECTIONAL_EXPOSURE,),
                        portfolio_greeks=(delta_after, gamma_after, theta_after, vega_after),
                        concentration=concentration, capital_available=round(capital_available, 2),
                        why_rejected=(f"|portfolio_delta_after|={abs(delta_after):.2f} > limit {_config.MAX_ABS_PORTFOLIO_DELTA}",),
                        dominant=taxonomy.REJECT_EXCESSIVE_DIRECTIONAL_EXPOSURE,
                        what_would_change=("an offsetting directional position, or a smaller/neutral structure",))

    if vega_after is not None and abs(vega_after) > _config.MAX_ABS_PORTFOLIO_VEGA:
        return _decide(taxonomy.REJECTED, (taxonomy.REJECT_EXCESSIVE_VOLATILITY_EXPOSURE,),
                        portfolio_greeks=(delta_after, gamma_after, theta_after, vega_after),
                        concentration=concentration, capital_available=round(capital_available, 2),
                        why_rejected=(f"|portfolio_vega_after|={abs(vega_after):.2f} > limit {_config.MAX_ABS_PORTFOLIO_VEGA}",),
                        dominant=taxonomy.REJECT_EXCESSIVE_VOLATILITY_EXPOSURE,
                        what_would_change=("an offsetting volatility position, or a smaller structure",))

    risk_budget_used = round((active_capital_used + capital_required) / total_capital, 4)
    return _decide(
        taxonomy.APPROVED, (), position_size_lots=approved_lots, capital_required=capital_required,
        capital_available=round(capital_available, 2), risk_budget_used=risk_budget_used,
        portfolio_greeks=(delta_after, gamma_after, theta_after, vega_after), concentration=concentration,
        confidence=taxonomy.CONFIDENCE_HIGH,
        why_approved=(
            f"all admission checks passed: DEFINED_RISK-or-allowed policy, confidence {selection_confidence} "
            f"meets floor, {approved_lots} lot(s) sized within capital, concentration and exposure limits both clear",
        ),
    )


def build_admitted_trade(trade: TradeConstructionAssessment, decision: PortfolioConstructionAssessment,
                          spot: float, as_of_date: str, underlying: str = "NIFTY") -> Optional[AdmittedTrade]:
    """Call only when `decision.approval_state == taxonomy.APPROVED` to
    fold the newly-approved trade into the caller's next `PortfolioState`."""
    if decision.approval_state != taxonomy.APPROVED:
        return None
    legs = tuple(_leg_greeks(leg, spot, as_of_date) for leg in trade.legs)
    position_close_date = max(leg.expiry for leg in trade.legs)
    return AdmittedTrade(
        assessment_id=trade.assessment_id, strategy_family=trade.strategy_family, underlying=underlying,
        position_close_date=position_close_date, legs=legs,
        approved_lots=decision.position_size_lots, capital_required=decision.capital_required,
        admitted_date=as_of_date,
    )
