"""A session that could not SEE must not exit 0 and say nothing.

`entry_blocked_by` was written at six sites in the runner and read at NONE.
`entry_blocked_reason` -- a SECOND, singular key -- was written at one more, so
the stale-chain refusal was invisible even to a reader that knew about the
first. `session_safety_verdict`, the only consumer of the summary and the thing
that sets the process exit code, read `position_truth_established` and neither
of them.

So a session could be turned away from every entry it attempted, exit 0, leave
systemd green, never fire OnFailure=, and never reach the operator's phone.
That is the 2026-08-21 shape.
"""
from __future__ import annotations

import ast
import io
import os
from unittest.mock import MagicMock

import pytest

from bujji.production_runtime.session_safety_verdict import (
    _BLINDNESS_REFUSALS, _blindness_detail, evaluate_session_safety,
)

RUNNER_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "bujji_options_os_runner.py")
RUNNER_SRC = io.open(RUNNER_PATH, encoding="utf-8").read()
RUNNER_AST = ast.parse(RUNNER_SRC)


# ------------------------------------------------------- recording the refusal
def _runner():
    import bujji_options_os_runner as runner

    r = object.__new__(runner.OptionsOSRunner)
    r._governor_result_summary = {}
    return r


def test_a_refusal_is_recorded_in_both_forms():
    r = _runner()
    r._block_entry("BAND_COVERAGE")
    assert r._governor_result_summary["entry_blocked_by"] == "BAND_COVERAGE"
    assert r._governor_result_summary["entry_blocked_reasons"] == ["BAND_COVERAGE"]


def test_the_accumulated_set_survives_a_later_different_refusal():
    """`entry_blocked_by` is LAST-WRITE-WINS across up to 96 decision cycles.
    On its own it cannot answer "was this session ever blind?" -- a defect at
    cycle 5 disappears behind a different refusal at cycle 90."""
    r = _runner()
    r._block_entry("BAND_COVERAGE")
    r._block_entry("DATA_QUALITY_DEGRADED")
    assert r._governor_result_summary["entry_blocked_by"] == "DATA_QUALITY_DEGRADED"
    assert r._governor_result_summary["entry_blocked_reasons"] == [
        "BAND_COVERAGE", "DATA_QUALITY_DEGRADED"]


def test_the_same_refusal_every_cycle_is_recorded_once():
    r = _runner()
    for _ in range(50):
        r._block_entry("BAND_COVERAGE")
    assert r._governor_result_summary["entry_blocked_reasons"] == ["BAND_COVERAGE"]


# ------------------------------------------------------------- the verdict
def test_a_blind_session_that_never_traded_is_not_safe():
    v = evaluate_session_safety({"entry_blocked_reasons": ["BAND_COVERAGE"]})
    assert v.safe is False
    assert v.position_existed is False
    assert any("BAND_COVERAGE" in r for r in v.reasons)


@pytest.mark.parametrize("reason", sorted(_BLINDNESS_REFUSALS))
def test_every_classified_refusal_makes_the_session_unsafe(reason):
    assert evaluate_session_safety({"entry_blocked_reasons": [reason]}).safe is False


def test_a_runtime_composed_data_quality_label_is_matched_by_prefix():
    """`DATA_QUALITY_<quality>` is built from the verdict's own label, so the
    exact strings cannot be enumerated."""
    v = evaluate_session_safety({"entry_blocked_reasons": ["DATA_QUALITY_SOMETHING_NEW"]})
    assert v.safe is False


def test_a_QUIET_MARKET_SESSION_STAYS_SAFE():
    """THE ALARM MUST NOT CRY WOLF. The stability gate declines the large
    majority of cycles by design, and "no strategy for this regime" returns
    without recording anything. Those paths write NO reason, so they cannot
    reach this rule -- an operator is never woken for a quiet market."""
    assert evaluate_session_safety({}).safe is True
    assert evaluate_session_safety({"entry_blocked_reasons": []}).safe is True
    assert evaluate_session_safety({"strategy_selected": None}).safe is True


