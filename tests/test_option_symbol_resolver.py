"""One origin for a broker option symbol, and it is never built.

THE DEFECT. Two vocabularies described the same contract: the chain spoke
the broker's ("NSE:NIFTY2681824100CE") and everything downstream rebuilt
its own from the leg ("NIFTY2026-08-2524500CE"). Measured live 2026-08-20:

    'NIFTY2026-08-2524500CE'  -> verified=False  total_margin=None
    'NSE:NIFTY26AUG22700PE'   -> verified=True   total_margin=98915.87

Every entry that session was blocked by a string format, and the same split
made every paper fill frictionless because the quote book was keyed by one
vocabulary while orders looked up the other.

These tests pin the cure: a lookup-only resolver, provenance-gated, sharing
ONE index between the margin gate and order construction.
"""
from __future__ import annotations

import ast
import inspect
import math
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from bujji.options_observation.engine import build_option_observation
from bujji.production_runtime import option_symbol_resolver as osr
from bujji.production_runtime.option_symbol_resolver import (
    OptionSymbolUnresolvable, build_symbol_index, quote_sync_eligible)

E1 = "2026-08-25"
E2 = "2026-09-01"
BROKER = "BROKER_AUTHORITATIVE"


def _row(strike, option_type, *, expiry=E1, symbol=None, provenance=BROKER,
         bid=None, ask=None, close=100.0):
    if symbol is None:
        symbol = f"NSE:NIFTY26AUG{int(strike)}{option_type}"
    return build_option_observation(
        underlying="NIFTY", instrument_symbol=symbol, strike=float(strike),
        expiry=expiry, option_type=option_type, exchange="NSE", segment="FO",
        timestamp="2026-08-21T09:20:00+05:30", resolution="SNAPSHOT",
        open_=None, high=None, low=None, close=close, settlement=None,
        volume=1000, open_interest=50000, change_in_open_interest=0,
        underlying_price=24113.0, origin="test",
        acquisition_timestamp="x", normalization_timestamp="x",
        bid=bid, ask=ask, symbol_provenance=provenance)


class _Leg:
    def __init__(self, strike, option_type, expiry=E1, ratio=1, side="SELL", premium=100.0):
        self.strike, self.option_type, self.expiry = strike, option_type, expiry
        self.ratio, self.side, self.premium = ratio, side, premium
        self.role = "SHORT"


class TestAValidLookup:
    def test_it_returns_the_chain_rows_symbol_verbatim(self):
        index = build_symbol_index([_row(24050, "PE", symbol="NSE:REAL_SYMBOL")])
        assert index.resolve(E1, 24050.0, "PE") == "NSE:REAL_SYMBOL"

    def test_the_synthetic_formula_is_never_the_answer(self):
        """Step 8's boundary case. The fixture deliberately makes the OLD
        formula produce a plausible-looking alternative, so an implementation
        that rebuilt instead of looking up cannot pass by luck."""
        index = build_symbol_index([_row(24050, "PE", symbol="NSE:REAL_SYMBOL")])
        got = index.resolve(E1, 24050.0, "PE")
        synthetic = f"NIFTY{E1}{24050}PE"
        assert got == "NSE:REAL_SYMBOL"
        assert got != synthetic
        assert "24050" not in got

    def test_an_int_strike_and_a_float_strike_find_the_same_row(self):
        index = build_symbol_index([_row(24050, "CE", symbol="NSE:X")])
        assert index.resolve(E1, 24050, "CE") == index.resolve(E1, 24050.0, "CE")

    def test_resolve_leg_reads_the_legs_own_fields(self):
        index = build_symbol_index([_row(24050, "CE", symbol="NSE:X")])
        assert index.resolve_leg(_Leg(24050.0, "CE")) == "NSE:X"


