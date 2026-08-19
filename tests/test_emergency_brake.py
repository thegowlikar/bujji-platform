"""D-6 safety spine: Gate B margin veto + emergency brake.

Gate B tests live beside the existing runtime tests (appended there) since
they reuse its fixtures. This file covers the pure brake decision.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

_spec = importlib.util.spec_from_file_location(
    "bujji_options_os_runner", REPO_ROOT / "bujji_options_os_runner.py")
runner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(runner)

brake = runner._emergency_brake


def test_loss_breach_fires():
    reason = brake(unrealized_pnl=-20_000.0, realized_pnl=-6_000.0,
                   daily_loss_limit=25_000.0, consecutive_blind_cycles=0,
                   max_consecutive_blind_cycles=3)
    assert reason is not None and reason.startswith("EMERGENCY_LOSS")


def test_loss_exactly_at_limit_fires():
    assert brake(unrealized_pnl=-25_000.0, realized_pnl=0.0, daily_loss_limit=25_000.0,
                 consecutive_blind_cycles=0, max_consecutive_blind_cycles=3) is not None


def test_loss_within_limit_holds():
    assert brake(unrealized_pnl=-24_999.0, realized_pnl=0.0, daily_loss_limit=25_000.0,
                 consecutive_blind_cycles=0, max_consecutive_blind_cycles=3) is None


def test_profit_never_fires():
    assert brake(unrealized_pnl=+50_000.0, realized_pnl=0.0, daily_loss_limit=25_000.0,
                 consecutive_blind_cycles=0, max_consecutive_blind_cycles=3) is None


def test_sustained_blindness_fires_even_with_unknown_pnl():
    reason = brake(unrealized_pnl=None, realized_pnl=0.0, daily_loss_limit=25_000.0,
                   consecutive_blind_cycles=3, max_consecutive_blind_cycles=3)
    assert reason is not None and reason.startswith("EMERGENCY_BLIND")


def test_brief_blindness_holds():
    assert brake(unrealized_pnl=None, realized_pnl=0.0, daily_loss_limit=25_000.0,
                 consecutive_blind_cycles=2, max_consecutive_blind_cycles=3) is None


def test_unknown_pnl_without_blindness_holds():
    # A single unpriced valuation is not a loss signal by itself.
    assert brake(unrealized_pnl=None, realized_pnl=-40_000.0, daily_loss_limit=25_000.0,
                 consecutive_blind_cycles=0, max_consecutive_blind_cycles=3) is None


def test_no_limit_configured_never_loss_fires():
    assert brake(unrealized_pnl=-1_000_000.0, realized_pnl=0.0, daily_loss_limit=None,
                 consecutive_blind_cycles=0, max_consecutive_blind_cycles=3) is None
