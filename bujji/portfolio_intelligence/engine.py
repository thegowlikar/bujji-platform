"""Portfolio Intelligence Engine -- Phase 15M. Pure functions, no
state, no IO, no broker, no execution, no strategy selection.

`build_portfolio_snapshot` is the ONLY entry point most callers need --
it takes the SAME `Dict[position_id, PositionLifecycle]` that
`bujji.position_lifecycle.recovery.hydrate_position_lifecycles` (or a
live session) already produces, and returns a `PortfolioSnapshot`.
This module NEVER reads `EventStore` itself, NEVER calls PaperBroker,
NEVER computes a NEW P&L number (reuses `PositionLifecycle.
realized_pnl`/`structured_exit`, already computed by
`bujji.position_lifecycle.pnl` in Phase 15K) -- it is aggregation only,
exactly per the mission's own "do NOT create a second position state
machine" / "aggregation layer only" instruction.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from .models import (
    ALL_AGGREGATE_STATUSES, CONFLICT_CORRELATED_EXPOSURE, CONFLICT_DIRECTIONAL, CONFLICT_EXPIRY_CONCENTRATION,
    CONFLICT_GREEK_CONCENTRATION, CONFLICT_NO_CONFLICT, CONFLICT_UNDERLYING_CONCENTRATION, CONFLICT_UNKNOWN,
    STATUS_KNOWN, STATUS_PARTIAL, STATUS_UNKNOWN, SCHEMA_VERSION,
    THESIS_HEALTH_ALL_INTACT, THESIS_HEALTH_DETERIORATING, THESIS_HEALTH_MIXED,
    THESIS_HEALTH_NO_OPEN_POSITIONS, THESIS_HEALTH_UNKNOWN,
    CapitalOverlay, ConcentrationEntry, GreekExposure, PortfolioConflictFinding, PortfolioPnL, PortfolioSnapshot,
    PremiumExposure,
)

_SIDE_SIGN = {"BUY": 1, "SELL": -1}
_INTACT_STATUSES = {"THESIS_INTACT"}
_WEAKENING_STATUSES = {"THESIS_WEAKENING"}
_INVALIDATED_STATUSES = {"THESIS_INVALIDATED"}


def _leg_sign(leg) -> Optional[int]:
    return _SIDE_SIGN.get(leg.side)


def _aggregate_net_delta(open_lifecycles) -> GreekExposure:
    """Uses `LegRecord.entry_delta` DIRECTLY -- the real, strike-
    accurate per-leg delta (Phase 14's `ShadowTradeLeg.delta`). A leg
    with no resolved delta contributes nothing and demotes the overall
    status to PARTIAL (or UNKNOWN if none resolved)."""
    total = 0.0
    resolved = 0
    total_legs = 0
    for lc in open_lifecycles:
        lot_size = lc.entry.lot_size
        for leg in lc.legs:
            total_legs += 1
            sign = _leg_sign(leg)
            if leg.entry_delta is None or leg.quantity is None or lot_size is None or sign is None:
                continue
            total += sign * leg.entry_delta * leg.quantity * lot_size
            resolved += 1
    if total_legs == 0:
        return GreekExposure(None, STATUS_UNKNOWN)
    if resolved == 0:
        return GreekExposure(None, STATUS_UNKNOWN)
    if resolved < total_legs:
        return GreekExposure(round(total, 6), STATUS_PARTIAL)
    return GreekExposure(round(total, 6), STATUS_KNOWN)


def _aggregate_higher_order_greek(open_lifecycles, greek_key: str) -> GreekExposure:
    """Gamma/theta/vega exist only as a single ATM CE/PE snapshot per
    position (`EntrySnapshot.entry_greeks`), captured once at entry --
    NOT resolved per-leg-strike. A leg only contributes if its OWN
    strike matches that snapshot's `strike` field exactly; otherwise
    the leg's contribution is honestly UNKNOWN (a strike mismatch, not
    a missing value) -- never approximated from the wrong strike."""
    total = 0.0
    resolved = 0
    total_legs = 0
    for lc in open_lifecycles:
        entry_greeks = lc.entry.entry_greeks
        lot_size = lc.entry.lot_size
        snapshot_strike = entry_greeks.get("strike") if entry_greeks else None
        for leg in lc.legs:
            total_legs += 1
            sign = _leg_sign(leg)
            if (entry_greeks is None or snapshot_strike is None or leg.quantity is None
                    or lot_size is None or sign is None or leg.strike != snapshot_strike):
                continue
            leg_greeks = entry_greeks.get(leg.option_type.lower())
            if not leg_greeks:
                continue
            value = leg_greeks.get(greek_key)
            if value is None:
                continue
            total += sign * value * leg.quantity * lot_size
            resolved += 1
    if total_legs == 0 or resolved == 0:
        return GreekExposure(None, STATUS_UNKNOWN)
    if resolved < total_legs:
        return GreekExposure(round(total, 6), STATUS_PARTIAL)
    return GreekExposure(round(total, 6), STATUS_KNOWN)


def _aggregate_premium_exposure(open_lifecycles) -> PremiumExposure:
    bought = 0.0
    sold = 0.0
    resolved = 0
    total_legs = 0
    for lc in open_lifecycles:
        lot_size = lc.entry.lot_size
        for leg in lc.legs:
            total_legs += 1
            if leg.entry_premium is None or leg.quantity is None or lot_size is None:
                continue
            notional = leg.entry_premium * leg.quantity * lot_size
            if leg.side == "BUY":
                bought += notional
            elif leg.side == "SELL":
                sold += notional
            else:
                continue
            resolved += 1
    if total_legs == 0 or resolved == 0:
        return PremiumExposure(None, None, STATUS_UNKNOWN)
    status = STATUS_KNOWN if resolved == total_legs else STATUS_PARTIAL
    return PremiumExposure(round(bought, 2), round(sold, 2), status)


def _capital_deployed(open_lifecycles) -> Tuple[Optional[float], str]:
    """Real, persisted-data-derivable exposure -- sum of the absolute
    entry premium notional across OPEN legs. NOT the same as broker
    margin/equity (see CapitalOverlay's own docstring: that requires a
    live broker read this pure snapshot never has)."""
    total = 0.0
    resolved = 0
    total_legs = 0
    for lc in open_lifecycles:
        lot_size = lc.entry.lot_size
        for leg in lc.legs:
            total_legs += 1
            if leg.entry_premium is None or leg.quantity is None or lot_size is None:
                continue
            total += abs(leg.entry_premium * leg.quantity * lot_size)
            resolved += 1
    if total_legs == 0 or resolved == 0:
        return None, STATUS_UNKNOWN
    return round(total, 2), (STATUS_KNOWN if resolved == total_legs else STATUS_PARTIAL)


def _concentration(lifecycles, key_fn) -> Tuple[ConcentrationEntry, ...]:
    groups: Dict[str, List[str]] = {}
    for lc in lifecycles:
        key = key_fn(lc)
        if key is None:
            continue
        groups.setdefault(key, []).append(lc.position_id)
    return tuple(
        ConcentrationEntry(key=k, position_count=len(v), position_ids=tuple(v))
        for k, v in sorted(groups.items()) if len(v) >= 1
    )


def _build_pnl(all_lifecycles) -> PortfolioPnL:
    gross_parts, net_parts, fee_parts, slip_parts = [], [], [], []
    all_gross_known = all_net_known = all_fees_known = all_slip_known = True
    per_position: Dict[str, Optional[float]] = {}
    contributing = 0
    for lc in all_lifecycles:
        per_position[lc.position_id] = lc.realized_pnl
        se = lc.structured_exit
        if se is None:
            continue
        contributing += 1
        if se.get("gross_realized_pnl") is not None:
            gross_parts.append(se["gross_realized_pnl"])
        else:
            all_gross_known = False
        if se.get("net_realized_pnl") is not None:
            net_parts.append(se["net_realized_pnl"])
        else:
            all_net_known = False
        if se.get("fees") is not None:
            fee_parts.append(se["fees"])
        else:
            all_fees_known = False
        if se.get("slippage") is not None:
            slip_parts.append(se["slippage"])
        else:
            all_slip_known = False

    if contributing == 0:
        status = STATUS_UNKNOWN
    elif all_gross_known:
        status = STATUS_KNOWN
    elif gross_parts:
        status = STATUS_PARTIAL
    else:
        status = STATUS_UNKNOWN

    return PortfolioPnL(
        realized_gross_pnl=(round(sum(gross_parts), 2) if (gross_parts and all_gross_known) else None),
        realized_net_pnl=(round(sum(net_parts), 2) if (net_parts and all_net_known) else None),
        total_fees=(round(sum(fee_parts), 2) if (fee_parts and all_fees_known) else None),
        total_slippage=(round(sum(slip_parts), 2) if (slip_parts and all_slip_known) else None),
        unrealized_pnl=None, unrealized_pnl_status=STATUS_UNKNOWN,
        status=status, per_position=per_position,
    )


def detect_conflicts(open_lifecycles) -> Tuple[PortfolioConflictFinding, ...]:
    """Describes the book -- never recommends or takes an action (Step
    5's own explicit instruction). Every finding is derived from REAL,
    already-persisted fields (`entry_direction`, `underlying_symbol`,
    leg `expiry`, leg `side`) -- nothing here is inferred or guessed."""
    if not open_lifecycles:
        return (PortfolioConflictFinding(CONFLICT_NO_CONFLICT, "no open positions", ()),)

    findings: List[PortfolioConflictFinding] = []

    # DIRECTIONAL_CONFLICT: same underlying, opposing entry_direction.
    by_underlying: Dict[str, List] = {}
    for lc in open_lifecycles:
        u = lc.entry.underlying_symbol
        if u is not None:
            by_underlying.setdefault(u, []).append(lc)
    for underlying, group in by_underlying.items():
        directions = {lc.entry.entry_direction for lc in group if lc.entry.entry_direction is not None}
        if {"BULLISH", "BEARISH"} <= directions:
            findings.append(PortfolioConflictFinding(
                CONFLICT_DIRECTIONAL, f"{underlying}: both BULLISH and BEARISH entry_direction open simultaneously",
                tuple(lc.position_id for lc in group),
            ))
        if len(group) >= 2:
            findings.append(PortfolioConflictFinding(
                CONFLICT_UNDERLYING_CONCENTRATION, f"{underlying}: {len(group)} concurrent open positions",
                tuple(lc.position_id for lc in group),
            ))

    # EXPIRY_CONCENTRATION: >=2 open positions sharing an identical expiry across ANY leg.
    by_expiry: Dict[str, set] = {}
    for lc in open_lifecycles:
        expiries = {leg.expiry for leg in lc.legs}
        for exp in expiries:
            by_expiry.setdefault(exp, set()).add(lc.position_id)
    for expiry, pids in by_expiry.items():
        if len(pids) >= 2:
            findings.append(PortfolioConflictFinding(
                CONFLICT_EXPIRY_CONCENTRATION, f"{expiry}: {len(pids)} open positions share this expiry", tuple(sorted(pids)),
            ))

    # CORRELATED_EXPOSURE: >=2 open positions where EVERY leg is SELL (correlated short-premium risk).
    all_short = [lc for lc in open_lifecycles if lc.legs and all(leg.side == "SELL" for leg in lc.legs)]
    if len(all_short) >= 2:
        findings.append(PortfolioConflictFinding(
            CONFLICT_CORRELATED_EXPOSURE, f"{len(all_short)} positions are entirely short-premium -- correlated volatility risk",
            tuple(lc.position_id for lc in all_short),
        ))

    # GREEK_CONCENTRATION: >=3 open positions whose net delta all share the same non-zero sign
    # (a conservative, documented default -- not a fabricated risk threshold).
    net_delta = _aggregate_net_delta(open_lifecycles)
    per_position_delta = []
    for lc in open_lifecycles:
        d = _aggregate_net_delta([lc])
        if d.status != STATUS_UNKNOWN and d.net is not None and d.net != 0:
            per_position_delta.append((lc.position_id, d.net))
    same_sign_positive = [pid for pid, v in per_position_delta if v > 0]
    same_sign_negative = [pid for pid, v in per_position_delta if v < 0]
    if len(same_sign_positive) >= 3:
        findings.append(PortfolioConflictFinding(
            CONFLICT_GREEK_CONCENTRATION, f"{len(same_sign_positive)} open positions all carry positive net delta", tuple(same_sign_positive),
        ))
    if len(same_sign_negative) >= 3:
        findings.append(PortfolioConflictFinding(
            CONFLICT_GREEK_CONCENTRATION, f"{len(same_sign_negative)} open positions all carry negative net delta", tuple(same_sign_negative),
        ))

    if not findings:
        return (PortfolioConflictFinding(CONFLICT_NO_CONFLICT, "no directional/concentration/correlation finding detected", ()),)
    return tuple(findings)


def _thesis_health(open_lifecycles) -> Tuple[str, dict]:
    if not open_lifecycles:
        return THESIS_HEALTH_NO_OPEN_POSITIONS, {"intact": 0, "weakening": 0, "invalidated": 0, "unknown": 0}
    intact = weakening = invalidated = unknown = 0
    for lc in open_lifecycles:
        status = lc.final_thesis_status
        if status in _INTACT_STATUSES:
            intact += 1
        elif status in _WEAKENING_STATUSES:
            weakening += 1
        elif status in _INVALIDATED_STATUSES:
            invalidated += 1
        else:
            unknown += 1
    detail = {"intact": intact, "weakening": weakening, "invalidated": invalidated, "unknown": unknown}
    total = len(open_lifecycles)
    if unknown == total:
        return THESIS_HEALTH_UNKNOWN, detail
    if invalidated > 0 or weakening > 0:
        health = THESIS_HEALTH_DETERIORATING if invalidated > 0 else THESIS_HEALTH_MIXED
        return health, detail
    if intact == total:
        return THESIS_HEALTH_ALL_INTACT, detail
    return THESIS_HEALTH_MIXED, detail


def _management_summary(open_lifecycles) -> Dict[str, int]:
    summary: Dict[str, int] = {}
    for lc in open_lifecycles:
        rec = lc.latest_management_recommendation or "UNKNOWN"
        summary[rec] = summary.get(rec, 0) + 1
    return summary


def build_portfolio_snapshot(
    states: Dict[str, object], session_id: str, as_of: Optional[str] = None,
    capital: Optional[CapitalOverlay] = None,
) -> PortfolioSnapshot:
    """`states`: the SAME `Dict[position_id, PositionLifecycle]` that
    `hydrate_position_lifecycles`/a live session already maintains --
    no new discovery mechanism. `capital`: an OPTIONAL, caller-supplied
    real broker read (`PaperBroker.get_funds()`) -- never fabricated;
    omit entirely for a pure, replay-safe snapshot."""
    all_lifecycles = list(states.values())
    open_lifecycles = [lc for lc in all_lifecycles if lc.status == "OPEN"]
    closed_lifecycles = [lc for lc in all_lifecycles if lc.status == "CLOSED"]

    thesis_health, thesis_detail = _thesis_health(open_lifecycles)

    return PortfolioSnapshot(
        session_id=session_id, as_of=as_of, schema_version=SCHEMA_VERSION,
        position_count=len(all_lifecycles),
        open_position_ids=tuple(lc.position_id for lc in open_lifecycles),
        closed_position_ids=tuple(lc.position_id for lc in closed_lifecycles),
        net_delta=_aggregate_net_delta(open_lifecycles),
        net_gamma=_aggregate_higher_order_greek(open_lifecycles, "gamma"),
        net_theta=_aggregate_higher_order_greek(open_lifecycles, "theta_per_day"),
        net_vega=_aggregate_higher_order_greek(open_lifecycles, "vega_per_pct"),
        premium_exposure=_aggregate_premium_exposure(open_lifecycles),
        capital_deployed=_capital_deployed(open_lifecycles)[0],
        capital_deployed_status=_capital_deployed(open_lifecycles)[1],
        concentration_by_underlying=_concentration(open_lifecycles, lambda lc: lc.entry.underlying_symbol),
        concentration_by_strategy_family=_concentration(open_lifecycles, lambda lc: lc.entry.strategy_family),
        concentration_by_expiry=_concentration(
            open_lifecycles, lambda lc: (sorted({leg.expiry for leg in lc.legs})[0] if lc.legs else None)
        ),
        pnl=_build_pnl(all_lifecycles),
        capital=capital if capital is not None else CapitalOverlay(),
        conflicts=detect_conflicts(open_lifecycles),
        thesis_health=thesis_health, thesis_health_detail=thesis_detail,
        management_recommendation_summary=_management_summary(open_lifecycles),
    )
