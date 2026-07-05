"""Paper Trading mode (composite broker) — integration tests.

Proves the three things the design must guarantee:
  1. Market data (spot, candles, LTP, contract resolution) is genuinely
     sourced from the live FYERS code path (`FyersBroker._call`), not
     synthesized.
  2. No live execution method (`place_order`/`cancel_order`/`get_order`/
     `get_open_positions`) is ever invoked on the live leg — proven both
     structurally (direct calls raise immediately) and behaviourally (a full
     trading cycle never touches those FYERS actions).
  3. Virtual positions, MTM, journaling, and exits behave exactly as they do
     in plain Paper mode — the Orchestrator/ExecutionEngine/TradeManager are
     completely unmodified; only the broker underneath differs.
"""
from datetime import timedelta

import pytest

from bujji.broker.errors import LiveExecutionDisabledError
from bujji.broker.factory import build_broker
from bujji.broker.fyers import FyersBroker
from bujji.broker.guard import disable_live_execution
from bujji.broker.hybrid import HybridPaperBroker
from bujji.broker.paper import PaperBroker
from bujji.core.clock import IST
from bujji.core.enums import Direction, OptionType, Side, State
from bujji.core.models import OptionContract, OrderRequest
from bujji.execution.engine import ExecutionEngine
from tests.conftest import c
from tests.test_tier1_capital_protection import build_orch


class FakeLiveFyers(FyersBroker):
    """A FyersBroker whose transport is a canned, in-memory stand-in for the
    real FYERS API — exercises the exact same method bodies (candle mapping,
    symbol construction, order-code mapping) that talk to the live broker in
    production, without needing real credentials or network access in CI."""

    def __init__(self, config, logger):
        super().__init__(config, logger)
        self.call_log: list[str] = []
        self.spot = 22050.0
        self.premium = 118.5

    async def _call(self, action: str, **params) -> dict:
        self.call_log.append(action)
        if action == "profile":
            return {"s": "ok", "code": 200}
        if action == "ltp":
            symbol = params.get("symbol", "")
            price = self.spot if symbol.endswith("-INDEX") else self.premium
            return {"s": "ok", "ltp": price}
        if action == "historical":
            base = 1_800_000_000
            candles = [
                [base + i * 300, self.spot, self.spot + 5, self.spot - 5,
                 self.spot, 1000]
                for i in range(params.get("count", 1))
            ]
            return {"s": "ok", "candles": candles}
        if action == "instruments":
            return {"s": "ok", "expiries": ["25JAN"]}
        raise AssertionError(f"unexpected live call in test double: {action}")


def _build_hybrid(config, logger):
    # FyersBroker.connect() now fails fast with AuthenticationError if
    # credentials are absent (this is the behavior requirement 3 asks for) —
    # supply dummy-but-present values so these tests exercise the fake
    # transport, not the credential-presence guard itself (which has its own
    # dedicated coverage elsewhere).
    config.broker.app_id = "test-app-id"
    config.broker.access_token = "test-access-token"
    live = disable_live_execution(FakeLiveFyers(config.broker, logger))
    ledger = PaperBroker()
    return HybridPaperBroker(live, ledger, logger), live, ledger


# ---------------------------------------------------------------------- #
# 1. Live market data is genuinely received via the FYERS code path.
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_market_data_flows_through_live_fyers_call_path(config, logger):
    hybrid, live, _ledger = _build_hybrid(config, logger)
    await hybrid.connect()

    spot = await hybrid.get_spot("NIFTY")
    assert spot == 22050.0
    assert "ltp" in live.call_log

    candles = await hybrid.get_recent_candles("NIFTY", 5, 3)
    assert len(candles) == 3
    assert "historical" in live.call_log
    # D2 regression: FYERS candle timestamps must be tz-aware IST, even
    # through the hybrid's delegation.
    assert candles[0].timestamp.tzinfo is not None
    assert candles[0].timestamp.utcoffset() == timedelta(hours=5, minutes=30)

    contract = await hybrid.resolve_atm_contract(
        "NIFTY", 22050.0, Direction.BULLISH, 50, 75
    )
    assert contract.strike == 22050
    assert contract.option_type is OptionType.PE
    assert "instruments" in live.call_log

    ltp = await hybrid.get_ltp(contract)
    assert ltp == 118.5
    assert live.call_log.count("ltp") == 2  # spot + option premium.


