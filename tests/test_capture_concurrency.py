"""The two daily capture scripts must run CONCURRENTLY, not sequentially.

Both poll until their own MARKET_CLOSE and neither is given --cycles, so each
runs ~6h20m. Under the previous blocking `subprocess.run` loop the second
script started only after the first returned at 15:40 and then aborted on its
own market-hours gate -- it could never capture anything. The bug was masked
whenever the first script crashed on startup.

These assertions are structural (launch order vs wait order), not textual.
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

_spec = importlib.util.spec_from_file_location(
    "run_daily_intelligence_session", REPO_ROOT / "run_daily_intelligence_session.py")
rdis = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rdis)


class _FakeProc:
    """Records when it was launched and when it was waited on."""

    def __init__(self, argv, journal, returncode=0, stderr=""):
        self.argv = argv
        self._journal = journal
        self.returncode = returncode
        self._stderr = stderr
        journal.append(("launch", argv[-1]))

    def communicate(self):
        self._journal.append(("wait", self.argv[-1]))
        return ("", self._stderr)


@pytest.fixture
def journal(monkeypatch):
    events = []

    class _Store:
        def __init__(self, *a, **k):
            pass

        def count(self):
            return 0

    monkeypatch.setattr(rdis, "HistoricalObservationStore", _Store)
    monkeypatch.setattr(rdis.subprocess, "Popen",
                        lambda argv, **k: _FakeProc(argv, events))
    return events


def _run(date_str="2026-08-19"):
    return asyncio.run(rdis._make_real_capture_fn(date_str)())


def test_every_capture_is_launched_before_any_is_waited_on(journal):
    _run()
    kinds = [kind for kind, _ in journal]
    first_wait = kinds.index("wait")
    assert "launch" not in kinds[first_wait:], (
        "a capture was launched only after another was waited on -- that is the "
        "sequential bug: the later script starts after market close")


def test_both_capture_scripts_are_launched(journal):
    _run()
    launched = [name for kind, name in journal if kind == "launch"]
    assert launched == [
        "scripts/capture_market_reality_session.py",
        "scripts/capture_options_reality_session.py",
    ]


def test_each_launched_capture_is_also_waited_on(journal):
    _run()
    launched = {name for kind, name in journal if kind == "launch"}
    waited = {name for kind, name in journal if kind == "wait"}
    assert launched == waited, "a child was launched but never reaped"


def test_a_failing_capture_does_not_suppress_the_other(monkeypatch):
    events = []

    class _Store:
        def __init__(self, *a, **k):
            pass

        def count(self):
            return 0

    def _popen(argv, **k):
        failing = argv[-1].endswith("capture_market_reality_session.py")
        return _FakeProc(argv, events,
                         returncode=1 if failing else 0,
                         stderr="PermissionError: boom" if failing else "")

    monkeypatch.setattr(rdis, "HistoricalObservationStore", _Store)
    monkeypatch.setattr(rdis.subprocess, "Popen", _popen)

    result = _run()
    launched = [name for kind, name in events if kind == "launch"]
    # The 2026-08-18 shape: spot/vix dies, options must still have been started.
    assert "scripts/capture_options_reality_session.py" in launched
    assert len(result.errors) == 1
    assert "capture_market_reality_session.py" in result.errors[0]
