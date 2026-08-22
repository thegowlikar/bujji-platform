"""Stage 3: legs stay mandatory until the BROKER proves the account is flat.

The operator's rule (2026-08-22): "Once a position exists, those legs and
hedges remain mandatory until the broker proves the account is flat."

The management loop terminated on `if not self._entry_prices` -- local state,
and the weakest kind. `_entry_prices` is assigned at three sites and cleared at
none, so after the first fill that branch was unreachable and its log line
could never be printed truthfully. Monitoring did continue, but by accident:
the moment anything cleared that dict, the loop would have stopped watching a
live position on local belief alone.
"""
from __future__ import annotations

import ast
import io
import os
from unittest.mock import MagicMock

import pytest

RUNNER_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "bujji_options_os_runner.py")
RUNNER_SRC = io.open(RUNNER_PATH, encoding="utf-8").read()
RUNNER_AST = ast.parse(RUNNER_SRC)


def _fn(name):
    for node in ast.walk(RUNNER_AST):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.unparse(node)
    raise AssertionError(f"{name} not found")


def _runner(positions=None, raises=None):
    import bujji_options_os_runner as runner

    r = object.__new__(runner.OptionsOSRunner)
    broker = MagicMock()

    async def _get():
        if raises is not None:
            raise raises
        return positions

    broker.get_open_positions = _get
    r._broker = broker
    r._logger = MagicMock()
    return r


# ------------------------------------------------ the three-valued proof
def test_no_open_legs_is_flat():
    flat, detail = _runner(positions=[])._broker_reports_flat()
    assert flat is True


def test_a_zero_quantity_leg_is_flat():
    flat, _ = _runner(positions=[{"symbol": "X", "qty": 0}])._broker_reports_flat()
    assert flat is True


def test_an_open_leg_is_not_flat():
    flat, detail = _runner(positions=[{"symbol": "X", "qty": 50}])._broker_reports_flat()
    assert flat is False
    assert "X" in detail


def test_a_failed_read_is_UNKNOWN_never_flat():
    """"I could not ask" must never become "there is nothing there"."""
    flat, detail = _runner(raises=RuntimeError("boom"))._broker_reports_flat()
    assert flat is None
    assert "failed" in detail


def test_a_missing_position_list_is_UNKNOWN_never_flat():
    flat, _ = _runner(positions=None)._broker_reports_flat()
    assert flat is None


# ------------------------------------------------------ the loop's rule
def test_the_loop_terminates_on_broker_truth_not_local_state():
    src = _fn("_position_management")
    assert "_broker_reports_flat()" in src, \
        "the monitoring loop still decides it is done from local state"


def _flat_breaks():
    """Every `if <...flat...>: ... break` in the monitoring loop."""
    out = []
    for node in ast.walk(ast.parse(_fn("_position_management"))):
        if isinstance(node, ast.If) and any(isinstance(b, ast.Break) for b in node.body):
            test_src = ast.unparse(node.test)
            if "flat" in test_src:
                out.append(test_src)
    return out


def test_only_a_positive_proof_of_flat_ends_monitoring():
    """`flat is False` (open legs) and `flat is None` (could not establish)
    must BOTH keep monitoring. Anything looser stops watching a position that
    may still be live."""
    breaks = _flat_breaks()
    assert breaks, "nothing in the monitoring loop breaks on flatness"
    for test_src in breaks:
        assert "flat is True" in test_src, test_src
        assert "is not False" not in test_src, test_src
        assert "not flat" not in test_src, test_src


def test_a_no_trade_session_keeps_reconciling_though_the_broker_is_flat():
    """THE REGRESSION THAT CAUGHT MY FIRST VERSION. On a no-trade day the
    broker is flat from the first pass, so breaking on `flat is True` alone
    stopped monitoring at cycle one -- defeating the reason this loop was made
    unconditional on 2026-08-21: "the case reconciliation exists for is 'Bujji
    believes it holds nothing while the broker holds something', and `entered`
    is False in exactly that case". A broker flat at 09:20 says nothing about a
    position appearing at 11:00.

    `_entry_prices` is a sound PRECONDITION precisely because it is never
    cleared: it can only ever say "something was opened", never "nothing is
    open", so it cannot end monitoring on its own. That was the defect in the
    condition this replaced.

    Asserted as NESTING, not as a substring of one condition: the guard also
    short-circuits the broker READ, so it is an enclosing `if`, and a test
    that only searched the break's own test would pass while the guard sat
    somewhere useless.
    """
    tree = ast.parse(_fn("_position_management"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        if ast.unparse(node.test) != "self._entry_prices":
            continue
        for inner in ast.walk(node):
            if (isinstance(inner, ast.If)
                    and "flat is True" in ast.unparse(inner.test)
                    and any(isinstance(b, ast.Break) for b in inner.body)):
                return
    raise AssertionError(
        "the flat-break is not nested inside `if self._entry_prices:` -- a "
        "session that never opened anything can end its own monitoring")


def test_entry_prices_no_longer_ends_the_monitoring_loop():
    """The dead branch is gone, not merely bypassed."""
    src = _fn("_position_management")
    assert "Position closed during management" not in src


def test_unknown_flatness_is_reported_not_swallowed():
    src = _fn("_position_management")
    assert "UNKNOWN is not flat" in src


# --------------------------------------------------- the premise it rests on
def test_entry_prices_is_still_never_cleared_so_it_could_not_have_worked():
    """Pins WHY the old branch was dead. If a future change starts clearing
    `_entry_prices`, this fails and the reasoning above must be revisited --
    the old condition would become live again, and wrong for a new reason."""
    assigns = []
    for node in ast.walk(RUNNER_AST):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (isinstance(target, ast.Attribute)
                        and target.attr == "_entry_prices"):
                    assigns.append(ast.unparse(node.value))
        elif isinstance(node, ast.AnnAssign):
            if (isinstance(node.target, ast.Attribute)
                    and node.target.attr == "_entry_prices" and node.value is not None):
                assigns.append(ast.unparse(node.value))
    assert assigns, "no assignment to _entry_prices found -- detector is broken"
    cleared = [a for a in assigns if a in ("{}", "dict()")]
    # The one empty assignment is the constructor's initialiser, never a reset.
    assert len(cleared) <= 1, f"_entry_prices is now cleared somewhere: {assigns}"
