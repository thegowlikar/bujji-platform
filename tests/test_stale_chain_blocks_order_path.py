"""A stale option chain must block the ORDER PATH, not merely log.

Freshness enforcement that stops at the provider proves nothing about the
runner. These tests drive `_attempt_entry` -- the choke point BOTH the
single-shot entry window and the continuous session loop share -- and assert
that a book past its hard age limit produces no order.

WHY THIS CHOKE POINT. The runner's own docstring records that
`_data_quality_permits_entry()` once lived in `_entry_window`, which
production never executes because the config sets `session.continuous`. Two
gates were reported as wired while being unreachable on the executed branch.
Anything that must gate an entry belongs here, where both modes pass through.

WHAT MUST NOT HAPPEN, equally: a stale book must not END the session. The
runner may be holding a position it is still managing from tick data, and
killing that session to avoid a bad ENTRY would trade one hazard for a worse
one. Stale data means "no entry this cycle", and a later cycle whose refresh
succeeds may still enter.
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

from bujji.production_runtime.market_data_provider import (  # noqa: E402
    MarketDataUnavailableError,
)


def _runner_module():
    spec = importlib.util.spec_from_file_location(
        "_runner_orderpath_guard", REPO_ROOT / "bujji_options_os_runner.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Selection:
    selected_strategy = "NEUTRAL_PREMIUM_SELLING"


class _Governor:
    def __init__(self):
        self.locked = 0

    def select_and_lock_strategy(self, trend, vol, **_evidence):
        self.locked += 1
        return _Selection()


class _StaleProvider:
    """A live provider whose book has aged past the hard limit."""

    def __init__(self):
        self.snapshot_calls = 0

    def snapshot(self, as_of_date):
        self.snapshot_calls += 1
        raise MarketDataUnavailableError(
            "live option chain for 'NIFTY' is 340s old (limit 120s) and the "
            "refresh failed (FYERS 502) -- refusing to serve a stale book to "
            "strike selection")


def _runner(provider):
    mod = _runner_module()
    r = mod.OptionsOSRunner.__new__(mod.OptionsOSRunner)
    r._logger = logging.getLogger("test-orderpath")
    r._governor_result_summary = {}
    r._governor = _Governor()
    r._market_data_provider = provider
    r._as_of_date = "2026-08-24"
    r._data_quality_permits_entry = lambda: True
    # If control ever reaches construction or placement, these blow up loudly
    # rather than quietly succeeding.
    r._broker = None
    r._registry = None
    return mod, r


class TestAStaleChainBlocksTheEntry:
    def test_attempt_entry_returns_False(self):
        provider = _StaleProvider()
        _mod, r = _runner(provider)
        assert r._attempt_entry("SIDEWAYS", "LOW_VOL") is False
        assert provider.snapshot_calls == 1

    def test_no_order_construction_is_reached(self):
        """`_broker` and `_registry` are None. Reaching construction would
        raise AttributeError/TypeError rather than returning False, so a clean
        False IS the proof that nothing downstream ran."""
        _mod, r = _runner(_StaleProvider())
        result = r._attempt_entry("SIDEWAYS", "LOW_VOL")
        assert result is False

    def test_the_block_is_recorded_for_the_operator(self):
        _mod, r = _runner(_StaleProvider())
        r._attempt_entry("SIDEWAYS", "LOW_VOL")
        assert r._governor_result_summary["entry_allowed"] is False
        reason = r._governor_result_summary["entry_blocked_reason"]
        assert reason.startswith("STALE_MARKET_DATA")
        assert "340s old" in reason, (
            "the recorded reason must carry the measured age; 'stale' without "
            "a number cannot be triaged")

    def test_it_is_logged_at_CRITICAL(self, caplog):
        _mod, r = _runner(_StaleProvider())
        with caplog.at_level(logging.CRITICAL):
            r._attempt_entry("SIDEWAYS", "LOW_VOL")
        assert any("ENTRY BLOCKED" in rec.getMessage() for rec in caplog.records)


class TestAStaleChainDoesNotEndTheSession:
    def test_the_error_does_not_propagate(self):
        """A session holding a position must keep managing it. Killing the
        session to avoid a bad entry trades one hazard for a worse one."""
        _mod, r = _runner(_StaleProvider())
        try:
            r._attempt_entry("SIDEWAYS", "LOW_VOL")
        except MarketDataUnavailableError:  # pragma: no cover
            pytest.fail(
                "a stale chain propagated out of _attempt_entry and would end "
                "the session, abandoning any open position to the abort path")

    def test_a_later_cycle_can_still_enter(self):
        """The block is per-cycle, not a latch."""
        provider = _StaleProvider()
        _mod, r = _runner(provider)
        assert r._attempt_entry("SIDEWAYS", "LOW_VOL") is False
        assert r._attempt_entry("SIDEWAYS", "LOW_VOL") is False
        assert provider.snapshot_calls == 2, (
            "the second cycle did not re-ask the provider -- a transient "
            "outage would permanently disable entry for the day")


class TestTheGateIsAtTheSharedChokePoint:
    def test_both_entry_modes_route_through_attempt_entry(self):
        """Production runs `_continuous_session`, not `_entry_window`. A gate
        placed in only one of them is unreachable in production while looking
        wired -- which has already happened twice in this file."""
        import ast

        src = (REPO_ROOT / "bujji_options_os_runner.py").read_text()
        tree = ast.parse(src)
        for name in ("_entry_window", "_continuous_session"):
            fn = next(n for n in ast.walk(tree)
                      if isinstance(n, ast.FunctionDef) and n.name == name)
            calls = {getattr(c.func, "attr", None) for c in ast.walk(fn)
                     if isinstance(c, ast.Call)}
            assert "_attempt_entry" in calls, (
                f"{name} does not route through _attempt_entry, so the "
                f"freshness gate does not apply to it")
