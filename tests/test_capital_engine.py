"""Capital Management Engine — core unit tests.

Covers the mandate's required scenarios: increasing/decreasing capital, zero
capital, margin rejection, margin API failure, safety buffer, and the
fail-safe-never-guess contract for every unverifiable-input scenario.
"""
import logging

import pytest

from bujji.broker.paper import PaperBroker
from bujji.capital.engine import CapitalManagementEngine
from bujji.capital.models import CapitalStatus
from bujji.core.enums import OptionType
from bujji.core.models import OptionContract


def _contracts():
    ce = OptionContract("NIFTY22000CE", "NIFTY", 22000, OptionType.CE, "2026-07-23", 75)
    pe = OptionContract("NIFTY22000PE", "NIFTY", 22000, OptionType.PE, "2026-07-23", 75)
    return ce, pe


@pytest.fixture
def logger():
    lg = logging.getLogger("bujji.test.capital")
    lg.addHandler(logging.NullHandler())
    return lg


# ---------------------------------------------------------------------- #
# Core sizing algorithm
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_approves_configured_lots_when_capital_is_ample(logger):
    broker = PaperBroker(available_margin=10_00_000.0, margin_per_lot=1_28_000.0)
    engine = CapitalManagementEngine(broker, logger, safety_buffer=0.90, configured_max_lots=1)
    decision = await engine.approve_trade(*_contracts())
    assert decision.approved is True
    assert decision.approved_lots == 1
    assert decision.status is CapitalStatus.SAFE


@pytest.mark.asyncio
async def test_worked_example_2_lakh_capital_1_28_lakh_margin_max_1_lot(logger):
    """The exact worked example from the audit: ₹2,00,000 capital,
    ₹1,28,000/lot margin -> maximum safe lots = 1."""
    broker = PaperBroker(available_margin=2_00_000.0, margin_per_lot=1_28_000.0)
    engine = CapitalManagementEngine(broker, logger, safety_buffer=1.0, configured_max_lots=5)
    decision = await engine.approve_trade(*_contracts())
    assert decision.maximum_safe_lots == 1
    assert decision.approved_lots == 1


@pytest.mark.asyncio
async def test_worked_example_margin_drops_to_95k_max_2_lots(logger):
    """Same ₹2,00,000 capital; margin requirement drops to ₹95,000 ->
    maximum safe lots = 2."""
    broker = PaperBroker(available_margin=2_00_000.0, margin_per_lot=95_000.0)
    engine = CapitalManagementEngine(broker, logger, safety_buffer=1.0, configured_max_lots=5)
    decision = await engine.approve_trade(*_contracts())
    assert decision.maximum_safe_lots == 2
    assert decision.approved_lots == 2


@pytest.mark.asyncio
async def test_configured_ceiling_binds_even_with_ample_capital(logger):
    """Capital could support 10 lots, but the operator's configured ceiling
    is 2 -- approved_lots must never exceed the configured ceiling."""
    broker = PaperBroker(available_margin=50_00_000.0, margin_per_lot=1_00_000.0)
    engine = CapitalManagementEngine(broker, logger, safety_buffer=1.0, configured_max_lots=2)
    decision = await engine.approve_trade(*_contracts())
    assert decision.maximum_safe_lots == 50
    assert decision.approved_lots == 2  # min(configured=2, safe=50)


# ---------------------------------------------------------------------- #
# Safety buffer
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_safety_buffer_reserves_configured_headroom(logger):
    """Capital ₹2,00,000, margin required ₹1,90,000, safety_buffer=0.90 ->
    usable = 1,80,000 < 1,90,000 -> 0 safe lots (BLOCKED), NOT 100% used."""
    broker = PaperBroker(available_margin=2_00_000.0, margin_per_lot=1_90_000.0)
    engine = CapitalManagementEngine(broker, logger, safety_buffer=0.90, configured_max_lots=1)
    decision = await engine.approve_trade(*_contracts())
    assert decision.usable_margin == pytest.approx(1_80_000.0)
    assert decision.maximum_safe_lots == 0
    assert decision.approved_lots == 0
    assert decision.status is CapitalStatus.BLOCKED


@pytest.mark.asyncio
async def test_safety_buffer_must_be_in_valid_range(logger):
    broker = PaperBroker()
    with pytest.raises(ValueError):
        CapitalManagementEngine(broker, logger, safety_buffer=0.0)
    with pytest.raises(ValueError):
        CapitalManagementEngine(broker, logger, safety_buffer=1.5)


# ---------------------------------------------------------------------- #
# Runtime behaviour: capital changes must change approved lots automatically
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_increasing_capital_automatically_increases_approved_lots(logger):
    """Day 1: ₹2,00,000 -> 1 lot. Capital grows to ₹4,50,000 -> more lots
    approved automatically, with NO configuration change -- only if the
    configured ceiling allows it."""
    broker = PaperBroker(margin_per_lot=1_28_000.0)
    engine = CapitalManagementEngine(broker, logger, safety_buffer=1.0, configured_max_lots=10)

    broker.set_capital(available_margin=2_00_000.0)
    day1 = await engine.approve_trade(*_contracts())
    assert day1.approved_lots == 1

    broker.set_capital(available_margin=4_50_000.0)
    day2 = await engine.approve_trade(*_contracts())
    assert day2.approved_lots == 3  # floor(450000/128000) = 3
    assert day2.approved_lots > day1.approved_lots  # automatic increase


