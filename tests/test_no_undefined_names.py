"""No production path may carry a NameError waiting to happen.

Bujji has shipped this defect class TWICE, both times on a SAFETY path, both
times found only after it fired in a live session:

  9f34b50  the emergency brake raised
           `NameError: name 'PositionHealthThresholds' is not defined`
           at 09:54:35 on 2026-08-21. The blind-cycle brake had fired
           correctly on a naked short strangle; the close never executed.

  85d7d15  `_capture_exit_fills_from` read `execution.orders_submitted` where
           `execution` was a CALLER's local, left behind when the function was
           extracted. It raised on EVERY exit, and a deliberate
           `except Exception` turned it into a log line.

Both are the same shape: a name that is bound on some OTHER code path, read on
a path that only executes when something has already gone wrong. The regression
suite could not see either -- 8122 tests were green while the second one was
live -- because a test that asserts a safety routine is CALLED cannot tell
whether it WORKS, and an except that exists to protect the session hides the
evidence.

These paths execute least and matter most. Static analysis is the only thing
that inspects them on every commit.

THE GUARD (tools/undefined_name_guard.py) uses symtable scope analysis:
a name read inside a function that is not local, not a parameter, not a
closure cell, not imported there, not module-level and not a builtin is an
implicit global the module does not define -- which is a NameError at runtime,
whatever the tests say. Annotations under `from __future__ import annotations`
are excluded: they are strings at runtime and cannot raise.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

sys.path.insert(0, str(REPO_ROOT / "tools"))
from undefined_name_guard import check  # noqa: E402


PRODUCTION_ROOTS = ["bujji", "bujji_options_os_runner.py"]


def _py_files(root: Path):
    if root.is_file():
        return [root]
    return sorted(p for p in root.rglob("*.py"))


class TestTheGuardActuallyDetects:
    """A guard that cannot fail is not a guard. These are the two real bugs,
    reproduced in their original shapes."""

    def test_it_catches_the_9f34b50_shape(self, tmp_path):
        """A name imported function-locally in ONE method and used in ANOTHER."""
        src = tmp_path / "brake.py"
        src.write_text(
            "from __future__ import annotations\n"
            "\n"
            "class Runner:\n"
            "    def _management_pass(self):\n"
            "        from thresholds import PositionHealthThresholds\n"
            "        return PositionHealthThresholds()\n"
            "\n"
            "    def _emergency_close(self):\n"
            "        # no import here -- the other method's import is not in scope\n"
            "        return PositionHealthThresholds()\n"
        )
        findings = check(str(src))
        assert any("PositionHealthThresholds" in msg for _l, _c, msg in findings), (
            "the guard did not catch the emergency-brake NameError that fired "
            "in production on 2026-08-21")

    def test_it_catches_the_85d7d15_shape(self, tmp_path):
        """A CALLER's local, left behind by an extract-function refactor."""
        src = tmp_path / "funnel.py"
        src.write_text(
            "from __future__ import annotations\n"
            "\n"
            "class Runner:\n"
            "    def _capture_exit_fills(self, result):\n"
            "        execution = getattr(result, 'forced_execution', None)\n"
            "        return self._capture_exit_fills_from(execution)\n"
            "\n"
            "    def _capture_exit_fills_from(self, orders_submitted):\n"
            "        for submitted in execution.orders_submitted:\n"
            "            print(submitted)\n"
        )
        findings = check(str(src))
        assert any("execution" in msg for _l, _c, msg in findings), (
            "the guard did not catch the exit-funnel NameError that ran on "
            "every exit until 85d7d15")

    def test_it_does_not_fire_on_a_correct_local_import(self, tmp_path):
        src = tmp_path / "ok.py"
        src.write_text(
            "from __future__ import annotations\n"
            "\n"
            "def f():\n"
            "    from thresholds import Thresholds\n"
            "    return Thresholds()\n"
        )
        assert check(str(src)) == []

    def test_it_does_not_fire_on_string_annotations(self, tmp_path):
        """With postponed evaluation these are never evaluated, so they cannot
        raise. Flagging them would bury the real findings in noise."""
        src = tmp_path / "ann.py"
        src.write_text(
            "from __future__ import annotations\n"
            "\n"
            "def f(x: NeverImported) -> OtherMissing:\n"
            "    return x\n"
        )
        assert check(str(src)) == []


class TestProductionIsClean:
    def test_no_production_file_has_an_undefined_name(self):
        offenders = []
        for root in PRODUCTION_ROOTS:
            for path in _py_files(REPO_ROOT / root):
                for lineno, _col, msg in check(str(path)):
                    offenders.append(
                        f"{path.relative_to(REPO_ROOT)}:{lineno}: {msg}")
        assert not offenders, (
            "a name is read on a path where nothing binds it. This is a "
            "NameError waiting for the exact moment the path executes, which "
            "on this system means the moment a safety routine fires:\n  "
            + "\n  ".join(offenders))
