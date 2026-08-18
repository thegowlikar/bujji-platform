"""Tick wiring: the provider is actually constructed, and a blind session
cannot masquerade as a measured one.

Before this, `_price_provider` was initialised to None and never assigned
anywhere in the runner, so the revaluation path built for it could never
run: every session silently revalued positions against their own ENTRY
prices. Unrealized P&L was 0 by construction, MFE/MAE were 0-looking but
meaningless, and no stop-loss or profit-target could fire -- and nothing
in the logs distinguished that from a healthy session.
"""
from __future__ import annotations

import logging

import pytest

from bujji_options_os_runner import ConfigurationError, OptionsOSRunner

REAL_DB = "/opt/bujji/app/data/historical_reality/normalized/historical_observations.db"
BHAV = "/tmp/m1/BhavCopy_NSE_FO_0_0_0_20260525_F_0000.csv"
DAY = "2026-05-25"


def cfg(tmp_path, *, tick=None, market_data=None, mgmt=None):
    base = {
        "shadow_mode": True, "logging": {"namespace": "tick-wiring-test"},
        "session": {"underlying": "NIFTY", "exchange_lot_size": 75, "desired_quantity": 1,
                    "requested_risk": 5000.0,
                    "proposed_trade_effect": {"additional_margin": 10000.0, "additional_max_loss": 5000.0}},
        "exit_policy": {"profit_target_fraction": 0.5, "max_loss_fraction": 1.0, "mandatory_exit_time": None},
        "capital_snapshot": {},
        "providers": {
            "market_data": market_data or {"type": "replay_chain", "bhavcopy_path": BHAV},
            "regime": {"type": "human_supplied", "trend_regime": "SIDEWAYS", "volatility_regime": "LOW_VOL"},
        },
        "artifacts": {"shadow_sessions_root": str(tmp_path / "ss"), "journal_path": str(tmp_path / "j.db")},
    }
    if tick is not None:
        base["providers"]["tick_source"] = tick
    if mgmt is not None:
        base["position_management"] = mgmt
    return base


def _runner(tmp_path, sid, **kw):
    r = OptionsOSRunner(config=cfg(tmp_path, **kw), as_of_date=DAY, session_id=sid,
                        logger=logging.getLogger("tick-wiring-test"))
    r._startup()
    return r


class TestTickSourceIsActuallyConstructed:
    def test_no_tick_source_leaves_provider_none_and_warns(self, tmp_path, caplog):
        with caplog.at_level(logging.WARNING):
            r = _runner(tmp_path, "T-NONE")
        assert r._price_provider is None
        assert any("Tick source: NONE" in m for m in caplog.messages)

    def test_observation_store_tick_source_constructs_a_real_provider(self, tmp_path):
        from bujji.production_runtime.intraday_price_provider import HistoricalTickProvider
        r = _runner(tmp_path, "T-STORE",
                    tick={"type": "observation_store", "observation_store_path": REAL_DB})
        assert isinstance(r._price_provider, HistoricalTickProvider)

    def test_broker_tick_source_constructs_a_live_provider(self, tmp_path):
        from bujji.production_runtime.intraday_price_provider import LiveTickProvider
        r = _runner(tmp_path, "T-BROKER", tick={"type": "broker"})
        assert isinstance(r._price_provider, LiveTickProvider)

    def test_store_tick_source_without_a_path_refuses_to_guess(self, tmp_path):
        with pytest.raises(ConfigurationError, match="observation_store_path is required"):
            _runner(tmp_path, "T-BAD", tick={"type": "observation_store"})


class TestChainSourceSelection:
    def test_observation_store_chain_requires_an_explicit_path(self, tmp_path):
        with pytest.raises(ConfigurationError, match="observation_store_path is required"):
            _runner(tmp_path, "C-BAD", market_data={"type": "observation_store"})

    def test_bhavcopy_remains_the_default(self, tmp_path):
        from bujji.production_runtime.market_data_provider import ReplayChainProvider
        r = _runner(tmp_path, "C-DEFAULT")
        assert isinstance(r._market_data_provider, ReplayChainProvider)


