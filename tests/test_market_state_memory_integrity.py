"""Tests -- Phase 10 Market Memory Integrity Upgrade.

Memory integrity + behavioral coverage for the historical event
hydration fix (MarketStateBuilder.process() -> build_market_state_assessment
now receives the full accumulated event_history, not just this cycle's
delta). No broker, no live calls, no strategy/threshold logic touched.
"""
from __future__ import annotations

from bujji.market_perception.models import HEALTH_OK, MarketSnapshot, SpotSnapshot, VixSnapshot
from bujji.market_state_builder.market_state import MarketStateBuilder
from bujji.market_state_builder.memory_health import compute_memory_health


def _snapshot(ts, ltp):
    return MarketSnapshot(
        snapshot_version="1.0", timestamp=ts, source="fyers_live", latency_ms=1.0,
        health_status=HEALTH_OK, missing_fields=(),
        spot=SpotSnapshot(symbol="NIFTY", ltp=ltp), vix=VixSnapshot(value=13.0),
        futures=None, option_chain=None,
    )


def _oscillating_walk(builder, n=20, start=24600.0):
    """A real reversal pattern (not monotonic) -- genuinely produces
    identifiable support/resistance, so a resolved structure_location
    here reflects real evidence, not a fabricated one."""
    price = start
    result = None
    for i in range(n):
        price += 5.0 if i % 4 < 2 else -5.0
        result = builder.process(_snapshot(f"2026-08-06T09:{15+i:02d}:00+05:30", price))
    return result


# ---------------------------------------------------------------------------
# Memory integrity
# ---------------------------------------------------------------------------
def test_episode_contains_historical_event_ids():
    builder = MarketStateBuilder()
    result = _oscillating_walk(builder)
    assert len(result.episodes) >= 1
    total_originating_ids = sum(len(ep.originating_event_ids) for ep in result.episodes)
    assert total_originating_ids > 5, "episode should have accumulated many historical event ids, not just the latest"


def test_assessment_receives_full_history_not_just_delta():
    """The actual point of Phase 10: market_structure's supporting_event_ids
    must reflect accumulated history, not be stuck at ~1-2 (the pre-fix
    bug's signature)."""
    builder = MarketStateBuilder()
    result = _oscillating_walk(builder)
    assert result.market_structure is not None
    assert len(result.market_structure.supporting_event_ids) > 5, (
        "supporting_event_ids stuck near 1-2 would indicate the pre-Phase-10 "
        "lookup-scope bug has regressed"
    )


def test_historical_events_resolve_correctly_no_missing_references():
    builder = MarketStateBuilder()
    _oscillating_walk(builder)
    result = builder.process(_snapshot("2026-08-06T09:40:00+05:30", 24650.0))
    health = compute_memory_health(result.episodes, result.events, builder.memory.event_history)
    assert health.unresolved_event_references == 0
    assert health.history_resolution_ratio == 1.0


def test_memory_health_reflects_growing_history():
    builder = MarketStateBuilder()
    price = 24600.0
    ratios = []
    for i in range(15):
        price += 5.0 if i % 4 < 2 else -5.0
        result = builder.process(_snapshot(f"2026-08-06T09:{15+i:02d}:00+05:30", price))
        health = compute_memory_health(result.episodes, result.events, builder.memory.event_history)
        ratios.append(health.history_resolution_ratio)
    # every cycle, references must fully resolve -- never degrades partway through a session.
    assert all(r == 1.0 for r in ratios)


# ---------------------------------------------------------------------------
# Behavioral -- PSI and MSSI both see the FULL accumulated history
# ---------------------------------------------------------------------------
def test_psi_sees_all_twenty_historical_events_not_only_latest():
    builder = MarketStateBuilder()
    result = _oscillating_walk(builder, n=20)
    assert result.price_structure is not None
    # A real trend/swing read requires more than the last 1-2 points --
    # confirms PSI resolved against accumulated history.
    assert result.price_structure.trend_state not in ("UNKNOWN", "NO_TREND", None)


def test_mssi_sees_all_twenty_historical_events_not_only_latest():
    builder = MarketStateBuilder()
    result = _oscillating_walk(builder, n=20)
    assert result.market_structure is not None
    # A genuinely oscillating 20-cycle walk should resolve real support/
    # resistance once enough history is available -- never fabricated,
    # but also never artificially stuck at UNKNOWN from a lookup-scope bug.
    assert result.market_structure.structure_location != "UNKNOWN"
    assert result.market_structure.confidence != "NONE"


def test_monotonic_walk_honestly_stays_unknown_no_fabrication():
    """A strictly increasing walk has no real reversal -- structure_location
    should honestly remain UNKNOWN even with full history available. Phase
    10 restores evidence access; it must never fabricate evidence that
    doesn't exist."""
    builder = MarketStateBuilder()
    result = None
    for i in range(20):
        result = builder.process(_snapshot(f"2026-08-06T09:{15+i:02d}:00+05:30", 24600.0 + i * 3.0))
    assert result.market_structure is not None
    assert result.market_structure.structure_location == "UNKNOWN"


def test_events_field_on_assessment_still_reflects_per_cycle_delta():
    """Backward compatibility: MarketStateAssessment.events (consumed by
    market_state.synthesizer's active_events telemetry) must remain the
    per-cycle delta, NOT silently balloon into the full history -- only
    the internal PSI/MSSI lookup source changed."""
    builder = MarketStateBuilder()
    result = _oscillating_walk(builder, n=20)
    assert len(result.events) < len(builder.memory.event_history)
