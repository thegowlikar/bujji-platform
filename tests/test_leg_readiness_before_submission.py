"""Stage 2: the exact legs and hedges, graded independently before submission.

The operator's rule (2026-08-22): "After it selects a proposed trade but before
it submits an order, the exact legs and hedges must independently pass freshness
and field-completeness checks."

There was no such gate. Between strike selection (trading_brain_runtime.py, the
`construct_trade` call) and submission the ONLY per-leg validation was
`SymbolIndex.resolve_leg`, which checks identity and provenance and explicitly
refuses to inspect price, age or any field -- "PROVENANCE, NOT SHAPE".
"""
from __future__ import annotations

import ast
import io
import os
from unittest.mock import MagicMock

import pytest

from bujji.production_runtime.leg_readiness import (
    LEG_INCOMPLETE, LEG_NO_QUOTE, LEG_READY, LEG_SILENT, LEG_STALE, LEG_UNRESOLVED,
    NOT_READY, READY, UNKNOWN, LegQuote, evaluate_leg_readiness,
)

MAX_AGE = 90.0


class Leg:
    __slots__ = ("symbol", "role")

    def __init__(self, symbol, role="SHORT"):
        self.symbol, self.role = symbol, role


def _good(premium=100.0, bid=99.0, ask=101.0):
    return LegQuote(premium=premium, bid=bid, ask=ask)


def _eval(legs, ages, quotes):
    return evaluate_leg_readiness(legs, tick_ages=ages, quotes=quotes,
                                  max_age_seconds=MAX_AGE)


# ------------------------------------------------------------ the happy path
def test_fresh_and_complete_legs_are_ready():
    legs = [Leg("A"), Leg("B", "WING_UPPER")]
    v = _eval(legs, {"A": 1.0, "B": 2.0}, {"A": _good(), "B": _good()})
    assert v.state == READY and v.permits_entry
    assert {l.state for l in v.legs} == {LEG_READY}


# ------------------------------------------------------------- freshness half
def test_a_silent_leg_refuses():
    v = _eval([Leg("A"), Leg("B")], {"A": 1.0, "B": None}, {"A": _good(), "B": _good()})
    assert not v.permits_entry and v.state == NOT_READY
    assert [l.state for l in v.legs] == [LEG_READY, LEG_SILENT]


def test_a_stale_leg_refuses():
    v = _eval([Leg("A")], {"A": MAX_AGE + 1}, {"A": _good()})
    assert not v.permits_entry and v.legs[0].state == LEG_STALE


def test_an_unreadable_age_is_silence_not_freshness():
    v = _eval([Leg("A")], {"A": "not-a-number"}, {"A": _good()})
    assert v.legs[0].state == LEG_SILENT


def test_a_leg_with_no_broker_symbol_refuses():
    v = _eval([Leg(None)], {}, {})
    assert v.legs[0].state == LEG_UNRESOLVED and not v.permits_entry


# ------------------------------------------------- field-completeness half
def test_a_ticking_leg_with_no_book_row_refuses():
    """Freshness and completeness are different failures. A tick proves the
    pipe is live; it says nothing about a two-sided book."""
    v = _eval([Leg("A")], {"A": 1.0}, {})
    assert v.legs[0].state == LEG_NO_QUOTE


@pytest.mark.parametrize("quote,missing", [
    (LegQuote(premium=100.0, bid=None, ask=101.0), "bid"),
    (LegQuote(premium=100.0, bid=99.0, ask=None), "ask"),
    (LegQuote(premium=None, bid=99.0, ask=101.0), "premium"),
    (LegQuote(premium=100.0, bid=0.0, ask=101.0), "bid"),
])
def test_an_incomplete_book_refuses(quote, missing):
    v = _eval([Leg("A")], {"A": 1.0}, {"A": quote})
    assert v.legs[0].state == LEG_INCOMPLETE
    assert missing in v.legs[0].detail


def test_a_crossed_book_refuses():
    """A live feed disagreeing with itself, reported apart from a missing
    field because the cause is different."""
    v = _eval([Leg("A")], {"A": 1.0}, {"A": LegQuote(premium=100.0, bid=101.0, ask=99.0)})
    assert v.legs[0].state == LEG_INCOMPLETE
    assert "crossed" in v.legs[0].detail


