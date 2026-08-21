"""The order-placing unit's OWN market-hours gate.

Bujji goes live WITH the market (operator directive 2026-08-19): the trading
unit's timer fires pre-open and this gate holds until 09:15:00, so the first
decision-grade observation lands with the first tick instead of 7.5 minutes
later. Moving the fire earlier is only safe because the gate below exists --
before it, the timer was the sole protection against this unit connecting a
broker and pulling a live chain against a closed-market book. These tests pin
that protection, and the phase-offset that replaced the late start.
"""
from __future__ import annotations

import datetime
import importlib.util
import pathlib
import logging
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

_spec = importlib.util.spec_from_file_location(
    "bujji_options_os_runner_gate", REPO_ROOT / "bujji_options_os_runner.py")
runner_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(runner_mod)

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
LOG = logging.getLogger("market-hours-gate-test")


class _Gatee:
    """Minimal stand-in: the gate touches only these three attributes, so it
    is exercised as the unbound method rather than behind a full runner
    construction (which would need a broker, a chain and a journal)."""

    def __init__(self, now, session_cfg, step_seconds=0.0):
        self._now = now
        self._step = datetime.timedelta(seconds=step_seconds)
        self._session_cfg = session_cfg
        self._logger = LOG
        self.slept = []

    def _clock(self):
        return self._now

    def advance(self, seconds):
        self._now += datetime.timedelta(seconds=seconds)


def _run_gate(g, monkeypatch, *, advance_per_sleep=None):
    """Drive the gate with sleep replaced by clock advancement, so a real
    wait is proven without the test actually sleeping."""
    import time as _time

    def fake_sleep(seconds):
        g.slept.append(seconds)
        g.advance(advance_per_sleep if advance_per_sleep is not None else seconds)

    monkeypatch.setattr(_time, "sleep", fake_sleep)
    return runner_mod.OptionsOSRunner._await_market_open(g)


CONT = {"observe_until": "15:30"}


def _cfg(**over):
    cfg = {"market_open": "09:15:00", "continuous": dict(CONT)}
    cfg.update(over)
    return cfg


def test_pre_open_start_waits_and_proceeds_at_the_open(monkeypatch):
    """A 09:14 fire does not refuse and does not run early -- it waits."""
    g = _Gatee(datetime.datetime(2026, 8, 20, 9, 14, 0, tzinfo=IST), _cfg())
    _run_gate(g, monkeypatch)
    assert g.slept, "gate returned without waiting for the open"
    assert g._clock().time() >= datetime.time(9, 15, 0)


def test_gate_does_not_overshoot_the_open_instant(monkeypatch):
    """First sample lands WITH the first tick, not a minute into it."""
    g = _Gatee(datetime.datetime(2026, 8, 20, 9, 14, 30, tzinfo=IST), _cfg())
    _run_gate(g, monkeypatch)
    assert g._clock().time() < datetime.time(9, 15, 5)


def test_at_open_proceeds_without_waiting(monkeypatch):
    g = _Gatee(datetime.datetime(2026, 8, 20, 9, 15, 1, tzinfo=IST), _cfg())
    _run_gate(g, monkeypatch)
    assert g.slept == []


def test_far_from_open_refuses_rather_than_hanging(monkeypatch):
    """A stray 05:00 fire must REFUSE. This is the protection the timer used
    to provide alone; it now lives in the process that places the orders."""
    g = _Gatee(datetime.datetime(2026, 8, 20, 5, 0, 0, tzinfo=IST), _cfg())
    with pytest.raises(runner_mod.ConfigurationError) as exc:
        _run_gate(g, monkeypatch)
    assert "REFUSING TO START" in str(exc.value)
    assert g.slept == [], "refusal must not sleep at all"


def test_after_session_end_refuses(monkeypatch):
    """A reboot at 21:00 must not open a session on a dead book."""
    g = _Gatee(datetime.datetime(2026, 8, 20, 21, 0, 0, tzinfo=IST), _cfg())
    with pytest.raises(runner_mod.ConfigurationError) as exc:
        _run_gate(g, monkeypatch)
    assert "past this session's configured end" in str(exc.value)


def test_session_end_derives_from_single_shot_config_when_not_continuous(monkeypatch):
    """No fourth market-close constant: the bound comes from the session's own
    configured end. Single-shot sessions use management.monitor_until."""
    cfg = {"market_open": "09:15:00", "management": {"monitor_until": "15:15:00"}}
    late = _Gatee(datetime.datetime(2026, 8, 20, 15, 20, 0, tzinfo=IST), cfg)
    with pytest.raises(runner_mod.ConfigurationError):
        _run_gate(late, monkeypatch)
    ok = _Gatee(datetime.datetime(2026, 8, 20, 15, 10, 0, tzinfo=IST), cfg)
    _run_gate(ok, monkeypatch)  # inside the window: proceeds


