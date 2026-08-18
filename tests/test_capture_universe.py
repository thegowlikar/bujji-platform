"""The capture universe: what gets recorded from here on.

These tests pin the properties that make the universe trustworthy months
later, when nobody remembers why a contract was or was not captured. The
band thresholds themselves are measured evidence (see the module docstring),
so a test that merely re-asserted the constants would prove nothing; these
assert BEHAVIOUR instead -- that bands are point-based, that roles collapse
without duplicating, and that every failure is loud.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional

import pytest

from bujji.capture_universe.builder import (
    DEFAULT_TIERS,
    KIND_FUTURE,
    KIND_OPTION,
    KIND_SPOT,
    KIND_VIX,
    ROLE_FRONT,
    ROLE_MONTHLY,
    ROLE_SECOND,
    UniverseConstructionError,
    atm_strike,
    build_capture_universe,
    is_monthly_expiry,
    select_expiries,
)


@dataclass(frozen=True)
class Row:
    """Mirrors instrument_master.OptionRow's consumed surface."""
    symbol: str
    strike: float
    option_type: str
    expiry_date: date
    lot_size: int = 75


def chain(expiry: date, low: int, high: int, step: int = 50):
    rows = []
    for k in range(low, high + 1, step):
        for ot in ("CE", "PE"):
            rows.append(Row(f"NSE:NIFTY{expiry:%y%m%d}{k}{ot}", float(k), ot, expiry))
    return rows


AS_OF = date(2026, 8, 17)
W1, W2, W3 = date(2026, 8, 18), date(2026, 8, 25), date(2026, 9, 1)
MONTHLY_AUG = date(2026, 8, 25)          # last August expiry in this fixture
SPOT = 24366.0


def _rows(*expiries, low=21800, high=26900):
    out = []
    for e in expiries:
        out.extend(chain(e, low, high))
    return out


class TestAtmCentring:
    def test_rounds_to_the_strike_grid(self):
        assert atm_strike(24366.0) == 24350
        assert atm_strike(24375.0) == 24400          # .5 rounds up on this grid

    def test_a_missing_spot_is_refused_not_defaulted(self):
        """Centring a band on a fabricated spot silently captures the wrong
        strikes -- invisible until someone backtests against it."""
        for bad in (0, -1.0):
            with pytest.raises(UniverseConstructionError, match="real observed"):
                atm_strike(bad)


class TestExpiryResolution:
    def test_monthly_is_the_last_expiry_of_its_calendar_month(self):
        expiries = [W1, W2, W3]
        assert is_monthly_expiry(W2, expiries) is True
        assert is_monthly_expiry(W1, expiries) is False

    def test_front_and_second_come_from_the_real_list(self):
        roles = select_expiries([W1, W2, W3], AS_OF)
        assert roles[ROLE_FRONT] == W1
        assert roles[ROLE_SECOND] == W2

    def test_past_expiries_are_never_selected(self):
        roles = select_expiries([date(2026, 8, 11), W1, W2], AS_OF)
        assert roles[ROLE_FRONT] == W1

    def test_an_empty_forward_calendar_refuses(self):
        with pytest.raises(UniverseConstructionError, match="no expiry"):
            select_expiries([date(2020, 1, 1)], AS_OF)