# ---------------------------------------------------------------------- #
# 2. No live execution method is ever called.
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_live_execution_methods_raise_immediately_if_invoked(config, logger):
    """Structural proof: even a direct call to the (neutered) live leg's
    execution methods raises before any network call is attempted."""
    hybrid, live, _ledger = _build_hybrid(config, logger)
    contract = OptionContract("NSE:NIFTY25JAN22050PE", "NIFTY", 22050,
                              OptionType.PE, "25JAN", 75)
    req = OrderRequest(contract, Side.SELL, 75, "CID-1")

    with pytest.raises(LiveExecutionDisabledError):
        await live.place_order(req)
    with pytest.raises(LiveExecutionDisabledError):
        await live.get_order("CID-1")
    with pytest.raises(LiveExecutionDisabledError):
        await live.cancel_order("CID-1")
    with pytest.raises(LiveExecutionDisabledError):
        await live.get_open_positions()

    # None of these reached _call at all — no simulated "network" activity.
    assert live.call_log == []


@pytest.mark.asyncio
async def test_full_trading_cycle_never_calls_live_execution_actions(
    config, logger, tmp_path
):
    """Behavioural proof: a real entry -> hold -> exit cycle through the
    Orchestrator/ExecutionEngine never invokes a FYERS execution action."""
    config.broker.order_timeout_seconds = 0.05
    config.broker.poll_interval_seconds = 0.01
    hybrid, live, ledger = _build_hybrid(config, logger)
    orch, status = build_orch(config, logger, hybrid, tmp_path)
    await orch.startup()

    await orch.on_candle(c(9, 15, 22000, 22010, 21990, 22005, vol=1000))
    await orch.on_candle(c(9, 20, 22006, 22080, 22005, 22079, vol=1000))
    assert orch.state is State.IN_POSITION

    await orch.on_candle(c(9, 25, 22078, 22079, 21950, 21951, vol=1000))
    assert orch.state is State.DONE_FOR_DAY

    live_execution_actions = {"place_order", "cancel_order", "order_history",
                              "positions"}
    assert not (live_execution_actions & set(live.call_log)), (
        f"a live execution action was reached: {live.call_log}"
    )
    # The ledger, not the live broker, actually recorded the fill.
    assert ledger.place_calls >= 1


# ---------------------------------------------------------------------- #
# 3. Behaves exactly like plain Paper mode: virtual positions, MTM, exits,
#    and journaling all work identically — only the broker underneath differs.
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_paper_live_data_behaves_like_paper_mode_end_to_end(
    config, logger, tmp_path
):
    config.broker.order_timeout_seconds = 0.05
    config.broker.poll_interval_seconds = 0.01
    hybrid, live, ledger = _build_hybrid(config, logger)
    orch, status = build_orch(config, logger, hybrid, tmp_path)
    await orch.startup()

    await orch.on_candle(c(9, 15, 22000, 22010, 21990, 22005, vol=1000))
    await orch.on_candle(c(9, 20, 22006, 22080, 22005, 22079, vol=1000))

    pos = orch._trade.position  # noqa: SLF001 - test introspection.
    assert pos is not None
    assert pos.entry_price == 118.5          # Filled at the LIVE premium.
    assert pos.contract.symbol.startswith("NSE:NIFTY")  # Real FYERS symbol.
    assert status.mtm is not None

    await orch.on_candle(c(9, 25, 22078, 22079, 21950, 21951, vol=1000))
    assert orch.state is State.DONE_FOR_DAY
    assert not orch.has_open_position()

    trades = orch._journal.all_trades()  # noqa: SLF001
    assert len(trades) == 1
    assert trades[0]["entry_premium"] == "118.5"
    assert "BULLISH" in trades[0]["thesis"]  # Decision Trace / thesis reused intact.


# ---------------------------------------------------------------------- #
# Factory wiring
# ---------------------------------------------------------------------- #
def test_factory_builds_hybrid_broker_for_fyers_paper(config, logger):
    config.broker.name = "fyers_paper"
    broker = build_broker(config, logger)
    assert isinstance(broker, HybridPaperBroker)
    assert broker.name == "fyers_paper"


def test_factory_paper_mode_unaffected(config, logger):
    config.broker.name = "paper"
    broker = build_broker(config, logger)
    assert isinstance(broker, PaperBroker)


def test_factory_live_fyers_mode_is_not_neutered(config, logger):
    """A genuine full-live instance must NOT have its execution methods
    disabled — the guard must only ever be applied to the Paper-mode data leg."""
    config.broker.name = "fyers"
    broker = build_broker(config, logger)
    assert isinstance(broker, FyersBroker)
    assert broker.place_order.__name__ != "place_order_disabled"