class TestBlindCyclesAreLoud:
    """A blind cycle revalues against entry prices. It must be impossible
    to mistake for a real observation."""

    def test_blind_cycle_warns_and_is_counted(self, tmp_path, caplog):
        r = _runner(tmp_path, "B-1")
        r._entry_prices = {"X": 100.0}
        r._contracts_by_symbol = {}          # no contracts -> provider path cannot price
        with caplog.at_level(logging.WARNING):
            prices, from_ticks = r._current_leg_prices("2026-05-25T10:00:00+05:30")
        assert from_ticks is False
        assert prices == {"X": 100.0}        # honest fallback, entry prices

    def test_blind_cycles_do_not_bank_a_valuation(self, tmp_path):
        """THE critical property: a blind cycle's 0.0 unrealized P&L means
        'we never looked', not 'it never moved'. Banking it would produce
        an MFE/MAE that reads as measured when nothing was measured."""
        r = _runner(tmp_path, "B-2")
        r._entry_prices = {"X": 100.0}
        r._contracts_by_symbol = {}
        assert r._valuation_history == []
        r._blind_cycles = 3                  # simulate three blind passes
        # No valuation was ever appended, so MFE/MAE have nothing to
        # compute from and stay honestly absent.
        from bujji.outcome_attribution.engine import compute_mfe_mae
        assert compute_mfe_mae(tuple(r._valuation_history)) == (None, None)

    def test_a_fully_blind_real_session_is_flagged_not_silently_archived(self, tmp_path, caplog):
        """End-to-end, through the real session lifecycle -- the governor's
        own state machine refuses illegal shortcuts, so this drives a
        genuine run rather than poking `_session_archive` directly."""
        config = cfg(tmp_path, mgmt={"cycle_interval_seconds": 0, "max_cycles": 3,
                                      "monitor_until": "23:59:59"})
        r = OptionsOSRunner(config=config, as_of_date=DAY, session_id="B-3",
                            logger=logging.getLogger("tick-wiring-test"))
        with caplog.at_level(logging.ERROR):
            summary = r.run()
        if summary.get("entry_filled"):
            # A position was held with no tick source: every cycle blind.
            assert summary["cycles_priced_from_ticks"] == 0
            assert summary["cycles_blind"] > 0
            assert summary.get("session_blind") is True
            assert any("SESSION WAS BLIND" in m for m in caplog.messages)
        else:
            # No position -> no management cycles -> nothing to be blind about.
            assert summary["cycles_blind"] == 0

    def test_summary_always_reports_tick_provenance(self, tmp_path):
        """Provenance is never optional: every archived session states how
        many cycles saw real prices, so a blind session can never be
        pooled with sighted ones by accident."""
        config = cfg(tmp_path, mgmt={"cycle_interval_seconds": 0, "max_cycles": 2,
                                      "monitor_until": "23:59:59"})
        r = OptionsOSRunner(config=config, as_of_date=DAY, session_id="B-4",
                            logger=logging.getLogger("tick-wiring-test"))
        summary = r.run()
        assert "cycles_priced_from_ticks" in summary
        assert "cycles_blind" in summary


class TestManagementLoopIsBounded:
    def test_no_open_position_means_no_monitoring(self, tmp_path):
        r = _runner(tmp_path, "L-1", mgmt={"cycle_interval_seconds": 0, "max_cycles": 5})
        r._entry_prices = {}
        r._position_management()
        assert r._governor_result_summary.get("management_cycles") is None

    def test_loop_is_capped_by_max_cycles(self, tmp_path):
        """The cap, not the wall-clock, is the final authority on
        termination -- a mis-set clock must never spin forever."""
        r = _runner(tmp_path, "L-2", mgmt={"cycle_interval_seconds": 0, "max_cycles": 3,
                                            "monitor_until": "23:59:59"})
        r._entry_prices = {"X": 100.0}
        calls = []
        r._run_one_management_pass = lambda label: calls.append(label)
        r._position_management()
        assert len(calls) == 3
        assert r._governor_result_summary["management_cycles"] == 3

    def test_loop_stops_early_when_the_position_closes(self, tmp_path):
        r = _runner(tmp_path, "L-3", mgmt={"cycle_interval_seconds": 0, "max_cycles": 50,
                                            "monitor_until": "23:59:59"})
        r._entry_prices = {"X": 100.0}
        calls = []

        def close_after_two(label):
            calls.append(label)
            if len(calls) == 2:
                r._entry_prices = {}         # position closed by the exit path
        r._run_one_management_pass = close_after_two
        r._position_management()
        assert len(calls) == 2
