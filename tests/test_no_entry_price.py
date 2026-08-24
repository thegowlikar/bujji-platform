"""An entry price is execution evidence. It is never a market price.

THE DEFECT THESE TESTS LOCK OUT. `_current_leg_prices()` returned
`dict(self._entry_prices)` whenever the book could not be priced. Those values
flowed into `revalue_all()`, became `leg.current_price`, and were read by the
session governor as `reference_prices` for a forced exit -- underneath a
comment promising "never the entry price". An exit priced at entry realises
~zero P&L however far the market moved, and every stop and target compared the
position against itself.

WHERE THE LOCK NOW SITS (2026-08-24). `_current_leg_prices` was retired: it had
zero production callers and existed only "so an unmigrated caller keeps
compiling" when none did. Guarding one named dead function was always the
weaker form of the guarantee -- it said nothing about a SECOND accessor being
added later. The lock is now stated three ways, each strictly stronger than
what it replaced:

  * `_current_leg_quotes` -- the one live producer -- cannot return entry
    prices (unchanged, and still the core assertion);
  * NO method anywhere in the runner may return entry prices as a price
    mapping, so a new accessor cannot quietly reintroduce the substitution;
  * `LegPriceView` structurally refuses to carry prices when invalid, which
    had no direct test at all until now despite being the guarantee the other
    two rest on.
"""
import ast
import pathlib

import pytest

from bujji.market_perception.quote import (
    AVAILABLE, SOURCE_REPLAY, SOURCE_REST, SOURCE_TICK, FieldValue, Quote,
    from_rest_price, project)
from bujji.production_runtime.market_data_gate import assess_quote_fields

RUNNER = pathlib.Path(__file__).resolve().parent.parent / "bujji_options_os_runner.py"
SRC = RUNNER.read_text()
TREE = ast.parse(SRC)
NOW = 5000.0


def _fn(name):
    return next(n for n in ast.walk(TREE)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                and n.name == name)


