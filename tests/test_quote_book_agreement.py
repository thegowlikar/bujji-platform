"""A quote-book miss is recorded, so a vocabulary mismatch cannot look like a fill.

THE HAZARD THIS EXISTS FOR. paper.py did
`self._quotes.get(symbol, (None, None))`. A miss is invisible: FillSimulator
gets bid=None/ask=None, produces no spread, and the caller falls back to
`reference_price`. The order FILLS, frictionlessly, and every downstream
number stays plausible.

That is exactly what two symbol vocabularies produce -- orders keyed one way,
the quote book keyed another. The pre-existing alarm cannot catch it: it tests
`quotes_applied == 0`, and in a mismatch the quotes WERE applied, under names
nothing looks up.

This ships BEFORE any vocabulary change so that a migration error presents as
an alarm rather than as a successful-looking fill.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from bujji.broker.paper import PaperBroker
from bujji.core.enums import OptionType, Side
from bujji.core.models import OptionContract, OrderRequest


def _contract(symbol):
    return OptionContract(symbol=symbol, underlying="NIFTY", strike=24050.0,
                          option_type=OptionType.PE, expiry="2026-08-25", lot_size=65)


def _order(symbol, coid="C1"):
    return OrderRequest(contract=_contract(symbol), side=Side.SELL, quantity=65,
                        client_order_id=coid, limit_price=None,
                        reference_price=120.0, tag="t")


def _place(broker, symbol, coid="C1"):
    asyncio.run(broker.connect())
    return asyncio.run(broker.place_order(_order(symbol, coid)))


class TestTheDangerousMissIsRecorded:
    def test_a_populated_book_missing_the_symbol_records_a_miss(self):
        """THE case. Quotes exist -- under other names."""
        b = PaperBroker()
        b.set_quote("NIFTY2026-08-2524050PE", 119.0, 121.0)      # old vocabulary
        _place(b, "NSE:NIFTY2682524050PE")                        # new vocabulary
        assert b.quote_lookup_misses == ("NSE:NIFTY2682524050PE",)

    def test_the_mismatched_order_still_fills_which_is_why_this_matters(self):
        """The miss does not stop the fill -- it makes it VISIBLE. If the
        fill were blocked this test would be asserting a different design."""
        b = PaperBroker()
        b.set_quote("OLD-VOCAB", 119.0, 121.0)
        result = _place(b, "NEW-VOCAB")
        assert result.filled_quantity > 0
        assert b.quote_lookup_misses == ("NEW-VOCAB",)

    def test_both_mismatch_directions_are_caught(self):
        """old order + new quote, and new order + old quote."""
        for book, order in (("NIFTY2026-08-2524050PE", "NSE:NIFTY2682524050PE"),
                            ("NSE:NIFTY2682524050PE", "NIFTY2026-08-2524050PE")):
            b = PaperBroker()
            b.set_quote(book, 119.0, 121.0)
            _place(b, order)
            assert b.quote_lookup_misses == (order,)

    def test_repeated_misses_are_all_recorded(self):
        b = PaperBroker()
        b.set_quote("OLD", 119.0, 121.0)
        _place(b, "NEW-A", "C1")
        _place(b, "NEW-B", "C2")
        assert b.quote_lookup_misses == ("NEW-A", "NEW-B")


class TestTheBenignMissIsNotRecorded:
    def test_an_empty_book_records_nothing(self):
        """No sync ran. Frictionless fills are the documented behaviour for
        that configuration and are already alarmed by PAPER_MARKET_SYNC. Most
        tests construct a PaperBroker without ever setting a quote -- treating
        that as a mismatch would make the signal meaningless."""
        b = PaperBroker()
        _place(b, "ANY-SYMBOL")
        assert b.quote_lookup_misses == ()

    def test_a_matching_symbol_records_nothing(self):
        b = PaperBroker()
        b.set_quote("MATCHING", 119.0, 121.0)
        _place(b, "MATCHING")
        assert b.quote_lookup_misses == ()


class TestTheQuoteIsStillUsedWhenPresent:
    def test_a_present_quote_reaches_the_fill_simulator(self):
        """The lookup must still WORK -- this change is observability, not
        behaviour."""
        b = PaperBroker()
        b.set_quote("S", 100.0, 110.0)
        assert b._quotes_for("S") == (100.0, 110.0)

    def test_a_missing_quote_still_returns_the_none_pair(self):
        b = PaperBroker()
        b.set_quote("OTHER", 100.0, 110.0)
        assert b._quotes_for("S") == (None, None)


class TestTheRunnerActuallyReadsIt:
    """An unread signal is the defect this whole week keeps finding."""

    RUNNER = (REPO_ROOT / "bujji_options_os_runner.py").read_text()

    def test_the_runner_checks_after_every_entry_attempt(self):
        import ast
        fn = next(n for n in ast.walk(ast.parse(self.RUNNER))
                  if isinstance(n, ast.FunctionDef) and n.name == "_attempt_entry")
        assert "_check_quote_book_agreement" in ast.unparse(fn)

    def test_the_check_is_reached_before_the_unfilled_early_return(self):
        """A mismatched order that did NOT fill is still worth knowing about."""
        import ast
        fn = next(n for n in ast.walk(ast.parse(self.RUNNER))
                  if isinstance(n, ast.FunctionDef) and n.name == "_attempt_entry")
        body = ast.unparse(fn)
        check = body.index("_check_quote_book_agreement")
        unfilled = body.index("if not cycle_result or not cycle_result.filled")
        assert check < unfilled

    def test_it_escalates_and_records(self):
        import ast
        fn = next(n for n in ast.walk(ast.parse(self.RUNNER))
                  if isinstance(n, ast.FunctionDef) and n.name == "_check_quote_book_agreement")
        body = ast.unparse(fn)
        assert "critical" in body
        assert "quote_lookup_misses" in body

    def test_it_never_ends_a_session(self):
        import ast
        fn = next(n for n in ast.walk(ast.parse(self.RUNNER))
                  if isinstance(n, ast.FunctionDef) and n.name == "_check_quote_book_agreement")
        assert any(isinstance(n, ast.ExceptHandler) for n in ast.walk(fn))
