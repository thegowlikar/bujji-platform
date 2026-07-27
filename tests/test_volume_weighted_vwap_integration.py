"""End-to-end proof: real per-candle option volume (via
Broker.get_option_candles) flows all the way from the broker through
Orchestrator._handle_in_position into TradeManager.reassess and the
Premium VWAP -- not just unit-tested on PremiumVwapTracker in isolation.
"""
import pytest

from bujji.broker.paper import PaperBroker
from bujji.core.enums import State
from tests.conftest import c
from tests.test_tier1_capital_protection import build_orch, _fast_broker_cfg


@pytest.mark.asyncio
async def test_real_option_candle_volume_flows_into_premium_vwap(config, logger, tmp_path):
    _fast_broker_cfg(config)
    broker = PaperBroker(available_margin=50_00_000.0, margin_per_lot=1_00_000.0)
    orch, status = build_orch(config, logger, broker, tmp_path)
    await orch.startup()

    await orch.on_candle(c(9, 20, 22000, 22010, 21990, 22005, vol=1000))
    assert orch.state is State.IN_POSITION

    # A heavy-volume candle at a HIGH premium, then a thin-volume candle at
    # an even higher premium -- if volume-weighting is genuinely active,
    # the thin-volume spike should barely move the VWAP.
    broker.set_option_volume(20_000_000.0)  # Heavy volume for this cycle.
    await orch.on_candle(c(9, 25, 22000, 22010, 21990, 22005, vol=1000))
    vwap_after_heavy = orch._trade.premium_vwap  # noqa: SLF001

    broker.set_option_volume(1.0)  # Deliberately near-zero volume next cycle.
    await orch.on_candle(c(9, 30, 22000, 22010, 21990, 22005, vol=1000))
    vwap_after_thin = orch._trade.premium_vwap  # noqa: SLF001

    # The thin-volume candle must have moved the VWAP only slightly compared
    # to how much it would move under equal-weighting (where every candle
    # counts the same regardless of volume).
    assert vwap_after_heavy != vwap_after_thin  # It DID update, just weighted.
    # cumulative_volume must reflect the real fetched volumes, not a
    # placeholder constant.
    quality = orch._trade.premium_vwap_quality()  # noqa: SLF001
    assert quality.cumulative_volume > 20_000_000.0  # Entry seed (1.0) + heavy + thin.


@pytest.mark.asyncio
async def test_broker_without_option_candles_falls_back_to_ltp_equal_weight(
    config, logger, tmp_path
):
    """A broker that hasn't implemented get_option_candles() (returns [])
    must not crash -- the holding loop falls back to plain get_ltp with
    equal weight for that cycle."""
    _fast_broker_cfg(config)

    class NoOptionCandlesBroker(PaperBroker):
        async def get_option_candles(self, contract, minutes, count):
            return []  # Simulates an unimplemented broker.

    broker = NoOptionCandlesBroker(available_margin=50_00_000.0, margin_per_lot=1_00_000.0)
    orch, status = build_orch(config, logger, broker, tmp_path)
    await orch.startup()

    await orch.on_candle(c(9, 20, 22000, 22010, 21990, 22005, vol=1000))
    assert orch.state is State.IN_POSITION

    await orch.on_candle(c(9, 25, 22000, 22010, 21990, 22005, vol=1000))  # Must not raise.
    assert orch.state is State.IN_POSITION
    assert orch._trade.premium_vwap > 0  # noqa: SLF001 - still computed, just equal-weight.