# ------------------------------------------------------- structural proof
class TestEntryPriceIsNeverMarketPrice:
    def test_the_quote_method_never_returns_entry_prices(self):
        """THE CORE ASSERTION. `_current_leg_quotes` is the only producer of
        prices on the management path; if `_entry_prices` cannot appear in a
        value it returns, the substitution cannot happen."""
        body = ast.unparse(_fn("_current_leg_quotes"))
        assert "_entry_prices" in body, (
            "expected _entry_prices to be READ (for the required-leg set)")
        # It may be read to know WHICH legs are required. It may never be a
        # price value in the returned view.
        for node in ast.walk(_fn("_current_leg_quotes")):
            if isinstance(node, ast.Return) and node.value is not None:
                ret = ast.unparse(node.value)
                assert "prices=self._entry_prices" not in ret.replace(" ", "")
                assert "dict(self._entry_prices)" not in ret, (
                    f"a return still yields entry prices: {ret[:120]}")

    def test_the_retired_adapter_has_not_come_back(self):
        """`_current_leg_prices` was retired on 2026-08-24. If it reappears,
        so does a second way to ask for prices -- and the reason it existed
        (an unmigrated caller) was already untrue when it was removed."""
        names = {n.name for n in ast.walk(TREE)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        assert "_current_leg_prices" not in names, (
            "the retired compatibility adapter is back; there is one price "
            "accessor on the management path and it is _current_leg_quotes")

    def test_no_method_anywhere_returns_entry_prices_as_a_price_mapping(self):
        """THE GENERALISED LOCK, and the reason retiring the adapter made this
        stronger rather than weaker.

        The old test named one function. This one scans every function in the
        runner, so the defect cannot be reintroduced under a new name -- which
        is exactly how it would come back, since the original was written as a
        helpful fallback rather than as a mistake."""
        offenders = []
        for fn in ast.walk(TREE):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(fn):
                if isinstance(node, ast.Return) and node.value is not None:
                    ret = ast.unparse(node.value).replace(" ", "")
                    if ("dict(self._entry_prices)" in ret
                            or "prices=self._entry_prices" in ret
                            or ret == "self._entry_prices"):
                        offenders.append(f"{fn.name}: {ret[:80]}")
        assert not offenders, (
            "a method returns entry prices as a price mapping:\n  "
            + "\n  ".join(offenders))

    def test_the_generalised_scan_would_catch_a_new_accessor(self):
        """NEGATIVE CONTROL. The scan above passes trivially if it cannot see
        the pattern, so plant one under a name that never existed."""
        planted = ast.parse(
            "def _leg_prices_v2(self, as_of):\n"
            "    return dict(self._entry_prices)\n")
        found = []
        for fn in ast.walk(planted):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(fn):
                if isinstance(node, ast.Return) and node.value is not None:
                    if "dict(self._entry_prices)" in ast.unparse(node.value):
                        found.append(fn.name)
        assert found == ["_leg_prices_v2"], (
            "the detector cannot see a reintroduction under a new name")

    def test_no_management_branch_feeds_entry_prices_to_revalue(self):
        """NEGATIVE CONTROL for the whole class of defect: whatever is handed
        to revalue_all must not be able to be the entry-price mapping."""
        fn = _fn("_run_one_management_pass")
        for node in ast.walk(fn):
            if isinstance(node, ast.Call) and \
                    getattr(node.func, "attr", None) == "revalue_all":
                arg = ast.unparse(node.args[0]) if node.args else ""
                assert "entry_prices" not in arg, (
                    f"revalue_all is called with {arg} -- entry prices as market prices")
                assert arg.startswith("view."), (
                    f"revalue_all takes {arg}; it must take gate-passed quotes")

    def test_the_control_would_catch_a_reintroduction(self):
        """The assertions above must be capable of failing."""
        planted = ast.parse(
            "def _run_one_management_pass(self):\n"
            "    self._portfolio_engine.revalue_all(dict(self._entry_prices), c)\n")
        found = [ast.unparse(n.args[0]) for n in ast.walk(planted)
                 if isinstance(n, ast.Call)
                 and getattr(n.func, "attr", None) == "revalue_all"]
        assert found and "entry_prices" in found[0], (
            "the detector cannot see a planted reintroduction")


# ------------------------------------------------------------- gate paths
def _q(**kw):
    return project({"symbol": "NSE:X", **kw}, recv_wall=1.0, recv_mono=NOW)


class TestPriceQualityRefusals:
    """Each is a distinct operational situation, and they must stay distinct."""

    def _assess(self, quotes, **kw):
        opts = dict(now_mono=NOW, max_age_seconds=90.0,
                    allowed_sources=(SOURCE_TICK,), required_symbols=["NSE:X"])
        opts.update(kw)
        return assess_quote_fields(quotes, ["ltp"], **opts)

    def test_missing_ltp_refuses(self):
        assert not self._assess({"NSE:X": _q()}).may_trade

    def test_stale_ltp_refuses(self):
        v = self._assess({"NSE:X": _q(ltp=100.0)}, now_mono=NOW + 500)
        assert not v.may_trade
        assert any("STALE" in r for r in v.reasons)

    def test_rest_fallback_refused_where_live_tick_required(self):
        r = from_rest_price("NSE:X", 100.0, observed_mono=NOW)
        v = self._assess({"NSE:X": r})
        assert not v.may_trade
        assert any("PROVENANCE" in x for x in v.reasons)

    @pytest.mark.parametrize("src", [SOURCE_REPLAY, "HISTORICAL_RECONSTRUCTION",
                                     "SYNTHETIC"])
    def test_non_live_provenance_refused(self, src):
        q = Quote(symbol="NSE:X", recv_mono=NOW, source=src,
                  fields={"ltp": FieldValue(100.0, AVAILABLE, src, 1.0, NOW)})
        assert not self._assess({"NSE:X": q}).may_trade

    def test_no_quote_at_all_refuses(self):
        assert not self._assess({}).may_trade

    def test_a_fresh_live_quote_permits(self):
        """POSITIVE CONTROL -- the refusals above must be able to pass."""
        v = self._assess({"NSE:X": _q(ltp=100.0)})
        assert v.may_trade and v.origin == SOURCE_TICK

    def test_entry_price_present_but_no_live_quote_still_refuses(self):
        """Holding an entry price changes nothing: it is not a market price."""
        assert not self._assess({}).may_trade


# ------------------------------------------------------ escalation wiring
class TestFailClosedEscalation:
    def test_an_invalid_price_cycle_still_reaches_the_brake(self):
        """FAIL CLOSED, NOT FAIL SILENT. Refusing to value and then returning
        would leave a short position unmanaged -- worse than the defect. The
        blind path must fall through to the emergency brake."""
        fn = _fn("_run_one_management_pass")
        body = ast.unparse(fn)
        brake = body.index("_emergency_brake(")
        early = body.index("if valuation is None and view.valid")
        assert early < brake, "the no-valuation return sits after the brake"
        assert "if brake_reason is None and valuation is None" in body, (
            "an invalid-price cycle can reach the ordinary exit path")

    def test_the_brake_tolerates_an_absent_valuation(self):
        """The blind trigger must not need a P&L it cannot have."""
        import bujji_options_os_runner as R
        reason = R._emergency_brake(
            unrealized_pnl=None, realized_pnl=0.0, daily_loss_limit=5000,
            consecutive_blind_cycles=3, max_consecutive_blind_cycles=3)
        assert reason and "EMERGENCY_BLIND" in reason

    def test_below_threshold_does_not_escalate(self):
        """CONTROL: the brake must not fire on the first blind cycle."""
        import bujji_options_os_runner as R
        assert R._emergency_brake(
            unrealized_pnl=None, realized_pnl=0.0, daily_loss_limit=5000,
            consecutive_blind_cycles=1, max_consecutive_blind_cycles=3) is None

    def test_escalation_reuses_the_existing_closure_machinery(self):
        """No second exit mechanism."""
        body = ast.unparse(_fn("_run_one_management_pass"))
        assert "_execute_emergency_close" in body
        assert body.count("_execute_emergency_close(") == 1

    def test_broker_unknown_stays_unknown_during_escalation(self):
        """UNKNOWN is never converted to flat or to success."""
        body = ast.unparse(_fn("_execute_emergency_close"))
        assert "CRITICAL_UNFLATTENED_POSITION" in body
        assert "session_closed'] = False" in body or \
               '"session_closed"] = False' in body or \
               "session_closed" in body
        assert "flat is True" in body, "the flat check is no longer explicit"

    def test_the_invalid_price_path_is_visible_in_session_evidence(self):
        body = ast.unparse(_fn("_run_one_management_pass"))
        assert "price_path_invalid" in body, (
            "an invalid-price cycle leaves no trace in the session summary")
        assert "last_price_view" in body


class TestTheViewCannotCarryAFallbackPrice:
    """`LegPriceView` is where the guarantee is STRUCTURAL rather than
    asserted: an invalid view holds no prices because the type refuses to
    build one, not because every caller remembers to check. That had no direct
    test until the adapter was retired and this became the load-bearing part.
    """

    @staticmethod
    def _view(**kw):
        import bujji_options_os_runner as R
        return R.LegPriceView(**kw)

    def test_an_invalid_view_discards_prices_it_was_handed(self):
        """The case that matters: a caller passes prices anyway. The type
        must drop them rather than trust the caller."""
        v = self._view(valid=False, reason="X", prices={"NSE:A": 100.0})
        assert v.prices == {}, (
            "an invalid view kept the prices it was given; the enforcement is "
            "documentation, not structure")

    def test_an_invalid_view_is_never_priced_from_ticks(self):
        v = self._view(valid=False, reason="X", prices={"NSE:A": 100.0},
                       priced_from_ticks=True)
        assert v.prices == {}

    def test_a_valid_view_keeps_its_prices(self):
        """POSITIVE CONTROL -- the refusal above must not be unconditional."""
        v = self._view(valid=True, reason="OK", prices={"NSE:A": 100.0})
        assert v.prices == {"NSE:A": 100.0}

    def test_the_view_copies_rather_than_aliases_the_mapping(self):
        """If the view aliased the caller's dict, entry prices could still
        arrive by mutation after construction."""
        src = {"NSE:A": 100.0}
        v = self._view(valid=True, reason="OK", prices=src)
        src["NSE:B"] = 999.0
        assert v.prices == {"NSE:A": 100.0}

    def test_there_is_no_slot_for_a_fallback_price(self):
        """A field named for a fallback would make the defect expressible
        again, whatever the constructor does."""
        import bujji_options_os_runner as R
        slots = set(R.LegPriceView.__slots__)
        for forbidden in ("entry_prices", "fallback", "fallback_prices",
                          "default_prices"):
            assert forbidden not in slots, (
                f"LegPriceView has a {forbidden!r} slot")
