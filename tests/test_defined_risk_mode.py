"""Real money never sells a shape whose loss is unbounded.

OPERATOR DECISION 2026-08-20, from a measurement. The stop-loss, the daily
loss limit and the emergency brake all run on ONE 300-second management
heartbeat. Over 168,194 real 5-minute NIFTY bars (2017-2026) the worst single
bar ranged 611.8 points -- against the real ATM straddle credit measured on
2026-08-19 (240.95 pts, 1 lot) that is ~Rs 39,764 of adverse move inside one
unchecked interval: 2.5x the stop, 1.6x the whole daily loss limit. Bars of
200+ points occur ~5 times a year.

A naked short strangle has no structural floor. A wing is a floor that holds
regardless of how slowly the loop runs. So real money takes the defined-risk
twin -- and these tests exist to make that impossible to forget, not merely
documented.
"""
from __future__ import annotations

import datetime
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from bujji.msi_trade_construction import taxonomy as mtc
from bujji.msi_trade_construction.config import FAMILY_DELTA_TARGETS
from bujji.production_runtime.trading_session_governor import strategy_selector as sel

CLOCK = lambda: datetime.datetime(2026, 8, 20, 10, 0)  # noqa: E731

REGIMES = [(t, v) for t in ("SIDEWAYS", "TRENDING_UP", "TRENDING_DOWN")
           for v in ("LOW_VOL", "HIGH_VOL", "CONTRACTION")]


def real(t, v):
    return sel.select_strategy(t, v, CLOCK, defined_risk_only=True).selected_strategy


def paper(t, v):
    return sel.select_strategy(t, v, CLOCK).selected_strategy


class TestTheInvariant:
    def test_no_regime_can_select_an_unbounded_shape_for_real_money(self):
        """THE point of the whole mode. Enumerated over every regime rather
        than spot-checked, so a future branch cannot slip past it."""
        selected = {real(t, v) for t, v in REGIMES} - {None}
        assert selected, "positive control: real-money mode must still select something"
        assert selected <= set(mtc.DEFINED_RISK_FAMILIES)
        assert not (selected & set(mtc.UNDEFINED_RISK_FAMILIES))

    def test_paper_mode_still_reaches_the_naked_shapes(self):
        """Real-money safety must not silently disarm the paper campaign that
        is gathering the evidence."""
        selected = {paper(t, v) for t, v in REGIMES} - {None}
        assert selected & set(mtc.UNDEFINED_RISK_FAMILIES)


class TestTheSubstitution:
    @pytest.mark.parametrize("vol,naked,winged", [
        ("HIGH_VOL", "VOLATILITY_COMPRESSION", "IRON_FLY"),
        ("LOW_VOL", "NEUTRAL_PREMIUM_SELLING", "IRON_CONDOR"),
        ("CONTRACTION", "NEUTRAL_PREMIUM_SELLING", "IRON_CONDOR"),
    ])
    def test_each_naked_sideways_shape_takes_its_twin(self, vol, naked, winged):
        assert paper("SIDEWAYS", vol) == naked
        assert real("SIDEWAYS", vol) == winged

    def test_the_twins_share_the_naked_shapes_short_strikes(self):
        """Why the substitution is clean rather than a different trade: the
        twin targets the SAME delta, so the short legs land on the same
        strikes and only the protection is added."""
        for naked, winged in sel.DEFINED_RISK_TWIN.items():
            assert FAMILY_DELTA_TARGETS[naked] == FAMILY_DELTA_TARGETS[winged]

    def test_the_trending_branches_need_no_twin(self):
        """Both credit spreads already pair every short with a protective
        long, so real-money mode must leave them untouched."""
        for t in ("TRENDING_UP", "TRENDING_DOWN"):
            assert real(t, "LOW_VOL") == paper(t, "LOW_VOL")

    def test_the_reasoning_records_the_substitution(self):
        """A decision record must show the shape was swapped and why, not
        silently report the winged family as if it had been chosen."""
        r = sel.select_strategy("SIDEWAYS", "LOW_VOL", CLOCK, defined_risk_only=True)
        assert "DEFINED-RISK MODE" in r.reasoning
        assert "NEUTRAL_PREMIUM_SELLING" in r.reasoning and "IRON_CONDOR" in r.reasoning
        assert "300s" in r.reasoning

    def test_every_twin_target_is_constructible(self):
        for winged in sel.DEFINED_RISK_TWIN.values():
            assert winged in mtc.SUPPORTED_FAMILIES


