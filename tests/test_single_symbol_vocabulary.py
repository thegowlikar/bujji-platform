"""Margin, order and quote book all speak the SAME string.

Before this, three consumers of one contract each obtained a symbol their
own way: Gate B looked it up on a (strike, type) key, order construction
REBUILT one from the leg, and the quote sync rebuilt a third with a copied
formula. Any two of them agreeing was a coincidence maintained by a test.

Now there is one origin -- `chain_row.instrument_symbol` -- and one index
per cycle, shared. These tests assert the agreement structurally, so it
cannot drift back apart.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from bujji.options_observation.engine import build_option_observation
from bujji.production_runtime import paper_market_sync as pms
from bujji.production_runtime import trading_brain_runtime as tbr
from bujji.production_runtime.option_symbol_resolver import build_symbol_index

E1 = "2026-08-25"


def _row(strike, option_type, *, symbol=None, provenance="BROKER_AUTHORITATIVE",
         bid=99.0, ask=101.0, expiry=E1):
    return build_option_observation(
        underlying="NIFTY", instrument_symbol=symbol or f"NSE:NIFTY26AUG{int(strike)}{option_type}",
        strike=float(strike), expiry=expiry, option_type=option_type,
        exchange="NSE", segment="FO", timestamp="T", resolution="SNAPSHOT",
        open_=None, high=None, low=None, close=100.0, settlement=None,
        volume=1, open_interest=1, change_in_open_interest=0,
        underlying_price=24113.0, origin="test",
        acquisition_timestamp="T", normalization_timestamp="T",
        bid=bid, ask=ask, symbol_provenance=provenance)


class _RecordingBroker:
    def __init__(self):
        self.quotes = {}

    def set_quote(self, symbol, bid, ask):
        self.quotes[symbol] = (bid, ask)


class TestQuoteBookAndResolverAgree:
    def test_every_applied_quote_key_equals_the_resolver_answer_for_that_row(self):
        """The provable invariant. NOT a superset claim -- see below."""
        chain = [_row(24000, "CE"), _row(24000, "PE"), _row(24100, "CE")]
        broker = _RecordingBroker()
        report = pms.sync_quotes_from_chain(broker, chain)
        index = build_symbol_index(chain)
        assert report.quotes_applied == 3
        for row in chain:
            assert index.resolve(row.expiry, row.strike, row.option_type) in broker.quotes

    def test_coverage_is_not_a_superset_and_the_gap_is_measured(self):
        """A row needs BOTH sides to be quoted, while _premium_for prices a
        leg off close alone. So a selected leg CAN be absent from the book.
        That gap is real and counted -- it is not closed by relaxing the
        filter, it is surfaced by quote_lookup_misses."""
        chain = [_row(24000, "CE", bid=None, ask=None)]
        broker = _RecordingBroker()
        report = pms.sync_quotes_from_chain(broker, chain)
        assert report.quotes_applied == 0
        assert report.rows_seen == 1
        assert build_symbol_index(chain).resolve(E1, 24000.0, "CE") not in broker.quotes

    def test_source_authoritative_rows_still_key_quotes(self):
        """Replay keeps paying a real spread; the real-venue path is closed
        by the startup guard below, not by refusing the symbol here."""
        chain = [_row(24000, "CE", provenance="SOURCE_AUTHORITATIVE",
                      symbol="NIFTY26AUG24000CE")]
        broker = _RecordingBroker()
        report = pms.sync_quotes_from_chain(broker, chain)
        assert report.quotes_applied == 1
        assert "NIFTY26AUG24000CE" in broker.quotes

    @pytest.mark.parametrize("provenance", ["ABSENT", "SYNTHETIC"])
    def test_sentinel_rows_are_skipped_and_counted(self, provenance):
        chain = [_row(24000, "CE", provenance=provenance,
                      symbol="UNRESOLVED|NIFTY|2026-08-25|24000|CE")]
        broker = _RecordingBroker()
        report = pms.sync_quotes_from_chain(broker, chain)
        assert broker.quotes == {}
        assert report.skipped_not_authoritative == 1

    def test_the_sync_no_longer_builds_a_key(self):
        assert not hasattr(pms, "contract_symbol_for"), \
            "the second symbol builder is back"
        for node in ast.walk(ast.parse(Path(pms.__file__).read_text())):
            if isinstance(node, ast.JoinedStr):
                src = ast.unparse(node)
                assert not ("strike" in src and "option_type" in src), \
                    f"paper_market_sync builds a symbol again: {src}"


class TestGateBAndOrderConstructionShareOneIndex:
    def test_the_runtime_builds_exactly_one_index_per_cycle(self):
        """Two indexes would be two chances to disagree."""
        src = Path(tbr.__file__).read_text()
        calls = [n for n in ast.walk(ast.parse(src))
                 if isinstance(n, ast.Call)
                 and ast.unparse(n.func).endswith("build_symbol_index")]
        assert len(calls) == 1, f"expected one index per cycle, found {len(calls)}"

    def test_order_construction_cannot_be_called_without_the_index(self):
        """Not optional, no default, no fallback -- a defaulted index is how
        a rebuild sneaks back in."""
        import inspect
        param = inspect.signature(tbr.TradingBrainRuntime._build_order_requests) \
            .parameters["symbol_index"]
        assert param.default is inspect.Parameter.empty

    def test_the_last_symbol_builder_is_gone_from_the_runtime(self):
        src = Path(tbr.__file__).read_text()
        calls = [n for n in ast.walk(ast.parse(src))
                 if isinstance(n, ast.Call)
                 and ast.unparse(n.func).endswith("_leg_to_core_contract")]
        assert calls == [], "trading_brain_runtime rebuilds a contract again"

    @staticmethod
    def _inline_symbol_builders(source: str):
        """f-strings that concatenate fields with NOTHING between them.

        WHY THIS EXISTS SEPARATELY. A negative control caught the test above
        passing while `_build_order_requests` was rebuilding the symbol
        INLINE:
            symbol = f"{root.underlying}{leg.expiry}{int(leg.strike)}{leg.option_type}"
        Checking for the old function's NAME only proves that one spelling is
        gone. What matters is the behaviour, and the behaviour has a shape:
        adjacent interpolations with no literal text between them. A message
        always has words separating its fields; a symbol never does.
        """
        found = []
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.JoinedStr):
                continue
            longest = run = 0
            has_text = False
            for part in node.values:
                if isinstance(part, ast.FormattedValue):
                    run += 1
                    longest = max(longest, run)
                else:
                    run = 0
                    if isinstance(part, ast.Constant) and str(part.value).strip():
                        has_text = True
            if longest >= 3 or (longest >= 2 and not has_text):
                found.append(ast.unparse(node))
        return found

    def test_the_runtime_builds_no_symbol_inline_either(self):
        assert self._inline_symbol_builders(Path(tbr.__file__).read_text()) == []

    def test_that_check_can_actually_fail(self):
        """Positive control, written because the guard above did not fail
        when it should have."""
        assert self._inline_symbol_builders(
            's = f"{u}{leg.expiry}{int(leg.strike)}{leg.option_type}"')
        assert self._inline_symbol_builders('s = f"leg {a} at strike {b}"') == []
        # Gate B's real diagnostic: two adjacent fields, but it is a message.
        assert self._inline_symbol_builders(
            's = f"{leg.option_type}{int(leg.strike)} [{exc.reason}: {exc}]"') == []

    def test_gate_b_no_longer_keeps_its_own_two_tuple_map(self):
        """The old map dropped expiry, so a CALENDAR's two legs collapsed
        onto one row and the margin call priced two legs of one contract."""
        # AST, not substring: the comment recording WHY it was removed
        # quotes the old name, and a text search matches my own explanation.
        names = {n.id for n in ast.walk(ast.parse(Path(tbr.__file__).read_text()))
                 if isinstance(n, ast.Name)}
        assert "symbol_by_leg" not in names

    def test_margin_symbol_equals_order_symbol_for_the_same_leg(self):
        """The end-to-end property, asserted on one shared index exactly as
        production builds it."""
        class _Leg:
            strike, option_type, expiry = 24000.0, "CE", E1
        chain = [_row(24000, "CE", symbol="NSE:ONLY_TRUE_SYMBOL"), _row(24100, "PE")]
        index = build_symbol_index(chain)
        margin_symbol = index.resolve_leg(_Leg())
        order_symbol = index.resolve(_Leg.expiry, _Leg.strike, _Leg.option_type)
        assert margin_symbol == order_symbol == "NSE:ONLY_TRUE_SYMBOL"


class TestTheStartupGuard:
    """(b). providers.market_data and providers.margin were selected
    independently, and config/options_os_shadow.yaml shipped
    replay_chain + fyers_certified -- a session one CLI flag away from
    quoting NSE-form symbols at the live FYERS SPAN endpoint."""

    @staticmethod
    def _runner():
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "_runner_guard", REPO_ROOT / "bujji_options_os_runner.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    @pytest.mark.parametrize("market_data", ["replay_chain", "observation_store"])
    @pytest.mark.parametrize("margin", ["fyers_certified", "fyers_uncertified"])
    def test_a_non_broker_chain_with_real_margin_refuses_to_start(self, market_data, margin):
        runner = self._runner()
        with pytest.raises(runner.ConfigurationError) as exc:
            runner._guard_provider_vocabulary(
                {"market_data": {"type": market_data}, "margin": {"type": margin}})
        assert market_data in str(exc.value) and margin in str(exc.value)

    def test_the_live_chain_with_real_margin_is_allowed(self):
        runner = self._runner()
        runner._guard_provider_vocabulary(
            {"market_data": {"type": "fyers_live"}, "margin": {"type": "fyers_certified"}})

    def test_a_replay_chain_with_simulated_margin_is_allowed(self):
        """Replay keeps working -- it simply never reaches a real broker."""
        runner = self._runner()
        runner._guard_provider_vocabulary(
            {"market_data": {"type": "replay_chain"}, "margin": {"type": "simulated"}})

    def test_the_defaults_are_allowed(self):
        runner = self._runner()
        runner._guard_provider_vocabulary({})

    def test_the_guard_runs_before_any_provider_is_constructed(self):
        """A guard that fired after the chain was fetched would already have
        touched the broker."""
        src = (REPO_ROOT / "bujji_options_os_runner.py").read_text()
        guard_at = src.index("_guard_provider_vocabulary(providers_cfg")
        assert guard_at < src.index("_make_margin_provider(providers_cfg)")
        assert guard_at < src.index('market_data_cfg = providers_cfg.get("market_data"')
