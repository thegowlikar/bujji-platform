"""LIVE vs HISTORICAL_REPLAY Equivalence Validation -- Shadow Runtime,
Phase 19.13.

Task 3, verbatim: "Same inputs must produce: same fingerprint, same
classification, same decision posture." This module runs the SAME
already-real `build_intelligence_heartbeat_cycle()` (Phase 19.10.1,
unmodified) twice against the identical `reality_snapshot`/
`spot_candles`/`as_of_time` -- once with `execution_mode=EXECUTION_MODE_LIVE`,
once with `EXECUTION_MODE_HISTORICAL_REPLAY` -- and compares the three
real outputs the task names. This is the SAME proof technique Phase
19.10.1's own real-data validation already used ad hoc against
production `historical_observations.db` data; this module makes it a
reusable, callable, tested function rather than a one-off validation
run.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from bujji.core.models import Candle
from bujji.intelligence.context import EXECUTION_MODE_HISTORICAL_REPLAY, EXECUTION_MODE_LIVE, IntelligenceContext
from bujji.market_phenomena import MarketPhenomenaAssessment
from bujji.market_reality_snapshot.models import MarketRealitySnapshot
from bujji.market_state_graph import MarketStateNode
from bujji.intelligence.market_intelligence_snapshot import MarketIntelligenceSnapshot

from .intelligence_pipeline_adapter import build_intelligence_heartbeat_cycle


@dataclass(frozen=True)
class LiveReplayEquivalenceReport:
    equivalent: bool
    live_intelligence_fingerprint: str
    replay_intelligence_fingerprint: str
    live_environment_classification: str
    replay_environment_classification: str
    live_decision_posture: str
    replay_decision_posture: str
    mismatches: tuple

    def to_dict(self) -> dict:
        return {
            "equivalent": self.equivalent,
            "live_intelligence_fingerprint": self.live_intelligence_fingerprint,
            "replay_intelligence_fingerprint": self.replay_intelligence_fingerprint,
            "live_environment_classification": self.live_environment_classification,
            "replay_environment_classification": self.replay_environment_classification,
            "live_decision_posture": self.live_decision_posture,
            "replay_decision_posture": self.replay_decision_posture,
            "mismatches": list(self.mismatches),
        }


def validate_live_replay_equivalence(
    *, reality_snapshot: MarketRealitySnapshot, spot_candles: List[Candle], as_of_time,
    previous_market_intelligence_snapshot: Optional[MarketIntelligenceSnapshot] = None,
    previous_phenomena: Optional[MarketPhenomenaAssessment] = None,
    previous_state_node: Optional[MarketStateNode] = None,
) -> LiveReplayEquivalenceReport:
    """Pure, no IO -- both calls compose from the SAME already-real
    `reality_snapshot`/`spot_candles`, never a second fetch. `previous_*`
    (if supplied) must be identical for both runs too, since they are
    also real content this composition depends on."""
    live_context = IntelligenceContext(
        as_of_time=as_of_time, execution_mode=EXECUTION_MODE_LIVE,
        reality_snapshot_reference=reality_snapshot.fingerprint(),
    )
    replay_context = IntelligenceContext(
        as_of_time=as_of_time, execution_mode=EXECUTION_MODE_HISTORICAL_REPLAY,
        reality_snapshot_reference=reality_snapshot.fingerprint(),
    )

    live_cycle = build_intelligence_heartbeat_cycle(
        reality_snapshot=reality_snapshot, spot_candles=spot_candles, context=live_context,
        previous_market_intelligence_snapshot=previous_market_intelligence_snapshot,
        previous_phenomena=previous_phenomena, previous_state_node=previous_state_node,
    )
    replay_cycle = build_intelligence_heartbeat_cycle(
        reality_snapshot=reality_snapshot, spot_candles=spot_candles, context=replay_context,
        previous_market_intelligence_snapshot=previous_market_intelligence_snapshot,
        previous_phenomena=previous_phenomena, previous_state_node=previous_state_node,
    )

    live_fp = live_cycle.market_intelligence_snapshot.intelligence_snapshot_id
    replay_fp = replay_cycle.market_intelligence_snapshot.intelligence_snapshot_id
    live_env = live_cycle.environment.environment_type.value
    replay_env = replay_cycle.environment.environment_type.value
    live_posture = live_cycle.decision_intelligence.recommended_posture.value
    replay_posture = replay_cycle.decision_intelligence.recommended_posture.value

    mismatches = []
    if live_fp != replay_fp:
        mismatches.append(f"intelligence_fingerprint: live={live_fp!r} != replay={replay_fp!r}")
    if live_env != replay_env:
        mismatches.append(f"environment_classification: live={live_env!r} != replay={replay_env!r}")
    if live_posture != replay_posture:
        mismatches.append(f"decision_posture: live={live_posture!r} != replay={replay_posture!r}")

    return LiveReplayEquivalenceReport(
        equivalent=len(mismatches) == 0,
        live_intelligence_fingerprint=live_fp, replay_intelligence_fingerprint=replay_fp,
        live_environment_classification=live_env, replay_environment_classification=replay_env,
        live_decision_posture=live_posture, replay_decision_posture=replay_posture,
        mismatches=tuple(mismatches),
    )
