"""Tests -- Phase 15E Premium Behaviour Recovery, RUNTIME layer
(ShadowSessionRunner). Reuses the same VaryingFakeBroker pattern as
test_shadow_runtime_observation_memory_recovery.py so real premium
movement actually occurs across cycles."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from bujji.core.enums import OptionType, Side
from bujji.core.models import Candle, OptionContract
from bujji.premium_behaviour.recovery import hydrate_premium_behaviour
from bujji.shadow_runtime.shadow_session_runner import ShadowSessionRunner
from bujji.state_persistence.models import RECOVERY_COMPLETE, RECOVERY_PARTIAL

CE_CONTRACT = OptionContract("NIFTY24450CE", "NIFTY", 24450, OptionType.CE, "2026-08-04", 65)
PE_CONTRACT = OptionContract("NIFTY24450PE", "NIFTY", 24450, OptionType.PE, "2026-08-04", 65)
BASE_NOW = datetime(2026, 8, 4, 9, 15, 0, tzinfo=timezone.utc)


async def no_sleep(_seconds):
    return None


class VaryingPremiumFakeBroker:
    """CE/PE quotes move a fixed amount each cycle -- real, non-flat
    premium movement for the behaviour engine to actually detect."""

    def __init__(self, start_ce=99.0, start_pe=89.0, start_cycle=0):
        self.connect_calls = 0
        self._ce = start_ce
        self._pe = start_pe
        self._cycle = start_cycle

    async def connect(self):
        self.connect_calls += 1

    async def get_quote(self, contract):
        return {"bid": 98.0, "ask": 100.0, "spread": 2.0}

    async def get_spot(self, underlying):
        self._cycle += 1
        return 24400.0 + self._cycle

    async def get_vix(self):
        return {"level": 13.0, "prev_close": 13.5}

    async def get_option_chain(self, underlying, spot, strike_count=5):
        self._ce += 6.0
        self._pe -= 2.0
        return [(24450.0, 12000.0, 15000.0)]

    async def get_futures_quote(self, underlying):
        return {"symbol": "NSE:NIFTY26AUGFUT", "ltp": 24650.0, "volume": 1000.0, "oi": None}

    async def get_recent_candles(self, underlying, minutes, count):
        out = []
        c = 24300.0
        for i in range(20):
            c += 5
            out.append(Candle(timestamp=BASE_NOW, open=c - 5, high=c + 2, low=c - 7, close=c, volume=1000))
        return out


def make_clock(start_n=0):
    state = {"n": start_n}

    def clock():
        state["n"] += 1
        return BASE_NOW + timedelta(seconds=state["n"])

    return clock, state


def make_runner(broker, tmp_path, clock, session_id="PB-TEST", **overrides):
    defaults = dict(
        broker=broker, watchlist=[(CE_CONTRACT, Side.SELL), (PE_CONTRACT, Side.SELL)],
        storage_path=str(tmp_path / "quotes.jsonl"), session_id=session_id, clock=clock,
        max_cycles=2, max_consecutive_failures=2, sleep_seconds=0.0, sleep_fn=no_sleep,
        market_perception_enabled=True,
        market_snapshot_path=str(tmp_path / "market_snapshots.jsonl"),
        intelligence_snapshot_path=str(tmp_path / "intelligence_snapshots.jsonl"),
        intelligence_cycle_enabled=True,
        intelligence_cycle_path=str(tmp_path / "intelligence_cycle.jsonl"),
        premium_behaviour_recovery_enabled=True,
    )
    defaults.update(overrides)
    return ShadowSessionRunner(**defaults)


def _read_jsonl(path):
    with open(path) as f:
        return [json.loads(l) for l in f]


@pytest.mark.asyncio
async def test_clean_startup_no_persisted_premium_history(tmp_path):
    broker = VaryingPremiumFakeBroker()
    clock, _ = make_clock()
    runner = make_runner(broker, tmp_path, clock, max_cycles=2)
    artifact = await runner.start()
    assert artifact.premium_behaviour_recovery_report is not None
    assert artifact.premium_behaviour_recovery_report["status"] == RECOVERY_COMPLETE
    assert artifact.premium_behaviour_recovery_report["events_discovered"] == 0


@pytest.mark.asyncio
async def test_recovery_not_requested_leaves_report_none(tmp_path):
    broker = VaryingPremiumFakeBroker()
    clock, _ = make_clock()
    runner = make_runner(broker, tmp_path, clock, max_cycles=2, premium_behaviour_recovery_enabled=False)
    artifact = await runner.start()
    assert artifact.premium_behaviour_recovery_report is None


@pytest.mark.asyncio
async def test_mid_session_restart_continues_premium_window(tmp_path):
    session_id = "PB-RESTART"
    market_snapshot_path = str(tmp_path / "market_snapshots.jsonl")
    intelligence_cycle_path = str(tmp_path / "intelligence_cycle.jsonl")

    broker1 = VaryingPremiumFakeBroker()
    clock1, clock1_state = make_clock(start_n=0)
    runner1 = make_runner(
        broker1, tmp_path, clock1, session_id=session_id, max_cycles=3,
        market_snapshot_path=market_snapshot_path, intelligence_cycle_path=intelligence_cycle_path,
    )
    await runner1.start()

    broker2 = VaryingPremiumFakeBroker(start_ce=broker1._ce, start_pe=broker1._pe, start_cycle=broker1._cycle)
    clock2, _ = make_clock(start_n=clock1_state["n"] - 2)
    runner2 = make_runner(
        broker2, tmp_path, clock2, session_id=session_id, max_cycles=2,
        market_snapshot_path=market_snapshot_path, intelligence_cycle_path=intelligence_cycle_path,
    )
    artifact2 = await runner2.start()

    assert artifact2.premium_behaviour_recovery_report["status"] == RECOVERY_COMPLETE
    assert artifact2.premium_behaviour_recovery_report["events_replayed"] == 3

    records = _read_jsonl(intelligence_cycle_path)
    assert len(records) == 5
    # Recovery genuinely continued the window: cycle 4's premium_behaviour
    # reading has real direction/acceleration confidence, not a
    # freshly-reset "insufficient history" read (which a broken
    # recovery would silently produce instead).
    assert records[3]["premium_behaviour"]["confidence"] != "NONE"
    assert records[3]["premium_behaviour"]["ce"]["direction"] in ("RISING", "FALLING", "STEADY")


@pytest.mark.asyncio
async def test_restart_with_torn_market_snapshot_line_degrades_safely(tmp_path):
    session_id = "PB-TORN"
    market_snapshot_path = str(tmp_path / "market_snapshots.jsonl")
    intelligence_cycle_path = str(tmp_path / "intelligence_cycle.jsonl")

    broker1 = VaryingPremiumFakeBroker()
    clock1, clock1_state = make_clock(start_n=0)
    runner1 = make_runner(
        broker1, tmp_path, clock1, session_id=session_id, max_cycles=2,
        market_snapshot_path=market_snapshot_path, intelligence_cycle_path=intelligence_cycle_path,
    )
    await runner1.start()

    with open(market_snapshot_path, "a") as f:
        f.write('{"snapshot_version": "1.0", "timestamp": "2026-08-04T09:3')  # torn.

    broker2 = VaryingPremiumFakeBroker(start_ce=broker1._ce, start_pe=broker1._pe, start_cycle=broker1._cycle)
    clock2, _ = make_clock(start_n=clock1_state["n"] - 2)
    runner2 = make_runner(
        broker2, tmp_path, clock2, session_id=session_id, max_cycles=1,
        market_snapshot_path=market_snapshot_path, intelligence_cycle_path=intelligence_cycle_path,
    )
    artifact2 = await runner2.start()
    assert artifact2.premium_behaviour_recovery_report["status"] == RECOVERY_PARTIAL
    assert artifact2.premium_behaviour_recovery_report["events_skipped_malformed"] == 1
    assert artifact2.premium_behaviour_recovery_report["events_replayed"] == 2


def test_premium_behaviour_not_wired_into_any_decision_engine():
    """Documents the deliberate Phase 15E scoping decision: no proven
    downstream consumer was established this phase, so this signal must
    not be imported anywhere near consensus/opportunity/selection yet."""
    import inspect
    from bujji.msi_consensus import engine as consensus_engine
    from bujji.msi_decision_synthesis import engine as synthesis_engine
    for mod in (consensus_engine, synthesis_engine):
        source = inspect.getsource(mod)
        assert "premium_behaviour" not in source
        assert "msi_greeks" not in source