class TestBandsAreInPointsNotStrikeCounts:
    def test_front_band_matches_the_measured_width(self):
        u = build_capture_universe(_rows(W1, W2, W3), SPOT, AS_OF)
        front = [i for i in u.by_kind(KIND_OPTION) if i.role == ROLE_FRONT]
        offsets = [abs(i.strike - u.atm_strike) for i in front]
        assert max(offsets) <= DEFAULT_TIERS[ROLE_FRONT]

    def test_tiers_get_progressively_narrower(self):
        """Needs a calendar where all three roles are DISTINCT: two weeklies
        early in a month, then that month's last expiry. With W1/W2/W3 the
        August monthly IS the second weekly, so the roles collapse instead
        (covered by TestRoleCollapse)."""
        early, mid, monthly = date(2026, 9, 1), date(2026, 9, 8), date(2026, 9, 29)
        u = build_capture_universe(
            _rows(early, mid, monthly), SPOT, date(2026, 9, 1))
        widths = {}
        for i in u.by_kind(KIND_OPTION):
            widths[i.role] = max(widths.get(i.role, 0), abs(i.strike - u.atm_strike))
        assert set(widths) == {ROLE_FRONT, ROLE_SECOND, ROLE_MONTHLY}, widths
        assert widths[ROLE_FRONT] > widths[ROLE_SECOND] > widths[ROLE_MONTHLY]

    def test_a_coarse_grid_yields_few_strikes_not_a_huge_band(self):
        """The real master steps long-dated LEAPS by 1500 points. A
        count-based band ("20 each side") would reach +/-30,000 there; a
        point band means the same thing on every expiry."""
        leaps = chain(date(2026, 8, 18), 1500, 48000, step=1500)
        u = build_capture_universe(leaps, SPOT, AS_OF)
        for i in u.by_kind(KIND_OPTION):
            assert abs(i.strike - u.atm_strike) <= DEFAULT_TIERS[ROLE_FRONT]


class TestMonthlyReachesForward:
    """The monthly tier exists to anchor the IV surface BEYOND the weeklies.
    A monthly that IS a weekly anchors nothing, so it advances."""

    def test_in_expiry_week_it_skips_to_the_next_monthly(self):
        # Aug 25 is both the 2nd weekly and August's monthly; Sep 29 is the
        # next monthly. Mirrors the real 2026-08-17 master.
        expiries = [W1, W2, W3, date(2026, 9, 8), date(2026, 9, 29)]
        roles = select_expiries(expiries, AS_OF)
        assert roles[ROLE_SECOND] == W2
        assert roles[ROLE_MONTHLY] == date(2026, 9, 29)

    def test_the_monthly_tier_actually_holds_contracts_now(self):
        """The bug this fixes: MONTHLY resolved to an expiry a wider role
        already owned, so its band was overridden and it captured nothing."""
        u = build_capture_universe(
            _rows(W1, W2, W3, date(2026, 9, 8), date(2026, 9, 29)), SPOT, AS_OF)
        monthly = [i for i in u.by_kind(KIND_OPTION) if i.role == ROLE_MONTHLY]
        assert monthly, "monthly anchor captured zero contracts"
        assert {i.expiry for i in monthly} == {"2026-09-29"}

    def test_no_reach_needed_when_the_monthly_is_already_distinct(self):
        expiries = [date(2026, 9, 1), date(2026, 9, 8), date(2026, 9, 29)]
        roles = select_expiries(expiries, date(2026, 9, 1))
        assert roles[ROLE_MONTHLY] == date(2026, 9, 29)

    def test_the_reach_is_disclosed(self):
        u = build_capture_universe(
            _rows(W1, W2, W3, date(2026, 9, 8), date(2026, 9, 29)), SPOT, AS_OF)
        assert any("reached forward" in n for n in u.notes), u.notes

    def test_a_missing_anchor_is_disclosed_not_substituted(self):
        """With only weeklies available there is no monthly to reach. The
        role stays absent and says so -- never a weekly relabelled."""
        expiries = [W1, W2]
        roles = select_expiries(expiries, AS_OF)
        assert ROLE_MONTHLY not in roles
        u = build_capture_universe(_rows(*expiries), SPOT, AS_OF)
        assert any("no MONTHLY anchor" in n for n in u.notes), u.notes


