"""Capture wide, require narrow -- the operator's rule of 2026-08-22.

The gate shipped in 3e28dbc required a fresh tick from every symbol in the
~242-symbol capture universe. That is the rule INVERTED: the tiers are drawn on
open interest and are deliberately wider than trading justifies, so one
legitimately quiet far strike refused the whole session.

What blocks is the ELIGIBLE SELECTION BAND -- every contract
`_build_strike_evidence` ranks -- plus the underlying. The wide universe is
still graded and written, because "which symbols went quiet" is the evidence
the tick journal needs; it simply is not a veto.
"""
from __future__ import annotations

import ast
import io
import os
from dataclasses import dataclass
from typing import Optional
from unittest.mock import MagicMock

import pytest

from bujji.production_runtime.selection_band import (
    BAND_NOT_BROKER_REAL, BAND_NO_EXPIRY, BAND_NO_ROWS, BAND_OK, selection_band,
)

AS_OF = "2026-08-24"
NEAR = "2026-08-25"      # dte 1  -- inside [DEFAULT_MIN_DTE, DEFAULT_MAX_DTE]
FAR = "2026-09-29"       # dte 36 -- inside the window, but not nearest
BROKER = "BROKER_AUTHORITATIVE"


@dataclass(frozen=True)
class Row:
    expiry: str
    strike: float
    option_type: str
    instrument_symbol: str
    symbol_provenance: str = BROKER


def _chain(expiry=NEAR, strikes=(24200.0, 24250.0, 24300.0), provenance=BROKER):
    out = []
    _e = expiry.replace("-", "")
    for k in strikes:
        for t in ("CE", "PE"):
            # THE EXPIRY IS IN THE SYMBOL, exactly as FYERS encodes it
            # ("NSE:NIFTY2681821850PE"). A first version built the symbol from
            # strike and type alone, so two expiries produced identical strings
            # and dict.fromkeys deduped them -- test_band_excludes_other_expiries
            # passed even with the expiry filter removed. A negative control
            # caught it.
            out.append(Row(expiry, k, t,
                           f"NSE:NIFTY{_e}{int(k)}{t}", provenance))
    return out


# ------------------------------------------------------------------ the band
def test_band_is_the_chain_rows_at_the_chosen_expiry():
    band = selection_band(_chain(), AS_OF)
    assert band.state == BAND_OK and band.usable
    assert band.expiry == NEAR
    assert len(band.symbols) == 6


def test_band_excludes_other_expiries():
    """The selector ranges over ONE expiry. Requiring freshness from expiries
    it will not touch would re-create the over-wide gate this replaces."""
    band = selection_band(_chain(NEAR) + _chain(FAR), AS_OF)
    assert band.expiry == NEAR
    assert all("NIFTY" in s for s in band.symbols)
    assert len(band.symbols) == 6, band.symbols


def test_band_refuses_symbols_that_are_not_broker_real():
    """A bhavcopy or store symbol cannot be matched against the feed. Matching
    it anyway reports every symbol as never-requested -- an unsatisfiable gate
    with a misleading reason. It is refused loudly instead."""
    band = selection_band(_chain(provenance="ABSENT"), AS_OF)
    assert band.state == BAND_NOT_BROKER_REAL
    assert not band.usable


def test_band_fails_closed_on_an_empty_chain():
    assert selection_band([], AS_OF).state == BAND_NO_EXPIRY
    assert not selection_band([], AS_OF).usable


def test_band_fails_closed_when_no_expiry_is_in_the_dte_window():
    """0-DTE is refused by DEFAULT_MIN_DTE=1, so an expiry-day-only chain
    leaves the selector nothing to range over."""
    band = selection_band(_chain(expiry=AS_OF), AS_OF)
    assert band.state == BAND_NO_EXPIRY and not band.usable


def test_band_never_raises_on_junk_rows():
    class Junk:
        pass
    assert selection_band([Junk()], AS_OF).usable is False


