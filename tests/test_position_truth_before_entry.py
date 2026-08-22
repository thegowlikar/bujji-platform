"""No entry may be taken before broker position truth is established.

THE DEFECT. `_reconciliation_blocks_entry` was initialised to False. The gate
in `_data_quality_permits_entry` reads it, so before ANY reconciliation had
run the gate said "not blocked" and the first entry of a session could be
taken with no broker-truth check whatsoever. That is UNKNOWN silently treated
as FLAT, at the moment it matters most.

WHEN IT BITES. A process that crashed yesterday, or was restarted mid-session,
begins with an empty in-memory registry and no knowledge of what the broker is
already holding. `PositionRealityRegistry` intersects broker positions with
symbols THIS process registered, so a position from a previous process is
invisible to it by construction. Only the unfiltered reconciliation read can
see it -- and nothing required that read to happen before entering.

Taking a fresh naked position on top of an unmanaged one is the compounding
case reconciliation exists to prevent.

WHY IT WAS NOT ALREADY BROKEN IN PRODUCTION -- and why that is not a defence.
`_continuous_session` reconciles at the top of every cycle, and the production
config sets `session.continuous`, so the live path happened to be covered.
`_entry_window` never reconciles at all. This runner has twice shipped a gate
that was live on one branch and dead on the other, and its own comments record
both. Relying on which branch the config selects is the bet that keeps losing.

THE INVARIANTS
  * the default is BLOCKED, not permitted
  * the shared choke point establishes truth on demand if nothing has yet
  * a session that could not establish it exits UNSAFE, even with no local
    position -- "this process opened nothing" is not evidence the account is
    flat
"""
from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bujji.production_runtime.session_safety_verdict import (  # noqa: E402
    evaluate_session_safety,
)


def _runner_module():
    spec = importlib.util.spec_from_file_location(
        "_runner_recovery_guard", REPO_ROOT / "bujji_options_os_runner.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _bare_runner():
    mod = _runner_module()
    r = mod.OptionsOSRunner.__new__(mod.OptionsOSRunner)
    r._logger = logging.getLogger("test-recovery")
    r._governor_result_summary = {}
    # Universe coverage is a precondition of the same gate; these tests
    # exercise the POSITION-TRUTH clause, so it is declared out of scope
    # explicitly rather than the gate being weakened.
    r._universe = None
    r._universe_requested = ()
    r._universe_error = "NOT_APPLICABLE: stub -- universe not under test"
    r._tick_feed = None
    r._last_reconciliation = None
    r._reconciliation_blocks_entry = True
    r._data_quality = None
    r._intelligence_origin = None
    return mod, r


class TestTheDefaultIsBlocked:
    def test_a_fresh_runner_starts_blocked(self):
        """UNKNOWN until established. The old default was False."""
        mod = _runner_module()
        r = mod.OptionsOSRunner.__new__(mod.OptionsOSRunner)
        import ast

        src = (REPO_ROOT / "bujji_options_os_runner.py").read_text()
        tree = ast.parse(src)
        init = next(n for n in ast.walk(tree)
                    if isinstance(n, ast.FunctionDef) and n.name == "__init__")
        assigned = None
        for node in ast.walk(init):
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Attribute) and \
                            t.attr == "_reconciliation_blocks_entry":
                        assigned = node.value
        assert assigned is not None, "_reconciliation_blocks_entry is never initialised"
        assert isinstance(assigned, ast.Constant) and assigned.value is True, (
            "the initial value permits entry before any broker read -- that is "
            "UNKNOWN treated as FLAT on the very first entry of a session")


    def test_a_runner_MISSING_the_attribute_still_blocks(self):
        """The gate reads `getattr(self, "_reconciliation_blocks_entry", ...)`.
        The DEFAULT in that getattr is what applies when the attribute is
        absent -- a partially-initialised runner, or one whose __init__ was
        bypassed. It must be True.

        Every other test here assigns the attribute explicitly, so none of
        them exercise the fallback: a negative control that flipped it to
        False left the suite green, which is how this gap was found.
        """
        mod = _runner_module()
        r = mod.OptionsOSRunner.__new__(mod.OptionsOSRunner)
        r._logger = logging.getLogger("test-recovery-missing")
        r._governor_result_summary = {}
        r._universe = None
        r._universe_requested = ()
        r._universe_error = "NOT_APPLICABLE: stub -- universe not under test"
        r._tick_feed = None
        r._last_reconciliation = object()          # truth already established
        r._data_quality = None
        r._intelligence_origin = None
        assert not hasattr(r, "_reconciliation_blocks_entry")

        assert r._data_quality_permits_entry() is False, (
            "a runner with no _reconciliation_blocks_entry attribute was "
            "permitted to enter -- the getattr fallback defaults to allowing "
            "risk")


class TestTruthIsEstablishedOnDemand:
    def test_the_choke_point_reconciles_when_nothing_has(self):
        mod, r = _bare_runner()
        called = []

        def _reconcile(label):
            called.append(label)
            r._last_reconciliation = object()
            r._reconciliation_blocks_entry = False

        r._reconcile_broker_positions = _reconcile
        r._data_quality_permits_entry()

        assert called == ["PRE_ENTRY"], (
            "the shared entry choke point did not establish position truth; it "
            "would have relied on whichever caller happened to run")
        assert r._governor_result_summary["position_truth_established"] is True

    def test_it_reconciles_only_once(self):
        mod, r = _bare_runner()
        called = []

        def _reconcile(label):
            called.append(label)
            r._last_reconciliation = object()
            r._reconciliation_blocks_entry = False

        r._reconcile_broker_positions = _reconcile
        r._data_quality_permits_entry()
        r._data_quality_permits_entry()
        assert called == ["PRE_ENTRY"], (
            f"reconciled {len(called)} times; one unfiltered broker read per "
            f"entry decision is the budgeted cost, not one per call")

    def test_a_failed_reconciliation_blocks_the_entry(self):
        """`_reconcile_broker_positions` fails closed internally: it sets
        _reconciliation_blocks_entry=True and leaves _last_reconciliation None."""
        mod, r = _bare_runner()

        def _reconcile(label):
            r._reconciliation_blocks_entry = True   # its own except branch

        r._reconcile_broker_positions = _reconcile
        assert r._data_quality_permits_entry() is False
        assert r._governor_result_summary["position_truth_established"] is False
        assert r._governor_result_summary["entry_blocked_by"] == "POSITION_RECONCILIATION"


class TestAnUnknownAccountExitsUnsafe:
    def test_no_position_but_unestablished_truth_is_UNSAFE(self):
        """"This process opened nothing" is not evidence the account is flat."""
        verdict = evaluate_session_safety({"position_truth_established": False})
        assert verdict.safe is False
        assert "position truth could not be established" in " ".join(verdict.reasons)

    def test_established_truth_with_no_position_is_safe(self):
        verdict = evaluate_session_safety({"position_truth_established": True})
        assert verdict.safe is True

    def test_a_session_that_never_decided_stays_quiet(self):
        """A missing key is not a False. A session that never reached an entry
        decision has no opinion, and alarming on every quiet day trains the
        operator to ignore alarms."""
        assert evaluate_session_safety({}).safe is True

    def test_it_also_applies_when_a_position_existed(self):
        verdict = evaluate_session_safety({
            "canonical_position_id": "POS-1",
            "final_positions_status": "FLAT",
            "position_truth_established": False,
        })
        assert verdict.safe is False
        assert any("position truth" in r for r in verdict.reasons)
