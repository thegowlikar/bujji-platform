"""Continuous session: Bujji lives through the whole market day.

Loop mechanics under an injected clock and a stub spot source: the organism
observes all day when evidence never stabilises, respects the entry cutoff,
honours observe_until, is bounded by the hard cycle cap, and records an
evidence trail. (The full entry path inside a continuous day is exercised
live and by replay -- these tests pin the lifetime contract itself.)
"""
from __future__ import annotations

import datetime
import importlib.util
import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

_spec = importlib.util.spec_from_file_location(
    "bujji_options_os_runner", REPO_ROOT / "bujji_options_os_runner.py")
runner_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(runner_mod)

REAL_DB = "/opt/bujji/app/data/historical_reality/normalized/historical_observations.db"
BHAV = "/tmp/m1/BhavCopy_NSE_FO_0_0_0_20260525_F_0000.csv"
IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))


class _Clock:
    """Advances a fixed step per call -- fast-forwards the market day."""

    def __init__(self, start, step_seconds):
        self.now = start
        self.step = datetime.timedelta(seconds=step_seconds)

    def __call__(self):
        self.now += self.step
        return self.now


class _StubSpotBroker:
    def __init__(self, prices=None):
        self._prices = list(prices or [])
        self.calls = 0

    async def get_spot(self, underlying):
        self.calls += 1
        if self._prices:
            return self._prices[(self.calls - 1) % len(self._prices)]
        # Erratic series: alternating jumps that no stability gate should bless.
        return 24000.0 + (137.0 if self.calls % 2 else -211.0)


def _cfg(tmp_path, continuous):
    return {
        "shadow_mode": True, "logging": {"namespace": "continuous-test"},
        "session": {"underlying": "NIFTY", "exchange_lot_size": 75, "desired_quantity": 1,
                    "requested_risk": 5000.0,
                    "proposed_trade_effect": {"additional_margin": 10000.0, "additional_max_loss": 5000.0},
                    "continuous": continuous},
        "exit_policy": {"profit_target_fraction": 0.5, "max_loss_fraction": 1.0, "mandatory_exit_time": None},
        "capital_snapshot": {},
        "providers": {
            "market_data": {"type": "replay_chain", "bhavcopy_path": BHAV},
            "regime": {"type": "human_supplied", "trend_regime": "SIDEWAYS", "volatility_regime": "LOW_VOL"},
        },
        "artifacts": {"shadow_sessions_root": str(tmp_path / "ss"), "journal_path": str(tmp_path / "j.db")},
    }


def _continuous_runner(tmp_path, *, start_hm=(10, 0), step=30, spot_prices=None, **cc_overrides):
    cc = {"evidence_poll_interval_seconds": 0.01, "decision_interval_seconds": 0.05,
          "evidence_window_polls": 16, "entry_cutoff": "14:30", "observe_until": "15:30",
          "max_cycles": 40}
    cc.update(cc_overrides)
    clock = _Clock(datetime.datetime(2026, 5, 25, *start_hm, tzinfo=IST), step)
    r = runner_mod.OptionsOSRunner(
        config=_cfg(tmp_path, cc), as_of_date="2026-05-25", session_id="T-CONT",
        logger=logging.getLogger("continuous-test"), clock=clock)
    r._startup()
    r._intelligence_broker = _StubSpotBroker(spot_prices)
    return r, clock


class TestTheOrganismOutlivesItsDecisions:
    def test_unstable_evidence_means_observation_all_day_never_exit(self, tmp_path):
        r, clock = _continuous_runner(tmp_path)
        r._continuous_session()
        summary = r._governor_result_summary
        assert summary["continuous_mode"] is True
        assert summary["continuous_cycles"] >= 5, "the session must keep cycling, not end at the first verdict"
        trail = summary["continuous_evidence"]
        assert len(trail) == summary["continuous_cycles"]
        assert summary.get("strategy_selected") is None
        assert all(e.get("stable") in (None, False) for e in trail if "stable" in e)

    def test_observe_until_ends_the_day_not_a_decision(self, tmp_path):
        # Big clock steps: the fake day reaches 15:30 after a handful of cycles.
        r, clock = _continuous_runner(tmp_path, start_hm=(15, 20), step=200)
        r._continuous_session()
        assert clock.now.time() >= datetime.time(15, 30)
        assert r._governor_result_summary["continuous_cycles"] < 10

    def test_the_hard_cycle_cap_is_the_final_authority(self, tmp_path):
        # A clock that barely advances would loop forever without the cap.
        r, _ = _continuous_runner(tmp_path, step=1, max_cycles=7)
        r._continuous_session()
        assert r._governor_result_summary["continuous_cycles"] == 7

    def test_past_the_entry_cutoff_it_observes_but_never_selects(self, tmp_path):
        r, _ = _continuous_runner(tmp_path, start_hm=(14, 45), step=30, max_cycles=10,
                                  spot_prices=[24000.0] * 500)
        r._continuous_session()
        # Flat spots could look "stable" -- but past 14:30 no selection may occur.
        assert r._governor_result_summary.get("strategy_selected") is None

    def test_evidence_trail_records_every_cycle_with_timestamps(self, tmp_path):
        r, _ = _continuous_runner(tmp_path, max_cycles=6)
        r._continuous_session()
        trail = r._governor_result_summary["continuous_evidence"]
        assert len(trail) == 6
        assert all("at" in e and "spots" in e for e in trail)

    def test_single_shot_mode_is_untouched_without_the_config(self, tmp_path):
        cfg = _cfg(tmp_path, None)
        cfg["session"].pop("continuous")
        r = runner_mod.OptionsOSRunner(config=cfg, as_of_date="2026-05-25",
                                       session_id="T-SINGLE", logger=logging.getLogger("continuous-test"))
        r._startup()
        assert not r._session_cfg.get("continuous")


class TestTheBurstOffsetLivesOnTheCadenceNotTheStartTime:
    """The 09:22:30 start bought FYERS burst separation from the shadow
    campaign by sacrificing the opening 7.5 minutes. The separation is a
    property of the decision cadence, so it belongs there: cycle 1 runs
    longer, later cycles are unchanged, and the session still begins at the
    open."""

    def test_offset_lengthens_only_the_first_cycle(self, tmp_path):
        base, _ = _continuous_runner(tmp_path, max_cycles=3)
        base._continuous_session()
        base_calls = base._intelligence_broker.calls

        offset_seconds = 0.05  # == one extra decision interval at the test cadence
        r, _ = _continuous_runner(tmp_path, max_cycles=3,
                                  decision_phase_offset_seconds=offset_seconds)
        r._continuous_session()

        extra = int(round(offset_seconds / 0.01))  # offset / poll interval
        assert r._intelligence_broker.calls == base_calls + extra, (
            "the offset must add polls to cycle 1 ONLY -- not to every cycle")

    def test_zero_offset_is_the_default_and_changes_nothing(self, tmp_path):
        a, _ = _continuous_runner(tmp_path, max_cycles=3)
        a._continuous_session()
        b, _ = _continuous_runner(tmp_path, max_cycles=3, decision_phase_offset_seconds=0)
        b._continuous_session()
        assert a._intelligence_broker.calls == b._intelligence_broker.calls

    def test_the_offset_never_delays_the_start_of_observation(self, tmp_path):
        """Evidence collection begins immediately; the offset defers the first
        DECISION, never the first observation."""
        r, _ = _continuous_runner(tmp_path, max_cycles=2, decision_phase_offset_seconds=0.05)
        r._continuous_session()
        trail = r._governor_result_summary["continuous_evidence"]
        assert trail and trail[0]["spots"] > 0
