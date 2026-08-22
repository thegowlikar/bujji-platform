"""Subscribed is not covered, and the universe comes before the entry.

TWO DEFECTS, one invariant.

(1) SUBSCRIPTION WAS A CONSEQUENCE OF TRADING, NOT A PRECONDITION FOR IT.
`WebsocketTickProvider.get_prices()` subscribes `list(contracts_by_symbol)`,
and that dict is populated only AFTER the entry orders fill. So the session
could select strikes, size them and place them without one live price having
arrived for anything. A dead feed first surfaced as a BLIND CYCLE warning
logged after a naked short strangle was already open -- which is what happened
on 2026-08-21, when the feed delivered zero ticks all day.

(2) "SUBSCRIBED" WAS NEVER PROVEN TO MEAN ANYTHING. A request that was sent,
accepted, and then delivered nothing is indistinguishable from a symbol that
is not trading. Both are silence. Only a TICK proves the pipe carries data for
that symbol, so the gate counts freshness, not acknowledgement.

Every uncertain state resolves to "do not enter": no universe, a symbol never
requested, a symbol requested but silent, an unreadable tick age.
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

from bujji.production_runtime.universe_coverage import (  # noqa: E402
    COVERAGE_COMPLETE, COVERAGE_INCOMPLETE, COVERAGE_UNKNOWN, evaluate_coverage,
)

U = ["NSE:NIFTY50-INDEX", "NSE:NIFTY2582524000CE", "NSE:NIFTY2582524000PE"]


class TestSubscribedIsNotCovered:
    def test_all_fresh_is_complete_and_permits(self):
        v = evaluate_coverage(U, U, {s: 1.0 for s in U}, 90.0)
        assert v.state == COVERAGE_COMPLETE and v.permits_entry is True
        assert v.fresh == 3

    def test_subscribed_but_never_ticked_blocks(self):
        """THE regression: the 2026-08-21 shape -- connected, silent."""
        v = evaluate_coverage(U, U, {s: None for s in U}, 90.0)
        assert v.permits_entry is False
        assert v.state == COVERAGE_UNKNOWN, "wholly silent is UNKNOWN, not merely incomplete"
        assert "subscribed is not covered" in " ".join(v.reasons)

    def test_one_silent_symbol_blocks_the_whole_entry(self):
        ages = {U[0]: 1.0, U[1]: 1.0, U[2]: None}
        v = evaluate_coverage(U, U, ages, 90.0)
        assert v.permits_entry is False
        assert v.state == COVERAGE_INCOMPLETE
        assert v.silent == (U[2],)

    def test_a_stale_tick_is_not_a_tick(self):
        v = evaluate_coverage(U, U, {U[0]: 1.0, U[1]: 1.0, U[2]: 91.0}, 90.0)
        assert v.permits_entry is False
        assert v.stale == (U[2],)

    def test_a_symbol_never_requested_blocks(self):
        v = evaluate_coverage(U, U[:2], {s: 1.0 for s in U}, 90.0)
        assert v.permits_entry is False
        assert v.never_requested == (U[2],)
        assert "never requested" in " ".join(v.reasons)

    def test_an_empty_universe_is_UNKNOWN_not_permissive(self):
        """"Nothing required" must never be inferred from "nothing built"."""
        v = evaluate_coverage([], [], {}, 90.0)
        assert v.state == COVERAGE_UNKNOWN and v.permits_entry is False

    def test_an_unreadable_age_counts_as_silent(self):
        v = evaluate_coverage(U, U, {U[0]: 1.0, U[1]: 1.0, U[2]: "nonsense"}, 90.0)
        assert v.permits_entry is False and U[2] in v.silent

    def test_the_summary_payload_is_bounded(self):
        """A 242-symbol universe that is wholly silent must not write 242 names
        into every session summary."""
        big = [f"NSE:SYM{i}" for i in range(242)]
        v = evaluate_coverage(big, big, {s: None for s in big}, 90.0)
        d = v.as_dict()
        assert len(d["silent_sample"]) == 10 and d["silent_count"] == 242


def _runner_module():
    spec = importlib.util.spec_from_file_location(
        "_runner_universe_guard", REPO_ROOT / "bujji_options_os_runner.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class _Feed:
    def __init__(self, ages):
        self.ages = ages
        self.subscribed = []

    def subscribe(self, symbols):
        self.subscribed.extend(symbols)

    def tick_age_seconds(self, symbol):
        return self.ages.get(symbol)


class _Universe:
    symbols = U
    atm_strike = 24000
    spot = 24000.0
    roles_resolved = ("FRONT",)
    expiries_available = 18
    expiries_excluded = 15


def _runner(feed=None, universe=None, error=None):
    mod = _runner_module()
    r = mod.OptionsOSRunner.__new__(mod.OptionsOSRunner)
    r._logger = logging.getLogger("test-universe")
    r._governor_result_summary = {}
    r._config = {"providers": {"tick_source": {"max_tick_age_seconds": 90.0}}}
    r._tick_feed = feed
    r._universe = universe
    r._universe_requested = tuple(universe.symbols) if universe else ()
    r._universe_error = error
    return mod, r


class TestTheWideUniverseIsRecordedNotEnforced:
    """RESCOPED 2026-08-22, and these tests changed direction on purpose.

    They used to assert that a silent WIDE universe blocked entry. That was
    the operator's rule inverted: the capture tiers are drawn on open interest
    and are deliberately wider than trading justifies, so one legitimately
    quiet far strike refused the whole session.

    The blocking scope moved to the eligible selection band -- see
    tests/test_selection_band_gate.py, which now carries the "silence blocks"
    assertions at the scope where silence actually means something. What is
    asserted HERE is the other half of the rule: the wide universe is still
    graded and still written, because which symbols went quiet is the evidence
    the tick journal needs.
    """

    def test_a_fully_ticking_universe_is_recorded_complete(self):
        _m, r = _runner(_Feed({s: 1.0 for s in U}), _Universe())
        r._record_universe_coverage()
        assert r._governor_result_summary["universe_coverage"]["state"] == "COMPLETE"
        assert "entry_blocked_by" not in r._governor_result_summary

    def test_a_silent_universe_is_RECORDED_and_does_NOT_block(self):
        """The inverted assertion, stated in its corrected form. A far strike
        that never prints is silence out where silence proves nothing."""
        _m, r = _runner(_Feed({s: None for s in U}), _Universe())
        r._record_universe_coverage()
        payload = r._governor_result_summary["universe_coverage"]
        assert payload["state"] == "UNKNOWN"        # still graded honestly
        assert payload["blocking"] is False         # and explicitly not a veto
        assert payload["silent_count"] == len(U)    # still written for the journal
        assert "entry_blocked_by" not in r._governor_result_summary

    def test_an_unbuilt_universe_is_recorded_UNKNOWN(self):
        _m, r = _runner(_Feed({}), None, error="BUILD_FAILED: boom")
        r._record_universe_coverage()
        assert r._governor_result_summary["universe_coverage"]["state"] == "UNKNOWN"

    def test_an_offline_source_is_NOT_APPLICABLE_not_a_silent_feed(self):
        """Replay and paper_synthetic are deliberately offline sources, not
        silent feeds. Recorded explicitly rather than implied."""
        _m, r = _runner(None, None, error="NOT_APPLICABLE: no websocket tick feed configured")
        r._record_universe_coverage()
        assert r._governor_result_summary["universe_coverage"]["state"] == "NOT_APPLICABLE"


class TestAnUnusableUniverseStillBlocks:
    """Recording instead of vetoing must not become "nothing blocks". A
    universe that could not be BUILT or SUBSCRIBED is not a quiet far strike --
    it is the absence of any evidence at all, and it still refuses. It does so
    through the band gate's containment check, because a band symbol that was
    never subscribed cannot be proven fresh."""

    def _chain(self):
        from dataclasses import dataclass

        @dataclass(frozen=True)
        class Row:
            expiry: str
            strike: float
            option_type: str
            instrument_symbol: str
            symbol_provenance: str = "BROKER_AUTHORITATIVE"

        return [Row("2026-08-25", 24000.0, t, f"NSE:NIFTY2608252400{i}{t}")
                for i, t in enumerate(("CE", "PE"))]

    def test_a_build_failure_blocks_because_nothing_was_subscribed(self):
        _m, r = _runner(_Feed({}), None, error="BUILD_FAILED: boom")
        r._as_of_date = "2026-08-24"
        assert r._band_coverage_permits_entry(self._chain()) is False
        assert r._governor_result_summary["entry_blocked_by"] == "BAND_NOT_SUBSCRIBED"

    def test_no_spot_blocks_rather_than_guessing_a_centre(self):
        _m, r = _runner(_Feed({}), None)
        r._last_spot = None
        r._session_cfg = {"underlying": "NIFTY"}
        r._as_of_date = "2026-08-24"
        r._ensure_universe_subscribed()
        assert r._universe_error.startswith("NO_SPOT")
        assert r._band_coverage_permits_entry(self._chain()) is False


class TestTheGateRunsBeforeStrategySelection:
    def test_coverage_is_checked_before_select_and_lock(self):
        """A universe proven AFTER selection would prove nothing: the strikes
        would already have been chosen from an unverified book."""
        import ast

        src = (REPO_ROOT / "bujji_options_os_runner.py").read_text()
        tree = ast.parse(src)
        entry = next(n for n in ast.walk(tree)
                     if isinstance(n, ast.FunctionDef) and n.name == "_attempt_entry")
        gate = next(c.lineno for c in ast.walk(entry) if isinstance(c, ast.Call)
                    and getattr(c.func, "attr", None) == "_data_quality_permits_entry")
        select = next(c.lineno for c in ast.walk(entry) if isinstance(c, ast.Call)
                      and getattr(c.func, "attr", None) == "select_and_lock_strategy")
        assert gate < select, "the entry gate runs AFTER strategy selection"

    def test_the_universe_is_recorded_before_position_truth_is_read(self):
        """Unchanged in intent, updated for the rename: a book that never
        ticked makes the position read moot, so the universe verdict is
        captured first even though it no longer vetoes."""
        import ast

        src = (REPO_ROOT / "bujji_options_os_runner.py").read_text()
        tree = ast.parse(src)
        fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                  and n.name == "_data_quality_permits_entry")
        calls = [(c.lineno, getattr(c.func, "attr", None)) for c in ast.walk(fn)
                 if isinstance(c, ast.Call)]
        uni = min(l for l, n in calls if n == "_record_universe_coverage")
        recon = [l for l, n in calls if n == "_reconcile_broker_positions"]
        assert not recon or uni < min(recon), (
            "position truth is established before the universe is recorded; a book "
            "that never ticked makes that read moot")
