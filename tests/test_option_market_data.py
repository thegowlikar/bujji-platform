"""Tests -- Phase 20.26 Live Option Market Data Acquisition.
Zero real network access, zero real broker credentials anywhere in
this file -- a stub `Broker` implementation stands in for FyersBroker."""
from __future__ import annotations

import ast
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from bujji.broker.base import Broker
from bujji.broker.option_market_data import fetch_option_market_data_for_cycle
from bujji.core.enums import Direction, OptionType
from bujji.core.models import OptionContract


class _StubBroker(Broker):
    """Minimal stub implementing only what `Broker` requires + what
    this adapter calls. Every response is a plain, caller-controlled
    real-shaped value -- never wired to any live network call."""

    name = "stub"

    def __init__(self, *, quote_missing=False, ltp_missing=False, chain_missing=False, raise_on_resolve=False):
        self._quote_missing = quote_missing
        self._ltp_missing = ltp_missing
        self._chain_missing = chain_missing
        self._raise_on_resolve = raise_on_resolve

    async def connect(self) -> None:
        pass

    async def get_spot(self, underlying: str) -> float:
        return 24500.0

    async def get_recent_candles(self, underlying, minutes, count):
        return []

    async def resolve_atm_contract(self, underlying, spot, direction, strike_interval, lot_size) -> OptionContract:
        if self._raise_on_resolve:
            raise RuntimeError("simulated resolution failure")
        opt_type = OptionType.PE if direction is Direction.BULLISH else OptionType.CE
        return OptionContract(
            symbol=f"NSE:NIFTY24AUG24500{opt_type.value}", underlying=underlying, strike=24500,
            option_type=opt_type, expiry=(datetime.now() + timedelta(days=7)).isoformat(), lot_size=lot_size,
        )

    async def get_ltp(self, contract: OptionContract) -> float:
        if self._ltp_missing:
            return None
        return 82.5 if contract.option_type == OptionType.CE else 68.0

    async def place_order(self, request):
        raise NotImplementedError

    async def get_order(self, client_order_id):
        raise NotImplementedError

    async def cancel_order(self, client_order_id):
        raise NotImplementedError

    async def get_open_positions(self):
        return []

    async def get_quote(self, contract: OptionContract):
        if self._quote_missing:
            return None
        if contract.option_type == OptionType.CE:
            return {"bid": 82.4, "ask": 82.6, "spread": 0.2}
        return {"bid": 68.0, "ask": 68.05, "spread": 0.05}

    async def get_option_chain(self, underlying, spot, strike_count=5):
        if self._chain_missing:
            return None
        return [(24700.0, 1_000_000.0, 400_000.0), (24300.0, 400_000.0, 900_000.0)]


# --------------------------------------------------------------------- #
# 1. Complete option market data supplied
# --------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_complete_data_produces_real_option_market_data():
    broker = _StubBroker()
    result = await fetch_option_market_data_for_cycle(broker, "NIFTY", 24500.0, now=datetime.now())
    assert result is not None
    assert result.spot == 24500.0
    assert result.strike == 24500.0
    assert result.ce_premium == 82.5
    assert result.pe_premium == 68.0
    assert result.ce_bid == 82.4 and result.ce_ask == 82.6
    assert result.strikes == ((24700.0, 1_000_000.0, 400_000.0), (24300.0, 400_000.0, 900_000.0))
    assert result.t_years > 0


# --------------------------------------------------------------------- #
# 2. Partial option data missing -> None, never fabricated
# --------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_missing_quote_returns_none_not_fabricated():
    broker = _StubBroker(quote_missing=True)
    result = await fetch_option_market_data_for_cycle(broker, "NIFTY", 24500.0, now=datetime.now())
    assert result is None


@pytest.mark.asyncio
async def test_missing_ltp_returns_none_not_fabricated():
    broker = _StubBroker(ltp_missing=True)
    result = await fetch_option_market_data_for_cycle(broker, "NIFTY", 24500.0, now=datetime.now())
    assert result is None


@pytest.mark.asyncio
async def test_missing_option_chain_returns_none_not_fabricated():
    broker = _StubBroker(chain_missing=True)
    result = await fetch_option_market_data_for_cycle(broker, "NIFTY", 24500.0, now=datetime.now())
    assert result is None


# --------------------------------------------------------------------- #
# 3. Broker data unavailable -> shadow cycle continues safely
# --------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_broker_resolution_failure_returns_none_no_crash():
    broker = _StubBroker(raise_on_resolve=True)
    result = await fetch_option_market_data_for_cycle(broker, "NIFTY", 24500.0, now=datetime.now())
    assert result is None


@pytest.mark.asyncio
async def test_process_cycle_continues_safely_when_option_data_unavailable():
    from bujji.core.models import Candle
    from bujji.intelligence.context import EXECUTION_MODE_HISTORICAL_REPLAY
    from bujji.live_shadow_runner.models import SHADOW_RUN_OPEN, ShadowRunState
    from bujji.live_shadow_runner.runner import process_cycle
    from bujji.shadow_decision_runtime import ShadowDecisionLog
    from bujji.strategy_intelligence import StrategyEvidence

    broker = _StubBroker(raise_on_resolve=True)
    option_data = await fetch_option_market_data_for_cycle(broker, "NIFTY", 24500.0, now=datetime.now())
    assert option_data is None

    base = datetime.fromisoformat("2026-08-13T09:15:00")
    candles = [
        Candle(timestamp=base + timedelta(minutes=i), open=24500.0 + i, high=24505.0 + i,
               low=24495.0 + i, close=24500.0 + i, volume=1000)
        for i in range(10)
    ]
    state = ShadowRunState(session_date="2026-08-13", state=SHADOW_RUN_OPEN, cycles_completed=0,
                             last_cycle_timestamp=None, errors=())
    log = ShadowDecisionLog()
    evidence = StrategyEvidence(
        strategy_name="TrendFollowing", sample_size=11278, win_rate=0.665, profit_factor=2.30,
        gross_expectancy=949.0, net_expectancy=517.0, train_expectancy=512.0,
        validation_expectancy=481.0, out_of_sample_expectancy=573.0,
    )
    new_state, observations = process_cycle(
        state, "2026-08-13T09:15:00", candles, current_vix=13.5, trailing_vix=[13.0, 13.2, 13.4],
        strategies=[(evidence, ("RANGE",), ())], campaign_log=log,
        execution_mode=EXECUTION_MODE_HISTORICAL_REPLAY, option_market_data=option_data,
    )
    assert new_state.errors == ()
    assert len(observations) == 1


# --------------------------------------------------------------------- #
# Safety: read-only broker calls only
# --------------------------------------------------------------------- #

def test_no_order_placement_or_write_calls_in_adapter():
    path = Path(__file__).resolve().parent.parent / "bujji" / "broker" / "option_market_data.py"
    forbidden = (".place_order(", ".modify_order(", ".cancel_order(", ".connect(")
    source = path.read_text()
    for pattern in forbidden:
        assert pattern not in source, f"{pattern!r} found in option_market_data.py"


def test_adapter_only_calls_documented_read_only_broker_methods():
    path = Path(__file__).resolve().parent.parent / "bujji" / "broker" / "option_market_data.py"
    tree = ast.parse(path.read_text())
    allowed = {"resolve_atm_contract", "get_ltp", "get_quote", "get_option_chain"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
            if node.func.value.id == "broker":
                assert node.func.attr in allowed, f"unexpected broker method called: {node.func.attr}"
