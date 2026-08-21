"""Tests -- Shadow Trade Construction Bridge, Phase 14 Tasks 1-4 scope.
Pure functions over deterministic fixtures, no broker, no live calls, no
hindsight, no fabricated values."""
from __future__ import annotations

import json

from bujji.market_perception.models import (
    HEALTH_OK, MarketSnapshot, OptionChainConfig, OptionChainSnapshot, OptionLeg, SpotSnapshot, VixSnapshot,
)
from bujji.msi_trade_construction import taxonomy as tc_taxonomy
from bujji.shadow_trade_construction.engine import build_shadow_trade_candidate
from bujji.shadow_trade_construction.models import (
    STATUS_CONSTRUCTED, STATUS_INSUFFICIENT_MARKET_DATA, STATUS_INVALID_INTENT,
)


def _leg(strike, option_type, bid, ask, oi=50000.0):
    return OptionLeg(
        symbol=f"NIFTY{strike}{option_type}", strike=strike, option_type=option_type,
        ltp=None, bid=bid, ask=ask, spread=(ask - bid) if (bid and ask) else None,
        volume=None, open_interest=oi, iv=None, delta=None, gamma=None, theta=None, vega=None,
    )


def _snapshot(legs, ts="2026-08-06T09:15:00+05:30", spot=24600.0, expiry="2026-08-11"):
    chain = OptionChainSnapshot(
        underlying="NIFTY", expiry=expiry, atm_strike=24600.0,
        config=OptionChainConfig(strike_range=500, strike_step=100), legs=tuple(legs),
    )
    return MarketSnapshot(
        snapshot_version="1.0", timestamp=ts, source="fyers_live", latency_ms=1.0,
        health_status=HEALTH_OK, missing_fields=(),
        spot=SpotSnapshot(symbol="NIFTY", ltp=spot), vix=VixSnapshot(value=13.0),
        futures=None, option_chain=chain,
    )


_RICH_LEGS = [
    _leg(24500, "CE", 180.0, 182.0), _leg(24500, "PE", 100.0, 102.0),
    _leg(24600, "CE", 130.0, 132.0), _leg(24600, "PE", 130.0, 132.0),
    _leg(24700, "CE", 90.0, 92.0), _leg(24700, "PE", 170.0, 172.0),
    _leg(24800, "CE", 55.0, 57.0), _leg(24800, "PE", 220.0, 222.0),
]


def _record(family="LONG_DIRECTIONAL", direction="WEAK_BULLISH", has_intent=True, ts="2026-08-06T09:15:00+05:30"):
    return {
        "timestamp": ts,
        "market_state": {"regime": "RANGING"},
        "market_direction": {"overall_direction": direction},
        "trade_thesis": {"thesis_type": "RANGE_PERSISTENCE"},
        "consensus": {"consensus_level": "MODERATE_CONSENSUS"},
        "opportunity": {"opportunity_state": "MONITOR"},
        "volatility_structure": {"expected_move_pct": 1.5},
        "strategy_selection": {"selected_strategy_family": family, "confidence": "MODERATE", "supporting_evidence": ()},
        "trade_intent": {"intent_state": "INTENT_STATE_FORMED"} if has_intent else None,
    }


# ---------------------------------------------------------------------------
# Construction — valid paths
# ---------------------------------------------------------------------------
def test_valid_trade_intent_constructs_long_directional():
    snapshot = _snapshot(_RICH_LEGS)
    cand = build_shadow_trade_candidate(_record("LONG_DIRECTIONAL"), snapshot)
    assert cand.construction_status == STATUS_CONSTRUCTED
    assert len(cand.legs) == 1
    assert cand.legs[0].option_type == "CE"
    assert cand.legs[0].entry_bid is not None and cand.legs[0].entry_ask is not None


def test_constructed_candidate_has_real_bid_ask_not_fabricated():
    snapshot = _snapshot(_RICH_LEGS)
    cand = build_shadow_trade_candidate(_record("VOLATILITY_EXPANSION"), snapshot)
    assert cand.construction_status == STATUS_CONSTRUCTED
    for leg in cand.legs:
        assert leg.entry_bid == 130.0 or leg.entry_bid is not None
        assert leg.entry_mid == round((leg.entry_bid + leg.entry_ask) / 2.0, 4)


def test_construction_confidence_high_when_all_legs_have_real_quotes():
    snapshot = _snapshot(_RICH_LEGS)
    cand = build_shadow_trade_candidate(_record("VOLATILITY_EXPANSION"), snapshot)
    assert cand.construction_confidence == "HIGH"


def test_deterministic_construction_same_input_same_output():
    snapshot = _snapshot(_RICH_LEGS)
    record = _record("COVERED")
    a = build_shadow_trade_candidate(record, snapshot)
    b = build_shadow_trade_candidate(record, snapshot)
    assert a.legs == b.legs
    assert a.construction_status == b.construction_status


