"""Four registration gaps: a leg that may be live is always made visible.

THE UNIFYING PRINCIPLE. Registration places NO order, and
PositionGroupReality.is_open is derived from a live get_open_positions() read.
So registering a leg we are unsure about costs nothing -- it simply never
reads open if it does not exist -- while declining to register one that turns
out to be real costs an unmanaged naked position. The asymmetry points one
way, so the fix is: register a SUPERSET and let broker truth filter it.

Every reachable BROKER_ONLY on this deployment is "Bujji filled and failed to
register". A detector whose true positives are all registration failures is
cured by registering, never by reversing -- which is why auto-containment was
rejected and this was built instead.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

RUNNER = (REPO_ROOT / "bujji_options_os_runner.py").read_text()
BRIDGE = (REPO_ROOT / "bujji" / "production_runtime" / "execution_journal_bridge.py").read_text()


def _fn(source: str, name: str):
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} not found")


class TestGap1UnknownTruthRegisters:
    """An UNKNOWN leg may or may not be live -- that is what UNKNOWN means.
    This branch registered nothing, so a leg that HAD filled became a position
    nothing owned, surfacing only as a BROKER_ONLY finding hours later."""

    def test_the_unknown_branch_registers(self):
        body = ast.unparse(_fn(RUNNER, "_attempt_entry"))
        assert "BROKER_TRUTH_UNKNOWN:" in body
        i = body.index("BROKER_TRUTH_UNKNOWN:")
        assert "_register_orphaned_legs" in body[i:i + 400]

    def test_both_failure_reasons_register(self):
        body = ast.unparse(_fn(RUNNER, "_attempt_entry"))
        assert body.count("_register_orphaned_legs") == 2

    def test_the_two_paths_use_distinct_group_ids(self):
        """register_entry refuses a duplicate group id -- registration is
        one-time by design -- and both reasons can fire for one assessment."""
        body = ast.unparse(_fn(RUNNER, "_register_orphaned_legs"))
        assert "{label}" in body


class TestGap2ShortUnwindIsAnOrphan:
    """An entry that filled 75 and an unwind that filled 25 was reported
    CONTAINED, so the runner registered nothing and 50 stayed live."""

    def test_the_unwind_compares_quantities(self):
        body = ast.unparse(_fn(BRIDGE, "contain_partial_entry"))
        assert "unwound_qty" in body and "opened_qty" in body

    def test_a_short_unwind_is_orphaned_not_unwound(self):
        body = ast.unparse(_fn(BRIDGE, "contain_partial_entry"))
        i = body.index("unwound_qty < opened_qty")
        assert "orphaned.append" in body[i:i + 300]

    def test_a_full_unwind_is_still_contained(self):
        """The guard must not turn every containment into an orphan."""
        body = ast.unparse(_fn(BRIDGE, "contain_partial_entry"))
        assert "unwound.append" in body


class TestGap3NoMatchFallsBackToAllLegs:
    """This logged CRITICAL and returned, registering nothing -- leaving the
    legs it had just declared unaccounted-for invisible to management, which
    is the state the log was warning about."""

    def test_it_falls_back_to_every_proposal_leg(self):
        body = ast.unparse(_fn(RUNNER, "_register_orphaned_legs"))
        i = body.index("no matching order")
        assert "cycle_result.proposal.legs" in body[i:i + 900]

    def test_it_still_refuses_when_there_are_no_legs_at_all(self):
        """A superset of nothing is nothing -- say so rather than pretend."""
        body = ast.unparse(_fn(RUNNER, "_register_orphaned_legs"))
        assert "ORPHAN REGISTRATION IMPOSSIBLE" in body

    def test_the_fallback_registers_before_returning(self):
        body = ast.unparse(_fn(RUNNER, "_register_orphaned_legs"))
        fallback = body.index("no matching order")
        register = body.index("register_entry")
        assert fallback < register, "the fallback path returns before registering"


class TestGap4FailureStillForcesVisibility:
    """The handler logged and returned, so a leg that may be live was lost:
    no registration, and management never started because
    _orphan_position_live was never set. A log alone does not stop a short."""

    def test_the_handler_forces_management_on(self):
        body = ast.unparse(_fn(RUNNER, "_register_orphaned_legs"))
        i = body.index("except Exception")
        assert "_orphan_position_live = True" in body[i:]

    def test_the_failure_is_recorded_in_the_summary(self):
        body = ast.unparse(_fn(RUNNER, "_register_orphaned_legs"))
        assert "orphan_registration_failed" in body

    def test_the_handler_does_not_swallow_silently(self):
        body = ast.unparse(_fn(RUNNER, "_register_orphaned_legs"))
        i = body.index("except Exception")
        assert "critical" in body[i:]


class TestTheSharedPrinciple:
    def test_registration_places_no_order(self):
        """The whole safety argument: this is a MEMORY WRITE. If it ever
        places an order, registering a leg we are unsure about stops being
        free and the superset strategy becomes dangerous."""
        body = ast.unparse(_fn(RUNNER, "_register_orphaned_legs"))
        for forbidden in ("place_order", "submit_and_confirm", "_execute_reduce",
                          "cancel_order"):
            assert forbidden not in body

    def test_is_open_is_still_broker_derived(self):
        """The filter that makes a superset safe. If is_open ever becomes a
        cached flag, registering unsure legs would assert positions."""
        import inspect

        from bujji.production_runtime.position_reality_registry import PositionRealityRegistry
        src = inspect.getsource(PositionRealityRegistry.get_group_reality)
        assert "_open_symbols" in src