# ---------------------------------------------------------- the runner gate
def _runner(band_chain, ages, requested=None, universe_symbols=None, spot_symbol=None):
    from bujji_options_os_runner import OptionsOSRunner
    from bujji.capture_universe.builder import KIND_SPOT, KIND_OPTION

    band = selection_band(band_chain, AS_OF)
    symbols = list(universe_symbols if universe_symbols is not None else band.symbols)
    insts = [MagicMock(kind=KIND_OPTION, symbol=s) for s in symbols]
    if spot_symbol:
        insts.append(MagicMock(kind=KIND_SPOT, symbol=spot_symbol))
        symbols = symbols + [spot_symbol]

    r = object.__new__(OptionsOSRunner)
    r._config = {"providers": {"tick_source": {"max_tick_age_seconds": 90.0}}}
    r._as_of_date = AS_OF
    r._universe_error = None
    r._universe = MagicMock(symbols=tuple(symbols), instruments=tuple(insts))
    r._universe_requested = tuple(requested if requested is not None else symbols)
    r._governor_result_summary = {}
    r._logger = MagicMock()
    r._tick_feed = MagicMock()
    r._tick_feed.tick_age_seconds.side_effect = lambda s: ages.get(s, 1.0)
    return r


def test_all_band_contracts_fresh_permits_entry():
    chain = _chain()
    r = _runner(chain, ages={})
    assert r._band_coverage_permits_entry(chain) is True
    assert r._governor_result_summary["band_coverage"]["blocking"] is True


def test_one_stale_band_contract_blocks_entry():
    """Stale, not absent. A contract that has not traded in an hour still sits
    in a freshly-fetched chain with a plausible price -- the dangerous case
    that chain age cannot see."""
    chain = _chain()
    stale = chain[0].instrument_symbol
    r = _runner(chain, ages={stale: 4000.0})
    assert r._band_coverage_permits_entry(chain) is False
    assert r._governor_result_summary["entry_blocked_by"] == "BAND_COVERAGE"


def test_one_silent_band_contract_blocks_entry():
    chain = _chain()
    r = _runner(chain, ages={chain[3].instrument_symbol: None})
    assert r._band_coverage_permits_entry(chain) is False


def test_a_silent_contract_OUTSIDE_the_band_does_not_block():
    """THE OPERATOR'S RULE, STATED DIRECTLY. A naturally inactive far-out
    contract is captured and recorded as silent, and does not block a trade
    whose decision never relied on it."""
    chain = _chain()
    far = "NSE:NIFTY26200CE"                       # captured, never ticks, not eligible
    r = _runner(chain, ages={far: None},
                universe_symbols=[c.instrument_symbol for c in chain] + [far])
    assert r._band_coverage_permits_entry(chain) is True


def test_a_band_symbol_that_was_never_subscribed_blocks_with_its_own_reason():
    """Otherwise a configuration mismatch between `strike_count` (which widens
    the band) and the tier table (which widens the universe) surfaces as a
    permanent, unexplained refusal."""
    chain = _chain()
    r = _runner(chain, ages={}, requested=[c.instrument_symbol for c in chain[:-2]])
    assert r._band_coverage_permits_entry(chain) is False
    assert r._governor_result_summary["entry_blocked_by"] == "BAND_NOT_SUBSCRIBED"


def test_a_stale_underlying_blocks_the_whole_band():
    """Every delta is computed against spot, so a stale spot mis-ranks the
    entire band at once."""
    chain = _chain()
    spot = "NSE:NIFTY50-INDEX"
    r = _runner(chain, ages={spot: 5000.0}, spot_symbol=spot)
    assert r._band_coverage_permits_entry(chain) is False


def test_no_tick_feed_is_not_applicable_not_a_silent_feed():
    chain = _chain()
    r = _runner(chain, ages={})
    r._universe_error = "NOT_APPLICABLE: this session has no websocket tick feed"
    assert r._band_coverage_permits_entry(chain) is True
    assert r._governor_result_summary["band_coverage"]["state"] == "NOT_APPLICABLE"


