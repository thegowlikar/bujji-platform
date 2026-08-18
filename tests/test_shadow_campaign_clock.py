"""The shadow campaign must stamp cycles with real time, not a virtual one.

THE DEFECT. `now` was initialised to `open_dt` and advanced by exactly the
sleep interval, so cycle k was stamped 09:15 + k*5min regardless of when the
process started -- while the data inside it came from whenever the fetch
actually ran. Observed 2026-08-17: process started 09:01:47, first artifact
stamped 2026-08-17T09:15:00+05:30. Every artifact this unit has written
carries that offset, and the early cycles of an early start describe a
market that had not opened.

WHY THE FETCHES CANNOT SIMPLY HONOUR `as_of`. `get_recent_candles` takes a
count, not a point in time, and `get_vix` has no historical form. Both
return the latest real values. Given that, the only label that can be true
is the wall-clock time at which the data was read.

Asserted over the PARSED SYNTAX TREE rather than raw text: this file's own
explanation quotes the defective expression, and a substring search would
match the explanation and report the bug as still present. That failure mode
has bitten this codebase repeatedly -- structure, not prose.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

SCRIPT = Path("/opt/bujji/app/scripts/run_phase20_13_live_entrypoint.py")

pytestmark = pytest.mark.skipif(not SCRIPT.exists(), reason="VPS-only script")


def _tree() -> ast.AST:
    return ast.parse(SCRIPT.read_text())


def _all_nodes():
    return list(ast.walk(_tree()))


class TestTheVirtualClockIsGone:
    def test_now_is_never_initialised_to_the_market_open(self):
        """`now = open_dt` was the seed of the whole defect: it declared
        the first observation to be the open, whatever the real time."""
        for node in _all_nodes():
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target, value = node.targets[0], node.value
                if (isinstance(target, ast.Name) and target.id == "now"
                        and isinstance(value, ast.Name) and value.id == "open_dt"):
                    pytest.fail("`now = open_dt` is back -- the virtual clock has returned")

    def test_the_timestamp_is_never_advanced_by_arithmetic(self):
        """`now = now.fromtimestamp(now.timestamp() + interval)` made every
        later label a pure function of the START time, so a late start
        back-dated the entire session."""
        for node in _all_nodes():
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr == "fromtimestamp":
                    value = node.func.value
                    if isinstance(value, ast.Name) and value.id == "now":
                        pytest.fail("`now.fromtimestamp(...)` is back -- labels are computed again")


class TestWallClockIsTheSource:
    def test_a_wall_clock_reader_exists(self):
        names = {n.name for n in _all_nodes() if isinstance(n, ast.FunctionDef)}
        assert "_wall_now" in names

    def test_the_wall_clock_reader_calls_datetime_now(self):
        for node in _all_nodes():
            if isinstance(node, ast.FunctionDef) and node.name == "_wall_now":
                calls = [n for n in ast.walk(node) if isinstance(n, ast.Call)]
                assert any(
                    isinstance(c.func, ast.Attribute) and c.func.attr == "now" for c in calls
                ), "must read the real clock"
                return
        pytest.fail("_wall_now not found")

    def test_the_cycle_stamp_comes_from_the_wall_clock(self):
        """`now` must be assigned from _wall_now() -- that is the whole fix.
        Everything downstream already stamps `now.isoformat()`."""
        assigned_from_wall = False
        for node in _all_nodes():
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target, value = node.targets[0], node.value
                if (isinstance(target, ast.Name) and target.id == "now"
                        and isinstance(value, ast.Call)
                        and isinstance(value.func, ast.Name)
                        and value.func.id == "_wall_now"):
                    assigned_from_wall = True
        assert assigned_from_wall, "the cycle label is not read from the real clock"


class TestPreMarketIsRefused:
    def test_it_waits_rather_than_observing_before_the_open(self):
        """A pre-market read stamped as a market cycle is the fabrication
        this codebase forbids everywhere else."""
        source = SCRIPT.read_text()
        assert "seconds_until_open" in source
        # The wait must be conditional on actually being early, not blind.
        tree = _tree()
        guarded = False
        for node in ast.walk(tree):
            if isinstance(node, ast.If):
                test_names = {n.id for n in ast.walk(node.test) if isinstance(n, ast.Name)}
                if "seconds_until_open" in test_names:
                    guarded = True
        assert guarded, "the open-wait must be conditional, not unconditional"

    def test_starting_after_the_close_is_reported_not_backfilled(self):
        source = SCRIPT.read_text()
        assert "after the" in source and "no cycle observed" in source


class TestFetchDocstringsNoLongerClaimAsOf:
    def test_the_candle_fetch_admits_it_ignores_as_of(self):
        """The docstring claiming "ending at or before `as_of`" is what let
        the caller believe a virtual clock was safe."""
        for node in _all_nodes():
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "candle_fetch_fn":
                doc = ast.get_docstring(node) or ""
                # Positive assertion only. A "the old claim is absent" check
                # fails against the CORRECTED docstring, because that
                # docstring quotes the old claim in order to explain it --
                # which is precisely the prose-matching trap this file's own
                # header warns about, and which caught this test on its
                # first run. Presence of the disclaimer is the real proof.
                assert "NOT HONOURED" in doc or "not honoured" in doc, doc
                return
        pytest.fail("candle_fetch_fn not found")

    def test_the_vix_fetch_admits_the_same(self):
        for node in _all_nodes():
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "vix_fetch_fn":
                doc = ast.get_docstring(node) or ""
                assert "not honoured" in doc or "NOT HONOURED" in doc, doc
                return
        pytest.fail("vix_fetch_fn not found")


class TestStillValid:
    def test_the_script_parses(self):
        assert _tree() is not None

    def test_the_loop_still_sleeps_between_cycles(self):
        """The cadence itself was never the bug -- only the labelling. A
        fix that dropped the sleep would spin the API at full rate."""
        source = SCRIPT.read_text()
        assert "cycle_interval_minutes * 60" in source