class TestProvenanceGating:
    """Eligibility is decided by recorded origin, never by string shape --
    a well-formed fake is still a fake."""

    @pytest.mark.parametrize("provenance", ["ABSENT", "SYNTHETIC"])
    def test_a_symbol_naming_no_real_contract_is_refused(self, provenance):
        """ABSENT carries the UNRESOLVED| sentinel only because the model
        cannot hold an empty instrument; SYNTHETIC was manufactured. Neither
        identifies a contract at any venue, so neither can be ordered or
        quoted -- refused everywhere, permanently."""
        index = build_symbol_index([_row(24050, "CE", provenance=provenance)])
        with pytest.raises(OptionSymbolUnresolvable) as exc:
            index.resolve(E1, 24050.0, "CE")
        assert exc.value.reason == osr.SYMBOL_NOT_AUTHORITATIVE

    def test_source_authoritative_resolves_because_it_names_a_real_contract(self):
        """NSE's FinInstrmNm is a REAL observed identity; what is unproven is
        whether FYERS accepts it. That only matters where a symbol leaves for
        a real venue, and exactly one production path does -- Gate B's SPAN
        call -- which `_guard_provider_vocabulary` closes at startup instead.

        Refusing it here would be redundant on that path and would make every
        bhavcopy-driven backtest unable to construct an order. See
        TestTheStartupGuard in test_single_symbol_vocabulary.py, which is what
        actually protects the venue."""
        index = build_symbol_index([_row(24050, "CE", provenance="SOURCE_AUTHORITATIVE",
                                          symbol="NIFTY26AUG24050CE")])
        assert index.resolve(E1, 24050.0, "CE") == "NIFTY26AUG24050CE"

    def test_a_row_that_declares_nothing_is_refused_not_trusted(self):
        """Authority is never granted by omission. A duck-typed row with no
        provenance attribute at all must not slip through."""
        class _Bare:
            expiry, strike, option_type = E1, 24050.0, "CE"
            instrument_symbol = "NSE:LOOKS_FINE"
        index = build_symbol_index([_Bare()])
        with pytest.raises(OptionSymbolUnresolvable) as exc:
            index.resolve(E1, 24050.0, "CE")
        assert exc.value.reason == osr.SYMBOL_NOT_AUTHORITATIVE

    def test_the_refusal_says_which_provenance_it_saw(self):
        index = build_symbol_index([_row(24050, "CE", provenance="SYNTHETIC")])
        with pytest.raises(OptionSymbolUnresolvable, match="SYNTHETIC"):
            index.resolve(E1, 24050.0, "CE")

    def test_a_blank_symbol_is_refused_even_when_authoritative(self):
        index = build_symbol_index([_row(24050, "CE", symbol="   ")])
        with pytest.raises(OptionSymbolUnresolvable) as exc:
            index.resolve(E1, 24050.0, "CE")
        assert exc.value.reason == osr.SYMBOL_ABSENT


class TestMalformedInput:
    def test_a_string_strike_is_refused_never_coerced(self):
        """`float("24050")` succeeding is the exact silent coercion this
        change removes: it would match a row never proven to be this one."""
        index = build_symbol_index([_row(24050, "CE")])
        with pytest.raises(OptionSymbolUnresolvable) as exc:
            index.resolve(E1, "24050", "CE")
        assert exc.value.reason == osr.MALFORMED_STRIKE

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf")])
    def test_nan_and_infinity_are_refused(self, bad):
        index = build_symbol_index([_row(24050, "CE")])
        with pytest.raises(OptionSymbolUnresolvable) as exc:
            index.resolve(E1, bad, "CE")
        assert exc.value.reason == osr.MALFORMED_STRIKE

    def test_a_boolean_cannot_index_a_strike(self):
        """isinstance(True, int) is True and True == 1.0 -- without an
        explicit bar, a bool would silently look up strike 1."""
        index = build_symbol_index([_row(1, "CE")])
        with pytest.raises(OptionSymbolUnresolvable) as exc:
            index.resolve(E1, True, "CE")
        assert exc.value.reason == osr.MALFORMED_STRIKE

    def test_each_missing_field_is_named_distinctly(self):
        index = build_symbol_index([_row(24050, "CE")])
        for args, reason in (
            ((None, 24050.0, "CE"), osr.MISSING_EXPIRY),
            ((E1, None, "CE"), osr.MISSING_STRIKE),
            ((E1, 24050.0, None), osr.MISSING_OPTION_TYPE),
            ((E1, 24050.0, "XX"), osr.INVALID_OPTION_TYPE),
        ):
            with pytest.raises(OptionSymbolUnresolvable) as exc:
                index.resolve(*args)
            assert exc.value.reason == reason, args


