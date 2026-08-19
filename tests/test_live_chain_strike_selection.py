"""Strike selection must work on a LIVE chain, not only on a bhavcopy.

THE DEFECT (found 2026-08-19). The construction engine read `row.settlement`
and nothing else. The bhavcopy replay provider populates settlement; the live
chain provider explicitly does NOT -- it sets settlement=None and puts the
traded price in `close`. So on live data every strike resolved to
premium=None -> iv=None -> delta=None -> ZERO candidates, and every live
entry attempt died with REJECT_STRIKE_UNAVAILABLE.

Bujji could not construct a trade on live data AT ALL. Nothing caught it
because the entire suite drives the bhavcopy path -- a live-only failure
behind a fully green test run, which is precisely what a shape-specific
fixture hides. These tests exercise BOTH shapes so it cannot come back.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from bujji.msi_trade_construction.engine import (PREMIUM_LAST_TRADE, PREMIUM_MID,
                                                 PREMIUM_SETTLEMENT, _build_strike_evidence,
                                                 _premium_for)
from bujji.options_observation.engine import build_option_observation

EXPIRY = "2026-08-25"
SPOT = 24113.0
T = 0.0164


def _row(strike, option_type, *, settlement=None, close=None, bid=None, ask=None):
    return build_option_observation(
        underlying="NIFTY", instrument_symbol=f"NSE:NIFTY{int(strike)}{option_type}",
        strike=float(strike), expiry=EXPIRY, option_type=option_type,
        exchange="NSE", segment="FO", timestamp="2026-08-19T15:28:00+05:30",
        resolution="SNAPSHOT", open_=None, high=None, low=None,
        close=close, settlement=settlement,
        volume=1000, open_interest=50000, change_in_open_interest=100,
        underlying_price=SPOT, origin="test",
        acquisition_timestamp="x", normalization_timestamp="x", bid=bid, ask=ask)


def _chain(shape: str):
    """`shape` is 'bhavcopy' (settlement) or 'live' (close + two-sided book)."""
    rows = []
    for i in range(11):
        strike = 23600 + 100 * i
        for option_type in ("CE", "PE"):
            fair = max(5.0, (SPOT - strike if option_type == "CE" else strike - SPOT) + 120)
            if shape == "bhavcopy":
                rows.append(_row(strike, option_type, settlement=fair))
            else:
                rows.append(_row(strike, option_type, close=fair,
                                 bid=fair - 0.5, ask=fair + 0.5))
    return rows


class TestALiveChainProducesCandidates:
    def test_the_live_shape_now_yields_iv_and_delta(self):
        """The regression that mattered: before the fix this was 0 of 22."""
        evidence = _build_strike_evidence(_chain("live"), EXPIRY, SPOT, T)
        with_delta = [e for e in evidence.values() if e.delta is not None]
        assert len(with_delta) == len(evidence) > 0

    def test_the_bhavcopy_shape_is_unchanged(self):
        evidence = _build_strike_evidence(_chain("bhavcopy"), EXPIRY, SPOT, T)
        assert all(e.delta is not None for e in evidence.values())
        assert all(e.premium_basis == PREMIUM_SETTLEMENT for e in evidence.values())

    def test_both_shapes_agree_on_the_same_underlying_prices(self):
        """Same fair values, two carriers: the IVs must match. If they did
        not, the fix would be changing decisions rather than enabling them."""
        live = _build_strike_evidence(_chain("live"), EXPIRY, SPOT, T)
        bhav = _build_strike_evidence(_chain("bhavcopy"), EXPIRY, SPOT, T)
        for key, b in bhav.items():
            assert live[key].iv == pytest.approx(b.iv, rel=1e-9)


class TestThePriorityOrderIsNonRegressive:
    def test_settlement_wins_when_present(self):
        """Settlement stays FIRST so every bhavcopy decision, replay and test
        is bit-for-bit unchanged. The new sources are reached only where
        settlement is absent -- exactly the live case."""
        row = _row(24100, "CE", settlement=100.0, close=200.0, bid=290.0, ask=310.0)
        assert _premium_for(row) == (100.0, PREMIUM_SETTLEMENT)

    def test_mid_is_preferred_over_the_last_trade(self):
        """A print on a far strike can be minutes old while the book has
        moved, and a stale price implies a stale volatility."""
        row = _row(24100, "CE", close=200.0, bid=290.0, ask=310.0)
        assert _premium_for(row) == (300.0, PREMIUM_MID)

    def test_the_last_trade_is_the_final_fallback(self):
        row = _row(24100, "CE", close=200.0)
        assert _premium_for(row) == (200.0, PREMIUM_LAST_TRADE)

    def test_a_one_sided_book_does_not_count_as_a_mid(self):
        row = _row(24100, "CE", close=200.0, bid=290.0)
        assert _premium_for(row) == (200.0, PREMIUM_LAST_TRADE)


class TestAbsenceStaysAbsence:
    def test_a_row_with_no_usable_price_yields_nothing(self):
        """Never defaulted to zero: a zero premium would imply a volatility
        and put a fabricated strike into the candidate set."""
        assert _premium_for(_row(24100, "CE")) == (None, None)

    def test_non_positive_prices_are_not_prices(self):
        assert _premium_for(_row(24100, "CE", settlement=0.0, close=0.0)) == (None, None)
        assert _premium_for(_row(24100, "CE", close=-5.0)) == (None, None)

    def test_a_priceless_row_produces_no_candidate(self):
        rows = _chain("live") + [_row(25000, "CE")]
        evidence = _build_strike_evidence(rows, EXPIRY, SPOT, T)
        assert evidence[(25000.0, "CE")].delta is None
        assert evidence[(25000.0, "CE")].premium_basis is None

    def test_the_basis_is_recorded_on_every_evidence_row(self):
        """An IV is only as current as the price behind it; a reader of the
        reasoning must be able to tell which price a strike choice rests on."""
        evidence = _build_strike_evidence(_chain("live"), EXPIRY, SPOT, T)
        assert all(e.premium_basis == PREMIUM_MID for e in evidence.values())
