"""CP-D: one account, one rate budget, shared across processes.

The FYERS ceiling is per ACCOUNT; the old pacer was per interpreter. Bujji
runs four processes against one account, three of which now wake together at
09:14 -- each pacing itself to ~8.3/s presented the account with ~33/s. The
tell was architectural rather than a crash: the trading unit's fire time
carried a rate-limit offset for months because schedule separation was the
only cross-process control that existed.

These tests use REAL files and REAL child processes for the concurrency
claim -- a mock of flock would prove nothing about the thing being fixed.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from bujji.broker.rate_budget import CrossProcessRateBudget


class _FakeClock:
    """A clock that only moves when something sleeps -- so a test can assert
    on the pacing arithmetic without spending real seconds."""

    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def _budget(tmp_path, clock, interval=0.12):
    return CrossProcessRateBudget(str(tmp_path / "budget"), interval,
                                  clock=clock, sleep=clock.sleep)


class TestTheBudgetPaces:
    def test_the_first_call_does_not_wait(self, tmp_path):
        clock = _FakeClock()
        outcome = _budget(tmp_path, clock).reserve()
        assert outcome.waited_seconds == 0.0 and outcome.shared is True

    def test_a_second_immediate_call_waits_one_interval(self, tmp_path):
        clock = _FakeClock()
        budget = _budget(tmp_path, clock)
        budget.reserve()
        outcome = budget.reserve()
        assert outcome.waited_seconds == pytest.approx(0.12)

    def test_a_caller_that_arrives_late_does_not_wait(self, tmp_path):
        clock = _FakeClock()
        budget = _budget(tmp_path, clock)
        budget.reserve()
        clock.now += 5.0
        assert budget.reserve().waited_seconds == 0.0

    def test_two_independent_objects_share_one_budget(self, tmp_path):
        """The whole point: two Broker instances -- and, below, two
        PROCESSES -- must not each get their own allowance."""
        clock = _FakeClock()
        a = _budget(tmp_path, clock)
        b = _budget(tmp_path, clock)
        a.reserve()
        assert b.reserve().waited_seconds == pytest.approx(0.12)


class TestItRefusesToTrustNonsense:
    def test_a_corrupt_budget_file_is_ignored_not_obeyed(self, tmp_path):
        path = tmp_path / "budget"
        path.write_text("not-a-number")
        clock = _FakeClock()
        outcome = CrossProcessRateBudget(str(path), 0.12, clock=clock,
                                         sleep=clock.sleep).reserve()
        assert outcome.waited_seconds == 0.0 and outcome.shared is True

    def test_an_implausible_future_slot_is_discarded(self, tmp_path):
        """A value from before a reboot would otherwise block every Bujji
        process for the length of the previous uptime -- indistinguishable
        from a hang."""
        path = tmp_path / "budget"
        path.write_text("999999999.0")
        clock = _FakeClock()
        outcome = CrossProcessRateBudget(str(path), 0.12, clock=clock,
                                         sleep=clock.sleep).reserve()
        assert outcome.waited_seconds == 0.0

    def test_a_stale_past_slot_is_discarded(self, tmp_path):
        path = tmp_path / "budget"
        path.write_text("1.0")
        clock = _FakeClock()
        assert CrossProcessRateBudget(str(path), 0.12, clock=clock,
                                      sleep=clock.sleep).reserve().waited_seconds == 0.0

    def test_the_directory_is_created_when_absent(self, tmp_path):
        clock = _FakeClock()
        budget = CrossProcessRateBudget(str(tmp_path / "deep" / "dir" / "budget"), 0.12,
                                        clock=clock, sleep=clock.sleep)
        assert budget.reserve().shared is True


class TestItFailsOpenAndSaysSo:
    def test_an_unusable_path_degrades_rather_than_blocking_trading(self, tmp_path):
        """A rate ceiling is a throughput protection, not a safety guard.
        Refusing to trade because a lock file is unwritable would turn a
        throughput problem into an outage -- but it must be reported, since
        the in-process pacer alone lets concurrent processes over-drive the
        account."""
        clock = _FakeClock()
        blocked = tmp_path / "afile"
        blocked.write_text("x")
        budget = CrossProcessRateBudget(str(blocked / "budget"), 0.12,
                                        clock=clock, sleep=clock.sleep)
        outcome = budget.reserve()
        assert outcome.shared is False and outcome.reason
        assert outcome.waited_seconds == 0.0


class TestRealProcessesReallyShareIt:
    def test_two_processes_serialise_through_the_same_file(self, tmp_path):
        """The claim under test is cross-PROCESS, so this uses real child
        processes and a real file lock. Mocking flock would prove nothing
        about the thing being fixed."""
        budget_path = tmp_path / "budget"
        script = textwrap.dedent(f"""
            import sys, time
            sys.path.insert(0, {str(REPO_ROOT)!r})
            from bujji.broker.rate_budget import CrossProcessRateBudget
            b = CrossProcessRateBudget({str(budget_path)!r}, 0.25)
            start = time.monotonic()
            for _ in range(4):
                b.reserve()
            print(time.monotonic() - start)
        """)
        procs = [subprocess.Popen([sys.executable, "-c", script],
                                  stdout=subprocess.PIPE, text=True) for _ in range(2)]
        elapsed = [float(p.communicate()[0].strip()) for p in procs]

        # 8 calls total at 0.25s apart across both processes. Were the budget
        # per-process, each would finish its 4 calls in ~0.75s.
        assert max(elapsed) >= 1.2, (
            f"processes did not share the budget (elapsed={elapsed}) -- each got its "
            "own allowance, which is the bug this exists to fix")

    def test_the_shared_file_is_left_in_a_usable_state(self, tmp_path):
        clock = _FakeClock()
        budget = _budget(tmp_path, clock)
        budget.reserve()
        assert float((tmp_path / "budget").read_text()) > 0


class TestTheBrokerUsesIt:
    def test_the_pacer_applies_the_host_budget_first(self):
        """The wiring, not just the module: fyers._wait_for_slot must go
        through the shared budget, with its own pacer as the second layer."""
        import inspect

        from bujji.broker import fyers

        source = inspect.getsource(fyers._wait_for_slot)
        assert "_host_rate_budget()" in source
        assert "_pacing_lock" in source, "the in-process pacer must remain as the fallback layer"

    def test_the_budget_interval_is_the_account_ceiling_not_a_per_process_one(self):
        from bujji.broker import fyers
        from bujji.broker.rate_budget import DEFAULT_MIN_INTERVAL_SECONDS

        assert fyers._MIN_SECONDS_BETWEEN_CALLS == DEFAULT_MIN_INTERVAL_SECONDS
        assert 1.0 / DEFAULT_MIN_INTERVAL_SECONDS < 10.0, "must stay under the documented ceiling"