class TestDuplicates:
    def test_two_rows_for_one_contract_refuse_even_with_identical_symbols(self):
        """Agreement is not proof. Two rows for one contract means the CHAIN
        is malformed; answering anyway hides a provider defect."""
        index = build_symbol_index([_row(24050, "CE", symbol="NSE:SAME"),
                                     _row(24050, "CE", symbol="NSE:SAME")])
        with pytest.raises(OptionSymbolUnresolvable) as exc:
            index.resolve(E1, 24050.0, "CE")
        assert exc.value.reason == osr.DUPLICATE_CONTRACT

    def test_differing_symbols_also_refuse_rather_than_last_wins(self):
        index = build_symbol_index([_row(24050, "CE", symbol="NSE:A"),
                                     _row(24050, "CE", symbol="NSE:B")])
        with pytest.raises(OptionSymbolUnresolvable) as exc:
            index.resolve(E1, 24050.0, "CE")
        assert exc.value.reason == osr.DUPLICATE_CONTRACT
        assert "NSE:B" not in str(exc.value), "last-wins leaked into the answer"

    def test_an_authoritative_and_a_non_authoritative_row_still_refuse(self):
        index = build_symbol_index([_row(24050, "CE", symbol="NSE:A"),
                                     _row(24050, "CE", symbol="X", provenance="SYNTHETIC")])
        with pytest.raises(OptionSymbolUnresolvable) as exc:
            index.resolve(E1, 24050.0, "CE")
        assert exc.value.reason == osr.DUPLICATE_CONTRACT

    def test_the_diagnostic_names_the_contract_without_dumping_the_row(self):
        index = build_symbol_index([_row(24050, "CE"), _row(24050, "CE")])
        with pytest.raises(OptionSymbolUnresolvable) as exc:
            index.resolve(E1, 24050.0, "CE")
        message = str(exc.value)
        assert "24050" in message and "CE" in message and E1 in message
        assert "open_interest" not in message and "payload" not in message

    def test_a_duplicate_does_not_poison_its_neighbours(self):
        index = build_symbol_index([_row(24050, "CE"), _row(24050, "CE"),
                                     _row(24100, "CE", symbol="NSE:CLEAN")])
        assert index.resolve(E1, 24100.0, "CE") == "NSE:CLEAN"


class TestExpiryDiscrimination:
    def test_a_calendar_resolves_its_two_expiries_independently(self):
        """CALENDAR emits two legs at the SAME strike, both CE, differing
        only by expiry (msi_trade_construction.py:474-487). A 2-tuple key
        collapses them onto one row -- which is what Gate B used to do."""
        index = build_symbol_index([_row(24050, "CE", expiry=E1, symbol="NSE:NEAR"),
                                     _row(24050, "CE", expiry=E2, symbol="NSE:FAR")])
        assert index.resolve(E1, 24050.0, "CE") == "NSE:NEAR"
        assert index.resolve(E2, 24050.0, "CE") == "NSE:FAR"

    def test_same_strike_two_expiries_is_not_a_duplicate(self):
        index = build_symbol_index([_row(24050, "CE", expiry=E1),
                                     _row(24050, "CE", expiry=E2)])
        assert len(index) == 2

    def test_a_wrong_expiry_is_diagnosed_as_such_and_names_the_real_ones(self):
        index = build_symbol_index([_row(24050, "CE", expiry=E1),
                                     _row(24050, "CE", expiry=E2)])
        with pytest.raises(OptionSymbolUnresolvable) as exc:
            index.resolve("2026-12-31", 24050.0, "CE")
        assert exc.value.reason == osr.EXPIRY_MISMATCH
        assert E1 in str(exc.value) and E2 in str(exc.value)

    def test_a_genuinely_absent_contract_is_no_match_not_expiry_mismatch(self):
        index = build_symbol_index([_row(24050, "CE")])
        with pytest.raises(OptionSymbolUnresolvable) as exc:
            index.resolve(E1, 99999.0, "CE")
        assert exc.value.reason == osr.NO_MATCH

    def test_ce_and_pe_at_one_strike_never_cross(self):
        index = build_symbol_index([_row(24050, "CE", symbol="NSE:CALL"),
                                     _row(24050, "PE", symbol="NSE:PUT")])
        assert index.resolve(E1, 24050.0, "CE") == "NSE:CALL"
        assert index.resolve(E1, 24050.0, "PE") == "NSE:PUT"

    def test_a_wrong_expiry_is_still_diagnosed_for_an_ineligible_row(self):
        """The strike/type index is populated BEFORE provenance is checked,
        so an operator is not told 'no match' when the contract is right
        there under another expiry."""
        index = build_symbol_index([_row(24050, "CE", expiry=E2, provenance="ABSENT")])
        with pytest.raises(OptionSymbolUnresolvable) as exc:
            index.resolve(E1, 24050.0, "CE")
        assert exc.value.reason == osr.EXPIRY_MISMATCH


