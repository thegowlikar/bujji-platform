"""MarketIntelligenceSnapshot -- Phase 19.3 tests.

Proves:
1. Determinism: same MarketRealitySnapshot-shaped inputs + same
   IntelligenceContext + same brain outputs -> identical intelligence
   fingerprint.
2. Replay equivalence: LIVE vs HISTORICAL_REPLAY at 2026-08-14 10:30 IST
   -> same fingerprint, same thesis, same posture.
3. Composition invariants: no reinterpretation (brain Readings preserved
   verbatim), evidence lineage genuinely consumed, no fabricated evidence,
   identity naming does not collide with existing identity fields.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from bujji.core.clock import IST
from bujji.core.models import Candle
from bujji.intelligence.context import (
    EXECUTION_MODE_HISTORICAL_REPLAY,
    EXECUTION_MODE_LIVE,
    IntelligenceContext,
)
from bujji.intelligence.event_brain import EventBrain
from bujji.intelligence.greeks_brain import GreeksBrain
from bujji.intelligence.liquidity_brain import LiquidityBrain
from bujji.intelligence.market_intelligence_snapshot import (
    ContradictionScore,
    IntelligenceEvidenceBundle,
    MarketIntelligenceSnapshot,
    MarketPosture,
    MarketThesis,
    build_market_intelligence_snapshot,
)
from bujji.intelligence.models import DataQuality
from bujji.intelligence.regime_brain import RegimeBrain
from bujji.intelligence.structure_brain import StructureBrain
from bujji.intelligence.volatility_brain import VolatilityBrain

FIXED_TIME = datetime(2026, 8, 14, 10, 30, tzinfo=IST)


def _candles():
    closes = [24000 + i * 3 + (5 if i % 3 == 0 else -2) for i in range(20)]
    return [
        Candle(FIXED_TIME - timedelta(minutes=5 * (20 - i)), c, c + 1, c - 1, c, 1000)
        for i, c in enumerate(closes)
    ]


def _build_readings(context: IntelligenceContext):
    candles = _candles()
    regime = RegimeBrain().analyze(candles, context)
    structure = StructureBrain().analyze(24000, [(23900, 500, 100), (24100, 300, 700)], context=context)
    liquidity = LiquidityBrain().analyze(100.0, 101.0, 80.0, 80.5, context=context)
    volatility = VolatilityBrain().analyze(candles, 24000, 24000, 5 / 365, 150.0, 150.0, context=context)
    greeks = GreeksBrain().analyze(spot=24000, strike=24000, t_years=5 / 365, iv_ce=0.15, iv_pe=0.15, context=context)
    event = EventBrain().analyze(date(2026, 8, 21), date(2026, 8, 14), 15.0, context=context)
    return regime, structure, liquidity, volatility, greeks, event


def _build_snapshot(execution_mode: str, reality_ref: str = "real-snap-abc") -> MarketIntelligenceSnapshot:
    context = IntelligenceContext(
        as_of_time=FIXED_TIME, execution_mode=execution_mode, reality_snapshot_reference=reality_ref,
    )
    regime, structure, liquidity, volatility, greeks, event = _build_readings(context)
    return build_market_intelligence_snapshot(
        context=context, created_at=datetime(2026, 8, 14, 10, 30, 5, tzinfo=IST),
        regime=regime, structure=structure, liquidity=liquidity,
        volatility=volatility, greeks=greeks, event=event,
    )


# ---------------------------------------------------------------------- #
# Property 1: determinism
# ---------------------------------------------------------------------- #
def test_same_inputs_produce_identical_fingerprint():
    snap_a = _build_snapshot(EXECUTION_MODE_LIVE)
    snap_b = _build_snapshot(EXECUTION_MODE_LIVE)

    assert snap_a.intelligence_snapshot_id == snap_b.intelligence_snapshot_id
    assert snap_a.fingerprint() == snap_b.fingerprint() == snap_a.intelligence_snapshot_id


def test_fingerprint_recomputation_matches_stored_id():
    snap = _build_snapshot(EXECUTION_MODE_LIVE)
    assert snap.fingerprint() == snap.intelligence_snapshot_id


def test_created_at_excluded_from_fingerprint():
    context = IntelligenceContext(as_of_time=FIXED_TIME, execution_mode=EXECUTION_MODE_LIVE, reality_snapshot_reference="ref")
    regime, structure, liquidity, volatility, greeks, event = _build_readings(context)
    snap_a = build_market_intelligence_snapshot(
        context=context, created_at=datetime(2026, 8, 14, 10, 30, 5, tzinfo=IST),
        regime=regime, structure=structure, liquidity=liquidity, volatility=volatility, greeks=greeks, event=event,
    )
    snap_b = build_market_intelligence_snapshot(
        context=context, created_at=datetime(2026, 8, 14, 11, 45, 0, tzinfo=IST),  # different wall-clock build time
        regime=regime, structure=structure, liquidity=liquidity, volatility=volatility, greeks=greeks, event=event,
    )
    assert snap_a.created_at != snap_b.created_at
    assert snap_a.intelligence_snapshot_id == snap_b.intelligence_snapshot_id


def test_different_reality_reference_changes_fingerprint():
    snap_a = _build_snapshot(EXECUTION_MODE_LIVE, reality_ref="snap-A")
    snap_b = _build_snapshot(EXECUTION_MODE_LIVE, reality_ref="snap-B")
    assert snap_a.intelligence_snapshot_id != snap_b.intelligence_snapshot_id


# ---------------------------------------------------------------------- #
# Property 2: replay equivalence
# ---------------------------------------------------------------------- #
def test_live_and_historical_replay_produce_identical_fingerprint_thesis_posture():
    live_snap = _build_snapshot(EXECUTION_MODE_LIVE)
    replay_snap = _build_snapshot(EXECUTION_MODE_HISTORICAL_REPLAY)

    assert live_snap.intelligence_snapshot_id == replay_snap.intelligence_snapshot_id
    assert live_snap.thesis == replay_snap.thesis
    assert live_snap.posture == replay_snap.posture
    assert live_snap.as_of_time == replay_snap.as_of_time == FIXED_TIME
    # Only the caller-supplied execution_mode itself differs.
    assert live_snap.execution_mode != replay_snap.execution_mode


# ---------------------------------------------------------------------- #
# Property 3: composition invariants
# ---------------------------------------------------------------------- #
def test_brain_readings_preserved_verbatim_not_reinterpreted():
    context = IntelligenceContext(as_of_time=FIXED_TIME, execution_mode=EXECUTION_MODE_LIVE)
    regime, structure, liquidity, volatility, greeks, event = _build_readings(context)
    snap = build_market_intelligence_snapshot(
        context=context, created_at=FIXED_TIME,
        regime=regime, structure=structure, liquidity=liquidity, volatility=volatility, greeks=greeks, event=event,
    )
    assert snap.regime is regime
    assert snap.structure is structure
    assert snap.liquidity is liquidity
    assert snap.volatility is volatility
    assert snap.greeks is greeks
    assert snap.event is event


def test_evidence_bundle_items_trace_back_to_real_brain_evidence():
    snap = _build_snapshot(EXECUTION_MODE_LIVE)
    assert len(snap.evidence_bundle.items) > 0
    for item in snap.evidence_bundle.items:
        # Every item name is prefixed with its owning brain -- no
        # collision, no ambiguity about where a metric came from.
        assert "." in item.metric_name
        brain_name = item.metric_name.split(".", 1)[0]
        assert brain_name in ("regime", "structure", "liquidity", "volatility", "greeks", "event")
    # Real reality reference threaded through, never fabricated.
    assert "real-snap-abc" in snap.evidence_bundle.source_references


def test_evidence_bundle_confidence_is_the_weakest_link_not_an_average():
    snap = _build_snapshot(EXECUTION_MODE_LIVE)
    all_confidences = [snap.regime.confidence, snap.structure.confidence, snap.liquidity.confidence,
                        snap.volatility.confidence, snap.greeks.confidence, snap.event.confidence]
    assert snap.evidence_bundle.confidence == min(all_confidences)


def test_contradiction_score_counts_real_data_quality_not_a_fabricated_directional_score():
    snap = _build_snapshot(EXECUTION_MODE_LIVE)
    total = snap.contradiction.supporting_domain_count + snap.contradiction.contradicting_domain_count
    assert total == 6  # all six in-scope brains counted, exactly once each
    assert 0.0 <= snap.contradiction.overall <= 1.0


def test_posture_is_a_documented_mapping_not_a_new_signal():
    snap = _build_snapshot(EXECUTION_MODE_LIVE)
    assert isinstance(snap.posture, MarketPosture)
    # EVENT_RISK must win over regime shape when expiry/VIX say so.
    context = IntelligenceContext(as_of_time=FIXED_TIME, execution_mode=EXECUTION_MODE_LIVE)
    event_risk_event = EventBrain().analyze(date(2026, 8, 14), date(2026, 8, 14), 25.0, context=context)  # expiry day + high VIX
    regime, structure, liquidity, volatility, greeks, _ = _build_readings(context)
    snap_risk = build_market_intelligence_snapshot(
        context=context, created_at=FIXED_TIME, regime=regime, structure=structure, liquidity=liquidity,
        volatility=volatility, greeks=greeks, event=event_risk_event,
    )
    assert snap_risk.posture == MarketPosture.EVENT_RISK


def test_invalidation_conditions_use_real_numeric_levels_never_invented():
    snap = _build_snapshot(EXECUTION_MODE_LIVE)
    # These exact numeric strikes came from the real StructureReading fed in.
    joined = " ".join(snap.invalidation_conditions)
    assert str(snap.structure.resistance_strike) in joined or snap.structure.resistance_strike is None
    assert str(snap.structure.support_strike) in joined or snap.structure.support_strike is None
    assert snap.invalidation_conditions == snap.thesis.invalidation_conditions  # single source of truth


def test_intelligence_snapshot_id_does_not_collide_with_other_identity_names():
    """Phase 19.1.1's own confirmed 3-way collision risk (mic_adapter /
    mil_next / trading_brain.ontology) -- the bare word 'snapshot_id' is
    never used as a field name anywhere on this object."""
    field_names = MarketIntelligenceSnapshot.__dataclass_fields__.keys()
    assert "snapshot_id" not in field_names
    assert "intelligence_snapshot_id" in field_names


def test_out_of_scope_brains_are_not_referenced():
    """No PremiumReading/BehaviourReading field exists anywhere on this
    object -- Phase 19.0.1/19.1's established exclusion, reaffirmed in
    Phase 19.2.2/19.2.3, holds through Phase 19.3 too."""
    field_names = set(MarketIntelligenceSnapshot.__dataclass_fields__.keys())
    assert "premium" not in field_names
    assert "behaviour" not in field_names
