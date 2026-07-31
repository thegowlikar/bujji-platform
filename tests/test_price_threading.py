"""Tests — Live Shadow Real-Time Paper Execution sprint: real observed
price threaded from NiftyOptionChainEntry.last_price through
NiftyOptionContract -> OrderRequest.reference_price, without any
default/estimate introduced along the way (Part 1's own root cause,
identified in the EQ1 micro-investigation)."""
from __future__ import annotations

from datetime import datetime

from bujji.trading_brain.capital_brain.models import CapitalDecision
from bujji.trading_brain.nifty_contract_builder import engine as ccb_engine
from bujji.trading_brain.nifty_contract_builder.models import NiftyOptionChainEntry, NiftyOptionChainSnapshot, NiftySpotSnapshot
from bujji.trading_brain.order_construction.engine import construct_orders
from bujji.trading_brain.order_construction.models import ExecutionPolicy, TradingConfiguration
from bujji.trading_brain.position_sizing.engine import size_position
from bujji.trading_brain.position_sizing.models import CapitalPolicy, LotSpecification
from bujji.trading_brain.position_sizing.config import PositionSizingConfig
from bujji.trading_brain.strategy_selector.models import StrategyDecision

FIXED_CLOCK = lambda: datetime(2026, 7, 30, 9, 20, 0)


def _strategy():
    return StrategyDecision(
        decision_id="SD-1", selected_strategy="PREMIUM_VWAP_STRADDLE", selection_status="SELECTED",
        selection_confidence="VERY_HIGH", selection_reason="x", supporting_conditions=(), rejecting_conditions=(),
        alternative_candidates=(), all_evaluations=(), decision_trace="x",
        market_state_assessment_id="MSA-1", timestamp="2026-01-01T09:00:00", version="1.0.0",
    )


def _capital():
    return CapitalDecision(
        decision_id="CD-1", capital_intent="STANDARD", allocation_status="APPROVED", allocation_reason="x",
        allocation_constraints=("NONE",), required_controls=("NONE",), confidence="VERY_HIGH",
        decision_trace="x", risk_assessment_id="RA-1", timestamp="2026-01-01T09:15:00", version="1.0.0",
    )


def _chain_with_prices():
    entries = (
        NiftyOptionChainEntry(strike=25150, option_type="CE", expiry="2026-07-31", contract_symbol="NSE:X25150CE", last_price=124.15),
        NiftyOptionChainEntry(strike=25150, option_type="PE", expiry="2026-07-31", contract_symbol="NSE:X25150PE", last_price=110.30),
    )
    return NiftyOptionChainSnapshot(expiries=("2026-07-31",), entries=entries, as_of="2026-07-30T09:20:00")


class TestChainEntryPriceIsOptionalAndDefaultsToNone:
    def test_entry_without_last_price_defaults_to_none(self):
        entry = NiftyOptionChainEntry(strike=25150, option_type="CE", expiry="2026-07-31", contract_symbol="NSE:X25150CE")
        assert entry.last_price is None


class TestContractBuilderPropagatesRealPrice:
    def test_constructed_contract_carries_the_real_chain_price(self):
        result = ccb_engine.build_contracts(_strategy(), _capital(), NiftySpotSnapshot(spot=25148.0, as_of="2026-07-30T09:20:00"), _chain_with_prices(), clock=FIXED_CLOCK)
        assert result.status == "CONSTRUCTED"
        prices = {c.contract_symbol: c.last_price for c in result.contracts}
        assert prices["NSE:X25150CE"] == 124.15
        assert prices["NSE:X25150PE"] == 110.30

    def test_no_price_in_chain_yields_none_never_defaulted(self):
        entries = (NiftyOptionChainEntry(strike=25150, option_type="CE", expiry="2026-07-31", contract_symbol="NSE:X25150CE"),
                   NiftyOptionChainEntry(strike=25150, option_type="PE", expiry="2026-07-31", contract_symbol="NSE:X25150PE"))
        chain = NiftyOptionChainSnapshot(expiries=("2026-07-31",), entries=entries, as_of="2026-07-30T09:20:00")
        result = ccb_engine.build_contracts(_strategy(), _capital(), NiftySpotSnapshot(spot=25148.0, as_of="2026-07-30T09:20:00"), chain, clock=FIXED_CLOCK)
        assert all(c.last_price is None for c in result.contracts)


class TestOrderConstructionPropagatesReferencePrice:
    def test_order_request_carries_contracts_real_price(self):
        ccr = ccb_engine.build_contracts(_strategy(), _capital(), NiftySpotSnapshot(spot=25148.0, as_of="2026-07-30T09:20:00"), _chain_with_prices(), clock=FIXED_CLOCK)
        pp = size_position(_capital(), ccr.contracts, CapitalPolicy(policy="SIMULATION"),
                           LotSpecification(underlying="NIFTY", lot_size=75, effective_date="1970-01-01"),
                           PositionSizingConfig(), clock=FIXED_CLOCK)
        ocr = construct_orders(pp, ExecutionPolicy(policy="MARKET"),
                               TradingConfiguration(product="MIS", validity="DAY", session_id="S-1",
                                                     pipeline_version="1.0.0", qualification_fingerprint="FP-1"),
                               clock=FIXED_CLOCK)
        assert ocr.status == "CONSTRUCTED"
        prices = {r.contract.contract_symbol: r.reference_price for r in ocr.requests}
        assert prices["NSE:X25150CE"] == 124.15
        assert prices["NSE:X25150PE"] == 110.30