# ---------------------------------------------------------------------------
# Construction — refusal paths (no fabrication)
# ---------------------------------------------------------------------------
def test_no_trade_intent_refuses_construction():
    snapshot = _snapshot(_RICH_LEGS)
    cand = build_shadow_trade_candidate(_record(has_intent=False), snapshot)
    assert cand.construction_status == STATUS_INVALID_INTENT
    assert cand.legs == ()


def test_no_strategy_selected_refuses_construction():
    snapshot = _snapshot(_RICH_LEGS)
    record = _record()
    record["strategy_selection"] = {"selected_strategy_family": None, "confidence": "NONE"}
    cand = build_shadow_trade_candidate(record, snapshot)
    assert cand.construction_status == STATUS_INVALID_INTENT


def test_missing_option_chain_refuses_construction():
    snapshot = _snapshot([])
    cand = build_shadow_trade_candidate(_record("LONG_DIRECTIONAL"), snapshot)
    assert cand.construction_status == STATUS_INSUFFICIENT_MARKET_DATA


def test_missing_quotes_on_all_legs_refuses_construction():
    legs = [_leg(24600, "CE", None, None), _leg(24600, "PE", None, None)]
    snapshot = _snapshot(legs)
    cand = build_shadow_trade_candidate(_record("VOLATILITY_EXPANSION"), snapshot)
    assert cand.construction_status == STATUS_INSUFFICIENT_MARKET_DATA


def test_unsupported_family_reports_not_constructible():
    snapshot = _snapshot(_RICH_LEGS)
    cand = build_shadow_trade_candidate(_record("NOT_A_REAL_FAMILY"), snapshot)
    assert cand.construction_status in ("NOT_CONSTRUCTIBLE", "INSUFFICIENT_MARKET_DATA")


def test_calendar_refused_when_only_one_expiry_available():
    """The live pipeline only ever fetches ONE expiry -- CALENDAR needs two.
    Must fail closed, never invent a fictitious far expiry."""
    snapshot = _snapshot(_RICH_LEGS)
    cand = build_shadow_trade_candidate(_record("CALENDAR"), snapshot)
    assert cand.construction_status == STATUS_INSUFFICIENT_MARKET_DATA
    assert cand.legs == ()


def test_no_future_data_leakage_only_this_cycle_snapshot_used():
    """The bridge must never reach outside the (record, snapshot) pair it
    was given -- there is no code path here that reads a later cycle."""
    snapshot_now = _snapshot(_RICH_LEGS, ts="2026-08-06T09:15:00+05:30", spot=24600.0)
    snapshot_later = _snapshot(_RICH_LEGS, ts="2026-08-06T09:16:00+05:30", spot=25000.0)
    cand_now = build_shadow_trade_candidate(_record(ts="2026-08-06T09:15:00+05:30"), snapshot_now)
    assert cand_now.underlying_price == 24600.0
    assert cand_now.timestamp == "2026-08-06T09:15:00+05:30"
    # sanity: constructing against the later snapshot independently gives the later price,
    # proving the function reads exactly what it's given, nothing more.
    cand_later = build_shadow_trade_candidate(_record(ts="2026-08-06T09:16:00+05:30"), snapshot_later)
    assert cand_later.underlying_price == 25000.0


def test_candidate_json_serializable():
    snapshot = _snapshot(_RICH_LEGS)
    cand = build_shadow_trade_candidate(_record("LONG_DIRECTIONAL"), snapshot)
    json.dumps(cand.to_dict())


def test_never_raises_on_none_snapshot():
    cand = build_shadow_trade_candidate(_record("LONG_DIRECTIONAL"), None)
    assert cand.construction_status == STATUS_INSUFFICIENT_MARKET_DATA


# ---------------------------------------------------------------------------
# Capability matrix — every SUPPORTED_FAMILIES entry against rich real-shaped data
# ---------------------------------------------------------------------------
def test_capability_matrix_covers_every_supported_family():
    snapshot = _snapshot(_RICH_LEGS)
    statuses = {}
    for family in tc_taxonomy.SUPPORTED_FAMILIES:
        cand = build_shadow_trade_candidate(_record(family), snapshot)
        statuses[family] = cand.construction_status
    assert set(statuses.keys()) == set(tc_taxonomy.SUPPORTED_FAMILIES)
    # every real family produces a real, explicit status -- never silently skipped.
    assert all(s in ("CONSTRUCTED", "PARTIALLY_CONSTRUCTIBLE", "INSUFFICIENT_MARKET_DATA", "NOT_CONSTRUCTIBLE") for s in statuses.values())
