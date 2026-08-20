"""The market-hours gate must precede every market touch, not just the broker.

OBSERVED LIVE 2026-08-20. The gate was called from `_pre_market_check`, which
runs AFTER `_startup` -- but `_startup` builds the regime provider, and that
runs the warm-up: 16 spot polls at 30s intervals. With the timer firing at
09:14 the trading session began polling at 09:14:01, so its first two warm-up
samples read a PRE-OPEN book and fed the evidence window the stability gate
then judges.

The capture units were unaffected -- their first rows landed at 09:15:00.866,
exactly at the open. This was the trading unit alone, sampling a market that
had not opened.
"""
from __future__ import annotations

import datetime
import logging
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from bujji_options_os_runner import ConfigurationError, OptionsOSRunner

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))


def _config_without_bypass(tmp_path):
    from tests.test_options_os_runner import base_config

    cfg = base_config(tmp_path, trend="SIDEWAYS", volatility="LOW_VOL")
    cfg["session"].pop("skip_market_hours_check", None)
    cfg["session"]["market_open"] = "09:15:00"
    return cfg


def _runner(tmp_path, at):
    from tests.test_options_os_runner import DAY

    return OptionsOSRunner(
        config=_config_without_bypass(tmp_path), as_of_date=DAY,
        session_id="GATE-ORDER", logger=logging.getLogger("gate-order-test"),
        clock=lambda: at)


class TestNothingTouchesTheMarketBeforeTheGate:
    def test_a_far_from_open_start_refuses_before_building_a_broker(self, tmp_path):
        """The behavioural proof. A 05:00 start must not get as far as
        constructing a broker, resolving the instrument master, or running a
        warm-up poll."""
        r = _runner(tmp_path, datetime.datetime(2026, 5, 25, 5, 0, tzinfo=IST))
        with pytest.raises(ConfigurationError) as exc:
            r._startup()
        assert "REFUSING TO START" in str(exc.value)
        assert r._broker is None, "a broker was constructed before the gate ran"
        assert r._regime_provider is None, "a regime provider (warm-up) ran before the gate"
        assert r._root is None

    def test_a_start_inside_market_hours_proceeds(self, tmp_path):
        r = _runner(tmp_path, datetime.datetime(2026, 5, 25, 10, 0, tzinfo=IST))
        r._startup()
        assert r._broker is not None


class TestTheOrderingIsPinnedInSource:
    """A behavioural test can only prove the paths it exercises. This pins the
    ordering itself, so a future edit that moves provider construction above
    the gate fails here rather than in a live session."""

    def _lines(self):
        source = (REPO_ROOT / "bujji_options_os_runner.py").read_text().splitlines()
        found = {}
        for i, line in enumerate(source, 1):
            for key, needle in (
                ("gate", "self._await_market_open()"),
                ("lot_size", "_resolve_exchange_lot_size(session_cfg)"),
                ("broker", "self._broker = _production_paper_broker()"),
                ("regime", "self._regime_provider = self._build_market_thesis_regime_provider()"),
            ):
                if needle in line and key not in found:
                    found[key] = i
        return found

    def test_the_gate_precedes_the_instrument_master_read(self):
        lines = self._lines()
        assert lines["gate"] < lines["lot_size"]

    def test_the_gate_precedes_broker_construction(self):
        lines = self._lines()
        assert lines["gate"] < lines["broker"]

    def test_the_gate_precedes_the_regime_warmup(self):
        """The specific ordering that failed live: the warm-up polls spot 16
        times, and it must not start before the market does."""
        lines = self._lines()
        assert lines["gate"] < lines["regime"]

    def test_the_second_gate_call_in_pre_market_check_is_kept(self):
        """Both methods do broker work. Once the first has waited the second
        is a no-op, so a second net costs nothing and protects any path that
        reaches _pre_market_check without coming through _startup."""
        source = (REPO_ROOT / "bujji_options_os_runner.py").read_text()
        assert source.count("self._await_market_open()") == 2

    def test_the_session_config_is_available_to_the_gate(self):
        """The gate reads self._session_cfg for market_open and the bypass;
        it used to be assigned only after provider construction."""
        source = (REPO_ROOT / "bujji_options_os_runner.py").read_text().splitlines()
        first_assign = next(i for i, l in enumerate(source, 1)
                            if "self._session_cfg = session_cfg" in l)
        gate = next(i for i, l in enumerate(source, 1)
                    if "self._await_market_open()" in l)
        assert first_assign < gate
