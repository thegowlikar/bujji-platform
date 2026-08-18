"""The single accumulation loop, and proof that it is actually single.

WHY THESE EXIST. `replay_snapshots` was introduced as an "anti-duplication
move" -- one loop, two entry points -- and for a while that was simply
false: `hydrate_observation_memory` kept its own loop, the runner's
stability-gate `derive` closure ran a THIRD, and only the seeding path
called the new function. The docstring claimed a consolidation that had not
happened, which is the same failure an earlier design was refuted for.

So these tests do not merely exercise the function; they assert that the
callers genuinely route through it. A shared helper nobody shares is worse
than no helper, because it reads as if the problem were solved.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from bujji.market_state_builder import recovery
from bujji.market_state_builder.market_state import MarketStateBuilder
from bujji.market_state_builder.recovery import ReplayOutcome, replay_snapshots


class _Snap:
    """Minimal stand-in: replay_snapshots only forwards to builder.process
    and reads .timestamp."""
    def __init__(self, ts):
        self.timestamp = ts


class _Builder:
    def __init__(self, fail_on=()):
        self.seen = []
        self._fail_on = set(fail_on)
        self.memory = "MEMORY"

    def process(self, snapshot):
        if snapshot.timestamp in self._fail_on:
            raise ValueError(f"bad snapshot {snapshot.timestamp}")
        self.seen.append(snapshot.timestamp)
        return f"assessment@{snapshot.timestamp}"


class TestOneBuilderNotOnePerSnapshot:
    def test_all_snapshots_go_through_the_same_builder(self):
        """Price structure is cross-cycle: a fresh builder per snapshot
        accumulates nothing."""
        b = _Builder()
        snaps = [_Snap(f"t{i}") for i in range(5)]
        outcome = replay_snapshots(snaps, b)
        assert b.seen == ["t0", "t1", "t2", "t3", "t4"]
        assert outcome.replayed == 5

    def test_it_returns_the_last_assessment(self):
        """The stability gate needs this; without it the gate ran its own
        duplicate loop."""
        outcome = replay_snapshots([_Snap("t0"), _Snap("t1")], _Builder())
        assert outcome.last_assessment == "assessment@t1"

    def test_it_returns_the_last_timestamp(self):
        outcome = replay_snapshots([_Snap("t0"), _Snap("t9")], _Builder())
        assert outcome.last_timestamp == "t9"

    def test_an_empty_series_yields_an_honest_empty_outcome(self):
        outcome = replay_snapshots([], _Builder())
        assert outcome.replayed == 0
        assert outcome.last_assessment is None
        assert outcome.last_timestamp is None


class TestOneBadSnapshotDoesNotDiscardTheRest:
    def test_a_raising_snapshot_is_recorded_and_skipped(self):
        b = _Builder(fail_on={"t2"})
        outcome = replay_snapshots([_Snap(f"t{i}") for i in range(5)], b)
        assert b.seen == ["t0", "t1", "t3", "t4"]
        assert outcome.replayed == 4
        assert len(outcome.errors) == 1 and "bad snapshot t2" in outcome.errors[0]

    def test_the_last_timestamp_reflects_the_last_SUCCESS(self):
        b = _Builder(fail_on={"t4"})
        outcome = replay_snapshots([_Snap(f"t{i}") for i in range(5)], b)
        assert outcome.last_timestamp == "t3", "a failed snapshot must not set last_timestamp"


class TestTheCallersActuallyRouteThroughIt:
    """The point of the extraction. Asserted over the parsed tree, because
    prose in a docstring is what created this problem in the first place."""

    def _calls_replay_snapshots(self, func) -> bool:
        tree = ast.parse(inspect.getsource(func).lstrip())
        return any(isinstance(n, ast.Call) and getattr(n.func, "id", None) == "replay_snapshots"
                   for n in ast.walk(tree))

    def test_hydrate_observation_memory_delegates(self):
        assert self._calls_replay_snapshots(recovery.hydrate_observation_memory)

    def test_hydrate_no_longer_runs_its_own_process_loop(self):
        src = inspect.getsource(recovery.hydrate_observation_memory)
        assert "builder.process(" not in src, (
            "hydrate is looping process() itself again -- the delegation regressed")

    def test_the_runner_stability_derive_delegates(self):
        src = Path("/opt/bujji/app/bujji_options_os_runner.py")
        if not src.exists():
            pytest.skip("VPS-only")
        tree = ast.parse(src.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "derive":
                calls = [n for n in ast.walk(node)
                         if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "replay_snapshots"]
                assert calls, "derive() is not using the shared loop"
                inline = [n for n in ast.walk(node)
                          if isinstance(n, ast.Call) and getattr(n.func, "attr", None) == "process"]
                assert not inline, "derive() is looping process() itself -- a fourth copy"
                return
        pytest.fail("derive() not found in the runner")

    def test_only_the_known_process_loops_exist(self):
        """A ratchet. `replay_engine/engine.py` is the one documented
        un-consolidated copy; anything NEW showing up here means the
        duplication crept back."""
        import subprocess
        out = subprocess.run(
            ["grep", "-rln", "--include=*.py", "MarketStateBuilder()", "/opt/bujji/app/bujji"],
            capture_output=True, text=True).stdout
        files = {l.split("/opt/bujji/app/")[-1] for l in out.strip().splitlines() if l}
        allowed = {
            "bujji/market_state_builder/recovery.py",   # the shared loop itself
            "bujji/market_state_builder/market_state.py",
            "bujji/replay_engine/engine.py",            # documented, not consolidated
        }
        assert files <= allowed, f"a new MarketStateBuilder() construction appeared: {files - allowed}"
