"""Tests -- Shadow Runtime, Phase-5 (ShadowSessionRunner / ShadowSessionArtifact).

Fake broker only. No live broker calls, no credentials, no .env
loading anywhere in this file.
"""
from __future__ import annotations

import dataclasses
from datetime import datetime, timezone

import pytest

from bujji.core.enums import OptionType, Side
from bujji.core.models import OptionContract
from bujji.shadow_runtime.shadow_session_artifact import ShadowSessionArtifact
from bujji.shadow_runtime.shadow_session_runner import ShadowRuntimeStartupError, ShadowSessionRunner

FIXED_NOW = datetime(2026, 8, 4, 9, 15, 0, tzinfo=timezone.utc)
CE_CONTRACT = OptionContract("NIFTY25000CE", "NIFTY", 25000, OptionType.CE, "2026-08-06", 75)
PE_CONTRACT = OptionContract("NIFTY25000PE", "NIFTY", 25000, OptionType.PE, "2026-08-06", 75)


def clock():
    return FIXED_NOW


async def no_sleep(_seconds):
    return None


class FakeBroker:
    """Configurable fake -- no real network, no credentials."""

    def __init__(self, quote_response=None, connect_fails=False, connect_fail_count=0, quote_fails_after=None):
        self._quote_response = quote_response or {"bid": 98.0, "ask": 100.0, "spread": 2.0}
        self.connect_calls = 0
        self._connect_fails = connect_fails
        self._connect_fail_count = connect_fail_count
        self._quote_fails_after = quote_fails_after
        self.get_quote_calls = 0
        self.order_methods_called = []

    async def connect(self):
        self.connect_calls += 1
        if self._connect_fails:
            raise RuntimeError("simulated connect failure")
        if self._connect_fail_count and self.connect_calls <= self._connect_fail_count:
            raise RuntimeError("simulated transient connect failure")

    async def get_quote(self, contract):
        self.get_quote_calls += 1
        if self._quote_fails_after is not None and self.get_quote_calls > self._quote_fails_after:
            return None
        return self._quote_response

    # Deliberately present, to prove the runner never calls them.
    async def place_order(self, *a, **k):
        self.order_methods_called.append("place_order")
        raise AssertionError("place_order must never be called by ShadowSessionRunner")

    async def modify_order(self, *a, **k):
        self.order_methods_called.append("modify_order")
        raise AssertionError("modify_order must never be called")

    async def cancel_order(self, *a, **k):
        self.order_methods_called.append("cancel_order")
        raise AssertionError("cancel_order must never be called")

    async def get_open_positions(self, *a, **k):
        self.order_methods_called.append("get_open_positions")
        raise AssertionError("get_open_positions must never be called")


def make_runner(broker, tmp_path, **overrides):
    defaults = dict(
        broker=broker, watchlist=[(CE_CONTRACT, Side.SELL), (PE_CONTRACT, Side.SELL)],
        storage_path=tmp_path / "quotes.jsonl", session_id="SHADOW-TEST-1", clock=clock,
        max_cycles=3, max_consecutive_failures=2, sleep_seconds=0.0, sleep_fn=no_sleep,
    )
    defaults.update(overrides)
    return ShadowSessionRunner(**defaults)


# 1. Construction without broker
def test_construction_without_live_broker(tmp_path):
    # A bare object with no real connect()/get_quote() implementation --
    # construction itself must never attempt to reach it.
    class Unimplemented:
        pass

    runner = make_runner(Unimplemented(), tmp_path)
    assert runner is not None  # no exception during construction alone


# 2. Broker unavailable startup failure
@pytest.mark.asyncio
async def test_broker_unavailable_fails_closed_at_startup(tmp_path):
    broker = FakeBroker(connect_fails=True)
    runner = make_runner(broker, tmp_path)
    artifact = await runner.start()

    assert artifact.observations_count == 0  # loop never entered -- no partial session
    assert any("startup_failed" in e for e in artifact.errors)


# 3. Successful fake broker session
@pytest.mark.asyncio
async def test_successful_session_against_fake_broker(tmp_path):
    broker = FakeBroker()
    runner = make_runner(broker, tmp_path)
    artifact = await runner.start()

    assert isinstance(artifact, ShadowSessionArtifact)
    assert artifact.observations_count == 6  # 2 contracts x 3 cycles
    assert artifact.errors == ()


# 4. Quote observations persisted
@pytest.mark.asyncio
async def test_quote_observations_persisted_to_store(tmp_path):
    broker = FakeBroker()
    storage_path = tmp_path / "quotes.jsonl"
    runner = make_runner(broker, tmp_path, storage_path=storage_path)
    await runner.start()

    from bujji.execution_reality.quote_observation_store import QuoteObservationStore
    store = QuoteObservationStore(storage_path)
    rows = store.read_all()
    assert len(rows) == 6
    assert rows[0]["normalized_result"]["data_quality"] == "LIVE_QUOTE"
    assert rows[0]["normalized_result"]["bid"] == 98.0


# 5. Final artifact creation
@pytest.mark.asyncio
async def test_final_artifact_has_correct_summary(tmp_path):
    broker = FakeBroker()
    runner = make_runner(broker, tmp_path)
    artifact = await runner.start()

    assert artifact.session_id == "SHADOW-TEST-1"
    assert artifact.data_quality_summary == {"LIVE_QUOTE": 6}
    assert artifact.liquidity_summary is None  # no monitoring_pair_roles configured
    assert artifact.runtime_health["heartbeats"] == 3


