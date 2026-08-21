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
    sync_capital,
    sync_quotes_from_chain,
)

SYMBOL = "NSE:NIFTY2681824100CE"


@dataclass
class _Row:
    strike: float = 24100.0
    expiry: str = "26818"
    option_type: str = "CE"
    bid: Optional[float] = 319.2
    ask: Optional[float] = 320.1
    bid_quantity: Optional[float] = None
    ask_quantity: Optional[float] = None
    # The broker's own string, and its recorded origin. Both are now part of
    # a chain row's contract with this module: the symbol IS the key, and the
    # provenance decides whether the row may key anything at all.
    instrument_symbol: str = SYMBOL
    symbol_provenance: str = "BROKER_AUTHORITATIVE"


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
    """The one failure mode that looks like success: setting quotes under
    names no order ever looks up -- full reported coverage, frictionless
    fills.

    THIS CLASS ASSERTED THE OPPOSITE INVARIANT UNTIL 2026-08-21, and was
    right to at the time. Orders then carried the entry bridge's internal
    format, so keying quotes by the chain's own FYERS symbol WOULD have been
    the silent no-op, and these tests pinned the two builders together.

    The migration removed the second builder instead of keeping it in step:
    orders now carry `chain_row.instrument_symbol` verbatim
    (option_symbol_resolver), so this module writes that same field. The
    tests are inverted rather than deleted, because the failure mode they
    guard is unchanged -- only which side was wrong has moved."""

    def test_the_key_is_the_rows_own_symbol_verbatim(self):
        b = _Broker()
        sync_quotes_from_chain(b, [_Row()])
        assert list(b.quotes) == [SYMBOL]

    def test_the_old_internal_format_is_no_longer_produced(self):
        """"NIFTY2681824100CE" was the bridge's form. Nothing writes it now."""
        b = _Broker()
        sync_quotes_from_chain(b, [_Row()])
        assert "NIFTY2681824100CE" not in b.quotes

    def test_the_key_survives_a_symbol_this_module_could_not_have_built(self):
        """Proof it is a passthrough, not a coincidence: a symbol bearing no
        relation to strike/expiry/type still becomes the key."""
        b = _Broker()
        sync_quotes_from_chain(b, [_Row(instrument_symbol="NSE:UNGUESSABLE")])
        assert list(b.quotes) == ["NSE:UNGUESSABLE"]


class TestOnlyRealQuotesAreApplied:
    def test_a_two_sided_quote_is_pushed(self):
        b = _Broker()
        report = sync_quotes_from_chain(b, [_Row()])
        assert report.quotes_applied == 1
        assert b.quotes[SYMBOL] == (319.2, 320.1)

    def test_a_one_sided_quote_is_skipped_entirely_not_half_applied(self):
        """Half a quote is worse than none: one direction would cross a real
        price while the other silently fell back, looking like coverage."""
        b = _Broker()
        report = sync_quotes_from_chain(b, [_Row(ask=None)])
        assert b.quotes == {} and report.quotes_applied == 0 and report.skipped_no_ask == 1

    def test_non_positive_prices_are_not_quotes(self):
        b = _Broker()
        report = sync_quotes_from_chain(b, [_Row(bid=0.0), _Row(ask=-1.0)])
        assert b.quotes == {} and report.quotes_applied == 0

    def test_a_row_with_no_symbol_is_counted_not_guessed(self):
        """"Unusable key" now means exactly one thing: the row carries no
        symbol to key by. It is never reconstructed from the other fields."""
        b = _Broker()
        report = sync_quotes_from_chain(b, [_Row(instrument_symbol="   ")])
        assert report.skipped_unusable_key == 1 and b.quotes == {}

    def test_a_non_authoritative_row_keys_nothing_and_is_counted(self):
        """An ABSENT row carries a sentinel, not a symbol. Quoting under it
        would be coverage that does not exist."""
        b = _Broker()
        report = sync_quotes_from_chain(b, [_Row(
            instrument_symbol="UNRESOLVED|NIFTY|26818|24100|CE",
            symbol_provenance="ABSENT")])
        assert b.quotes == {} and report.skipped_not_authoritative == 1

    def test_a_source_authoritative_row_does_key_a_quote(self):
        """A bhavcopy replay keeps paying a real spread. What stops that
        symbol reaching FYERS is _guard_provider_vocabulary, which refuses to
        start a replay session with a real margin provider at all."""
        b = _Broker()
        report = sync_quotes_from_chain(b, [_Row(
            instrument_symbol="NIFTY26AUG24100CE",
            symbol_provenance="SOURCE_AUTHORITATIVE")])
        assert report.quotes_applied == 1 and "NIFTY26AUG24100CE" in b.quotes

    def test_every_row_is_accounted_for(self):
        rows = [_Row(), _Row(strike=24200.0, bid=None),
                _Row(strike=24300.0, instrument_symbol="")]
        report = sync_quotes_from_chain(_Broker(), rows)
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
        report = sync_quotes_from_chain(b, [_WithVolume()])
        assert b.depth == {} and report.depth_applied == 0

    def test_real_top_of_book_quantities_are_used_when_present(self):
        b = _Broker()
        report = sync_quotes_from_chain(b, [_Row(bid_quantity=900, ask_quantity=525)])
        assert report.depth_applied == 1
        # The conservative side: the broker holds one number per symbol.
        assert b.depth[SYMBOL] == 525

    def test_one_sided_quantity_is_not_depth(self):
        b = _Broker()
        report = sync_quotes_from_chain(b, [_Row(bid_quantity=900)])
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

        report = sync_quotes_from_chain(_Bare(), [_Row()])
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
        # The order carries the CHAIN ROW'S symbol -- the same string the
        # sync writes. That agreement is the whole point of the migration:
        # under the old two-vocabulary world these had to be built by two
        # formulas kept in step, and this round trip was frictionless
        # whenever they drifted.
        contract = OptionContract(symbol=SYMBOL, underlying="NIFTY", strike=24100,
                                  option_type=OptionType.CE, expiry="26818", lot_size=65)
        if apply_quotes:
            sync_quotes_from_chain(broker, [_Row()])

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
