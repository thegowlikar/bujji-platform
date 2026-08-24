"""The two market-data paths, measured before they are merged.

Bujji reads the market twice: a REST-fed MarketSnapshot feeds regime
derivation and strike selection, and a websocket tick path feeds position
pricing. `MarketDataAdapter.quote_source` is what would let the adapter see
the tick path. It existed from the start and was never supplied, so
`live_quotes()` had ZERO callers anywhere in the repository -- the two paths
could not even be compared, let alone merged.

These tests lock the measurement, and lock the two ways it could quietly
become a lie: comparing REST against REST, and treating a symbol the feed
never delivered as though it had delivered a price.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

from bujji.market_perception.market_data_adapter import MarketDataAdapter
from bujji.market_perception.models import (
    OptionChainSnapshot, OptionChainConfig, OptionLeg,
)

RUNNER = pathlib.Path(__file__).resolve().parent.parent / "bujji_options_os_runner.py"
TREE = ast.parse(RUNNER.read_text())


class _Quote:
    """Stands in for a typed Quote: `ltp` is None when the field was never
    delivered, which is NOT zero."""

    def __init__(self, ltp, source="LIVE_TICK"):
        self.ltp = ltp
        self.source = source


class _Snapshot:
    def __init__(self, legs):
        self.option_chain = OptionChainSnapshot(
            underlying="NIFTY", expiry="2026-08-27", atm_strike=25000.0,
            config=OptionChainConfig(), legs=tuple(legs))


def _leg(symbol, ltp, strike=25000.0, option_type="CE"):
    return OptionLeg(symbol=symbol, strike=strike, option_type=option_type,
                     ltp=ltp, bid=None, ask=None, spread=None, volume=None,
                     open_interest=None, iv=None, delta=None, gamma=None,
                     theta=None, vega=None)


def _adapter(quote_source=None):
    return MarketDataAdapter(broker=object(), clock=lambda: None,
                             quote_source=quote_source)


class TestLiveQuotesFinallyHasACaller:
    def test_the_runner_supplies_a_quote_source(self):
        """REACHABILITY. Without this the adapter can never see ticks and
        `live_quotes()` stays dead code, however correct it is."""
        calls = [n for n in ast.walk(TREE)
                 if isinstance(n, ast.Call)
                 and getattr(n.func, "id", None) == "MarketDataAdapter"]
        assert calls, "the runner no longer constructs a MarketDataAdapter"
        for call in calls:
            assert "quote_source" in {k.arg for k in call.keywords}, (
                "a MarketDataAdapter is built without a quote_source; the tick "
                "path is invisible to it")

    def test_the_coverage_record_is_actually_taken(self):
        assert any(getattr(n.func, "attr", None) == "tick_rest_coverage"
                   for n in ast.walk(TREE) if isinstance(n, ast.Call)), (
            "nothing calls tick_rest_coverage, so live_quotes has no caller again")


class TestTheSourceIsTheFeedNotTheProvider:
    """THE SUBTLE FAILURE. `WebsocketTickProvider.get_quotes()` falls back to
    REST and labels the result REST_FALLBACK. Sourcing this comparison from
    the provider would compare REST against REST and report perfect
    agreement -- the most misleading possible answer, and one that would look
    like success."""

    @staticmethod
    def _closure():
        for node in ast.walk(TREE):
            if isinstance(node, ast.FunctionDef) and node.name == "_tick_quotes":
                return node
        raise AssertionError("the _tick_quotes closure no longer exists")

    def test_it_reads_the_feed(self):
        body = ast.unparse(self._closure())
        assert "all_quotes" in body, (
            "the tick source no longer reads the feed's own quote store")

    def test_it_never_reaches_for_the_rest_capable_provider(self):
        body = ast.unparse(self._closure())
        for forbidden in ("get_quotes", "get_prices", "_price_provider",
                          "_fallback"):
            assert forbidden not in body, (
                f"the tick source references {forbidden!r}, which can return "
                f"REST data; the comparison would then be REST against REST")

    def test_it_resolves_the_feed_at_call_time(self):
        """LAZY ON PURPOSE. The feed is assigned after this closure is
        created, so binding it eagerly would freeze None forever and the
        record would always read 'no feed wired'."""
        body = ast.unparse(self._closure())
        assert "getattr(self, '_tick_feed', None)" in body.replace('"', "'"), (
            "the feed is not resolved at call time")


class TestTheRecordIsHonestAboutCoverage:
    def test_no_quote_source_is_reported_as_unwired_not_as_agreement(self):
        record = _adapter().tick_rest_coverage(_Snapshot([_leg("NSE:A", 10.0)]))
        assert record["tick_source_wired"] is False
        assert record["tick_covered"] == 0
        assert record["compared"] == 0
        assert record["max_abs_difference"] is None

    def test_a_wired_but_empty_feed_is_distinguishable_from_no_feed(self):
        """THE FIRST DERIVATION OF EVERY SESSION. A feed exists to ask and has
        nothing yet, because the universe is not subscribed until the entry
        gate. That is a different fact from no feed being wired, and the
        record must not blur them."""
        record = _adapter(lambda syms: {}).tick_rest_coverage(
            _Snapshot([_leg("NSE:A", 10.0)]))
        assert record["tick_source_wired"] is True
        assert record["tick_covered"] == 0
        assert record["tick_uncovered"] == 1

    def test_partial_coverage_is_counted_exactly(self):
        legs = [_leg("NSE:A", 10.0), _leg("NSE:B", 20.0), _leg("NSE:C", 30.0)]
        src = lambda syms: {"NSE:A": _Quote(10.5), "NSE:C": _Quote(30.0)}
        record = _adapter(src).tick_rest_coverage(_Snapshot(legs))
        assert record["chain_symbols"] == 3
        assert record["tick_covered"] == 2
        assert record["tick_uncovered"] == 1
        assert record["compared"] == 2

    def test_an_undelivered_field_is_never_compared_as_zero(self):
        """UNAVAILABLE IS NOT ZERO. A symbol the feed acknowledged but never
        priced reports ltp=None. Comparing that as 0.0 would manufacture a
        difference the size of the whole premium."""
        src = lambda syms: {"NSE:A": _Quote(None)}
        record = _adapter(src).tick_rest_coverage(_Snapshot([_leg("NSE:A", 10.0)]))
        assert record["tick_covered"] == 1, "the symbol WAS covered"
        assert record["tick_missing_ltp"] == 1
        assert record["compared"] == 0, "an absent price was compared anyway"
        assert record["max_abs_difference"] is None

    def test_a_missing_rest_price_is_counted_separately(self):
        src = lambda syms: {"NSE:A": _Quote(10.0)}
        record = _adapter(src).tick_rest_coverage(_Snapshot([_leg("NSE:A", None)]))
        assert record["rest_missing_ltp"] == 1
        assert record["compared"] == 0

    def test_differences_are_reported_raw_with_no_tolerance(self):
        src = lambda syms: {"NSE:A": _Quote(12.5)}
        record = _adapter(src).tick_rest_coverage(_Snapshot([_leg("NSE:A", 10.0)]))
        assert record["compared"] == 1
        assert record["differences"][0]["difference"] == pytest.approx(2.5)
        assert record["differences"][0]["rest_ltp"] == 10.0
        assert record["differences"][0]["tick_ltp"] == 12.5
        assert record["max_abs_difference"] == pytest.approx(2.5)
        assert not any("tolerance" in k or "within" in k for k in record), (
            "a tolerance appeared; no measurement justifies one yet")

    def test_the_largest_difference_survives_the_report_cap(self):
        """Only ten differences are listed, but the worst one is a summary
        statistic and must reflect every compared symbol."""
        legs = [_leg(f"NSE:{i}", 10.0) for i in range(30)]
        src = lambda syms: {f"NSE:{i}": _Quote(10.0 + (100.0 if i == 29 else 0.5))
                            for i in range(30)}
        record = _adapter(src).tick_rest_coverage(_Snapshot(legs))
        assert record["compared"] == 30
        assert len(record["differences"]) == 10
        assert record["max_abs_difference"] == pytest.approx(100.0)

    def test_a_failing_tick_source_reports_no_coverage_rather_than_raising(self):
        def boom(symbols):
            raise RuntimeError("feed exploded")
        record = _adapter(boom).tick_rest_coverage(_Snapshot([_leg("NSE:A", 10.0)]))
        assert record["tick_covered"] == 0
        assert record["compared"] == 0


class TestItDecidesNothing:
    def test_the_snapshot_is_not_modified(self):
        legs = [_leg("NSE:A", 10.0)]
        snap = _Snapshot(legs)
        before = tuple((l.symbol, l.ltp) for l in snap.option_chain.legs)
        _adapter(lambda syms: {"NSE:A": _Quote(99.0)}).tick_rest_coverage(snap)
        after = tuple((l.symbol, l.ltp) for l in snap.option_chain.legs)
        assert before == after, (
            "the coverage measurement rewrote the snapshot it was measuring")

    def test_build_snapshot_does_not_consult_the_quote_source(self):
        """The merge has NOT happened, and the tests must say so. Selection
        still runs on REST alone; anything else would make an unproven feed
        load-bearing for strike choice before Gate 1 has measured it."""
        import inspect
        src = inspect.getsource(MarketDataAdapter.build_snapshot)
        for forbidden in ("_quote_source", "live_quotes", "tick_rest_coverage"):
            assert forbidden not in src, (
                f"build_snapshot now consults {forbidden}; the REST snapshot has "
                f"become tick-dependent without the evidence to justify it")

    def test_a_snapshot_with_no_chain_is_handled(self):
        class _Empty:
            option_chain = None
        record = _adapter(lambda syms: {}).tick_rest_coverage(_Empty())
        assert record["chain_symbols"] == 0
        assert record["compared"] == 0