def test_wide_universe_is_recorded_and_never_blocks():
    chain = _chain()
    far = "NSE:NIFTY26200CE"
    r = _runner(chain, ages={far: None},
                universe_symbols=[c.instrument_symbol for c in chain] + [far])
    r._record_universe_coverage()                  # returns None -- cannot veto
    payload = r._governor_result_summary["universe_coverage"]
    assert payload["blocking"] is False
    assert payload["silent_count"] == 1            # still graded, still written
    assert "entry_blocked_by" not in r._governor_result_summary


# --------------------------------------------------------------- structural
RUNNER_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "bujji_options_os_runner.py")
RUNNER_SRC = io.open(RUNNER_PATH, encoding="utf-8").read()
RUNNER_AST = ast.parse(RUNNER_SRC)


def _method(name: str) -> str:
    for node in ast.walk(RUNNER_AST):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.unparse(node)
    raise AssertionError(f"{name} not found")


def test_record_universe_coverage_returns_nothing_so_it_cannot_veto():
    """A method that returns a bool invites `if not ...: return False` to come
    back. Returning None makes the non-blocking contract structural."""
    for node in ast.walk(RUNNER_AST):
        if isinstance(node, ast.FunctionDef) and node.name == "_record_universe_coverage":
            returns = [n for n in ast.walk(node) if isinstance(n, ast.Return) and n.value is not None]
            assert not returns, "a bare `return` only -- this gate must never carry a verdict out"
            return
    raise AssertionError("_record_universe_coverage not found")


def test_band_gate_runs_before_the_entry_is_attempted():
    """Coverage proven AFTER selection proves nothing: the strikes are chosen."""
    src = _method("_attempt_entry")
    assert "_band_coverage_permits_entry" in src
    assert src.index("_band_coverage_permits_entry") < src.index("attempt_entry(chain="), \
        "the band gate must precede the governor's attempt_entry"


def test_band_gate_reads_the_same_chain_the_selector_will_use():
    """Deriving the band from a second, independently fetched chain would let
    the gate check a different set than the selector ranges over."""
    src = _method("_attempt_entry")
    assert "self._band_coverage_permits_entry(chain)" in src


def _vix_identifiers(src: str):
    """Identifiers referencing VIX. Deliberately NOT a substring search over
    the file: the first version of this test grepped the source and tripped on
    this module's own docstring quoting the operator's rule, which contains the
    word. Comments and prose must not be able to fail -- or pass -- a premise
    check about what the CODE reads."""
    found = set()
    for node in ast.walk(ast.parse(src)):
        name = None
        if isinstance(node, ast.Name):
            name = node.id
        elif isinstance(node, ast.Attribute):
            name = node.attr
        elif isinstance(node, ast.keyword):
            name = node.arg
        elif isinstance(node, ast.arg):
            name = node.arg
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # AsyncFunctionDef is NOT a FunctionDef subclass. Omitting it made
            # this detector blind to `async def get_vix`, and the positive
            # control below is what caught that.
            name = node.name
        if name and "vix" in name.lower():
            found.add(name)
    return found


def test_vix_is_still_not_an_entry_input():
    """The band deliberately omits VIX because nothing consumes it. The day
    that changes, this fails and the gate must be revisited -- the operator's
    rule says 'VIX where relevant', and relevance is decided by the code."""
    # POSITIVE CONTROL FIRST. A detector that cannot find VIX anywhere would
    # pass this test forever while proving nothing.
    base = io.open(os.path.join(os.path.dirname(RUNNER_PATH), "bujji", "broker", "base.py"),
                   encoding="utf-8").read()
    assert _vix_identifiers(base), "the detector finds no VIX in broker/base.py -- it is broken"

    assert not _vix_identifiers(RUNNER_SRC), (
        "the runner now references VIX in code -- re-evaluate whether it belongs "
        "in the eligible selection band")

    warmup = io.open(os.path.join(os.path.dirname(RUNNER_PATH),
                                  "bujji", "regime_stability", "warmup.py"),
                     encoding="utf-8").read()
    assert "VixSnapshot(value=None)" in warmup, \
        "the production regime path no longer declares VIX absent"