class TestNoTradePathsAreIdenticalInBothModes:
    @pytest.mark.parametrize("trend,vol", [
        ("SIDEWAYS", "EXPANSION"), ("TRENDING_UP", "EXPANSION"),
        ("UNKNOWN", "LOW_VOL"), (None, "LOW_VOL"), ("SIDEWAYS", None),
        ("SIDEWAYS", "SOME_NEW_STATE"),
    ])
    def test_a_refusal_stays_a_refusal(self, trend, vol):
        """Defined-risk mode restricts WHAT may be sold. It must not turn a
        no-trade into a trade."""
        assert paper(trend, vol) is None
        assert real(trend, vol) is None


class TestTheRunnerFailsClosed:
    """The mode is DERIVED from the execution mode, never configured on its
    own -- a second switch is a second thing to forget on the day it matters
    most. Anything that is not literally `shadow_mode: true` must land safe."""

    def _runner_with(self, tmp_path, shadow_mode="__omit__"):
        import logging

        from tests.test_options_os_runner import DAY, base_config
        from bujji_options_os_runner import OptionsOSRunner

        cfg = base_config(tmp_path, trend="SIDEWAYS", volatility="LOW_VOL")
        if shadow_mode == "__omit__":
            cfg.pop("shadow_mode", None)
        else:
            cfg["shadow_mode"] = shadow_mode
        r = OptionsOSRunner(config=cfg, as_of_date=DAY, session_id="DR-TEST",
                            logger=logging.getLogger("defined-risk-test"))
        r._startup()
        return r

    def test_paper_mode_permits_naked_shapes(self, tmp_path):
        r = self._runner_with(tmp_path, shadow_mode=True)
        assert r._governor._defined_risk_only is False

    @pytest.mark.parametrize("value", [False, "true", "false", 1, None, "__omit__"])
    def test_anything_other_than_literal_true_selects_defined_risk(self, tmp_path, value):
        """A missing key, a typo, a YAML string, or the real-money switch
        itself -- every one of them must arrive at the safe answer."""
        r = self._runner_with(tmp_path, shadow_mode=value)
        assert r._governor._defined_risk_only is True

    def test_the_mode_is_recorded_in_the_session_summary(self, tmp_path):
        r = self._runner_with(tmp_path, shadow_mode=True)
        assert r._governor_result_summary["defined_risk_only"] is False

    def test_there_is_no_independent_config_flag_to_forget(self):
        """If a `defined_risk_only:` key ever appears in the production config
        it means the derivation was bypassed, which is the failure this
        design exists to prevent."""
        import yaml

        cfg = yaml.safe_load((REPO_ROOT / "config" / "options_os_paper_trading.yaml").read_text())
        assert "defined_risk_only" not in cfg
        assert "defined_risk_only" not in (cfg.get("session") or {})

    def test_the_production_config_is_still_paper(self):
        import yaml

        cfg = yaml.safe_load((REPO_ROOT / "config" / "options_os_paper_trading.yaml").read_text())
        assert cfg["shadow_mode"] is True, "today's campaign must stay in paper mode"


class TestTheGovernorHonoursIt:
    def test_the_governor_passes_the_mode_into_selection(self):
        import inspect

        src = inspect.getsource(
            sel.__class__ if False else
            __import__("bujji.production_runtime.trading_session_governor.session_governor",
                       fromlist=["x"]))
        assert "defined_risk_only=self._defined_risk_only" in src

    def test_the_governor_default_is_explicit(self):
        """Defaulting to paper is correct HERE -- the runner owns the
        fail-closed derivation -- but it must be visible, not implied."""
        import inspect

        from bujji.production_runtime.trading_session_governor.session_governor import (
            TradingSessionGovernor)
        params = inspect.signature(TradingSessionGovernor.__init__).parameters
        assert params["defined_risk_only"].default is False
