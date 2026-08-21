"""Tests -- Execution Reality Layer, Phase-2 (TradeLiquidityContext).

Covers: contract construction, frozen immutability, timestamp
preservation (no regeneration, no clock), data-quality propagation,
multi-leg (iron-condor-shaped) preservation with no collapsing,
no-decision-field schema safety, and isolation from every protected
system.
"""
from __future__ import annotations

import dataclasses

import pytest

from bujji.core.enums import Side
from bujji.execution_reality.liquidity_aggregator import LiquidityHealthReading
from bujji.execution_reality.models import DATA_QUALITY_LIVE_QUOTE, LegQuote
from bujji.execution_reality.trade_liquidity_context import (
    TradeLiquidityContext, build_trade_liquidity_context,
)
from bujji.intelligence.models import DataQuality, LiquidityReading, SpreadTightness


def make_leg(symbol, option_type, bid, ask, timestamp, strike=25000.0, side=Side.SELL):
    mid = (bid + ask) / 2
    return LegQuote(
        symbol=symbol, strike=strike, option_type=option_type, side=side, bid=bid, ask=ask, mid=mid,
        absolute_spread=ask - bid, spread_percentage=(ask - bid) / mid, data_quality=DATA_QUALITY_LIVE_QUOTE,
        timestamp=timestamp,
    )


def make_reading(tightness=SpreadTightness.TIGHT, data_quality=DataQuality.SUFFICIENT, as_of="2026-08-04T09:30:00"):
    return LiquidityReading(
        ce_bid=98.0, ce_ask=100.0, pe_bid=97.0, pe_ask=99.0, ce_spread_pct=1.0, pe_spread_pct=1.0,
        combined_spread=4.0, combined_spread_pct=1.0, tightness=tightness, confidence=1.0,
        data_quality=data_quality, as_of=as_of,
    )


# --------------------------------------------------------------------- #
# 1. Contract creation
# --------------------------------------------------------------------- #

def test_contract_constructs_correctly():
    legs = [
        make_leg("NIFTY25000CE", "CE", 98.0, 100.0, "2026-08-04T09:29:59"),
        make_leg("NIFTY25000PE", "PE", 97.0, 99.0, "2026-08-04T09:29:58"),
    ]
    health = LiquidityHealthReading(
        structure_id="S1", pair_readings=(("body", make_reading()),),
        overall_tightness=SpreadTightness.TIGHT, overall_data_quality=DataQuality.SUFFICIENT,
        as_of="2026-08-04T09:30:00",
    )
    ctx = build_trade_liquidity_context("S1", health, legs, created_at="2026-08-04T09:30:00.005")

    assert isinstance(ctx, TradeLiquidityContext)
    assert ctx.structure_id == "S1"
    assert ctx.liquidity_health is health
    assert ctx.data_quality == DataQuality.SUFFICIENT
    assert ctx.created_at == "2026-08-04T09:30:00.005"


# --------------------------------------------------------------------- #
# 2. Frozen immutability
# --------------------------------------------------------------------- #

def test_context_is_frozen():
    legs = [make_leg("NIFTY25000CE", "CE", 98.0, 100.0, "2026-08-04T09:29:59")]
    health = LiquidityHealthReading(
        structure_id="S1", pair_readings=(("body", make_reading()),),
        overall_tightness=SpreadTightness.TIGHT, overall_data_quality=DataQuality.SUFFICIENT,
        as_of="2026-08-04T09:30:00",
    )
    ctx = build_trade_liquidity_context("S1", health, legs, created_at="2026-08-04T09:30:00.005")

    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.structure_id = "changed"


# --------------------------------------------------------------------- #
# 3. Timestamp preservation -- no regeneration, no clock
# --------------------------------------------------------------------- #

def test_timestamps_preserved_exactly_no_regeneration():
    ce_ts = "2026-08-04T09:29:58.100"
    pe_ts = "2026-08-04T09:29:58.150"
    health_as_of = "2026-08-04T09:30:00.000"
    created_at = "2026-08-04T09:30:00.500"

    legs = [
        make_leg("NIFTY25000CE", "CE", 98.0, 100.0, ce_ts),
        make_leg("NIFTY25000PE", "PE", 97.0, 99.0, pe_ts),
    ]
    health = LiquidityHealthReading(
        structure_id="S1", pair_readings=(("body", make_reading(as_of=health_as_of)),),
        overall_tightness=SpreadTightness.TIGHT, overall_data_quality=DataQuality.SUFFICIENT, as_of=health_as_of,
    )
    ctx = build_trade_liquidity_context("S1", health, legs, created_at=created_at)

    assert ctx.created_at == created_at  # exactly what was supplied, never regenerated
    assert ctx.liquidity_health.as_of == health_as_of
    timestamps_by_symbol = dict(ctx.quote_observation_timestamps)
    assert timestamps_by_symbol["NIFTY25000CE"] == ce_ts
    assert timestamps_by_symbol["NIFTY25000PE"] == pe_ts


