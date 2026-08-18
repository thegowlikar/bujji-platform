"""The trading unit, its config, and the operational surface systemd needs.

This is the first unit in the project that can place an order, so the
tests here are mostly about what it must REFUSE to do.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

REPO = Path("/opt/bujji/app")
SERVICE = REPO / "deploy/bujji-options-os-trading.service"
TIMER = REPO / "deploy/bujji-options-os-trading.timer"
CONFIG = REPO / "config/options_os_paper_trading.yaml"
_DEPLOYED = SERVICE.exists() and TIMER.exists() and CONFIG.exists()
_REASON = "deployment files present only on the VPS"


def _directives(path: Path) -> str:
    """Unit-file DIRECTIVES only, comments stripped.

    These files deliberately document what they do NOT do ("No Restart=",
    "enabled via the TIMER's [Install]"), so a raw substring search finds
    the prose and reports the opposite of the truth. Structure, not text.
    """
    return "\n".join(
        line for line in path.read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    )


@pytest.mark.skipif(not _DEPLOYED, reason=_REASON)
class TestTradingUnit:
    def test_service_is_a_oneshot_not_a_restarting_daemon(self):
        directives = _directives(SERVICE)
        assert "Type=oneshot" in directives
        assert "Restart=" not in directives, "a bounded daily session must never auto-restart"

    def test_service_has_no_install_section(self):
        """A timer-triggered oneshot is enabled via the TIMER's [Install];
        enabling the service directly would run it at boot."""
        assert "[Install]" not in _directives(SERVICE)

    def test_service_uses_its_own_lock_path(self):
        """Sharing another system's lock would let two trading sessions
        each believe they hold the one-strategy-per-day lock."""
        text = SERVICE.read_text()
        assert "options_os_trading.lock" in text
        assert "daily_intelligence.lock" not in text
        assert "shadow_decision_campaign.lock" not in text

    def test_service_runs_the_canonical_trading_runner(self):
        assert "bujji_options_os_runner.py" in SERVICE.read_text()

    def test_service_passes_date_today_not_a_hardcoded_date(self):
        assert "--date-today" in SERVICE.read_text()

    def test_timer_fires_after_market_open_and_after_the_observers(self):
        text = TIMER.read_text()
        assert "Mon..Fri" in text
        hhmm = next(line for line in text.splitlines()
                    if line.startswith("OnCalendar=")).split()[1]
        hour, minute = (int(part) for part in hhmm.split(":")[:2])
        assert (hour, minute) > (9, 15), f"fires at {hhmm}, at or before the NSE open"
        # Deliberately NOT pinned to an exact minute: the value is a phase
        # offset from the observation units' 5-minute cadence, and pinning
        # the string made a schedule tweak fail a test about market open.
        assert minute % 5 != 0, (
            f"fires at {hhmm}, on the observation units' 5-minute beat -- "
            "two paced processes would burst against one FYERS account together")
        assert "Asia/Kolkata" in text

    def test_timer_owns_the_install_section(self):
        assert "WantedBy=timers.target" in TIMER.read_text()


@pytest.mark.skipif(not _DEPLOYED, reason=_REASON)
class TestPaperOnlyByConstruction:
    def test_config_never_selects_a_live_execution_broker(self):
        """Orders go to PaperBroker. The live FYERS connection is market
        DATA only and is execution-neutered by broker.guard.

        Asserted over PARSED VALUES, not raw text: the file's own comments
        name `disable_live_execution()`, so a substring search matches the
        safety note and fails a config that is in fact safe."""
        cfg = yaml.safe_load(CONFIG.read_text())
        assert cfg.get("shadow_mode") is True
        assert "capital_mode" not in cfg
        providers = cfg["providers"]
        # No provider may name a live EXECUTION broker; live market DATA is fine.
        for name, block in providers.items():
            assert block.get("type") != "fyers_execution", f"{name} selects live execution"

    def test_service_documents_the_paper_boundary(self):
        text = SERVICE.read_text()
        assert "PaperBroker" in text
        assert "disable_live_execution" in text


@pytest.mark.skipif(not _DEPLOYED, reason=_REASON)
class TestConfigIsCoherent:
    def _cfg(self):
        return yaml.safe_load(CONFIG.read_text())

    def test_regime_is_derived_not_human_supplied(self):
        regime = self._cfg()["providers"]["regime"]
        assert regime["type"] == "market_thesis_live"
        assert "trend_regime" not in regime, "a derived regime must not carry a human answer"
        assert "volatility_regime" not in regime

    def test_a_tick_source_is_configured(self):
        """Without one every management cycle is blind and no stop-loss or
        profit-target can fire, however they are configured."""
        assert self._cfg()["providers"]["tick_source"]["type"] != "none"

    def test_management_actually_loops_across_the_session(self):
        mgmt = self._cfg()["position_management"]
        assert mgmt["cycle_interval_seconds"] > 0
        assert mgmt["max_cycles"] > 2, "two passes cannot express an excursion"

    def test_mandatory_exit_is_set_and_not_after_the_close(self):
        exit_cfg = self._cfg()["exit_policy"]
        assert exit_cfg["mandatory_exit_time"] is not None
        assert exit_cfg["mandatory_exit_time"] <= "15:30:00"

    def test_monitoring_does_not_outlast_the_mandatory_exit(self):
        cfg = self._cfg()
        assert cfg["position_management"]["monitor_until"] <= cfg["exit_policy"]["mandatory_exit_time"]


class TestRunnerOperationalSurface:
    """What systemd requires of the entrypoint."""

    def test_date_today_is_accepted_instead_of_an_explicit_date(self):
        from bujji_options_os_runner import parse_args
        args = parse_args(["--date-today"])
        assert args.date_today is True and args.as_of_date is None

    def test_a_date_is_mandatory_one_way_or_the_other(self):
        from bujji_options_os_runner import parse_args
        with pytest.raises(SystemExit):
            parse_args([])

    def test_date_and_date_today_are_mutually_exclusive(self):
        from bujji_options_os_runner import parse_args
        with pytest.raises(SystemExit):
            parse_args(["--date-today", "--as-of-date", "2026-08-14"])

    def test_lock_path_defaults_to_its_own_file(self):
        from bujji_options_os_runner import parse_args
        args = parse_args(["--date-today"])
        assert "options_os_trading.lock" in args.lock_path

    def test_calendar_check_is_on_by_default(self):
        """Skipping the trading-day gate is a manual/testing affordance;
        systemd must never get it implicitly."""
        from bujji_options_os_runner import parse_args
        assert parse_args(["--date-today"]).skip_calendar_check is False
