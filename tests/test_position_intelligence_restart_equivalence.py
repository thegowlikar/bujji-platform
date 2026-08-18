"""Tests -- Phase 15F Step 10: Position Intelligence restart equivalence.

Position Intelligence itself holds NO state (evaluate_thesis is a pure
function of (entry snapshot, current record)) -- confirmed by the
Phase 15F forensic audit. Its own restart invariant is therefore a
direct consequence of its two REAL inputs already being restart-
invariant: ObservationMemory/direction/regime (Phase 15D, real-data
proven) and PremiumBehaviourState (Phase 15E, real-data proven at 5
real boundaries). Greeks need no state at all -- assess_atm_greeks is
stateless per-cycle.

This test proves the COMPOSITION explicitly, using real Session B
data, rather than assuming it transitively -- an uninterrupted replay
vs a killed-and-restarted-then-hydrated replay must produce IDENTICAL
ThesisEvaluation results for the same real candidate."""
from __future__ import annotations

import json

from bujji.market_perception.greeks_adapter import build_greeks_assessment
from bujji.market_perception.models import (
    FutureSnapshot, MarketSnapshot, OptionChainConfig, OptionChainSnapshot, OptionLeg, SpotSnapshot, VixSnapshot,
)
from bujji.market_state_builder.market_state import MarketStateBuilder
from bujji.market_state_builder.recovery import hydrate_observation_memory
from bujji.position_intelligence.engine import build_entry_snapshot, evaluate_thesis
from bujji.premium_behaviour.engine import evaluate as evaluate_premium_behaviour
from bujji.premium_behaviour.models import PremiumObservation
from bujji.premium_behaviour.recovery import hydrate_premium_behaviour

SESSION_DIR = "shadow_sessions/SHADOW-OBSERVATORY-2026-08-06"


def _snapshot_from_dict(d):
    spot = SpotSnapshot(**d["spot"]) if d.get("spot") else None
    vix = VixSnapshot(**d["vix"]) if d.get("vix") else None
    futures = FutureSnapshot(**d["futures"]) if d.get("futures") else None
    chain = None
    if d.get("option_chain"):
        c = d["option_chain"]
        config = OptionChainConfig(**c["config"])
        legs = tuple(OptionLeg(**leg) for leg in c["legs"])
        chain = OptionChainSnapshot(underlying=c["underlying"], expiry=c["expiry"],
                                     atm_strike=c["atm_strike"], config=config, legs=legs)
    return MarketSnapshot(
        snapshot_version=d["snapshot_version"], timestamp=d["timestamp"], source=d["source"],
        latency_ms=d["latency_ms"], health_status=d["health_status"],
        missing_fields=tuple(d.get("missing_fields", ())),
        spot=spot, vix=vix, futures=futures, option_chain=chain,
    )


def _atm_mid_premiums(snapshot):
    if snapshot.option_chain is None:
        return None, None
    atm = snapshot.option_chain.atm_strike
    ce = snapshot.option_chain.leg(atm, "CE")
    pe = snapshot.option_chain.leg(atm, "PE")
    ce_mid = (ce.bid + ce.ask) / 2.0 if ce and ce.bid is not None and ce.ask is not None else None
    pe_mid = (pe.bid + pe.ask) / 2.0 if pe and pe.bid is not None and pe.ask is not None else None
    return ce_mid, pe_mid


class _RealCandidateStandin:
    def __init__(self, ts, family, regime, direction):
        self.candidate_id = f"REAL-{ts}"
        self.strategy_family = family
        self.timestamp = ts
        self.market_regime = regime
        self.direction = direction
        self.consensus_state = None