def test_the_motivating_case_a_wing_with_no_premium():
    """THE DEFECT THIS EXISTS FOR. `_candidates_for_type` requires a solved
    delta, but `_nearest_grid` and `_at_strike` -- which pick every wing --
    iterate the UNFILTERED evidence. So a hedge can be a contract whose
    premium was absent while the short it protects is guaranteed one. The
    protective side is the least-validated part of the structure and is also
    submitted last."""
    legs = [Leg("SHORT_CE", "SHORT"), Leg("SHORT_PE", "SHORT"),
            Leg("WING_CE", "WING_UPPER"), Leg("WING_PE", "WING_LOWER")]
    ages = {l.symbol: 1.0 for l in legs}
    quotes = {l.symbol: _good() for l in legs}
    quotes["WING_CE"] = LegQuote(premium=None, bid=None, ask=None)
    v = _eval(legs, ages, quotes)
    assert not v.permits_entry
    bad = [l for l in v.legs if l.state != LEG_READY]
    assert len(bad) == 1 and bad[0].role == "WING_UPPER"


# ---------------------------------------------------------------- verdicts
def test_no_legs_at_all_is_unknown_not_permitted():
    v = _eval([], {}, {})
    assert v.state == UNKNOWN and not v.permits_entry


def test_nothing_priceable_is_unknown_not_merely_not_ready():
    v = _eval([Leg("A"), Leg("B")], {"A": None, "B": None}, {})
    assert v.state == UNKNOWN and not v.permits_entry


def test_every_failing_leg_is_reported_not_just_the_first():
    legs = [Leg("A"), Leg("B"), Leg("C")]
    v = _eval(legs, {"A": 1.0, "B": None, "C": MAX_AGE + 5},
              {"A": _good(), "B": _good(), "C": _good()})
    assert len(v.reasons) == 2, v.reasons


# ------------------------------------------------------------- placement
RUNTIME_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "bujji", "production_runtime", "trading_brain_runtime.py")
RUNTIME_SRC = io.open(RUNTIME_PATH, encoding="utf-8").read()
RUNNER_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "bujji_options_os_runner.py")
RUNNER_SRC = io.open(RUNNER_PATH, encoding="utf-8").read()


def _fn(src, name):
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.unparse(node)
    raise AssertionError(f"{name} not found")


def test_the_gate_precedes_the_only_place_an_order_is_built():
    """`_build_order_requests` is documented as "the ONLY place a production
    OrderRequest is built". Refusing after it would mean refusing an order
    that already exists."""
    src = _fn(RUNTIME_SRC, "process_entry_cycle")
    assert "evaluate_leg_readiness" in src
    assert src.index("evaluate_leg_readiness") < src.index("_build_order_requests"), \
        "the leg gate must run before any OrderRequest is constructed"


def test_the_gate_grades_the_same_chain_the_strikes_came_from():
    src = _fn(RUNTIME_SRC, "process_entry_cycle")
    assert "for row in chain or ()" in src.replace("(chain or ())", "chain or ()")


def test_no_tick_source_is_not_applicable_not_a_silent_feed():
    src = _fn(RUNTIME_SRC, "process_entry_cycle")
    assert "if tick_age_fn is None:" in src
    assert "NOT_APPLICABLE" in src


def test_the_runner_actually_supplies_the_tick_source():
    """BUILT-NOT-WIRED. A gate handed tick_age_fn=None grades NOT_APPLICABLE
    forever and every test above still passes."""
    assert "tick_age_fn=" in RUNNER_SRC, "the runner never passes tick_age_fn"
    assert "max_tick_age_seconds=self._max_tick_age()" in RUNNER_SRC


def test_leg_tick_age_is_none_without_a_feed_and_never_raises():
    import bujji_options_os_runner as runner

    r = object.__new__(runner.OptionsOSRunner)
    r._tick_feed = None
    assert r._leg_tick_age("X") is None

    feed = MagicMock()
    feed.tick_age_seconds.side_effect = RuntimeError("boom")
    r._tick_feed = feed
    assert r._leg_tick_age("X") is None      # unreadable is silence, not zero


def test_a_refusing_verdict_returns_a_blocked_result_not_a_submission():
    """The gate existing is not the same as the gate stopping anything. The
    refusal must sit BETWEEN the verdict and order construction, and it must
    return an unfilled result carrying why."""
    src = _fn(RUNTIME_SRC, "process_entry_cycle")
    between = src[src.index("evaluate_leg_readiness"):src.index("_build_order_requests")]
    assert "if not readiness.permits_entry" in between
    assert "filled=False" in between
    assert "LEG_NOT_READY" in between
