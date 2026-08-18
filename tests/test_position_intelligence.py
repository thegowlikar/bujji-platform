"""Tests -- Position Intelligence (Thesis Monitoring), Phase 15 gap #1.
Pure functions over deterministic fixtures, no broker, no execution, no
live calls. This layer only observes/reports -- it can never HOLD/EXIT/
ADJUST a real or paper position."""
from __future__ import annotations

import json

from bujji.position_intelligence.engine import build_entry_snapshot, evaluate_thesis
from bujji.position_intelligence.models import (
    CHECK_CONSISTENT, CHECK_DEVIATED, CHECK_UNKNOWN,
    RECOMMEND_EXIT, RECOMMEND_HOLD, RECOMMEND_UNKNOWN,
    THESIS_INTACT, THESIS_INVALIDATED, THESIS_UNKNOWN,
    PositionEntrySnapshot,
)


def _entry(family="LONG_DIRECTIONAL", direction="WEAK_BULLISH", regime="RANGING", vol_regime=None):
    return PositionEntrySnapshot(
        candidate_id="TEST", strategy_family=family, entry_timestamp="t0",
        entry_regime=regime, entry_direction=direction, entry_volatility_regime=vol_regime,
        entry_consensus_state=None, entry_liquidity_tightness=None,
    )


def _record(direction=None, regime=None, vol_regime=None, ts="t1"):
    return {
        "timestamp": ts,
        "market_direction": {"overall_direction": direction} if direction else None,
        "market_state": {"regime": regime} if regime else None,
        "volatility_structure": {"volatility_regime": vol_regime} if vol_regime else None,
    }


# ---------------------------------------------------------------------------
# Directional thesis
# ---------------------------------------------------------------------------
def test_direction_consistent_stays_intact():
    result = evaluate_thesis(_entry(direction="BULLISH"), _record(direction="STRONG_BULLISH"))
    assert result.thesis_status == THESIS_INTACT
    assert result.recommendation == RECOMMEND_HOLD


def test_direction_flip_invalidates_thesis():
    result = evaluate_thesis(_entry(direction="WEAK_BULLISH"), _record(direction="STRONG_BEARISH"))
    assert result.thesis_status == THESIS_INVALIDATED
    assert result.recommendation == RECOMMEND_EXIT
    assert "flipped" in result.recommendation_reason or "moved from" in result.recommendation_reason


def test_direction_unknown_current_stays_unknown_never_forced():
    result = evaluate_thesis(_entry(direction="BULLISH"), _record(direction="UNKNOWN"))
    assert result.thesis_status == THESIS_UNKNOWN
    assert result.recommendation == RECOMMEND_UNKNOWN


def test_non_directional_family_never_gets_a_direction_check():
    result = evaluate_thesis(_entry(family="VOLATILITY_EXPANSION", direction="BULLISH"), _record(direction="STRONG_BEARISH"))
    assert all(c.dimension != "direction" for c in result.checks)


# ---------------------------------------------------------------------------
# Volatility thesis
# ---------------------------------------------------------------------------
def test_expansion_thesis_deviates_when_compression_resolves_to_stable():
    entry = _entry(family="VOLATILITY_EXPANSION", vol_regime="COMPRESSED")
    result = evaluate_thesis(entry, _record(vol_regime="STABLE"))
    assert any(c.status == CHECK_DEVIATED for c in result.checks)
    assert result.thesis_status == THESIS_INVALIDATED


def test_expansion_thesis_intact_while_still_compressed():
    entry = _entry(family="VOLATILITY_EXPANSION", vol_regime="COMPRESSED")
    result = evaluate_thesis(entry, _record(vol_regime="COMPRESSED"))
    assert result.thesis_status == THESIS_INTACT


def test_compression_thesis_deviates_on_high_volatility_breakout():
    entry = _entry(family="VOLATILITY_COMPRESSION", vol_regime="COMPRESSED")
    result = evaluate_thesis(entry, _record(vol_regime="HIGH_VOLATILITY"))
    assert result.thesis_status == THESIS_INVALIDATED
    assert result.recommendation == RECOMMEND_EXIT


# ---------------------------------------------------------------------------
# Regime thesis (range-dependent families)
# ---------------------------------------------------------------------------
def test_range_dependent_family_deviates_on_trending_breakout():
    entry = _entry(family="IRON_FLY", regime="RANGING")
    result = evaluate_thesis(entry, _record(regime="TRENDING"))
    assert result.thesis_status == THESIS_INVALIDATED


def test_range_dependent_family_intact_while_still_ranging():
    entry = _entry(family="IRON_CONDOR", regime="RANGING")
    result = evaluate_thesis(entry, _record(regime="COMPRESSED"))
    assert result.thesis_status == THESIS_INTACT


# ---------------------------------------------------------------------------
# Honesty / no fabrication
# ---------------------------------------------------------------------------
def test_no_evidence_at_all_is_honestly_unknown():
    result = evaluate_thesis(_entry(family="LONG_DIRECTIONAL", direction=None), _record())
    assert result.thesis_status == THESIS_UNKNOWN
    assert result.recommendation == RECOMMEND_UNKNOWN


def test_never_raises_on_missing_record_fields():
    result = evaluate_thesis(_entry(), {})
    assert result.thesis_status in (THESIS_UNKNOWN, THESIS_INTACT, THESIS_INVALIDATED)


def test_result_json_serializable():
    result = evaluate_thesis(_entry(direction="BULLISH"), _record(direction="BULLISH"))
    json.dumps(result.to_dict())


def test_evaluate_thesis_never_mutates_entry():
    entry = _entry(direction="BULLISH")
    original = entry.to_dict()
    evaluate_thesis(entry, _record(direction="STRONG_BEARISH"))
    assert entry.to_dict() == original


# ---------------------------------------------------------------------------
# build_entry_snapshot -- from a real-shaped ShadowTradeCandidate stand-in
# ---------------------------------------------------------------------------
class _FakeCandidate:
    def __init__(self):
        self.candidate_id = "STC-1"
        self.strategy_family = "LONG_DIRECTIONAL"
        self.timestamp = "2026-08-06T09:00:00+05:30"
        self.market_regime = "RANGING"
        self.direction = "WEAK_BULLISH"
        self.consensus_state = "UNANIMOUS_CONSENSUS"


def test_build_entry_snapshot_copies_real_candidate_fields_only():
    snapshot = build_entry_snapshot(_FakeCandidate())
    assert snapshot.candidate_id == "STC-1"
    assert snapshot.strategy_family == "LONG_DIRECTIONAL"
    assert snapshot.entry_regime == "RANGING"
    assert snapshot.entry_direction == "WEAK_BULLISH"
    assert snapshot.entry_volatility_regime is None  # honestly None, not carried by ShadowTradeCandidate today.
