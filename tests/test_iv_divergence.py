"""Two IV derivations, measured against each other -- and nothing swapped.

Strike selection ranks by |delta - target|. The canonical engine derives
delta from spot-based Black-Scholes with an ASSUMED 6.5% rate;
options_analytics derives it from a forward recovered out of the chain by
put-call parity, assuming no rate at all. Over real captured chains they
disagree on the chosen strike in roughly a quarter of comparisons, always in
the same direction -- so swapping them changes which strikes get sold, which
is the operator's gate.

These tests pin the comparator's honesty (it measures, it never decides) and
the property that makes the record decision-grade: an unanswerable comparison
must read as unanswerable, never as agreement.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from bujji.options_analytics import compare_derivations
from bujji.options_analytics.black76 import price
from bujji.options_analytics.divergence import StrikeChoice
from bujji.options_observation.engine import build_option_observation

EXPIRY = "2026-08-25"
AS_OF = "2026-08-19T10:00:00+05:30"
SPOT = 24113.0
T = 0.0164


def _chain(forward=24123.0, sigma=0.10, df=0.9977, n=13):
    rows, dicts = [], []
    for i in range(n):
        strike = float(forward - 300 + 50 * i)
        for is_call, option_type in ((True, "CE"), (False, "PE")):
            p = price(forward, strike, sigma, T, is_call, df)
            rows.append(build_option_observation(
                # "X24500CE" is invented by this test and belongs to no venue.
                symbol_provenance="SYNTHETIC",
                underlying="NIFTY", instrument_symbol=f"X{int(strike)}{option_type}",
                strike=strike, expiry=EXPIRY, option_type=option_type,
                exchange="NSE", segment="FO", timestamp=AS_OF, resolution="SNAPSHOT",
                open_=None, high=None, low=None, close=p, settlement=None,
                volume=100, open_interest=1000, change_in_open_interest=10,
                underlying_price=SPOT, origin="test",
                acquisition_timestamp=AS_OF, normalization_timestamp=AS_OF,
                bid=p - 0.25, ask=p + 0.25))
            dicts.append({"strike": strike, "option_type": option_type,
                          "ltp": p, "bid": p - 0.25, "ask": p + 0.25})
    return rows, dicts


def _compare(**over):
    rows, dicts = _chain()
    kwargs = dict(rows=rows, chain_dicts=dicts, expiry=EXPIRY, spot=SPOT,
                  t_years=T, as_of=AS_OF, assumed_rate=0.065)
    kwargs.update(over)
    return compare_derivations(**kwargs)


class TestItMeasuresBothSides:
    def test_both_derivations_solve_the_same_chain(self):
        d = _compare()
        assert d.status == "OK"
        assert d.engine_solved > 0 and d.parity_solved > 0

    def test_the_recovered_forward_is_reported_beside_the_spot_used(self):
        """The whole difference in one line: the engine works from spot plus
        an assumed rate; parity works from a forward the market gave up."""
        d = _compare()
        assert d.forward_recovered is not None
        assert d.spot_used == SPOT
        assert d.forward_recovered > d.spot_used

    def test_the_assumed_rate_and_the_implied_rate_are_both_recorded(self):
        d = _compare()
        assert d.assumed_rate == 0.065
        assert d.implied_rate is not None

    def test_a_choice_is_recorded_for_every_target_and_side(self):
        d = _compare()
        assert len(d.choices) == 8          # 4 targets x CE/PE
        assert {c.option_type for c in d.choices} == {"CE", "PE"}

    def test_the_premium_selling_target_is_among_them(self):
        """0.20 is msi_trade_construction's NEUTRAL_PREMIUM_SELLING target --
        the one a strangle seller actually trades."""
        assert 0.20 in {c.target_delta for c in _compare().choices}


class TestTheGateQuestionIsAnswerable:
    def test_strikes_agree_summarises_every_comparable_target(self):
        d = _compare()
        assert d.strikes_agree in (True, False)
        assert d.disagreements == sum(1 for c in d.choices if c.agree is False)

    def test_the_gap_is_signed_so_direction_is_visible(self):
        """Measured on real chains every disagreement was +50 -- one strike
        step in the same direction. A magnitude-only field would have hidden
        that it is systematic rather than noise."""
        choice = StrikeChoice(target_delta=0.20, option_type="CE",
                              engine_strike=24100.0, engine_delta=0.2,
                              parity_strike=24150.0, parity_delta=0.2)
        assert choice.strike_gap == 50.0 and choice.agree is False

    def test_an_unanswerable_comparison_is_not_an_agreement(self):
        """If one side could not choose, that is missing evidence -- reading
        it as agreement would quietly inflate the case for leaving things
        alone."""
        choice = StrikeChoice(target_delta=0.20, option_type="CE",
                              engine_strike=24100.0, parity_strike=None)
        assert choice.agree is None and choice.strike_gap is None

    def test_strikes_agree_is_none_when_nothing_is_comparable(self):
        from bujji.options_analytics.divergence import DerivationDivergence

        empty = DerivationDivergence(expiry=EXPIRY, as_of=AS_OF)
        assert empty.strikes_agree is None


class TestItNeverDecides:
    def test_an_incoherent_chain_yields_no_comparison_not_a_verdict(self):
        rows, dicts = _chain()
        for d in dicts:
            d["bid"] = d["ask"] = None
            d["ltp"] = 1.0                      # flat, unparseable prices
        result = compare_derivations(rows=rows, chain_dicts=dicts, expiry=EXPIRY,
                                     spot=SPOT, t_years=T, as_of=AS_OF)
        assert result.status == "NO_PARITY_FORWARD"
        assert result.choices == () and result.strikes_agree is None

    def test_it_calls_the_real_engine_rather_than_a_copy(self):
        """A reimplementation would drift, and this module would then be
        measuring the parity derivation against MY IDEA of the engine."""
        source = (REPO_ROOT / "bujji" / "options_analytics" / "divergence.py").read_text()
        assert "from bujji.msi_trade_construction.engine import _build_strike_evidence" in source

    def test_the_comparator_does_not_write_to_the_protected_package(self):
        source = (REPO_ROOT / "bujji" / "options_analytics" / "divergence.py").read_text()
        for forbidden in ("_build_strike_evidence =", "engine.FAMILY_DELTA_TARGETS ="):
            assert forbidden not in source

    def test_the_result_is_json_serialisable(self):
        import json

        d = _compare().to_dict()
        assert json.loads(json.dumps(d)) == d


class TestTheRunnerRecordsAndIgnores:
    def _runner_module(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "runner_div", REPO_ROOT / "bujji_options_os_runner.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_the_entry_path_records_a_divergence_trail(self):
        import logging

        mod = self._runner_module()
        rows, _ = _chain()

        class _Stub:
            _logger = logging.getLogger("div")
            _governor_result_summary = {}

            def _clock(self):
                import datetime

                return datetime.datetime.fromisoformat(AS_OF)

        stub = _Stub()
        mod.OptionsOSRunner._record_iv_divergence(stub, rows, SPOT)
        trail = stub._governor_result_summary.get("iv_divergence")
        assert trail and trail[0]["expiry"] == EXPIRY

    def test_a_failure_never_ends_the_session(self):
        import logging

        mod = self._runner_module()

        class _Stub:
            _logger = logging.getLogger("div")
            _governor_result_summary = {}

            def _clock(self):
                raise RuntimeError("clock exploded")

        mod.OptionsOSRunner._record_iv_divergence(_Stub(), [object()], SPOT)

    def test_no_spot_or_no_chain_is_a_quiet_no_op(self):
        import logging

        mod = self._runner_module()

        class _Stub:
            _logger = logging.getLogger("div")
            _governor_result_summary = {}

            def _clock(self):
                import datetime

                return datetime.datetime.fromisoformat(AS_OF)

        stub = _Stub()
        mod.OptionsOSRunner._record_iv_divergence(stub, [], SPOT)
        mod.OptionsOSRunner._record_iv_divergence(stub, _chain()[0], None)
        assert "iv_divergence" not in stub._governor_result_summary

    def test_selection_still_consumes_only_the_canonical_engine(self):
        """The gate is the operator's: nothing in the decision chain may read
        the parity derivation until they open it."""
        source = (REPO_ROOT / "bujji" / "msi_trade_construction" / "engine.py").read_text()
        assert "options_analytics" not in source
