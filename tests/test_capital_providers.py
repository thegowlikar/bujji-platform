"""Margin Provider abstraction — unit tests.

Proves the four provider tiers behave exactly as documented, and that
CapitalManagementEngine consumes ONLY the MarginProvider abstraction (never
a broker directly for margin).
"""
import logging

import pytest

from bujji.broker.paper import PaperBroker
from bujji.capital.engine import CapitalManagementEngine
from bujji.capital.exceptions import BrokerCapitalQueryError
from bujji.capital.models import CapitalStatus
from bujji.capital.providers import (
    BrokerMarginProvider,
    CertifiedBrokerMarginProvider,
    ConfigMarginProvider,
    SyntheticMarginProvider,
)
from bujji.core.enums import OptionType
from bujji.core.models import OptionContract


def _contracts():
    ce = OptionContract("NIFTY22000CE", "NIFTY", 22000, OptionType.CE, "2026-07-23", 75)
    pe = OptionContract("NIFTY22000PE", "NIFTY", 22000, OptionType.PE, "2026-07-23", 75)
    return ce, pe


@pytest.fixture
def logger():
    lg = logging.getLogger("bujji.test.capital.providers")
    lg.addHandler(logging.NullHandler())
    return lg


@pytest.mark.asyncio
async def test_synthetic_provider_is_never_verified(logger):
    provider = SyntheticMarginProvider(margin_per_lot=1_00_000.0)
    result = await provider.get_margin_per_lot(*_contracts())
    assert result.margin_per_lot == 1_00_000.0
    assert result.verified is False
    assert result.source == "synthetic"


@pytest.mark.asyncio
async def test_synthetic_provider_schedule_changes_over_successive_calls(logger):
    provider = SyntheticMarginProvider(margin_per_lot=1_00_000.0,
                                       schedule={0: 1_00_000.0, 1: 2_00_000.0})
    first = await provider.get_margin_per_lot(*_contracts())
    second = await provider.get_margin_per_lot(*_contracts())
    assert first.margin_per_lot == 1_00_000.0
    assert second.margin_per_lot == 2_00_000.0


@pytest.mark.asyncio
async def test_config_provider_is_never_verified(logger):
    provider = ConfigMarginProvider(margin_per_lot=1_28_000.0)
    result = await provider.get_margin_per_lot(*_contracts())
    assert result.margin_per_lot == 1_28_000.0
    assert result.verified is False
    assert result.source == "config_estimate"


def test_config_provider_rejects_non_positive_estimate():
    with pytest.raises(ValueError):
        ConfigMarginProvider(margin_per_lot=0.0)
    with pytest.raises(ValueError):
        ConfigMarginProvider(margin_per_lot=-100.0)


@pytest.mark.asyncio
async def test_broker_margin_provider_is_always_uncertified(logger):
    """Even if the underlying broker's raw dict claims verified=True, the
    UNCERTIFIED tier must never report verified=True -- only
    CertifiedBrokerMarginProvider may."""
    broker = PaperBroker(margin_per_lot=1_00_000.0)  # PaperBroker's own dict says verified=True.
    provider = BrokerMarginProvider(broker)
    result = await provider.get_margin_per_lot(*_contracts())
    assert result.margin_per_lot == 1_00_000.0
    assert result.verified is False  # Forced False regardless of the broker's own claim.


@pytest.mark.asyncio
async def test_broker_margin_provider_propagates_query_failure(logger):
    class BoomBroker(PaperBroker):
        async def get_order_margin(self, ce, pe):
            raise TimeoutError("simulated")

    provider = BrokerMarginProvider(BoomBroker())
    with pytest.raises(BrokerCapitalQueryError):
        await provider.get_margin_per_lot(*_contracts())


@pytest.mark.asyncio
async def test_certified_provider_reports_verified_true(logger):
    broker = PaperBroker(margin_per_lot=1_28_000.0)
    provider = CertifiedBrokerMarginProvider(broker)
    result = await provider.get_margin_per_lot(*_contracts())
    assert result.margin_per_lot == 1_28_000.0
    assert result.verified is True


@pytest.mark.asyncio
async def test_certified_provider_never_fabricates_verified_true_from_nothing(logger):
    """If the broker has no margin figure at all, CertifiedBrokerMarginProvider
    must NOT invent verified=True on a None value."""
    broker = PaperBroker(margin_unavailable=True)
    provider = CertifiedBrokerMarginProvider(broker)
    result = await provider.get_margin_per_lot(*_contracts())
    assert result.margin_per_lot is None
    assert result.verified is False


# ---------------------------------------------------------------------- #
# CapitalManagementEngine consumes ONLY the provider abstraction
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_engine_uses_injected_provider_not_the_broker_directly(logger):
    """A broker whose OWN get_order_margin would return one figure, but an
    explicitly-injected provider reports a DIFFERENT one -- the engine must
    use the provider's figure, proving it never bypasses the abstraction."""
    broker = PaperBroker(available_margin=10_00_000.0, margin_per_lot=999_999.0)
    provider = SyntheticMarginProvider(margin_per_lot=50_000.0)  # Deliberately different.
    engine = CapitalManagementEngine(broker, logger, safety_buffer=1.0,
                                     configured_max_lots=5, margin_provider=provider)
    decision = await engine.approve_trade(*_contracts())
    assert decision.margin_required_per_lot == 50_000.0  # From the provider, not the broker.
    assert decision.approved_lots == 5  # min(configured_max_lots=5, maximum_safe_lots=20)


@pytest.mark.asyncio
async def test_require_verified_margin_blocks_uncertified_figure(logger):
    """STRICT-policy behavior: a numerically-present but UNCERTIFIED margin
    figure must still refuse to trade."""
    broker = PaperBroker(available_margin=10_00_000.0, margin_per_lot=1_00_000.0)
    provider = BrokerMarginProvider(broker)  # Uncertified by construction.
    engine = CapitalManagementEngine(broker, logger, margin_provider=provider,
                                     require_verified_margin=True)
    decision = await engine.approve_trade(*_contracts())
    assert decision.approved_lots == 0
    # UNVERIFIED, not BLOCKED -- per the mandate's own STRICT semantics,
    # an uncertified figure is treated identically to an unknown one.
    assert decision.status is CapitalStatus.UNVERIFIED
    assert "margin_not_certified" in decision.reason


@pytest.mark.asyncio
async def test_require_verified_margin_allows_certified_figure(logger):
    broker = PaperBroker(available_margin=10_00_000.0, margin_per_lot=1_00_000.0)
    provider = CertifiedBrokerMarginProvider(broker)
    engine = CapitalManagementEngine(broker, logger, safety_buffer=1.0,
                                     margin_provider=provider, require_verified_margin=True)
    decision = await engine.approve_trade(*_contracts())
    assert decision.approved_lots == 1
    assert decision.status is CapitalStatus.SAFE
