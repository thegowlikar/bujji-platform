"""D-7: the paper broker is told what the market actually looks like.

PaperBroker has always modelled a spread -- BUY lifts the ask, SELL hits the
bid -- but nothing in production ever called set_quote, so every fill fell
back to the leg's own reference premium. A short strangle opened and closed
at the identical mid and the bid-ask cost nothing. These tests pin the wiring
that fixes it, and, just as importantly, pin the ways it must NOT lie.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from bujji.production_runtime.paper_market_sync import (
    contract_symbol_for,
    sync_capital,
    sync_quotes_from_chain,
)


@dataclass
class _Row:
    strike: float = 24100.0
    expiry: str = "26818"
    option_type: str = "CE"
    bid: Optional[float] = 319.2
    ask: Optional[float] = 320.1
    bid_quantity: Optional[float] = None
    ask_quantity: Optional[float] = None


class _Broker:
    def __init__(self):
        self.quotes = {}
        self.depth = {}
        self.capital = None

    def set_quote(self, symbol, bid, ask):
        self.quotes[symbol] = (bid, ask)

    def set_depth(self, symbol, available_depth):
        self.depth[symbol] = available_depth

    def set_capital(self, **kwargs):
        self.capital = kwargs


class TestTheKeyMustMatchWhatTheOrderLooksUp:
    """The one failure mode that would look like success: setting quotes
    under names no order ever looks up. The sync would report full coverage
    while fills stayed frictionless."""

    def test_the_key_formula_matches_the_entry_bridge_exactly(self):
        """Pinned against the REAL bridge, not a copy of its formula -- if
        either side changes, this fails instead of silently reverting."""
        from bujji.core.enums import OptionType  # noqa: F401 -- import proves the bridge loads
        from bujji.trading_brain.risk_governor.msi_entry_bridge import _leg_to_core_contract

        @dataclass(frozen=True)
        class _Leg:
            strike: float = 24100.0
            expiry: str = "26818"
            option_type: str = "CE"
            ratio: int = 1
            premium: float = 319.8
            role: str = "SHORT"

        contract = _leg_to_core_contract(_Leg(), "NIFTY", 65)
        assert contract_symbol_for("NIFTY", "26818", 24100.0, "CE") == contract.symbol

    def test_the_chains_own_fyers_symbol_is_deliberately_not_used(self):
        """The chain carries 'NSE:NIFTY2681824100CE'; the broker keys on the
        bridge's format. Using the chain's own symbol is the silent no-op."""
        assert contract_symbol_for("NIFTY", "26818", 24100.0, "CE") != "NSE:NIFTY2681824100CE"

    def test_a_float_strike_does_not_leak_a_decimal_into_the_key(self):
        assert contract_symbol_for("NIFTY", "26818", 24100.0, "PE").endswith("24100PE")


class TestOnlyRealQuotesAreApplied:
    def test_a_two_sided_quote_is_pushed(self):
        b = _Broker()
        report = sync_quotes_from_chain(b, [_Row()], "NIFTY")
        assert report.quotes_applied == 1
        assert b.quotes[contract_symbol_for("NIFTY", "26818", 24100.0, "CE")] == (319.2, 320.1)

    def test_a_one_sided_quote_is_skipped_entirely_not_half_applied(self):
        """Half a quote is worse than none: one direction would cross a real
        price while the other silently fell back, looking like coverage."""
        b = _Broker()
        report = sync_quotes_from_chain(b, [_Row(ask=None)], "NIFTY")
        assert b.quotes == {} and report.quotes_applied == 0 and report.skipped_no_ask == 1

    def test_non_positive_prices_are_not_quotes(self):
        b = _Broker()
        report = sync_quotes_from_chain(b, [_Row(bid=0.0), _Row(ask=-1.0)], "NIFTY")
        assert b.quotes == {} and report.quotes_applied == 0

    def test_an_unusable_row_is_counted_not_guessed(self):
        b = _Broker()
        report = sync_quotes_from_chain(b, [_Row(option_type="XX")], "NIFTY")
        assert report.skipped_unusable_key == 1 and b.quotes == {}

    def test_every_row_is_accounted_for(self):
        rows = [_Row(), _Row(strike=24200.0, bid=None), _Row(strike=24300.0, option_type="")]
        report = sync_quotes_from_chain(_Broker(), rows, "NIFTY")
        assert report.rows_seen == 3
        assert (report.quotes_applied + report.skipped_no_bid
                + report.skipped_unusable_key) >= 3


class TestDepthIsRealOrAbsent:
    def test_traded_volume_is_never_used_as_depth(self):
        """Cumulative traded volume is a different quantity from top-of-book
        size. Passing it would be fabricated depth wearing a real number."""
        @dataclass
        class _WithVolume(_Row):
            volume: float = 137475.0

        b = _Broker()
        report = sync_quotes_from_chain(b, [_WithVolume()], "NIFTY")
        assert b.depth == {} and report.depth_applied == 0

    def test_real_top_of_book_quantities_are_used_when_present(self):
        b = _Broker()
        report = sync_quotes_from_chain(b, [_Row(bid_quantity=900, ask_quantity=525)], "NIFTY")
        assert report.depth_applied == 1
        # The conservative side: the broker holds one number per symbol.
        assert b.depth[contract_symbol_for("NIFTY", "26818", 24100.0, "CE")] == 525

    def test_one_sided_quantity_is_not_depth(self):
        b = _Broker()
        report = sync_quotes_from_chain(b, [_Row(bid_quantity=900)], "NIFTY")
        assert b.depth == {} and report.depth_applied == 0

    def test_todays_live_chain_provider_really_does_omit_quantities(self):
        """The claim 'depth is absent in practice today' is checked against
        the provider's source, not assumed -- if it starts populating them,
        this fails and the disclosure gets updated instead of going stale."""
        source = (REPO_ROOT / "bujji" / "production_runtime" / "live_chain_provider.py").read_text()
        assert "bid_quantity" not in source and "ask_quantity" not in source


class TestCapitalIsMeasuredNotInvented:
    @dataclass(frozen=True)
    class _Snap:
        total_capital: Optional[float] = 500000.0
        available_capital: Optional[float] = 500000.0

    def test_measured_capital_is_pushed(self):
        b = _Broker()
        assert sync_capital(b, self._Snap()) is True
        assert b.capital == {"account_equity": 500000.0, "available_margin": 500000.0}

    def test_margin_per_lot_is_never_inferred(self):
        """Gate B owns real margin. A per-lot figure derived from a
        whole-book SPAN number would be an invention wherever the book is
        not exactly one lot."""
        b = _Broker()
        sync_capital(b, self._Snap())
        assert "margin_per_lot" not in b.capital

    def test_no_snapshot_changes_nothing(self):
        b = _Broker()
        assert sync_capital(b, None) is False and b.capital is None

    def test_an_empty_snapshot_changes_nothing(self):
        @dataclass(frozen=True)
        class _Empty:
            total_capital = None
            available_capital = None

        b = _Broker()
        assert sync_capital(b, _Empty()) is False and b.capital is None


class TestItNeverBreaksTheSession:
    def test_a_broker_without_the_setters_is_a_no_op(self):
        class _Bare:
            pass

        report = sync_quotes_from_chain(_Bare(), [_Row()], "NIFTY")
        assert report.quotes_applied == 0 and report.rows_seen == 1

    def test_the_runner_records_coverage_and_survives_a_sync_failure(self):
        """Zero coverage must be LOUD, and a failure must not end a session
        that may be about to hold a real position."""
        import importlib.util
        import logging

        spec = importlib.util.spec_from_file_location(
            "runner_d7", REPO_ROOT / "bujji_options_os_runner.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        class _Root:
            underlying = "NIFTY"
            capital_snapshot_provider = None

        class _Stub:
            _broker = _Broker()
            _root = _Root()
            _logger = logging.getLogger("d7")
            _governor_result_summary = {}

        stub = _Stub()
        mod.OptionsOSRunner._sync_paper_market(stub, [_Row()])
        assert stub._governor_result_summary["paper_market_sync"]["quotes_applied"] == 1

        class _Exploding:
            def __len__(self):
                raise RuntimeError("chain blew up")

            def __iter__(self):
                raise RuntimeError("chain blew up")

        stub2 = _Stub()
        stub2._governor_result_summary = {}
        mod.OptionsOSRunner._sync_paper_market(stub2, _Exploding())
        assert "error" in stub2._governor_result_summary["paper_market_sync"]


class TestTheSpreadActuallyCostsSomethingNow:
    """The point of the whole phase, asserted against the REAL PaperBroker
    and as a DIFFERENTIAL: the same round trip, with and without the sync.
    Asserting only "the fill is near the quote" would pass in the broken
    world too, since the reference premium already sits inside the spread."""

    @staticmethod
    async def _round_trip(apply_quotes: bool) -> float:
        from bujji.broker.paper import PaperBroker
        from bujji.core.enums import OptionType, Side
        from bujji.core.models import OptionContract, OrderRequest

        broker = PaperBroker()
        await broker.connect()
        symbol = contract_symbol_for("NIFTY", "26818", 24100.0, "CE")
        contract = OptionContract(symbol=symbol, underlying="NIFTY", strike=24100,
                                  option_type=OptionType.CE, expiry="26818", lot_size=65)
        if apply_quotes:
            sync_quotes_from_chain(broker, [_Row()], "NIFTY")

        sold = await broker.place_order(OrderRequest(
            client_order_id="RT-SELL", contract=contract, side=Side.SELL,
            quantity=65, reference_price=319.8))
        bought = await broker.place_order(OrderRequest(
            client_order_id="RT-BUY", contract=contract, side=Side.BUY,
            quantity=65, reference_price=319.8))
        assert sold.average_price is not None and bought.average_price is not None
        # Sell then buy back at an unchanged market: the P&L is pure friction.
        return sold.average_price - bought.average_price

    @pytest.mark.asyncio
    async def test_without_the_sync_a_flat_round_trip_is_free(self):
        """The bug, pinned: both sides fill at the same reference premium,
        so the spread costs exactly nothing."""
        pnl = await self._round_trip(apply_quotes=False)
        assert pnl == pytest.approx(0.0, abs=1e-9)

    @pytest.mark.asyncio
    async def test_with_the_sync_the_same_round_trip_pays_the_spread(self):
        """The fix: SELL hits the bid, BUY lifts the ask, so a flat round
        trip LOSES money -- which is what really happens."""
        pnl = await self._round_trip(apply_quotes=True)
        assert pnl < 0.0, "a flat round trip still costs nothing -- fills are frictionless"
        # 319.2 bid / 320.1 ask -> the round trip pays the full 0.90 spread.
        assert pnl == pytest.approx(319.2 - 320.1, abs=1e-6)