def test_build_function_has_no_clock_or_datetime_dependency():
    import ast
    import inspect
    source = inspect.getsource(build_trade_liquidity_context)
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in ("now",), "build_trade_liquidity_context must never call .now()"
    assert "clock" not in inspect.signature(build_trade_liquidity_context).parameters


# --------------------------------------------------------------------- #
# 4. Data quality propagation
# --------------------------------------------------------------------- #

def test_data_quality_propagates_from_liquidity_health():
    legs = [make_leg("NIFTY25000CE", "CE", 98.0, 100.0, "2026-08-04T09:29:59")]
    health = LiquidityHealthReading(
        structure_id="S1", pair_readings=(("body", make_reading(data_quality=DataQuality.INSUFFICIENT)),),
        overall_tightness=SpreadTightness.UNKNOWN, overall_data_quality=DataQuality.INSUFFICIENT,
        as_of="2026-08-04T09:30:00",
    )
    ctx = build_trade_liquidity_context("S1", health, legs, created_at="2026-08-04T09:30:00.005")
    assert ctx.data_quality == health.overall_data_quality == DataQuality.INSUFFICIENT


# --------------------------------------------------------------------- #
# 5. Multi-leg preservation -- iron condor, 4 legs, no collapsing
# --------------------------------------------------------------------- #

def test_iron_condor_four_legs_all_timestamps_preserved():
    legs = [
        make_leg("NIFTY25000CE", "CE", 60.0, 62.0, "2026-08-04T09:29:58.100"),   # body CE
        make_leg("NIFTY24900PE", "PE", 58.0, 60.0, "2026-08-04T09:29:58.150"),   # body PE
        make_leg("NIFTY25500CE", "CE", 20.0, 21.0, "2026-08-04T09:29:58.200"),   # wing CE
        make_leg("NIFTY24500PE", "PE", 19.0, 20.0, "2026-08-04T09:29:58.250"),   # wing PE
    ]
    health = LiquidityHealthReading(
        structure_id="S1",
        pair_readings=(("body", make_reading(SpreadTightness.TIGHT)), ("wing", make_reading(SpreadTightness.WIDE))),
        overall_tightness=SpreadTightness.WIDE, overall_data_quality=DataQuality.SUFFICIENT,
        as_of="2026-08-04T09:30:00",
    )
    ctx = build_trade_liquidity_context("S1", health, legs, created_at="2026-08-04T09:30:00.500")

    assert len(ctx.quote_observation_timestamps) == 4  # no collapsing to 2 pairs
    timestamps_by_symbol = dict(ctx.quote_observation_timestamps)
    assert timestamps_by_symbol["NIFTY25000CE"] == "2026-08-04T09:29:58.100"
    assert timestamps_by_symbol["NIFTY24900PE"] == "2026-08-04T09:29:58.150"
    assert timestamps_by_symbol["NIFTY25500CE"] == "2026-08-04T09:29:58.200"
    assert timestamps_by_symbol["NIFTY24500PE"] == "2026-08-04T09:29:58.250"
    # the underlying LiquidityHealthReading itself still carries both pair readings, unmodified
    assert len(ctx.liquidity_health.pair_readings) == 2


# --------------------------------------------------------------------- #
# 6. No decision fields
# --------------------------------------------------------------------- #

def test_no_decision_fields_present():
    field_names = set(TradeLiquidityContext.__dataclass_fields__.keys())
    forbidden = {
        "allowed", "approved", "blocked", "decision", "trade_permission",
        "entry_permission", "risk", "signal", "recommendation",
    }
    assert not (field_names & forbidden), f"forbidden fields found: {field_names & forbidden}"


# --------------------------------------------------------------------- #
# 7. Isolation
# --------------------------------------------------------------------- #

def test_no_protected_module_references_trade_liquidity_context():
    import subprocess
    result = subprocess.run(
        ["grep", "-rl", "trade_liquidity_context",
         "bujji/production_runtime/", "bujji/trading_session_governor/", "bujji/trading_brain/",
         "bujji/msi_trade_construction/", "bujji/shadow_observatory/", "bujji/broker/"],
        cwd="/opt/bujji/app", capture_output=True, text=True,
    )
    assert result.stdout.strip() == "", f"unexpected references found: {result.stdout}"
