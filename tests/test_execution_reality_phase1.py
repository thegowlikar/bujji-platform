"""Tests -- Execution Reality Layer, Phase-1 (liquidity pairing + aggregation).

Uses the REAL LiquidityBrain.analyze() throughout, not a mock -- Part 3
of this phase's design explicitly requires proving the existing
analyze() signature is respected and that LiquidityBrain itself needs
no modification. Covers pairing (straddle/iron-fly/iron-condor shapes,
order-independence), LiquidityBrain invocation correctness, aggregation
(all five required cases), schema safety, and isolation.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from bujji.core.enums import Side
from bujji.execution_reality.liquidity_aggregator import LiquidityHealthReading, compute_liquidity_health
from bujji.execution_reality.liquidity_pairing_adapter import LegPair, LiquidityPairingAdapter, PairingError
from bujji.execution_reality.models import DATA_QUALITY_LIVE_QUOTE, LegQuote
from bujji.intelligence.liquidity_brain import LiquidityBrain
from bujji.intelligence.models import DataQuality, LiquidityReading, SpreadTightness

FIXED_NOW = datetime(2026, 8, 4, 9, 30, 0, tzinfo=timezone.utc)


def clock():
    return FIXED_NOW


def make_leg(symbol, option_type, bid, ask, strike=25000.0, side=Side.SELL):
    mid = (bid + ask) / 2
    return LegQuote(
        symbol=symbol, strike=strike, option_type=option_type, side=side, bid=bid, ask=ask, mid=mid,
        absolute_spread=ask - bid, spread_percentage=(ask - bid) / mid, data_quality=DATA_QUALITY_LIVE_QUOTE,
        timestamp=FIXED_NOW.isoformat(),
    )


# --------------------------------------------------------------------- #
# Pairing
# --------------------------------------------------------------------- #

def test_straddle_creates_one_pair():
    ce = make_leg("NIFTY25000CE", "CE", 98.0, 100.0)
    pe = make_leg("NIFTY25000PE", "PE", 97.0, 99.0)
    roles = {"NIFTY25000CE": "body", "NIFTY25000PE": "body"}

    adapter = LiquidityPairingAdapter()
    pairs = adapter.pair([ce, pe], roles)

    assert len(pairs) == 1
    assert pairs[0].role_label == "body"
    assert pairs[0].ce_leg is ce
    assert pairs[0].pe_leg is pe


def test_iron_fly_creates_body_and_wing_pairs():
    body_ce = make_leg("NIFTY25000CE", "CE", 98.0, 100.0)
    body_pe = make_leg("NIFTY25000PE", "PE", 97.0, 99.0)
    wing_ce = make_leg("NIFTY25500CE", "CE", 20.0, 21.0)
    wing_pe = make_leg("NIFTY24500PE", "PE", 19.0, 20.0)
    roles = {
        "NIFTY25000CE": "body", "NIFTY25000PE": "body",
        "NIFTY25500CE": "wing", "NIFTY24500PE": "wing",
    }

    adapter = LiquidityPairingAdapter()
    pairs = adapter.pair([body_ce, body_pe, wing_ce, wing_pe], roles)

    assert len(pairs) == 2
    by_role = {p.role_label: p for p in pairs}
    assert by_role["body"].ce_leg is body_ce and by_role["body"].pe_leg is body_pe
    assert by_role["wing"].ce_leg is wing_ce and by_role["wing"].pe_leg is wing_pe


def test_iron_condor_creates_body_and_wing_pairs():
    # Iron Condor differs from Iron Fly only in strike selection, not
    # in role structure -- same pairing model, per this phase's own scope.
    body_ce = make_leg("NIFTY25100CE", "CE", 60.0, 62.0)
    body_pe = make_leg("NIFTY24900PE", "PE", 58.0, 60.0)
    wing_ce = make_leg("NIFTY25500CE", "CE", 20.0, 21.0)
    wing_pe = make_leg("NIFTY24500PE", "PE", 19.0, 20.0)
    roles = {
        "NIFTY25100CE": "body", "NIFTY24900PE": "body",
        "NIFTY25500CE": "wing", "NIFTY24500PE": "wing",
    }

    adapter = LiquidityPairingAdapter()
    pairs = adapter.pair([body_ce, body_pe, wing_ce, wing_pe], roles)

    assert len(pairs) == 2
    by_role = {p.role_label: p for p in pairs}
    assert "body" in by_role and "wing" in by_role


def test_pairing_is_order_independent():
    body_ce = make_leg("NIFTY25000CE", "CE", 98.0, 100.0)
    body_pe = make_leg("NIFTY25000PE", "PE", 97.0, 99.0)
    wing_ce = make_leg("NIFTY25500CE", "CE", 20.0, 21.0)
    wing_pe = make_leg("NIFTY24500PE", "PE", 19.0, 20.0)
    roles = {
        "NIFTY25000CE": "body", "NIFTY25000PE": "body",
        "NIFTY25500CE": "wing", "NIFTY24500PE": "wing",
    }

    # Shuffled input order: PE wing, CE body, PE body, CE wing
    shuffled = [wing_pe, body_ce, body_pe, wing_ce]

    adapter = LiquidityPairingAdapter()
    pairs = adapter.pair(shuffled, roles)

    by_role = {p.role_label: p for p in pairs}
    assert len(pairs) == 2
    assert by_role["body"].ce_leg is body_ce and by_role["body"].pe_leg is body_pe
    assert by_role["wing"].ce_leg is wing_ce and by_role["wing"].pe_leg is wing_pe


def test_pairing_missing_role_fails_closed():
    ce = make_leg("NIFTY25000CE", "CE", 98.0, 100.0)
    pe = make_leg("NIFTY25000PE", "PE", 97.0, 99.0)
    adapter = LiquidityPairingAdapter()
    with pytest.raises(PairingError):
        adapter.pair([ce, pe], {"NIFTY25000CE": "body"})  # PE role missing


def test_pairing_incomplete_pair_fails_closed():
    ce = make_leg("NIFTY25000CE", "CE", 98.0, 100.0)
    adapter = LiquidityPairingAdapter()
    with pytest.raises(PairingError):
        adapter.pair([ce], {"NIFTY25000CE": "body"})  # no PE leg for "body" at all


def test_pairing_duplicate_option_type_for_same_role_fails_closed():
    ce1 = make_leg("NIFTY25000CE", "CE", 98.0, 100.0)
    ce2 = make_leg("NIFTY25100CE", "CE", 60.0, 62.0)
    pe = make_leg("NIFTY25000PE", "PE", 97.0, 99.0)
    adapter = LiquidityPairingAdapter()
    with pytest.raises(PairingError):
        adapter.pair([ce1, ce2, pe], {"NIFTY25000CE": "body", "NIFTY25100CE": "body", "NIFTY25000PE": "body"})


# --------------------------------------------------------------------- #
# LiquidityBrain invocation -- real calls, no mock
# --------------------------------------------------------------------- #

def test_pair_maps_correctly_onto_liquiditybrain_analyze():
    ce = make_leg("NIFTY25000CE", "CE", 98.0, 100.0)
    pe = make_leg("NIFTY25000PE", "PE", 97.0, 99.0)
    adapter = LiquidityPairingAdapter()
    pair = adapter.pair([ce, pe], {"NIFTY25000CE": "body", "NIFTY25000PE": "body"})[0]

    brain = LiquidityBrain()
    reading = brain.analyze(pair.ce_leg.bid, pair.ce_leg.ask, pair.pe_leg.bid, pair.pe_leg.ask)

    # Real LiquidityBrain output, unmodified signature, unmodified module.
    assert reading.ce_bid == 98.0 and reading.ce_ask == 100.0
    assert reading.pe_bid == 97.0 and reading.pe_ask == 99.0
    assert reading.tightness in (SpreadTightness.TIGHT, SpreadTightness.NORMAL, SpreadTightness.WIDE)
    assert reading.data_quality == DataQuality.SUFFICIENT


def test_liquiditybrain_module_unchanged_no_new_methods_added():
    # Structural guard: LiquidityBrain must still expose exactly the
    # same public surface it had before this phase -- confirms no
    # accidental monkeypatching/extension happened anywhere.
    brain = LiquidityBrain()
    public_methods = [m for m in dir(brain) if not m.startswith("_")]
    assert public_methods == ["analyze"]


# --------------------------------------------------------------------- #
# Aggregation
# --------------------------------------------------------------------- #

def _reading(tightness, data_quality=DataQuality.SUFFICIENT):
    """Directly constructs a LiquidityReading with an explicit
    tightness/data_quality -- deliberately isolating these aggregation
    tests from LiquidityBrain's own spread math (already exercised for
    real in `test_pair_maps_correctly_onto_liquiditybrain_analyze`
    above), so a failure here can only mean the aggregator's own
    worst-pair logic is wrong, nothing upstream."""
    return LiquidityReading(
        ce_bid=98.0, ce_ask=100.0, pe_bid=97.0, pe_ask=99.0,
        ce_spread_pct=1.0, pe_spread_pct=1.0, combined_spread=4.0, combined_spread_pct=1.0,
        tightness=tightness, confidence=1.0, data_quality=data_quality, as_of=FIXED_NOW,
    )


def test_aggregation_all_tight_is_tight():
    readings = (("body", _reading(SpreadTightness.TIGHT)), ("wing", _reading(SpreadTightness.TIGHT)))
    result = compute_liquidity_health("S1", readings, clock)
    assert result.overall_tightness == SpreadTightness.TIGHT
    assert result.overall_data_quality == DataQuality.SUFFICIENT


def test_aggregation_one_normal_is_normal():
    readings = (("body", _reading(SpreadTightness.TIGHT)), ("wing", _reading(SpreadTightness.NORMAL)))
    result = compute_liquidity_health("S1", readings, clock)
    assert result.overall_tightness == SpreadTightness.NORMAL


def test_aggregation_one_wide_is_wide():
    readings = (
        ("body", _reading(SpreadTightness.TIGHT)),
        ("wing", _reading(SpreadTightness.WIDE)),
        ("third", _reading(SpreadTightness.NORMAL)),
    )
    result = compute_liquidity_health("S1", readings, clock)
    assert result.overall_tightness == SpreadTightness.WIDE


def test_aggregation_one_unknown_is_unknown():
    readings = (
        ("body", _reading(SpreadTightness.TIGHT)),
        ("wing", _reading(SpreadTightness.UNKNOWN, data_quality=DataQuality.INSUFFICIENT)),
    )
    result = compute_liquidity_health("S1", readings, clock)
    assert result.overall_tightness == SpreadTightness.UNKNOWN
    assert result.overall_data_quality == DataQuality.INSUFFICIENT


def test_aggregation_multiple_pairs_worst_wins_regardless_of_order():
    readings = (
        ("a", _reading(SpreadTightness.WIDE)),
        ("b", _reading(SpreadTightness.TIGHT)),
        ("c", _reading(SpreadTightness.NORMAL)),
        ("d", _reading(SpreadTightness.TIGHT)),
    )
    result = compute_liquidity_health("S1", readings, clock)
    assert result.overall_tightness == SpreadTightness.WIDE
    assert len(result.pair_readings) == 4


def test_aggregation_empty_readings_fails_closed():
    with pytest.raises(ValueError):
        compute_liquidity_health("S1", (), clock)


def test_aggregation_never_recomputes_a_reading_only_carries_it():
    reading = _reading(SpreadTightness.TIGHT)
    result = compute_liquidity_health("S1", (("body", reading),), clock)
    assert result.pair_readings[0][1] is reading  # same object, never copied-with-changes


# --------------------------------------------------------------------- #
# Schema safety
# --------------------------------------------------------------------- #

def test_liquidity_health_reading_has_no_decision_fields():
    reading = _reading(SpreadTightness.TIGHT)
    result = compute_liquidity_health("S1", (("body", reading),), clock)
    field_names = {f for f in result.__dataclass_fields__}
    forbidden = {"approve", "approved", "allowed", "blocked", "decision", "trade", "risk"}
    assert not (field_names & forbidden), f"forbidden decision-shaped fields found: {field_names & forbidden}"


def test_leg_pair_has_no_decision_fields():
    ce = make_leg("NIFTY25000CE", "CE", 98.0, 100.0)
    pe = make_leg("NIFTY25000PE", "PE", 97.0, 99.0)
    pair = LiquidityPairingAdapter().pair([ce, pe], {"NIFTY25000CE": "body", "NIFTY25000PE": "body"})[0]
    field_names = {f for f in pair.__dataclass_fields__}
    forbidden = {"approve", "approved", "allowed", "blocked", "decision", "trade", "risk"}
    assert not (field_names & forbidden)


# --------------------------------------------------------------------- #
# Isolation
# --------------------------------------------------------------------- #

def test_no_protected_module_imports_phase1_files():
    import subprocess
    result = subprocess.run(
        ["grep", "-rl", "-E", "liquidity_pairing_adapter|liquidity_aggregator",
         "bujji/production_runtime/", "bujji/trading_session_governor/", "bujji/trading_brain/",
         "bujji/msi_trade_construction/", "bujji/shadow_observatory/", "bujji/broker/"],
        cwd="/opt/bujji/app", capture_output=True, text=True,
    )
    assert result.stdout.strip() == "", f"unexpected references found: {result.stdout}"
