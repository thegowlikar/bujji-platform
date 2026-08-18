"""The live runner closes the learning loop.

`bujji_options_os_runner.py` is the ONE entrypoint that reaches a real
PaperBroker fill. Before this wiring it stopped at
`PositionLifecycleRuntime.mark_closed()` -- an in-memory enum flip --
and produced no `OutcomeMemoryRecord` at all, so a 30-session paper
campaign would have taught Bujji nothing.

These tests drive the runner's OWN canonical-lifecycle methods with real
objects (real `StrikeLeg`s, real `OrderResult`s, the real canonical
reducer) and assert a real memory record comes out the other end.

Why not drive `r.run()` end to end: the pre-existing
`test_runner_full_lifecycle_real_regime_reaches_entry_window` documents
that session #1 against a fresh journal blocks at PORTFOLIO
(RISK_INVALID on a genuinely empty book), so a full run never reaches a
filled entry. That is an unrelated, already-disclosed gap; these tests
exercise the path that runs once an entry DOES fill, without papering
over it.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bujji.core.enums import OrderStatus
from bujji.core.models import OrderResult
from bujji.msi_trade_construction.models import StrikeLeg
from bujji.position_lifecycle.models import STATUS_CLOSED
from bujji_options_os_runner import OptionsOSRunner

REAL_BHAVCOPY = "/tmp/m1/BhavCopy_NSE_FO_0_0_0_20260525_F_0000.csv"
DAY = "2026-05-25"
LOT = 75


def _master_fixture(tmp_path):
    """A pinned instrument master carrying exactly LOT for NIFTY.

    The runner reads the lot size from the instrument master (never config)
    since the 2026-08-18 fix; without this fixture these tests would read the
    VPS's live cache and break whenever the exchange revises the real lot
    size -- the P&L expectations below are LOT-arithmetic, pinned with it.
    Row layout: 21 columns, index 3=lot, 8=expiry epoch, 9=symbol,
    13=underlying, 15=strike, 16=type (verified live 2026-07-19).
    """
    row = [""] * 21
    row[3] = str(LOT)
    row[8] = "1787652600"
    row[9] = "NSE:NIFTY26AUG24400CE"
    row[13] = "NIFTY"
    row[15] = "24400.0"
    row[16] = "CE"
    d = tmp_path / "instrument_master"
    d.mkdir(exist_ok=True)
    (d / "fyers_fo_NSE.csv").write_text(",".join(row) + "\n")
    return str(d)


def base_config(tmp_path):
    return {
        "shadow_mode": True,
        "logging": {"namespace": "bujji-options-os-outcome-test"},
        "session": {
            "underlying": "NIFTY", "exchange_lot_size": LOT, "desired_quantity": 1,
            "instrument_master_dir": _master_fixture(tmp_path),
            "requested_risk": 5000.0,
            "proposed_trade_effect": {"additional_margin": 10000.0, "additional_max_loss": 5000.0},
        },
        "exit_policy": {"profit_target_fraction": 0.5, "max_loss_fraction": 1.0, "mandatory_exit_time": None},
        "capital_snapshot": {},
        "providers": {
            "market_data": {"type": "replay_chain", "bhavcopy_path": REAL_BHAVCOPY},
            "regime": {"type": "human_supplied", "trend_regime": "SIDEWAYS", "volatility_regime": "LOW_VOL"},
        },
        "artifacts": {
            "shadow_sessions_root": str(tmp_path / "shadow_sessions"),
            "journal_path": str(tmp_path / "pg_journal.db"),
        },
    }


def _leg(role, option_type, strike, side, premium):
    return StrikeLeg(
        role=role, option_type=option_type, strike=strike, expiry="2026-05-28",
        delta=0.2, premium=premium, open_interest=100000.0, side=side, ratio=1,
        reasoning=("runner outcome-loop fixture",),
    )


class _Proposal:
    def __init__(self, legs):
        self.strategy_family = "SHORT_STRANGLE"
        self.legs = legs


class _CycleResult:
    """The real shape `_entry_window` reads off a filled entry."""

    def __init__(self, legs, fills):
        self.proposal = _Proposal(legs)
        self.order_results = [
            OrderResult(f"CID-{i}", OrderStatus.FILLED, broker_order_id=f"PAPER-{i}",
                        filled_quantity=LOT, average_price=p)
            for i, p in enumerate(fills)
        ]
        self.filled = True


class _Execution:
    def __init__(self, exit_prices):
        self.orders_submitted = tuple(
            OrderResult(f"X-{i}", OrderStatus.FILLED, broker_order_id=f"PAPER-X{i}",
                        filled_quantity=LOT, average_price=p)
            for i, p in enumerate(exit_prices)
        )


class _MgmtResult:
    def __init__(self, execution):
        self.forced_execution = execution


def _runner(tmp_path, session_id):
    r = OptionsOSRunner(config=base_config(tmp_path), as_of_date=DAY, session_id=session_id,
                        logger=logging.getLogger("test-outcome-loop"))
    r._startup()
    return r


LEGS = (
    _leg("SHORT", "CE", 25200.0, "SELL", 100.0),
    _leg("SHORT", "PE", 24800.0, "SELL", 90.0),
)


class TestRunnerClosesTheLoop:
    def test_entry_exit_produces_a_real_outcome_memory_record(self, tmp_path):
        r = _runner(tmp_path, "OUTCOME-1")
        # Real fills at 101.5 / 91.5.
        r._record_canonical_entry("PG-1", _CycleResult(LEGS, [101.5, 91.5]),
                                   spot=25000.0, trend_regime="SIDEWAYS", selection=None)
        assert r._canonical_position_id is not None
        lifecycle = r._lifecycle_states[r._canonical_position_id]
        # Each of the two SHORT legs kept its OWN real fill.
        assert [l.entry_premium for l in lifecycle.legs] == [101.5, 91.5]

        # Bought back at 40 / 30 -- map exit fills the same way the
        # executor emits them (ordered alongside the group's positions).
        r._contracts_by_symbol = {
            "CE": type("C", (), {"strike": 25200.0, "option_type": "CE", "expiry": "2026-05-28"})(),
            "PE": type("C", (), {"strike": 24800.0, "option_type": "PE", "expiry": "2026-05-28"})(),
        }
        exit_prices = {
            lifecycle.legs[0].leg_id: {"exit_price": 40.0},
            lifecycle.legs[1].leg_id: {"exit_price": 30.0},
        }
        r._exit_prices_by_leg.update(exit_prices)

        r._close_canonical_lifecycle()

        assert r._lifecycle_states[r._canonical_position_id].status == STATUS_CLOSED
        record = r._outcome_memory_record
        assert record is not None, "the live runner must now produce a real memory record"
        assert record.strategy_family == "SHORT_STRANGLE"
        assert record.entry_regime == "SIDEWAYS"
        # (101.5-40) + (91.5-30) = 123 points x 75 = 9225.
        assert record.realized_pnl == pytest.approx(9225.0)
        assert record.outcome_direction == "PROFIT"
        assert r._governor_result_summary["outcome_memory_id"] == record.memory_id

    def test_a_loss_is_recorded_just_as_faithfully(self, tmp_path):
        r = _runner(tmp_path, "OUTCOME-2")
        r._record_canonical_entry("PG-2", _CycleResult(LEGS, [100.0, 90.0]),
                                   spot=25000.0, trend_regime="TREND_UP", selection=None)
        lifecycle = r._lifecycle_states[r._canonical_position_id]
        r._exit_prices_by_leg.update({
            lifecycle.legs[0].leg_id: {"exit_price": 200.0},
            lifecycle.legs[1].leg_id: {"exit_price": 95.0},
        })
        r._close_canonical_lifecycle()
        record = r._outcome_memory_record
        # (100-200) + (90-95) = -105 x 75 = -7875.
        assert record.realized_pnl == pytest.approx(-7875.0)
        assert record.outcome_direction == "LOSS"


class TestNeverBreaksTheSession:
    def test_no_entry_means_no_close_attempt_and_no_crash(self, tmp_path):
        r = _runner(tmp_path, "OUTCOME-3")
        r._close_canonical_lifecycle()  # nothing was ever opened.
        assert r._outcome_memory_record is None

    def test_a_bookkeeping_failure_does_not_raise_into_the_session(self, tmp_path):
        """A malformed cycle_result must be logged and swallowed -- a
        live session holding a real open position must never die because
        the learning record could not be built."""
        r = _runner(tmp_path, "OUTCOME-4")
        broken = type("Broken", (), {})()  # no .proposal, no .order_results
        r._record_canonical_entry("PG-4", broken, spot=25000.0, trend_regime="SIDEWAYS", selection=None)
        assert r._canonical_position_id is None  # honestly absent, not fabricated.

    def test_exit_capture_without_an_open_lifecycle_is_a_noop(self, tmp_path):
        r = _runner(tmp_path, "OUTCOME-5")
        r._capture_exit_fills(["NIFTY25200CE"], _MgmtResult(_Execution([40.0])))
        assert r._exit_prices_by_leg == {}

    def test_exit_symbols_must_be_the_pre_exit_snapshot(self, tmp_path):
        """Regression guard for a real bug: exit symbols were originally
        read from the registry AFTER the exit executed, by which point
        the positions are flat and the registry returns nothing -- so
        every real exit fill was silently dropped and the outcome record
        carried no P&L. An empty symbol list must map to zero fills, which
        is exactly the symptom that regression produced."""
        r = _runner(tmp_path, "OUTCOME-7")
        r._record_canonical_entry("PG-7", _CycleResult(LEGS, [100.0, 90.0]),
                                   spot=25000.0, trend_regime="SIDEWAYS", selection=None)
        r._capture_exit_fills([], _MgmtResult(_Execution([40.0, 30.0])))
        assert r._exit_prices_by_leg == {}

    def test_legs_with_no_exit_price_degrade_to_unknown_not_assumed(self, tmp_path):
        r = _runner(tmp_path, "OUTCOME-6")
        r._record_canonical_entry("PG-6", _CycleResult(LEGS, [100.0, 90.0]),
                                   spot=25000.0, trend_regime="SIDEWAYS", selection=None)
        lifecycle = r._lifecycle_states[r._canonical_position_id]
        # Only one leg has real exit evidence.
        r._exit_prices_by_leg.update({lifecycle.legs[0].leg_id: {"exit_price": 40.0}})
        r._close_canonical_lifecycle()
        structured = r._lifecycle_states[r._canonical_position_id].structured_exit
        assert structured["pnl_status"] != "PNL_KNOWN"
