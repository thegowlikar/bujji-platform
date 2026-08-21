"""Real-capital snapshot provider: mapping, caching, staleness, fail-closed.

All broker interaction is via an injected `fetch`; the real one is a thin
asyncio.run wrapper over the live-certified get_funds(). Assertions are
structural -- field values and exception types, never log prose.
"""
from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from bujji.broker.fyers_funds_capital import (  # noqa: E402
    CapitalRealityUnavailable,
    FyersCapitalSnapshotProvider,
)

_spec = importlib.util.spec_from_file_location(
    "bujji_options_os_runner", REPO_ROOT / "bujji_options_os_runner.py")
runner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(runner)

LOG = logging.getLogger("test")
POLICY = {"daily_loss_limit": 25_000.0, "max_allowed_drawdown": 0.10}
FUNDS = {"account_equity": 312_450.0, "available_funds": 287_000.0,
         "used_margin": 25_450.0, "cash_balance": 200_000.0, "collateral": 0.0}


class _Clock:
    def __call__(self):
        return "2026-08-19T10:00:00+05:30"


class _Mono:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def _provider(fetch, mono=None, **kw):
    return FyersCapitalSnapshotProvider(
        policy=POLICY, clock=_Clock(), fetch=fetch,
        monotonic=mono or _Mono(), **kw)


def test_real_fields_map_from_the_certified_funds_shape():
    snap = _provider(lambda: dict(FUNDS))()
    assert snap.total_capital == 312_450.0
    assert snap.available_capital == 287_000.0
    assert snap.used_margin == 25_450.0
    assert snap.daily_loss_limit == 25_000.0
    assert snap.max_allowed_drawdown == 0.10
    assert (snap.open_risk, snap.reserved_risk, snap.daily_pnl,
            snap.consecutive_losses) == (0.0, 0.0, 0.0, 0)


def test_policy_limits_are_required_not_defaulted():
    with pytest.raises(ValueError):
        FyersCapitalSnapshotProvider(
            policy={"daily_loss_limit": 25_000.0}, clock=_Clock(), fetch=lambda: FUNDS)


def test_never_succeeded_fetch_fails_closed():
    def boom():
        raise ConnectionError("api down")
    with pytest.raises(CapitalRealityUnavailable):
        _provider(boom)()


def test_none_funds_response_is_a_failure_not_a_snapshot():
    with pytest.raises(CapitalRealityUnavailable):
        _provider(lambda: None)()


def test_missing_required_field_is_refused_not_fabricated():
    crippled = dict(FUNDS, account_equity=None)
    with pytest.raises(CapitalRealityUnavailable):
        _provider(lambda: crippled)()


def test_within_refresh_interval_the_cache_serves_without_a_call():
    calls = []
    mono = _Mono()

    def fetch():
        calls.append(1)
        return dict(FUNDS)

    p = _provider(fetch, mono=mono, refresh_interval_seconds=60.0)
    p()
    mono.now += 10
    p()
    assert len(calls) == 1


def test_after_the_interval_a_fresh_fetch_happens():
    calls = []
    mono = _Mono()

    def fetch():
        calls.append(1)
        return dict(FUNDS)

    p = _provider(fetch, mono=mono, refresh_interval_seconds=60.0)
    p()
    mono.now += 61
    p()
    assert len(calls) == 2


def test_failed_refresh_keeps_serving_the_last_real_snapshot():
    state = {"fail": False}
    mono = _Mono()

    def fetch():
        if state["fail"]:
            raise ConnectionError("blip")
        return dict(FUNDS)

    p = _provider(fetch, mono=mono, refresh_interval_seconds=60.0,
                  max_staleness_seconds=900.0)
    p()
    state["fail"] = True
    mono.now += 120
    assert p().total_capital == 312_450.0  # stale but within tolerance


def test_prolonged_staleness_fails_closed():
    state = {"fail": False}
    mono = _Mono()

    def fetch():
        if state["fail"]:
            raise ConnectionError("outage")
        return dict(FUNDS)

    p = _provider(fetch, mono=mono, refresh_interval_seconds=60.0,
                  max_staleness_seconds=900.0)
    p()
    state["fail"] = True
    mono.now += 901
    with pytest.raises(CapitalRealityUnavailable):
        p()


def test_peak_capital_tracks_the_session_maximum_equity():
    readings = [300_000.0, 350_000.0, 320_000.0]
    mono = _Mono()

    def fetch():
        return dict(FUNDS, account_equity=readings.pop(0))

    p = _provider(fetch, mono=mono, refresh_interval_seconds=60.0)
    assert p().peak_capital == 300_000.0
    mono.now += 61
    assert p().peak_capital == 350_000.0
    mono.now += 61
    snap = p()
    assert snap.peak_capital == 350_000.0      # peak held
    assert snap.total_capital == 320_000.0     # current is current


# ---------------------------------------------------------------- runner

def test_runner_defaults_to_static_with_a_warning(caplog):
    with caplog.at_level(logging.WARNING):
        p = runner._make_capital_snapshot_provider({}, clock=_Clock(), log=LOG)
    snap = p()
    assert snap.total_capital == 10_000_000.0  # the legacy fabrication, verbatim
    assert any(r.levelno == logging.WARNING for r in caplog.records)


def test_runner_builds_the_real_provider_for_fyers_funds():
    p = runner._make_capital_snapshot_provider(
        {"source": "fyers_funds", **POLICY}, clock=_Clock(), log=LOG)
    assert isinstance(p, FyersCapitalSnapshotProvider)


def test_runner_requires_declared_limits_for_fyers_funds():
    with pytest.raises(ValueError):
        runner._make_capital_snapshot_provider(
            {"source": "fyers_funds"}, clock=_Clock(), log=LOG)


def test_runner_rejects_unknown_source():
    with pytest.raises(RuntimeError):
        runner._make_capital_snapshot_provider(
            {"source": "real_trust_me"}, clock=_Clock(), log=LOG)
