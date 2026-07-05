"""D1/D2 — explicit Asia/Kolkata time management and clock-drift detection.

Verifies:
  - `now_ist()`/`epoch_to_ist()` are always tz-aware IST, independent of host
    timezone.
  - `ClockGuard` detects an in-session wall-clock jump and reports no drift on
    a normal tick.
  - The Orchestrator gates NEW entries when the clock is untrusted, but never
    blocks managing/exiting an existing position or the EOD square-off.
"""
from datetime import datetime, timezone

import pytest

from bujji.core import clock as clock_module
from bujji.core.clock import IST, ClockGuard, epoch_to_ist, now_ist
from bujji.core.enums import State
from tests.conftest import c
from tests.test_tier1_capital_protection import build_orch, _enter_position, _fast_broker_cfg
from bujji.broker.paper import PaperBroker


# ---------------------------------------------------------------------- #
# now_ist / epoch_to_ist
# ---------------------------------------------------------------------- #
def test_now_ist_is_tz_aware_ist():
    t = now_ist()
    assert t.tzinfo is not None
    assert t.utcoffset().total_seconds() == 5.5 * 3600


def test_epoch_to_ist_known_value():
    # Epoch 0 = 1970-01-01T00:00:00 UTC = 1970-01-01 05:30:00 IST.
    t = epoch_to_ist(0)
    assert t.tzinfo is not None
    assert (t.year, t.month, t.day, t.hour, t.minute) == (1970, 1, 1, 5, 30)


def test_epoch_to_ist_matches_utc_conversion():
    epoch = 1_800_000_000  # Arbitrary fixed instant.
    ist = epoch_to_ist(epoch)
    utc = datetime.fromtimestamp(epoch, tz=timezone.utc)
    assert ist.astimezone(timezone.utc) == utc


# ---------------------------------------------------------------------- #
# ClockGuard
# ---------------------------------------------------------------------- #
def test_clock_guard_first_check_is_baseline():
    guard = ClockGuard(max_drift_seconds=5.0)
    result = guard.check()
    assert result.drifted is False
    assert result.detail == "baseline_established"


def test_clock_guard_detects_no_drift_on_normal_tick(monkeypatch):
    guard = ClockGuard(max_drift_seconds=5.0)
    wall = [1000.0]
    mono = [1000.0]
    monkeypatch.setattr(clock_module.time, "time", lambda: wall[0])
    monkeypatch.setattr(clock_module.time, "monotonic", lambda: mono[0])

    guard.check()  # Baseline.
    wall[0] += 300.0   # 5 minutes elapse normally...
    mono[0] += 300.0   # ...and the monotonic clock agrees.
    result = guard.check()
    assert result.drifted is False


def test_clock_guard_detects_forward_jump(monkeypatch):
    guard = ClockGuard(max_drift_seconds=5.0)
    wall = [1000.0]
    mono = [1000.0]
    monkeypatch.setattr(clock_module.time, "time", lambda: wall[0])
    monkeypatch.setattr(clock_module.time, "monotonic", lambda: mono[0])

    guard.check()  # Baseline.
    wall[0] += 300.0 + 60.0   # Wall clock jumped an extra 60s (NTP correction).
    mono[0] += 300.0          # Monotonic clock only saw the real 300s elapse.
    result = guard.check()
    assert result.drifted is True
    assert result.delta_seconds > 0


def test_clock_guard_detects_backward_jump(monkeypatch):
    guard = ClockGuard(max_drift_seconds=5.0)
    wall = [1000.0]
    mono = [1000.0]
    monkeypatch.setattr(clock_module.time, "time", lambda: wall[0])
    monkeypatch.setattr(clock_module.time, "monotonic", lambda: mono[0])

    guard.check()
    wall[0] += 300.0 - 60.0   # Wall clock stepped backward relative to real time.
    mono[0] += 300.0
    result = guard.check()
    assert result.drifted is True
    assert result.delta_seconds < 0


def test_clock_guard_self_clears_after_transient_jump(monkeypatch):
    """A single jump is only flagged on the tick it occurred; the next normal
    tick reports no drift again (matches the orchestrator's self-clear via
    set_clock_trust being called with the latest result every tick)."""
    guard = ClockGuard(max_drift_seconds=5.0)
    wall = [1000.0]
    mono = [1000.0]
    monkeypatch.setattr(clock_module.time, "time", lambda: wall[0])
    monkeypatch.setattr(clock_module.time, "monotonic", lambda: mono[0])

    guard.check()
    wall[0] += 360.0
    mono[0] += 300.0
    assert guard.check().drifted is True

    wall[0] += 300.0  # Normal tick following the jump.
    mono[0] += 300.0
    assert guard.check().drifted is False


# ---------------------------------------------------------------------- #
# Orchestrator: clock-trust gating
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_untrusted_clock_blocks_new_entry(config, logger, tmp_path):
    _fast_broker_cfg(config)
    broker = PaperBroker()
    orch, status = build_orch(config, logger, broker, tmp_path)
    await orch.startup()

    orch.set_clock_trust(False, "simulated drift")
    await orch.on_candle(c(9, 15, 22000, 22010, 21990, 22005, vol=1000))
    await orch.on_candle(c(9, 20, 22006, 22080, 22005, 22079, vol=1000))

    assert orch.state is State.READY       # Not CONFIRMED/IN_POSITION.
    assert not orch.has_open_position()
    assert status.clock_trusted is False


@pytest.mark.asyncio
async def test_clock_distrust_does_not_affect_existing_position(config, logger, tmp_path):
    _fast_broker_cfg(config)
    broker = PaperBroker()
    orch, status = build_orch(config, logger, broker, tmp_path)
    await orch.startup()
    await _enter_position(orch)
    assert orch.has_open_position()

    orch.set_clock_trust(False, "simulated drift")
    # A candle that would trigger an exit (loses VWAP) must still be honored.
    await orch.on_candle(c(9, 25, 22078, 22079, 21950, 21951, vol=1000))

    assert orch.state is State.DONE_FOR_DAY
    assert not orch.has_open_position()    # Exit was NOT blocked by distrust.


@pytest.mark.asyncio
async def test_clock_distrust_does_not_block_eod_square_off(config, logger, tmp_path):
    _fast_broker_cfg(config)
    broker = PaperBroker()
    orch, status = build_orch(config, logger, broker, tmp_path)
    await orch.startup()
    await _enter_position(orch)

    orch.set_clock_trust(False, "simulated drift")
    ok = await orch.square_off("eod_hard_exit")
    assert ok is True
    assert not orch.has_open_position()


@pytest.mark.asyncio
async def test_clock_trust_restored_resumes_entries(config, logger, tmp_path):
    _fast_broker_cfg(config)
    broker = PaperBroker()
    orch, status = build_orch(config, logger, broker, tmp_path)
    await orch.startup()

    orch.set_clock_trust(False, "simulated drift")
    await orch.on_candle(c(9, 15, 22000, 22010, 21990, 22005, vol=1000))
    orch.set_clock_trust(True)  # Drift was transient; next tick restores trust.
    await orch.on_candle(c(9, 20, 22006, 22080, 22005, 22079, vol=1000))

    assert orch.state is State.IN_POSITION
    assert orch.has_open_position()
    assert status.clock_trusted is True