# 6. Graceful shutdown after exception
@pytest.mark.asyncio
async def test_graceful_shutdown_after_mid_session_exception(tmp_path):
    # MarketQuoteAdapter.fetch() already catches a raised broker exception
    # internally (returns UNAVAILABLE, never propagates) -- to genuinely
    # exercise the RUNNER's own top-level exception handling, this must
    # trigger a failure the adapter's own try/except does NOT cover: its
    # try/except wraps only the broker call itself, not the subsequent
    # `raw.get("bid")` parsing -- so a malformed (non-dict, non-None)
    # response escapes past it, exactly reproducing a genuine unexpected
    # mid-session failure.
    class MalformedResponseBroker(FakeBroker):
        async def get_quote(self, contract):
            self.get_quote_calls += 1
            if self.get_quote_calls == 2:
                return 12345  # malformed: not a dict, not None -- .get() will raise
            return self._quote_response

    broker = MalformedResponseBroker()
    runner = make_runner(broker, tmp_path)
    artifact = await runner.start()

    # start() never raises -- the exception is caught, recorded, and a real
    # artifact is still produced via try/finally.
    assert isinstance(artifact, ShadowSessionArtifact)
    assert any("unexpected_failure" in e for e in artifact.errors)


# 7. Bounded reconnect
@pytest.mark.asyncio
async def test_bounded_reconnect_never_hangs():
    # A broker whose connect() always fails after the initial smoke-test
    # connect -- reconnect attempts must be bounded, session must still end.
    import asyncio

    class AlwaysFailingReconnectBroker(FakeBroker):
        async def get_quote(self, contract):
            self.get_quote_calls += 1
            if self.get_quote_calls == 1:
                return self._quote_response  # smoke test (the very first fetch) must succeed
            return None  # every subsequent observation UNAVAILABLE -> drives consecutive_failures up

        async def connect(self):
            self.connect_calls += 1
            if self.connect_calls == 1:
                return  # initial connect (smoke test) succeeds
            raise RuntimeError("reconnect always fails")

    broker = AlwaysFailingReconnectBroker(quote_response={"bid": 98.0, "ask": 100.0, "spread": 2.0})

    async def _run():
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            runner = ShadowSessionRunner(
                broker=broker, watchlist=[(CE_CONTRACT, Side.SELL)], storage_path=f"{d}/q.jsonl",
                session_id="S", clock=clock, max_cycles=10, max_consecutive_failures=2,
                sleep_seconds=0.0, sleep_fn=no_sleep,
            )
            return await runner.start()

    artifact = await asyncio.wait_for(_run(), timeout=5.0)  # must terminate, never hang
    assert any("reconnect_failed" in e for e in artifact.errors)
    assert artifact.observations_count < 10  # stopped early, did not run all 10 cycles


# 8. No execution API calls
@pytest.mark.asyncio
async def test_no_execution_api_methods_ever_called(tmp_path):
    broker = FakeBroker()
    runner = make_runner(broker, tmp_path)
    await runner.start()
    assert broker.order_methods_called == []


def test_no_execution_api_calls_in_source():
    import subprocess
    result = subprocess.run(
        ["grep", "-nE", r"\.(place_order|modify_order|cancel_order|get_open_positions|get_positions|get_margin|get_funds)\(",
         "bujji/shadow_runtime/shadow_session_runner.py"],
        cwd="/opt/bujji/app", capture_output=True, text=True,
    )
    assert result.stdout.strip() == "", f"forbidden broker call found: {result.stdout}"


# 9. No strategy imports
def test_no_protected_imports_in_shadow_runtime():
    import subprocess
    result = subprocess.run(
        ["grep", "-rnE",
         r"^\s*(from|import)\s+(bujji\.)?(msi_trade_construction|trading_session_governor|"
         r"trading_brain|shadow_observatory|production_runtime|runtime_execution)\b",
         "bujji/shadow_runtime/"],
        cwd="/opt/bujji/app", capture_output=True, text=True,
    )
    assert result.stdout.strip() == "", f"forbidden import found: {result.stdout}"


def test_no_protected_module_references_shadow_runtime():
    import subprocess
    result = subprocess.run(
        ["grep", "-rl", "shadow_runtime",
         "bujji/production_runtime/", "bujji/trading_session_governor/", "bujji/trading_brain/",
         "bujji/msi_trade_construction/", "bujji/shadow_observatory/", "bujji/broker/",
         "bujji/execution_reality/", "bujji/execution_integration/", "bujji/integration/"],
        cwd="/opt/bujji/app", capture_output=True, text=True,
    )
    assert result.stdout.strip() == "", f"unexpected references found: {result.stdout}"


# 10. Frozen artifact immutability
def test_artifact_is_frozen():
    artifact = ShadowSessionArtifact(
        session_id="S", start_time="t0", end_time="t1", observations_count=0,
        data_quality_summary={}, liquidity_summary=None, errors=(), runtime_health={},
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        artifact.session_id = "changed"


# 11. No decision fields
def test_artifact_has_no_decision_or_trade_or_pnl_fields():
    field_names = set(ShadowSessionArtifact.__dataclass_fields__.keys())
    forbidden = {
        "allowed", "approved", "blocked", "decision", "trade", "trade_permission",
        "entry_permission", "risk", "signal", "recommendation", "pnl", "profit", "loss",
        "execution_result", "order",
    }
    assert not (field_names & forbidden), f"forbidden fields found: {field_names & forbidden}"
    assert field_names == {
        "session_id", "start_time", "end_time", "observations_count",
        "data_quality_summary", "liquidity_summary", "errors", "runtime_health",
    }
