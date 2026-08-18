"""The daily chain capture, narrowed to the tiered universe.

This wiring reverses an explicit earlier instruction ("do not only capture
ATM... the whole observable universe") on measured evidence, so these tests
pin BOTH directions: that the narrowing really happens, and that the old
whole-universe behaviour is still reachable rather than erased.

The chain fixture is the real dated capture
(`data_certification/fyers_option_chain_discovery_20260813.json`) wherever
it is present, so a change in FYERS's response shape breaks these rather
than passing against an invented one.
"""
from __future__ import annotations

import datetime
import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path("/opt/bujji/app")
SCRIPT = REPO / "scripts/capture_options_reality_session.py"
CAPTURE = REPO / "data_certification/fyers_option_chain_discovery_20260813.json"


def _load_script(monkeypatch, mode="tiered"):
    """Import the capture script fresh, with CAPTURE_UNIVERSE set.

    Re-imported per test because the plan is deliberately session-scoped
    module state -- the thing under test.
    """
    monkeypatch.setenv("CAPTURE_UNIVERSE", mode)
    sys.modules.pop("_capture_under_test", None)
    spec = importlib.util.spec_from_file_location("_capture_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pytestmark = pytest.mark.skipif(not SCRIPT.exists(), reason="VPS-only script")


def _real_chain():
    return json.loads(CAPTURE.read_text())["raw_response"]["data"]


class TestUnderlyingSpot:
    def test_reads_the_sentinel_row(self, monkeypatch):
        mod = _load_script(monkeypatch)
        data = {"optionsChain": [
            {"strike_price": -1, "ltp": 24287.65},
            {"strike_price": 24300, "ltp": 100.0, "option_type": "CE"},
        ]}
        assert mod._underlying_spot(data) == 24287.65

    def test_a_missing_or_zero_spot_is_none_not_a_guess(self, monkeypatch):
        mod = _load_script(monkeypatch)
        assert mod._underlying_spot({"optionsChain": []}) is None
        assert mod._underlying_spot(
            {"optionsChain": [{"strike_price": -1, "ltp": 0}]}) is None

    @pytest.mark.skipif(not CAPTURE.exists(), reason="real capture is VPS-only")
    def test_against_the_real_chain_response(self, monkeypatch):
        mod = _load_script(monkeypatch)
        spot = mod._underlying_spot(_real_chain())
        assert spot and spot > 1000, "expected a real NIFTY level"


class TestPlanResolution:
    def test_full_mode_disables_narrowing_entirely(self, monkeypatch):
        """The superseded instruction stays reachable, not erased."""
        mod = _load_script(monkeypatch, mode="full")
        data = {"optionsChain": [{"strike_price": -1, "ltp": 24287.65}]}
        assert mod._ensure_capture_plan(data, ["2026-08-18"]) is None

    def test_a_spot_less_response_captures_wide_rather_than_guessing(self, monkeypatch):
        """Capturing too much is recoverable; centring a band on an invented
        spot silently records the wrong contracts."""
        mod = _load_script(monkeypatch)
        assert mod._ensure_capture_plan({"optionsChain": []}, ["2026-08-18"]) is None

    def test_the_plan_is_resolved_once_and_reused(self, monkeypatch):
        """A band that re-centred intraday would start and stop capturing
        contracts mid-session, leaving ragged partial series."""
        mod = _load_script(monkeypatch)
        expiries = ["2026-08-18", "2026-08-25", "2026-09-29"]
        first = mod._ensure_capture_plan(
            {"optionsChain": [{"strike_price": -1, "ltp": 24287.65}]}, expiries)
        moved = mod._ensure_capture_plan(
            {"optionsChain": [{"strike_price": -1, "ltp": 25100.0}]}, expiries)
        assert first is moved
        assert moved.atm_strike == first.atm_strike


class TestNarrowing:
    def _plan(self, monkeypatch):
        mod = _load_script(monkeypatch)
        plan = mod._ensure_capture_plan(
            {"optionsChain": [{"strike_price": -1, "ltp": 24287.65}]},
            ["2026-08-18", "2026-08-25", "2026-09-01", "2026-09-29",
             "2026-12-29", "2031-06-24"],
        )
        return mod, plan

    def test_untiered_expiries_are_dropped(self, monkeypatch):
        _mod, plan = self._plan(monkeypatch)
        assert datetime.date(2031, 6, 24) not in plan.band_by_expiry
        assert datetime.date(2026, 12, 29) not in plan.band_by_expiry
        assert datetime.date(2026, 8, 18) in plan.band_by_expiry

    def test_far_strikes_are_dropped_within_a_kept_expiry(self, monkeypatch):
        _mod, plan = self._plan(monkeypatch)
        front = datetime.date(2026, 8, 18)
        assert plan.accepts(front, 24300) is True
        assert plan.accepts(front, 21800) is False        # 2,500 pts out

    def test_a_row_without_a_strike_is_dropped_not_admitted(self, monkeypatch):
        """`r.get("strike_price") or 0` must score as far out-of-band."""
        _mod, plan = self._plan(monkeypatch)
        assert plan.accepts(datetime.date(2026, 8, 18), 0) is False

    @pytest.mark.skipif(not CAPTURE.exists(), reason="real capture is VPS-only")
    def test_the_filter_admits_real_in_band_rows_and_rejects_out_of_band(self, monkeypatch):
        """The dated capture is a strikecount=5 DISCOVERY probe, so all 22 of
        its rows already sit near ATM and none should be filtered -- keeping
        every row is the correct result here, not a broken filter. The live
        script runs STRIKE_COUNT=50, which is where narrowing bites. So this
        asserts the two things the fixture can actually prove: real rows pass,
        and an out-of-band strike at the same expiry does not."""
        mod, plan = self._plan(monkeypatch)
        rows = [r for r in _real_chain()["optionsChain"]
                if r.get("option_type") in ("CE", "PE")]
        expiry = datetime.date(2026, 8, 18)
        kept = [r for r in rows if plan.accepts(expiry, r.get("strike_price") or 0)]
        assert kept, "no real row survived the band"
        for row in kept:
            assert abs(row["strike_price"] - plan.atm_strike) <= plan.band_by_expiry[expiry]
        assert plan.accepts(expiry, plan.atm_strike + 5000) is False
