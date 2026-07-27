"""Capital policy — build_margin_provider() unit tests.

Proves each of the four policies constructs exactly the right provider tier,
and that unsafe/self-contradictory configurations fail loudly at
construction time rather than silently falling back to something else.
"""
import logging

import pytest

from bujji.broker.paper import PaperBroker
from bujji.capital.policy import CapitalPolicy, CapitalPolicyError, build_margin_provider
from bujji.capital.providers import (
    BrokerMarginProvider,
    CertifiedBrokerMarginProvider,
    ConfigMarginProvider,
    SyntheticMarginProvider,
)


@pytest.fixture
def logger():
    lg = logging.getLogger("bujji.test.capital.policy")
    lg.addHandler(logging.NullHandler())
    return lg


def test_simulation_policy_builds_synthetic_provider(logger):
    provider = build_margin_provider(CapitalPolicy.SIMULATION, PaperBroker(), logger,
                                     simulated_margin_per_lot=50_000.0)
    assert isinstance(provider, SyntheticMarginProvider)


def test_estimated_policy_builds_config_provider(logger):
    provider = build_margin_provider(CapitalPolicy.ESTIMATED, PaperBroker(), logger,
                                     estimated_margin_per_lot=1_28_000.0)
    assert isinstance(provider, ConfigMarginProvider)


def test_estimated_policy_without_an_estimate_fails_loudly(logger):
    with pytest.raises(CapitalPolicyError):
        build_margin_provider(CapitalPolicy.ESTIMATED, PaperBroker(), logger,
                              estimated_margin_per_lot=None)


def test_strict_policy_builds_uncertified_broker_provider(logger):
    provider = build_margin_provider(CapitalPolicy.STRICT, PaperBroker(), logger)
    assert isinstance(provider, BrokerMarginProvider)
    assert not isinstance(provider, CertifiedBrokerMarginProvider)


def test_certified_policy_without_certification_flag_fails_loudly(logger):
    """The mandate's explicit requirement: selecting CERTIFIED policy alone
    is NOT enough -- a human must separately confirm live verification."""
    with pytest.raises(CapitalPolicyError):
        build_margin_provider(CapitalPolicy.CERTIFIED, PaperBroker(), logger,
                              margin_provider_certified=False)


def test_certified_policy_with_certification_flag_builds_certified_provider(logger):
    provider = build_margin_provider(CapitalPolicy.CERTIFIED, PaperBroker(), logger,
                                     margin_provider_certified=True)
    assert isinstance(provider, CertifiedBrokerMarginProvider)


@pytest.mark.asyncio
async def test_end_to_end_strict_policy_blocks_uncertified_paper_broker(logger):
    """A concrete proof this whole chain works: STRICT policy against a
    PaperBroker (whose margin is never broker-certified by design) must
    refuse to trade, even though PaperBroker's own dict says verified=True
    -- the provider tier, not the broker's self-report, decides."""
    from bujji.capital.engine import CapitalManagementEngine
    from bujji.capital.models import CapitalStatus
    from bujji.core.enums import OptionType
    from bujji.core.models import OptionContract

    broker = PaperBroker(available_margin=10_00_000.0, margin_per_lot=1_00_000.0)
    provider = build_margin_provider(CapitalPolicy.STRICT, broker, logger)
    engine = CapitalManagementEngine(broker, logger, margin_provider=provider,
                                     require_verified_margin=True)
    ce = OptionContract("NIFTY22000CE", "NIFTY", 22000, OptionType.CE, "2026-07-23", 75)
    pe = OptionContract("NIFTY22000PE", "NIFTY", 22000, OptionType.PE, "2026-07-23", 75)
    decision = await engine.approve_trade(ce, pe)
    assert decision.approved_lots == 0
    assert decision.status is CapitalStatus.UNVERIFIED
