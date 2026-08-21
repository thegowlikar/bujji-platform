"""IV, Greeks and skew: derived from observed prices, never estimated.

The FYERS chain returns no implied volatility, so a premium seller had no
delta, no vega, no gamma and no skew. The fix is legitimate only because
implied vol is BY DEFINITION the volatility that reproduces a printed price
-- inverting an observation, not inventing one -- and because the forward it
is inverted against is recovered from the same chain by put-call parity
rather than assumed from a rate nobody measured.

These tests pin the maths against identities that must hold, and pin the
refusals: every input the model cannot honestly invert must come back empty
with a reason, never as a boundary value that looks like a reading.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from bujji.options_analytics import (analyse_expiry, estimate_forward, greeks,
                                     implied_volatility, intrinsic, price,
                                     time_to_expiry_years)
from bujji.options_analytics import forward as fwd_mod
from bujji.options_analytics import implied as iv_mod

F, K, T, SIGMA = 24000.0, 24000.0, 0.02, 0.12


class TestTheModelObeysItsOwnIdentities:
    def test_put_call_parity_holds_at_every_strike(self):
        """The identity the forward recovery depends on. If pricing broke
        parity, the recovered forward would be wrong and every IV with it."""
        for strike in (22000.0, 24000.0, 26000.0):
            call = price(F, strike, SIGMA, T, True)
            put = price(F, strike, SIGMA, T, False)
            assert call - put == pytest.approx(F - strike, abs=1e-6)

    def test_price_is_monotonic_in_volatility(self):
        """What makes plain bisection a valid solver rather than a hope."""
        prices = [price(F, K, s, T, True) for s in (0.05, 0.10, 0.20, 0.40)]
        assert prices == sorted(prices)

    def test_price_never_falls_below_intrinsic(self):
        deep = price(F, 20000.0, 0.05, T, True)
        assert deep >= intrinsic(F, 20000.0, True) - 1e-9

    def test_call_and_put_share_gamma_vega_and_theta(self):
        """Same strike, same vol: these three are identical for a call and a
        put. A sign or factor slip shows up here immediately."""
        c = greeks(F, K, SIGMA, T, True)
        p = greeks(F, K, SIGMA, T, False)
        assert c.gamma == pytest.approx(p.gamma, rel=1e-9)
        assert c.vega == pytest.approx(p.vega, rel=1e-9)
        assert c.theta_per_day == pytest.approx(p.theta_per_day, rel=1e-9)

    def test_deltas_are_bounded_and_opposite(self):
        c = greeks(F, K, SIGMA, T, True)
        p = greeks(F, K, SIGMA, T, False)
        assert 0.0 <= c.forward_delta <= 1.0
        assert -1.0 <= p.forward_delta <= 0.0
        # call delta - put delta = DF (1.0 undiscounted)
        assert c.forward_delta - p.forward_delta == pytest.approx(1.0, abs=1e-9)

    def test_vega_is_positive_and_theta_negative_for_a_long_option(self):
        g = greeks(F, K, SIGMA, T, True)
        assert g.vega > 0 and g.theta_per_day < 0

    def test_vega_numerically_matches_a_bump(self):
        """The analytic Greek against a finite difference of the price."""
        bump = 1e-5
        numeric = (price(F, K, SIGMA + bump, T, True)
                   - price(F, K, SIGMA - bump, T, True)) / (2 * bump)
        assert greeks(F, K, SIGMA, T, True).vega == pytest.approx(numeric, rel=1e-4)

    def test_undefined_inputs_raise_rather_than_return_a_number(self):
        for bad in [dict(forward=0.0), dict(strike=-1.0), dict(t_years=0.0),
                    dict(sigma=0.0)]:
            kwargs = dict(forward=F, strike=K, sigma=SIGMA, t_years=T, is_call=True)
            kwargs.update(bad)
            with pytest.raises(ValueError):
                price(**kwargs)


class TestTheForwardComesFromTheMarket:
    def _chain(self, forward=24123.0, df=0.9977, sigma=0.10, t=0.0164):
        strikes = [forward - 400 + 100 * i for i in range(9)]
        calls = {k: price(forward, k, sigma, t, True, df) for k in strikes}
        puts = {k: price(forward, k, sigma, t, False, df) for k in strikes}
        return calls, puts

    def test_the_forward_and_discount_factor_are_both_recovered(self):
        """No rate assumed, no dividend assumed -- both fall out of parity."""
        calls, puts = self._chain()
        est = estimate_forward(calls, puts)
        assert est.is_usable
        assert est.forward == pytest.approx(24123.0, rel=1e-6)
        assert est.discount_factor == pytest.approx(0.9977, rel=1e-6)
        assert est.r_squared > 0.9999

    def test_too_few_paired_strikes_is_refused(self):
        est = estimate_forward({24000.0: 100.0}, {24000.0: 90.0})
        assert not est.is_usable
        assert est.status == fwd_mod.STATUS_TOO_FEW_STRIKES

    def test_an_incoherent_chain_is_refused_not_best_fitted(self):
        """A wrong forward silently poisons every IV built on it, so a chain
        that is not on a line yields nothing rather than an approximation."""
        calls = {24000.0 + 100 * i: 500.0 - 30 * i for i in range(8)}
        puts = {24000.0 + 100 * i: 100.0 + (i * i * 37) for i in range(8)}
        est = estimate_forward(calls, puts)
        assert not est.is_usable
        assert est.status in (fwd_mod.STATUS_POOR_FIT, fwd_mod.STATUS_IMPLAUSIBLE_DF)
        assert est.reason

    def test_a_zero_quote_is_absence_not_a_price(self):
        calls, puts = self._chain()
        calls[list(calls)[0]] = 0.0
        est = estimate_forward(calls, puts)
        assert est.strikes_used == len(calls) - 1

    def test_only_strikes_with_both_legs_are_used(self):
        calls, puts = self._chain()
        puts.pop(list(puts)[0])
        assert estimate_forward(calls, puts).strikes_used == len(calls) - 1


class TestImpliedVolInvertsRatherThanEstimates:
    def test_a_price_round_trips_to_the_volatility_that_made_it(self):
        for sigma in (0.05, 0.12, 0.35, 0.80):
            observed = price(F, K, sigma, T, True)
            solved = implied_volatility(observed, F, K, T, True)
            assert solved.is_solved
            assert solved.sigma == pytest.approx(sigma, abs=1e-5)

    def test_it_round_trips_off_the_money_too(self):
        for strike in (22500.0, 25500.0):
            observed = price(F, strike, 0.18, T, True)
            assert implied_volatility(observed, F, strike, T, True).sigma == pytest.approx(
                0.18, abs=1e-5)

    def test_a_price_below_intrinsic_is_refused_not_floored(self):
        """No volatility reproduces it. Returning the bracket floor would
        publish a 1% reading that looks like a measurement."""
        floor = intrinsic(F, 20000.0, True)
        solved = implied_volatility(floor - 50.0, F, 20000.0, T, True)
        assert not solved.is_solved
        assert solved.status == iv_mod.STATUS_BELOW_INTRINSIC
        assert solved.sigma is None and "stale" in solved.reason

    def test_an_absurd_price_is_refused(self):
        solved = implied_volatility(F * 2, F, K, T, True)
        assert not solved.is_solved and solved.status == iv_mod.STATUS_ABOVE_MAX

    def test_an_expired_option_has_no_implied_volatility(self):
        solved = implied_volatility(100.0, F, K, 0.0, True)
        assert not solved.is_solved and solved.status == iv_mod.STATUS_BAD_INPUT

    def test_a_missing_price_yields_nothing(self):
        assert implied_volatility(None, F, K, T, True).sigma is None
        assert implied_volatility(0.0, F, K, T, True).sigma is None


class TestTimeConvention:
    def test_time_to_expiry_is_positive_before_expiry(self):
        t = time_to_expiry_years("2026-08-19T10:00:00+05:30", "2026-08-25")
        assert t is not None and 0.01 < t < 0.02

    def test_an_expired_option_returns_none_not_a_tiny_positive(self):
        """A tiny positive T would manufacture an implied vol for something
        that no longer exists."""
        assert time_to_expiry_years("2026-08-26T10:00:00+05:30", "2026-08-25") is None

    def test_the_expiry_instant_is_respected_not_just_the_date(self):
        before = time_to_expiry_years("2026-08-25T15:00:00+05:30", "2026-08-25")
        after = time_to_expiry_years("2026-08-25T15:31:00+05:30", "2026-08-25")
        assert before is not None and after is None


class TestTheWholeChain:
    def _rows(self, forward=24123.0, sigma=0.10, t=0.0164, df=0.9977):
        rows = []
        for i in range(9):
            k = forward - 400 + 100 * i
            for is_call, name in ((True, "CE"), (False, "PE")):
                p = price(forward, k, sigma, t, is_call, df)
                rows.append({"strike": k, "option_type": name,
                             "bid": p - 0.5, "ask": p + 0.5, "ltp": p})
        return rows

    def test_a_coherent_chain_yields_iv_greeks_and_skew(self):
        result = analyse_expiry(rows=self._rows(), expiry="2026-08-25",
                                as_of="2026-08-19T09:30:00+05:30")
        assert result.status == "OK"
        assert result.forward.is_usable
        assert result.solved == len(result.contracts)
        assert result.skew.atm_iv is not None

    def test_the_mid_is_preferred_over_the_last_trade(self):
        """The LTP can be minutes stale on a far strike while the book has
        moved, and a stale price implies a stale vol."""
        rows = self._rows()
        for row in rows:
            row["ltp"] = row["ltp"] * 2      # a badly stale print
        result = analyse_expiry(rows=rows, expiry="2026-08-25",
                                as_of="2026-08-19T09:30:00+05:30")
        assert all(c.price_basis == "MID" for c in result.contracts)

    def test_a_one_sided_book_falls_back_to_ltp_and_says_so(self):
        rows = self._rows()
        for row in rows:
            row["ask"] = None
        result = analyse_expiry(rows=rows, expiry="2026-08-25",
                                as_of="2026-08-19T09:30:00+05:30")
        assert all(c.price_basis == "LTP" for c in result.contracts)

    def test_no_forward_means_no_published_volatilities(self):
        rows = [{"strike": 24000.0, "option_type": "CE", "ltp": 100.0}]
        result = analyse_expiry(rows=rows, expiry="2026-08-25",
                                as_of="2026-08-19T09:30:00+05:30")
        assert result.status == "NO_FORWARD"
        assert result.contracts == () and "quietly wrong" in result.reason

    def test_an_expired_chain_is_refused(self):
        result = analyse_expiry(rows=self._rows(), expiry="2026-08-01",
                                as_of="2026-08-19T09:30:00+05:30")
        assert result.status == "EXPIRED_OR_NO_TIME"

    def test_every_derived_value_is_labelled_derived(self):
        """A consumer must never mistake these for the observed LTP beside
        them."""
        result = analyse_expiry(rows=self._rows(), expiry="2026-08-25",
                                as_of="2026-08-19T09:30:00+05:30")
        assert result.value_class == "DERIVED" and result.model == "black76_forward"
        assert all(c.value_class == "DERIVED" for c in result.contracts)
        assert result.time_convention

    def test_the_result_is_json_serialisable(self):
        import json

        d = analyse_expiry(rows=self._rows(), expiry="2026-08-25",
                           as_of="2026-08-19T09:30:00+05:30").to_dict()
        assert json.loads(json.dumps(d)) == d


class TestSkew:
    def test_a_symmetric_smile_gives_no_risk_reversal(self):
        """Built from one flat vol: calls and puts must imply the same
        number, so the skew reads flat rather than showing a phantom tilt."""
        rows = []
        forward, t, df = 24123.0, 0.0164, 0.9977
        for i in range(11):
            k = forward - 500 + 100 * i
            for is_call, name in ((True, "CE"), (False, "PE")):
                p = price(forward, k, 0.10, t, is_call, df)
                rows.append({"strike": k, "option_type": name, "bid": p, "ask": p})
        result = analyse_expiry(rows=rows, expiry="2026-08-25",
                                as_of="2026-08-19T09:30:00+05:30")
        assert result.skew.risk_reversal_25d == pytest.approx(0.0, abs=1e-4)

    def test_skew_refuses_when_nothing_solved(self):
        from bujji.options_analytics import build_skew

        assert build_skew([], None).atm_iv is None