@pytest.mark.asyncio
async def test_decreasing_capital_automatically_reduces_approved_lots(logger):
    broker = PaperBroker(margin_per_lot=1_00_000.0)
    engine = CapitalManagementEngine(broker, logger, safety_buffer=1.0, configured_max_lots=10)

    broker.set_capital(available_margin=5_00_000.0)
    before = await engine.approve_trade(*_contracts())
    assert before.approved_lots == 5

    broker.set_capital(available_margin=1_50_000.0)
    after = await engine.approve_trade(*_contracts())
    assert after.approved_lots == 1
    assert after.approved_lots < before.approved_lots  # automatic reduction


# ---------------------------------------------------------------------- #
# Zero / insufficient capital
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_zero_available_margin_rejects_trade(logger):
    broker = PaperBroker(available_margin=0.0, margin_per_lot=1_00_000.0)
    engine = CapitalManagementEngine(broker, logger)
    decision = await engine.approve_trade(*_contracts())
    assert decision.approved is False
    assert decision.approved_lots == 0
    assert decision.status is CapitalStatus.BLOCKED


@pytest.mark.asyncio
async def test_capital_below_one_lot_rejects_cleanly(logger):
    broker = PaperBroker(available_margin=50_000.0, margin_per_lot=1_00_000.0)
    engine = CapitalManagementEngine(broker, logger, safety_buffer=1.0)
    decision = await engine.approve_trade(*_contracts())
    assert decision.approved_lots == 0
    assert decision.status is CapitalStatus.BLOCKED


# ---------------------------------------------------------------------- #
# Fail-safe: never guess when a required figure is unverifiable
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_funds_unavailable_returns_unverified_never_a_guess(logger):
    broker = PaperBroker(funds_unavailable=True)
    engine = CapitalManagementEngine(broker, logger)
    decision = await engine.approve_trade(*_contracts())
    assert decision.status is CapitalStatus.UNVERIFIED
    assert decision.approved_lots == 0
    assert "capital_unverified" in decision.reason or "funds" in decision.reason


@pytest.mark.asyncio
async def test_margin_unavailable_returns_unverified_never_a_guess(logger):
    """Margin rejection / unable to price the exact straddle -- must refuse,
    never fall back to a config-estimated margin."""
    broker = PaperBroker(margin_unavailable=True)
    engine = CapitalManagementEngine(broker, logger)
    decision = await engine.approve_trade(*_contracts())
    assert decision.status is CapitalStatus.UNVERIFIED
    assert decision.approved_lots == 0
    assert decision.margin.verified is False


@pytest.mark.asyncio
async def test_broker_funds_query_raises_is_treated_as_unverified_not_crash(logger):
    """Broker timeout / disconnect while fetching funds -- engine must
    never raise; must return a BLOCKED/UNVERIFIED decision."""
    class BoomBroker(PaperBroker):
        async def get_funds(self):
            raise TimeoutError("simulated broker timeout")

    engine = CapitalManagementEngine(BoomBroker(), logger)
    decision = await engine.approve_trade(*_contracts())  # Must not raise.
    assert decision.status is CapitalStatus.UNVERIFIED
    assert decision.approved_lots == 0
    assert "funds_query_failed" in decision.reason


@pytest.mark.asyncio
async def test_broker_margin_query_raises_is_treated_as_unverified_not_crash(logger):
    class BoomBroker(PaperBroker):
        async def get_order_margin(self, ce_contract, pe_contract):
            raise ConnectionError("simulated disconnect")

    engine = CapitalManagementEngine(BoomBroker(), logger)
    decision = await engine.approve_trade(*_contracts())  # Must not raise.
    assert decision.status is CapitalStatus.UNVERIFIED
    assert decision.approved_lots == 0
    assert "margin_query_failed" in decision.reason


@pytest.mark.asyncio
async def test_partial_funds_response_missing_equity_is_unverified(logger):
    """A broker response with SOME fields present but account_equity
    missing must still refuse -- partial data is not enough."""
    class PartialBroker(PaperBroker):
        async def get_funds(self):
            return {"available_margin": 5_00_000.0}  # No account_equity.

    engine = CapitalManagementEngine(PartialBroker(), logger)
    decision = await engine.approve_trade(*_contracts())
    assert decision.status is CapitalStatus.UNVERIFIED
    assert decision.approved_lots == 0


# ---------------------------------------------------------------------- #
# Warning threshold
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_high_utilization_reports_warning_status(logger):
    broker = PaperBroker(available_margin=1_00_000.0, margin_per_lot=95_000.0)
    engine = CapitalManagementEngine(broker, logger, safety_buffer=1.0,
                                     configured_max_lots=1, warning_utilization=0.85)
    decision = await engine.approve_trade(*_contracts())
    assert decision.approved_lots == 1
    assert decision.capital_utilization >= 0.85
    assert decision.status is CapitalStatus.WARNING


# ---------------------------------------------------------------------- #
# Health report rendering
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_health_report_renders_exact_institutional_format(logger):
    broker = PaperBroker(available_margin=2_00_000.0, margin_per_lot=1_28_000.0)
    engine = CapitalManagementEngine(broker, logger, safety_buffer=1.0, configured_max_lots=1)
    decision = await engine.approve_trade(*_contracts())
    report = decision.render()
    for label in ("CAPITAL HEALTH", "Account Equity", "Available Margin",
                  "Required Margin", "Safety Buffer", "Maximum Safe Lots",
                  "Configured Max Lots", "Approved Lots", "Capital Utilization",
                  "Remaining Margin", "Status", "Reason"):
        assert label in report


@pytest.mark.asyncio
async def test_health_report_shows_not_verified_for_unverifiable_fields(logger):
    broker = PaperBroker(funds_unavailable=True)
    engine = CapitalManagementEngine(broker, logger)
    decision = await engine.approve_trade(*_contracts())
    report = decision.render()
    assert "NOT VERIFIED" in report
    assert "UNVERIFIED" in report