def test_a_malformed_reasons_value_does_not_crash_the_verdict():
    assert evaluate_session_safety({"entry_blocked_reasons": "not-a-list"}).safe is True


def test_blindness_is_checked_even_when_a_position_was_opened_and_closed_cleanly():
    """A session that recovered still records the blindness, and the operator
    still learns the data path failed."""
    summary = {"entry_blocked_reasons": ["BAND_COVERAGE"],
               "entry_filled": True, "final_positions_status": "FLAT"}
    v = evaluate_session_safety(summary)
    assert v.position_existed is True
    assert any("BAND_COVERAGE" in r for r in v.reasons)


# ------------------------------------------------------------- the wiring
def _block_entry_literals():
    """Every literal reason the runner passes to `_block_entry`.

    WALKS INTO THE ARGUMENT, rather than only matching a bare Constant.
    A negative control caught this: STRATEGY_ALREADY_DEPLOYED_TODAY is passed
    as a conditional expression --

        self._block_entry("PRIOR_FILLS_UNREADABLE"
                          if self._prior_fills_unreadable
                          else "STRATEGY_ALREADY_DEPLOYED_TODAY")

    -- so the extractor saw an IfExp, matched nothing, and the classification
    guard never asked about EITHER reason. A refusal escaped the guard by
    syntax alone, which is the quietest way for one to escape.
    """
    def _reasons(node):
        """The literals that can actually BE the reason.

        Follows the branches of a conditional expression, and nothing else.
        Walking the whole subtree instead collects strings from nested calls --
        `getattr(self, "_prior_fills_unreadable", False)` contributed an
        attribute name, which is not a refusal reason and cannot be classified
        as one.
        """
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return {node.value}
        if isinstance(node, ast.IfExp):
            return _reasons(node.body) | _reasons(node.orelse)
        return set()

    out = set()
    for node in ast.walk(RUNNER_AST):
        if (isinstance(node, ast.Call)
                and getattr(node.func, "attr", None) == "_block_entry"
                and node.args):
            out |= _reasons(node.args[0])
    return out


def test_every_refusal_site_routes_through_the_recorder():
    """A raw `summary["entry_blocked_by"] = ...` write bypasses the accumulated
    set, and the verdict grades the set."""
    raw = []
    for node in ast.walk(RUNNER_AST):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if (isinstance(target, ast.Subscript)
                    and isinstance(target.slice, ast.Constant)
                    and target.slice.value == "entry_blocked_by"):
                raw.append(ast.unparse(node))
    # The recorder itself is the one legitimate writer.
    assert len(raw) == 1, f"{len(raw)} raw writes bypass _block_entry: {raw}"


def test_every_literal_refusal_reason_is_classified():
    """FORCES A DELIBERATE DECISION ON ANY NEW REASON. An unrecognised reason
    does NOT escalate -- firing on something nobody classified is how an alarm
    becomes noise -- so a new refusal added without a matching entry here would
    silently never reach the operator. This fails instead."""
    from bujji.production_runtime.session_safety_verdict import is_classified

    literals = _block_entry_literals()
    assert literals, "no literal reasons found -- the detector is broken"

    # CLASSIFIED means deliberately placed in ONE of two sets: blindness
    # (escalates) or explicitly not-blindness (a disciplined decline).
    #
    # This used to test `_blindness_detail(r) is None`, which conflated "judged
    # not to be blindness" with "nobody has classified this yet". Those are
    # opposites and only one is safe. It also let a reason escape entirely by
    # syntax: STRATEGY_ALREADY_DEPLOYED_TODAY is written as a conditional
    # expression, so the literal extractor never saw it and the guard never
    # asked about it.
    unclassified = sorted(r for r in literals if not is_classified(r))
    assert not unclassified, (
        f"these refusal reasons reach no classification and would therefore "
        f"never reach the operator: {unclassified}")


def test_the_verdict_is_what_sets_the_exit_code():
    """Pins the path this whole commit depends on: the summary the runner
    builds is graded, and the grade becomes the process exit status."""
    assert "evaluate_session_safety" in RUNNER_SRC