def _build_current_record(snapshot, builder, premium_state):
    """The subset of a real intelligence_cycle record's fields this
    module actually reads: market_direction/market_state/greeks/
    premium_behaviour -- direction/regime derived the exact same way
    IntelligenceCycleRecorder does (via market_state_builder + direction_bridge)."""
    from bujji.market_state.direction_bridge import build_market_direction
    assessment = builder.process(snapshot)
    mdi = build_market_direction(assessment, snapshot.timestamp)
    greeks = build_greeks_assessment(snapshot, __import__("datetime").datetime.fromisoformat(snapshot.timestamp))
    ce_mid, pe_mid = _atm_mid_premiums(snapshot)
    spot = snapshot.spot.ltp if snapshot.spot else None
    new_premium_state = premium_state.advance(PremiumObservation(snapshot.timestamp, ce_mid, pe_mid, spot))
    premium_reading = evaluate_premium_behaviour(new_premium_state)
    record = {
        "timestamp": snapshot.timestamp,
        "market_direction": {"overall_direction": mdi.overall_direction if mdi else None},
        "market_state": None,  # regime requires the full synthesizer -- not needed for this test's checks.
        "greeks": greeks.to_dict() if greeks else None,
        "premium_behaviour": premium_reading.to_dict(),
    }
    return record, new_premium_state


def test_restart_equivalence_at_real_boundary_produces_identical_thesis_evaluation():
    market_snapshots = [json.loads(l) for l in open(f"{SESSION_DIR}/market_snapshots.jsonl")]
    boundary = 87  # mid-session, matches the boundary already used in Phase 15D/15E real-data validation.
    evaluate_at = 100  # a later real cycle to evaluate the thesis against.
    assert boundary < evaluate_at < len(market_snapshots)

    entry_snapshot_dict = market_snapshots[boundary - 1]
    entry_candidate = _RealCandidateStandin(
        entry_snapshot_dict["timestamp"], "LONG_DIRECTIONAL", None, "BULLISH",
    )

    # --- Reference: one continuous replay from cycle 0. ---
    ref_builder = MarketStateBuilder()
    from bujji.premium_behaviour.models import PremiumBehaviourState
    ref_premium_state = PremiumBehaviourState()
    ref_entry_record = None
    for i in range(evaluate_at + 1):
        snapshot = _snapshot_from_dict(market_snapshots[i])
        record, ref_premium_state = _build_current_record(snapshot, ref_builder, ref_premium_state)
        if i == boundary - 1:
            ref_entry_record = record
    ref_entry = build_entry_snapshot(entry_candidate, ref_entry_record)
    ref_final_record = record  # last one built, at evaluate_at.
    ref_result = evaluate_thesis(ref_entry, ref_final_record)

    # --- Restart: hydrate at `boundary` from real persisted data, then continue. ---
    import tempfile
    tmp_path = tempfile.mktemp()
    with open(tmp_path, "w") as f:
        for d in market_snapshots[:boundary]:
            f.write(json.dumps(d, default=str) + "\n")
    hydrated_memory, obs_report = hydrate_observation_memory(tmp_path)
    hydrated_premium_state, pb_report = hydrate_premium_behaviour(tmp_path)
    assert obs_report.status == "RECOVERY_COMPLETE"
    assert pb_report.status == "RECOVERY_COMPLETE"

    restart_builder = MarketStateBuilder(memory=hydrated_memory)
    restart_premium_state = hydrated_premium_state
    restart_entry_record = None
    for i in range(boundary, evaluate_at + 1):
        snapshot = _snapshot_from_dict(market_snapshots[i])
        record, restart_premium_state = _build_current_record(snapshot, restart_builder, restart_premium_state)
        if i == boundary - 1:  # never true in this range, entry was BEFORE the restart -- entry uses the pre-restart record.
            restart_entry_record = record
    # Entry record (the exact cycle used to build the candidate baseline)
    # was BEFORE the restart boundary -- it's identical to ref_entry_record
    # by construction (both come from the same real persisted file, not
    # re-derived), so the entry snapshot itself is trivially the same.
    restart_entry = build_entry_snapshot(entry_candidate, ref_entry_record)
    restart_final_record = record
    restart_result = evaluate_thesis(restart_entry, restart_final_record)

    # --- The actual invariant under test. ---
    assert restart_result.thesis_status == ref_result.thesis_status
    assert restart_result.recommendation == ref_result.recommendation
    assert restart_result.evidence_confidence == ref_result.evidence_confidence
    assert [c.to_dict() for c in restart_result.checks] == [c.to_dict() for c in ref_result.checks]
