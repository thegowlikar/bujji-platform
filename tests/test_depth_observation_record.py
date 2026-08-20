"""Depth is recorded beside the direction it did not inform.

OPERATOR DECISION 2026-08-20: measure first. Depth is fetched every cycle and
is deliberately NOT a direction lens, because reconcile_lenses ignores
confidence when detecting conflict AND takes the MINIMUM confidence across
opinionated lenses -- so an honest LOW confidence on a single order-book
snapshot would cap the whole direction read at LOW whenever it spoke, and
manufacture MIXED whenever the book leaned against real structure.

So the raw imbalance is recorded next to what direction actually concluded on
the same cycle. NO THRESHOLD is baked in: choosing one before measuring is
exactly what this record exists to avoid.
"""
from __future__ import annotations

import importlib.util
import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from bujji.shadow_observatory.thesis_artifact import build_thesis_artifact

_spec = importlib.util.spec_from_file_location(
    "runner_depth", REPO_ROOT / "bujji_options_os_runner.py")
runner_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(runner_mod)


@dataclass(frozen=True)
class _Futures:
    symbol: str = "NSE:NIFTY26AUGFUT"
    total_buy_qty: Optional[int] = 266760
    total_sell_qty: Optional[int] = 318435


@dataclass(frozen=True)
class _Snapshot:
    futures: Optional[_Futures] = _Futures()


@dataclass(frozen=True)
class _Thesis:
    directional_bias: str = "NEUTRAL"
    confidence: str = "MODERATE"
    market_regime: str = "RANGE_PERSISTENCE"


class _Stub:
    _logger = logging.getLogger("depth-record-test")


def _observe(snapshot=None, thesis=None):
    return runner_mod.OptionsOSRunner._depth_observation(
        _Stub(), snapshot if snapshot is not None else _Snapshot(),
        thesis if thesis is not None else _Thesis())


class TestItRecordsTheComparison:
    def test_the_real_captured_numbers_produce_the_real_imbalance(self):
        obs = _observe()
        assert obs["status"] == "OK"
        assert obs["total_buy_qty"] == 266760 and obs["total_sell_qty"] == 318435
        assert obs["imbalance"] == pytest.approx(-0.0883, abs=1e-4)

    def test_it_records_what_direction_concluded_on_the_same_cycle(self):
        """The whole point: the book's opinion beside the decision it did not
        inform, so 'does the book agree or fight?' becomes answerable."""
        obs = _observe(thesis=_Thesis(directional_bias="STRONG_BEARISH"))
        assert obs["direction_concluded"] == "STRONG_BEARISH"
        assert obs["thesis_confidence"] == "MODERATE"

    def test_the_record_says_it_is_not_consumed(self):
        """Explicit in the record itself, so no reader can mistake a recorded
        observation for an input to the decision."""
        assert _observe()["consumed_by_direction"] is False

    def test_no_threshold_or_lean_is_recorded(self):
        """Baking a bullish/bearish call in here would pre-empt the very
        measurement this exists to enable."""
        obs = _observe()
        assert "lean" not in obs and "directional_lean" not in obs
        assert "threshold" not in json.dumps(obs).lower()


class TestAbsenceStaysAbsent:
    def test_no_futures_snapshot_is_named_not_faked(self):
        obs = _observe(snapshot=_Snapshot(futures=None))
        assert obs["status"] == "NO_FUTURES_SNAPSHOT"
        assert obs["consumed_by_direction"] is False

    def test_a_failed_depth_poll_records_not_observed_with_a_null_imbalance(self):
        """None, never 0.0 -- a zero imbalance is a measured balanced book."""
        obs = _observe(snapshot=_Snapshot(_Futures(total_buy_qty=None, total_sell_qty=None)))
        assert obs["status"] == "NOT_OBSERVED"
        assert obs["imbalance"] is None

    def test_a_measured_balance_records_zero_not_null(self):
        obs = _observe(snapshot=_Snapshot(_Futures(total_buy_qty=200000, total_sell_qty=200000)))
        assert obs["status"] == "OK" and obs["imbalance"] == 0.0

    def test_a_broken_snapshot_is_captured_not_raised(self):
        obs = runner_mod.OptionsOSRunner._depth_observation(_Stub(), object(), _Thesis())
        assert obs["status"] == "NO_FUTURES_SNAPSHOT"


class TestItReachesTheThesisRecord:
    def test_the_artifact_carries_the_observation(self):
        artifact = build_thesis_artifact(
            thesis=_Thesis(), cycle_record=None, depth_observation=_observe())
        assert artifact["depth_observation"]["imbalance"] == pytest.approx(-0.0883, abs=1e-4)

    def test_an_absent_observation_is_recorded_as_a_real_absence(self):
        artifact = build_thesis_artifact(thesis=_Thesis(), cycle_record=None)
        assert "depth_observation" in artifact
        assert artifact["depth_observation"] is None

    def test_the_artifact_stays_json_serialisable(self):
        artifact = build_thesis_artifact(
            thesis=_Thesis(), cycle_record=None, depth_observation=_observe())
        assert json.loads(json.dumps(artifact)) == artifact


class TestDepthIsStillNotALens:
    def test_direction_does_not_read_depth(self):
        """The held decision, asserted. If depth ever becomes a lens this
        test should fail and be updated deliberately, not drift."""
        source = (REPO_ROOT / "bujji" / "msi_market_direction" / "engine.py").read_text()
        assert "depth" not in source.lower()

    def test_the_direction_bridge_does_not_pass_depth(self):
        source = (REPO_ROOT / "bujji" / "market_state" / "direction_bridge.py").read_text()
        assert "depth" not in source.lower()

    def test_the_reserved_liquidity_slot_is_still_unfilled(self):
        from bujji.msi_market_direction import taxonomy as mdi_tax

        source = (REPO_ROOT / "bujji" / "msi_market_direction" / "engine.py").read_text()
        assert mdi_tax.LIQUIDITY_DIRECTION in mdi_tax.KNOWN_LENS_NAMES
        assert "LIQUIDITY_DIRECTION" not in source