def test_a_continuous_session_may_start_after_the_single_shot_bound(monkeypatch):
    """The continuous day runs to 15:30; 15:20 is late but legitimate for it."""
    g = _Gatee(datetime.datetime(2026, 8, 20, 15, 20, 0, tzinfo=IST), _cfg())
    _run_gate(g, monkeypatch)
    assert g.slept == []


def test_malformed_open_is_a_configuration_error_not_a_crash(monkeypatch):
    g = _Gatee(datetime.datetime(2026, 8, 20, 9, 14, 0, tzinfo=IST), _cfg(market_open="09:99"))
    with pytest.raises(runner_mod.ConfigurationError):
        _run_gate(g, monkeypatch)


def test_production_config_wakes_bujji_at_the_open():
    """The shipped config must actually carry the directive -- a gate nobody
    configures is a gate nobody has."""
    import yaml

    cfg = yaml.safe_load((REPO_ROOT / "config" / "options_os_paper_trading.yaml").read_text())
    assert cfg["session"]["market_open"] == "09:15:00"
    # And the burst separation must live on the cadence, not the start time.
    assert cfg["session"]["continuous"]["decision_phase_offset_seconds"] == 150


def test_open_offset_gives_this_unit_its_own_slot(monkeypatch):
    """Three units wake at 09:14 and release at the open. This one is the
    heaviest starter (broker connect + full chain pull), so it releases after
    the capture scripts' +0/+2/+5s slots -- inside the opening seconds, but
    never stacked on them against the shared 10/s FYERS ceiling."""
    g = _Gatee(datetime.datetime(2026, 8, 20, 9, 14, 0, tzinfo=IST),
               _cfg(market_open_offset_seconds=8))
    _run_gate(g, monkeypatch)
    assert g._clock().time() >= datetime.time(9, 15, 8)
    assert g._clock().time() < datetime.time(9, 15, 15), "still the opening seconds"


def test_production_config_carries_a_distinct_startup_slot():
    """The offset must differ from every capture script's slot, or the
    staggering is decorative."""
    import yaml

    cfg = yaml.safe_load((REPO_ROOT / "config" / "options_os_paper_trading.yaml").read_text())
    offset = cfg["session"]["market_open_offset_seconds"]
    assert offset not in (0, 2, 5), "collides with a capture script's open slot"
    assert 0 < offset <= 30, "must stay inside the opening seconds"


class TestTheBypassStaysOutOfProduction:
    """The gate has an explicit bypass for replay and unit tests. A bypass
    nobody guards is a hole: these tests keep it out of anything that runs
    unattended."""

    def test_bypass_is_never_the_default(self, monkeypatch):
        g = _Gatee(datetime.datetime(2026, 8, 20, 5, 0, 0, tzinfo=IST), _cfg())
        with pytest.raises(runner_mod.ConfigurationError):
            _run_gate(g, monkeypatch)  # no flag -> still refuses

    def test_bypass_when_set_skips_the_gate(self, monkeypatch):
        g = _Gatee(datetime.datetime(2026, 8, 20, 5, 0, 0, tzinfo=IST),
                   _cfg(skip_market_hours_check=True))
        _run_gate(g, monkeypatch)
        assert g.slept == []

    def test_no_installed_systemd_unit_passes_the_bypass(self):
        units = list(pathlib.Path("/etc/systemd/system").glob("bujji-*.service"))
        if not units:
            pytest.skip("units are not installed on this host")
        offenders = [u.name for u in units if "--skip-market-hours-check" in u.read_text()]
        assert offenders == [], f"unattended units bypassing the market-hours gate: {offenders}"

    def test_no_repo_unit_file_passes_the_bypass(self):
        repo_units = list((REPO_ROOT / "deploy").rglob("*.service")) + \
            list(REPO_ROOT.glob("systemd/*.service"))
        offenders = [u.name for u in repo_units if "--skip-market-hours-check" in u.read_text()]
        assert offenders == [], f"repo unit files bypassing the market-hours gate: {offenders}"

    def test_the_production_config_does_not_set_the_bypass(self):
        import yaml

        cfg = yaml.safe_load((REPO_ROOT / "config" / "options_os_paper_trading.yaml").read_text())
        assert not cfg["session"].get("skip_market_hours_check")
