"""Which contracts to capture, and why exactly those.

MEASURED, NOT ASSUMED. Every threshold here comes from the real captured
session of 2026-08-14 (162,150 five-minute option rows, 2,190 contracts,
spot 24,366), analysed 2026-08-17:

  EXPIRY CONCENTRATION      dte=4 weekly  = 94.31% of volume
                            + 2nd weekly  = 99.21% cumulative
                            + 3rd/monthly = 99.87% cumulative
                            6 of 18 expiries traded ZERO contracts all day.

  MONEYNESS (front 3)       band     volume%   openinterest%
                            +/- 500   79.9%      58.4%
                            +/-1000   95.2%      80.3%
                            +/-1500   98.6%      91.8%

Capturing all 2,190 contracts costs ~305 MB/day (~76 GB/year) and the VPS
has 51 GB free -- roughly 167 trading days before the disk is full. The
tiers below hold ~99.5% of volume and ~92% of open interest for ~34 MB/day.

WHY OPEN INTEREST SETS THE BAND, NOT VOLUME. OI is far more dispersed than
volume: at +/-500 points you keep 80% of the volume but only 58% of the OI.
MPPI reads positioning and the structure engines lean on OI walls, so a
band drawn on volume alone would quietly blind them. The front tier is
deliberately wider than trading alone would justify.

WHY POINTS, NOT A STRIKE COUNT. `market_timeseries.subscription` bands by
`strikes_each_side`, which assumes a uniform grid. The real master does not
have one: near expiries step by 50, long-dated LEAPS step by 1500, so
"20 strikes each side" means +/-1000 on one expiry and +/-30000 on another.
A point band means the same thing everywhere.

WHY REAL ROWS, NOT CONSTRUCTED SYMBOLS. This module SELECTS contracts from
the exchange symbol master rather than formatting symbol strings itself.
`subscription.option_symbol()` needs a caller-supplied `expiry_code` and its
own docstring warns a wrong code "would silently stream the wrong contract".
Filtering real rows removes that failure mode: a contract that does not
exist cannot be selected.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

# Roles, in the order they are resolved from the real expiry list.
ROLE_FRONT = "FRONT"
ROLE_SECOND = "SECOND"
ROLE_MONTHLY = "MONTHLY"

# Band half-widths in INDEX POINTS. See the module docstring for the
# measurement each one is drawn from.
DEFAULT_TIERS: Dict[str, int] = {
    ROLE_FRONT: 1500,     # 98.6% volume / 91.8% OI -- trading + OI structure
    ROLE_SECOND: 1000,    # roll behaviour and near term structure
    ROLE_MONTHLY: 500,    # IV-surface anchor only
}

KIND_SPOT = "SPOT"
KIND_VIX = "VIX"
KIND_FUTURE = "FUTURE"
KIND_OPTION = "OPTION"

NIFTY_SPOT_SYMBOL = "NSE:NIFTY50-INDEX"
INDIA_VIX_SYMBOL = "NSE:INDIAVIX-INDEX"


class UniverseConstructionError(Exception):
    """Raised when the universe cannot be built from real inputs.

    Never softened into an empty or partial universe: capturing the wrong
    contracts silently is worse than capturing nothing, because the gap is
    invisible until someone backtests against it months later.
    """


@dataclass(frozen=True)
class CaptureInstrument:
    symbol: str
    kind: str
    role: str                          # which tier selected it (provenance)
    expiry: Optional[str] = None       # ISO date
    strike: Optional[float] = None
    option_type: Optional[str] = None
    lot_size: Optional[int] = None


@dataclass(frozen=True)
class CaptureUniverse:
    as_of_date: str
    spot: float
    atm_strike: int
    instruments: Tuple[CaptureInstrument, ...]
    roles_resolved: Dict[str, str]           # role -> ISO expiry actually used
    collapsed_roles: Tuple[str, ...]         # roles that shared an expiry
    expiries_available: int
    expiries_excluded: int
    notes: Tuple[str, ...] = field(default_factory=tuple)

    @property
    def symbols(self) -> List[str]:
        """Just the strings, for `FyersTickFeed.subscribe(symbols=...)`."""
        return [i.symbol for i in self.instruments]

    def by_kind(self, kind: str) -> List[CaptureInstrument]:
        return [i for i in self.instruments if i.kind == kind]

    def summary(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for i in self.instruments:
            out[i.kind] = out.get(i.kind, 0) + 1
        for role in self.roles_resolved:
            out[f"role:{role}"] = sum(1 for i in self.instruments if i.role == role)
        out["total"] = len(self.instruments)
        return out


def atm_strike(spot: float, step: int = 50) -> int:
    """Nearest strike on the `step` grid. Mirrors
    `market_timeseries.subscription.atm_strike` deliberately -- same rule,
    so a capture band and a subscription band centre identically."""
    if spot <= 0:
        raise UniverseConstructionError(
            f"spot must be a real observed level, got {spot!r} -- refusing to "
            "centre a capture band on an invented price."
        )
    return int(round(spot / step) * step)


def is_monthly_expiry(expiry: date, all_expiries: Sequence[date]) -> bool:
    """A monthly expiry is the LAST expiry within its calendar month.

    Same definition `scripts/gate1/build_universe.py` established against the
    real master, restated here rather than imported so this module has no
    dependency on a scripts/ path.
    """
    same_month = [e for e in all_expiries if (e.year, e.month) == (expiry.year, expiry.month)]
    return bool(same_month) and expiry == max(same_month)


def upcoming_expiries(expiries: Sequence[date], as_of: date) -> List[date]:
    upcoming = sorted(e for e in expiries if e >= as_of)
    if not upcoming:
        raise UniverseConstructionError(
            f"no expiry on/after {as_of.isoformat()} in the symbol master -- "
            "refusing to capture against a stale or empty contract list."
        )
    return upcoming


def nearest_monthly(expiries: Sequence[date], as_of: date) -> Optional[date]:
    """The soonest monthly expiry, ignoring whether another role holds it."""
    upcoming = upcoming_expiries(expiries, as_of)
    return next((e for e in upcoming if is_monthly_expiry(e, upcoming)), None)


def select_expiries(expiries: Sequence[date], as_of: date) -> Dict[str, date]:
    """Assign FRONT / SECOND / MONTHLY from the REAL expiry list.

    Nothing is guessed: an expiry the master does not contain yields no
    entry for that role, and the caller sees the gap in `roles_resolved`.

    MONTHLY REACHES FORWARD. In expiry week the nearest monthly IS the front
    or second weekly. Assigning it anyway left the monthly tier holding zero
    contracts (observed for real on 2026-08-17: FRONT 2026-08-18, SECOND and
    MONTHLY both 2026-08-25), which defeats the tier's entire purpose -- it
    exists to anchor the IV surface BEYOND the weeklies, so a monthly that
    duplicates a weekly anchors nothing. It therefore advances to the next
    monthly not already taken.

    Reaching forward is skipped, not faked, when the master lists no further
    monthly: a missing anchor is disclosed through the absent role rather
    than filled with a weekly wearing a monthly label.
    """
    upcoming = upcoming_expiries(expiries, as_of)
    roles: Dict[str, date] = {ROLE_FRONT: upcoming[0]}
    if len(upcoming) > 1:
        roles[ROLE_SECOND] = upcoming[1]
    taken = set(roles.values())
    monthly = next(
        (e for e in upcoming if is_monthly_expiry(e, upcoming) and e not in taken), None)
    if monthly is not None:
        roles[ROLE_MONTHLY] = monthly
    return roles


@dataclass(frozen=True)
class CapturePlan:
    """The tier POLICY resolved against a real expiry list and a real spot.

    Deliberately separate from `CaptureUniverse`: the policy is decided
    once, but it has two very different consumers. The offline planner
    resolves it against the exchange symbol master to enumerate contracts;
    the live capture applies it as a filter to rows FYERS is streaming
    back. Both must agree exactly, so the band arithmetic lives here once
    rather than being restated at each call site.
    """
    as_of_date: str
    spot: float
    atm_strike: int
    band_by_expiry: Dict[date, int]
    role_by_expiry: Dict[date, str]
    roles_resolved: Dict[str, date]
    collapsed_roles: Tuple[str, ...]
    notes: Tuple[str, ...]

    def accepts(self, expiry: date, strike: float) -> bool:
        """Is this contract inside its expiry's band? False for an expiry
        no tier selected -- absence of a band is a rejection, never an
        unbounded accept."""
        band = self.band_by_expiry.get(expiry)
        if band is None:
            return False
        return abs(float(strike) - self.atm_strike) <= band

    @property
    def expiries(self) -> Tuple[date, ...]:
        return tuple(sorted(self.band_by_expiry))


def plan_capture(
    expiries: Sequence[date], spot: float, as_of: date, *,
    tiers: Optional[Dict[str, int]] = None, step: int = 50,
) -> CapturePlan:
    """Resolve roles and bands. No contracts involved -- policy only."""
    tiers = dict(tiers or DEFAULT_TIERS)
    atm = atm_strike(spot, step)
    roles = select_expiries(expiries, as_of)

    # Widest band per expiry, so a collapsed role never truncates coverage.
    band_by_expiry: Dict[date, int] = {}
    role_by_expiry: Dict[date, str] = {}
    for role, expiry in roles.items():
        band = tiers.get(role)
        if band is None:
            continue
        if band > band_by_expiry.get(expiry, -1):
            band_by_expiry[expiry] = band
            role_by_expiry[expiry] = role

    collapsed = tuple(sorted(
        role for role, expiry in roles.items() if role_by_expiry.get(expiry) != role
    ))
    notes: List[str] = []
    if collapsed:
        notes.append(
            f"roles {', '.join(collapsed)} share an expiry with a wider tier "
            "(normal during expiry week); widest band applied, contracts emitted once."
        )
    soonest_monthly = nearest_monthly(expiries, as_of)
    selected_monthly = roles.get(ROLE_MONTHLY)
    if selected_monthly is not None and soonest_monthly != selected_monthly:
        notes.append(
            f"MONTHLY reached forward from {soonest_monthly.isoformat()} to "
            f"{selected_monthly.isoformat()} (dte={(selected_monthly - as_of).days}) "
            "because the soonest monthly is already a weekly role; a monthly that "
            "duplicates a weekly anchors no term structure."
        )
    elif ROLE_MONTHLY in tiers and selected_monthly is None:
        notes.append(
            "no MONTHLY anchor captured: the master lists no monthly expiry beyond "
            "the weekly roles. Term structure is unobservable today -- disclosed, "
            "not substituted."
        )
    return CapturePlan(
        as_of_date=as_of.isoformat(), spot=spot, atm_strike=atm,
        band_by_expiry=band_by_expiry, role_by_expiry=role_by_expiry,
        roles_resolved=roles, collapsed_roles=collapsed, notes=tuple(notes),
    )


def build_capture_universe(
    option_rows: Iterable,
    spot: float,
    as_of: date,
    *,
    tiers: Optional[Dict[str, int]] = None,
    step: int = 50,
    futures_symbol: Optional[str] = None,
    include_vix: bool = True,
    spot_symbol: str = NIFTY_SPOT_SYMBOL,
) -> CaptureUniverse:
    """The exact instrument set to capture today.

    `option_rows` are real rows from `bujji.broker.instrument_master`
    (anything exposing `.symbol`, `.strike`, `.option_type`, `.expiry_date`,
    `.lot_size`). Deterministic and side-effect free: same inputs, same
    universe, so a replay reproduces the capture set exactly.

    When two roles resolve to the SAME expiry the WIDER band wins and the
    contract is emitted once. Emitting it twice would double-subscribe and
    silently inflate every per-symbol tick-rate measurement taken from it.
    """
    rows = list(option_rows)
    if not rows:
        raise UniverseConstructionError(
            "symbol master returned no option rows -- refusing to build an "
            "empty capture universe."
        )
    all_expiries = sorted({r.expiry_date for r in rows})
    plan = plan_capture(all_expiries, spot, as_of, tiers=tiers, step=step)
    atm, roles = plan.atm_strike, plan.roles_resolved

    instruments: List[CaptureInstrument] = [
        CaptureInstrument(symbol=spot_symbol, kind=KIND_SPOT, role="INDEX")
    ]
    if include_vix:
        instruments.append(
            CaptureInstrument(symbol=INDIA_VIX_SYMBOL, kind=KIND_VIX, role="INDEX"))
    if futures_symbol:
        instruments.append(
            CaptureInstrument(symbol=futures_symbol, kind=KIND_FUTURE, role="INDEX"))

    seen: set = set()
    for row in sorted(rows, key=lambda r: (r.expiry_date, r.strike, r.option_type)):
        if not plan.accepts(row.expiry_date, row.strike):
            continue                       # wrong expiry, or outside its band
        if row.option_type not in ("CE", "PE"):
            continue
        if row.symbol in seen:
            continue
        seen.add(row.symbol)
        instruments.append(CaptureInstrument(
            symbol=row.symbol, kind=KIND_OPTION,
            role=plan.role_by_expiry[row.expiry_date],
            expiry=row.expiry_date.isoformat(), strike=float(row.strike),
            option_type=row.option_type, lot_size=getattr(row, "lot_size", None),
        ))

    if not any(i.kind == KIND_OPTION for i in instruments):
        raise UniverseConstructionError(
            f"no option contract fell within any tier band around ATM {atm} "
            f"(spot {spot}) -- the spot and the symbol master disagree; "
            "refusing to capture a chain with no strikes."
        )

    return CaptureUniverse(
        as_of_date=as_of.isoformat(), spot=spot, atm_strike=atm,
        instruments=tuple(instruments), roles_resolved={r: e.isoformat() for r, e in roles.items()},
        collapsed_roles=plan.collapsed_roles, expiries_available=len(all_expiries),
        expiries_excluded=len(all_expiries) - len(plan.band_by_expiry),
        notes=plan.notes,
    )