class TestItNeverConstructs:
    @staticmethod
    def _symbol_builders(source: str):
        """f-strings that CONCATENATE contract fields -- the shape of a
        symbol, as opposed to a message.

        The rule is about literal text, because that is what actually
        distinguishes them. A symbol is fields butted together with nothing
        between: f"{underlying}{expiry}{int(strike)}{option_type}". A message
        always has words in it, even when two fields happen to touch --
        Gate B's own diagnostic is f"{leg.option_type}{int(leg.strike)}
        [{exc.reason}: {exc}]", which is CE24050 [NO_MATCH: ...] and is not a
        symbol. So: three or more fields in an unbroken run, or an f-string
        made of nothing but fields.
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

    def test_the_module_contains_no_option_symbol_builder(self):
        assert self._symbol_builders(Path(osr.__file__).read_text()) == []

    def test_that_check_can_actually_fail(self):
        """Positive control. An absence claim from a search I wrote is worth
        nothing until the search is shown capable of finding something."""
        assert self._symbol_builders(
            's = f"{u}{leg.expiry}{int(leg.strike)}{leg.option_type}"')
        assert self._symbol_builders('s = f"leg {a} at strike {b}"') == []
        # Gate B's real diagnostic: two adjacent fields, but it is a message.
        assert self._symbol_builders(
            's = f"{leg.option_type}{int(leg.strike)} [{exc.reason}: {exc}]"') == []

    def test_it_never_adds_or_strips_an_exchange_prefix(self):
        source = Path(osr.__file__).read_text()
        for forbidden in ('"NSE:"', "'NSE:'", ".lstrip(", ".removeprefix(", ".upper()", ".lower()"):
            assert forbidden not in source.split('"""')[-1], \
                f"the resolver transforms symbols: {forbidden}"

    def test_an_unusual_symbol_survives_untouched(self):
        """Whatever the venue said, character for character."""
        weird = "  NSE:NIFTY26AUG24050ce  "
        index = build_symbol_index([_row(24050, "CE", symbol=weird)])
        assert index.resolve(E1, 24050.0, "CE") == weird


class TestRejectionsAreCounted:
    """A chain whose rows were all quietly dropped must never be
    indistinguishable from a genuinely empty one."""

    def test_a_clean_chain_rejects_nothing(self):
        index = build_symbol_index([_row(24050, "CE"), _row(24100, "PE")])
        assert len(index) == 2
        assert index.rejected == {}

    def test_duplicates_are_counted_and_both_rows_withheld(self):
        index = build_symbol_index([_row(24050, "CE"), _row(24050, "CE")])
        assert len(index) == 0
        assert index.rejected == {osr.DUPLICATE_CONTRACT: 1}

    def test_an_unkeyable_row_is_counted_not_silently_skipped(self):
        class _Junk:
            expiry, strike, option_type = None, None, None
            instrument_symbol, symbol_provenance = "NSE:X", BROKER
        index = build_symbol_index([_Junk(), _row(24050, "CE")])
        assert len(index) == 1
        assert index.rejected == {osr.MISSING_EXPIRY: 1}

    def test_the_no_match_message_discloses_what_was_rejected(self):
        class _Junk:
            expiry, strike, option_type = None, None, None
            instrument_symbol, symbol_provenance = "NSE:X", BROKER
        index = build_symbol_index([_Junk()])
        with pytest.raises(OptionSymbolUnresolvable, match="MISSING_EXPIRY"):
            index.resolve(E1, 24050.0, "CE")


class TestQuoteSyncEligibility:
    def test_it_is_exactly_the_set_the_resolver_accepts(self):
        """ONE predicate, not two that happen to agree. An order and its
        quote must carry the same string, so anything orderable must be
        quotable -- two sets would be two things to drift, which is the
        defect class this module ends."""
        for provenance in ("BROKER_AUTHORITATIVE", "SOURCE_AUTHORITATIVE"):
            row = _row(24050, "CE", provenance=provenance)
            assert quote_sync_eligible(row)
            assert build_symbol_index([row]).resolve(E1, 24050.0, "CE")
        for provenance in ("ABSENT", "SYNTHETIC"):
            row = _row(24050, "CE", provenance=provenance)
            assert not quote_sync_eligible(row)
            with pytest.raises(OptionSymbolUnresolvable):
                build_symbol_index([row]).resolve(E1, 24050.0, "CE")

    @pytest.mark.parametrize("provenance", ["ABSENT", "SYNTHETIC"])
    def test_sentinel_and_synthetic_rows_key_no_quotes(self, provenance):
        assert not quote_sync_eligible(_row(24050, "CE", provenance=provenance))

    def test_a_row_declaring_nothing_keys_no_quotes(self):
        class _Bare:
            instrument_symbol = "NSE:X"
        assert not quote_sync_eligible(_Bare())