class TestRoleCollapse:
    def test_expiry_week_collapse_emits_each_contract_once(self):
        """During expiry week the front weekly IS the monthly. Emitting it
        under both roles would double-subscribe and inflate every per-symbol
        tick-rate measurement taken from the capture."""
        u = build_capture_universe(_rows(W1, W2), SPOT, AS_OF)
        symbols = [i.symbol for i in u.by_kind(KIND_OPTION)]
        assert len(symbols) == len(set(symbols))

    def test_collapse_is_disclosed_not_hidden(self):
        u = build_capture_universe(_rows(W1, W2), SPOT, AS_OF)
        if u.collapsed_roles:
            assert u.notes, "a collapsed role must be explained"

    def test_the_wider_band_wins_when_roles_share_an_expiry(self):
        """Otherwise a MONTHLY role landing on the front expiry would
        silently truncate the front tier from 1500 to 500 points."""
        u = build_capture_universe(_rows(W2), SPOT, AS_OF)
        offsets = [abs(i.strike - u.atm_strike) for i in u.by_kind(KIND_OPTION)]
        assert max(offsets) > DEFAULT_TIERS[ROLE_MONTHLY]


class TestOnlyRealContracts:
    def test_symbols_come_from_the_master_never_constructed(self):
        rows = _rows(W1)
        u = build_capture_universe(rows, SPOT, AS_OF)
        master_symbols = {r.symbol for r in rows}
        for i in u.by_kind(KIND_OPTION):
            assert i.symbol in master_symbols

    def test_a_gap_in_the_chain_is_simply_absent(self):
        """A strike the exchange does not list cannot be captured, and must
        not be fabricated to make the band look symmetric."""
        rows = [r for r in _rows(W1) if r.strike != 24350.0]
        u = build_capture_universe(rows, SPOT, AS_OF)
        assert 24350.0 not in {i.strike for i in u.by_kind(KIND_OPTION)}

    def test_lot_size_is_carried_from_the_master(self):
        u = build_capture_universe(_rows(W1), SPOT, AS_OF)
        assert all(i.lot_size == 75 for i in u.by_kind(KIND_OPTION))


class TestFailsClosed:
    def test_no_rows_refuses(self):
        with pytest.raises(UniverseConstructionError, match="no option rows"):
            build_capture_universe([], SPOT, AS_OF)

    def test_a_spot_far_from_every_listed_strike_refuses(self):
        """Spot and master disagreeing means one of them is wrong. Capturing
        an empty chain would look like a quiet market rather than a fault."""
        with pytest.raises(UniverseConstructionError, match="no option contract"):
            build_capture_universe(_rows(W1), 90000.0, AS_OF)


class TestNonOptionLegs:
    def test_spot_and_vix_are_always_included(self):
        u = build_capture_universe(_rows(W1), SPOT, AS_OF)
        assert len(u.by_kind(KIND_SPOT)) == 1
        assert len(u.by_kind(KIND_VIX)) == 1

    def test_futures_included_only_when_a_real_symbol_is_supplied(self):
        """This module never derives a futures symbol -- resolving it is
        `instrument_master.resolve_nearest_future()`'s job."""
        assert build_capture_universe(_rows(W1), SPOT, AS_OF).by_kind(KIND_FUTURE) == []
        u = build_capture_universe(_rows(W1), SPOT, AS_OF, futures_symbol="NSE:NIFTY26AUGFUT")
        assert len(u.by_kind(KIND_FUTURE)) == 1


class TestDeterminism:
    def test_same_inputs_produce_the_same_universe(self):
        """A replay must reproduce the exact capture set, or the recorded
        data cannot be explained after the fact."""
        rows = _rows(W1, W2, W3)
        a = build_capture_universe(rows, SPOT, AS_OF)
        b = build_capture_universe(list(reversed(rows)), SPOT, AS_OF)
        assert a.symbols == b.symbols

    def test_the_band_follows_spot(self):
        """A frozen band decays as the market moves away from it."""
        low = build_capture_universe(_rows(W1), 23000.0, AS_OF)
        high = build_capture_universe(_rows(W1), 25500.0, AS_OF)
        assert low.atm_strike != high.atm_strike
        assert set(low.symbols) != set(high.symbols)
