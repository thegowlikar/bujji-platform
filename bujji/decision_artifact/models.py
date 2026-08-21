"""Decision Artifact models — frozen, immutable, append-only record.
One artifact per decision cycle -- the single evidence trail a human
(or a future learning system) reads to answer "why did Bujji do
that?" without re-deriving anything. Every field here is either a
direct pass-through of an already-real object's own field, or a
small, disclosed synthesis (`learning_tag`) -- see engine.py.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class DecisionArtifact:
    decision_id: str
    timestamp: str
    session_id: Optional[str]

    # --- Market Thesis (from intelligence_orchestrator.DecisionTrace) ---
    market_thesis_assessment_id: Optional[str]
    market_regime: Optional[str]
    directional_bias: Optional[str]
    volatility_environment: Optional[str]
    premium_environment: Optional[str]

    # --- Strategy Evaluation (from the real RankedCandidates object) ---
    candidate_strategies: Tuple[str, ...]         # every strategy_id strategy_evaluator scored, best first.
    rejected_strategies: Tuple[str, ...]          # strategy_selector's own ineligible strategy_ids -- never re-scored.
    selected_strategy: Optional[str]
    selection_reason: Tuple[str, ...]
    confidence: str

    # --- Trade Construction (from an optional real TradeConstructionAssessment) ---
    trade_constructed: bool
    trade_legs_summary: Tuple[str, ...]           # e.g. "SELL CE22600" -- human-readable, derived from real StrikeLeg objects.

    # --- Outcome (from an optional real OutcomeMemoryRecord, once the position closes) ---
    entry_timestamp: Optional[str]
    exit_timestamp: Optional[str]
    outcome_direction: Optional[str]
    realized_pnl: Optional[float]
    learning_tag: str                             # WIN/LOSS/BREAKEVEN/OPEN/NO_TRADE/UNKNOWN -- see engine.py.

    # --- Full traceability ---
    steps: Tuple[str, ...]                        # verbatim DecisionTrace.steps narration.
    supporting_assessment_ids: Tuple[str, ...]
    provenance: str
    schema_version: str

    # --- Governor Selection (Phase 5) -- from an optional real
    # StrategySelectionResult (TradingSessionGovernor's own local,
    # narrow, IRON_CONDOR/IRON_FLY-only selector). Kept SEPARATE from
    # `selected_strategy` above (intelligence_orchestrator's 11-
    # registry view) rather than overwriting it -- these are two real,
    # distinct decisions from two different lineages; conflating them
    # would misrepresent which one actually executes. All default to
    # None so every artifact built before Phase 5 still hydrates
    # (`from_dict`) without change. ---
    governor_selected_strategy: Optional[str] = None
    governor_trend_regime: Optional[str] = None
    governor_volatility_regime: Optional[str] = None
    governor_confidence: Optional[str] = None
    governor_reasoning: Optional[str] = None

    # --- Perception Cross-Reference (Phase 6) -- from an optional real
    # `bujji.intelligence.market_intelligence_snapshot.
    # MarketIntelligenceSnapshot`. DELIBERATELY NOT merged into
    # market_regime/directional_bias/etc. above (those come from
    # market_thesis, the canonical interpretation layer) -- this
    # snapshot's OWN `thesis` field is a genuinely independent,
    # separately-composed read (a different brain lineage entirely --
    # RegimeBrain/StructureBrain/VolatilityBrain/GreeksBrain/EventBrain,
    # not MDI/PSI/MSSI/VSB/MPPI/Consensus). Recording it side-by-side,
    # never combined, is what "MarketIntelligenceSnapshot remains the
    # perception layer, market_thesis remains the interpretation layer,
    # do not merge them" means in practice -- two real, independent
    # opinions, both disclosed, never silently reconciled into one.
    perception_snapshot_id: Optional[str] = None
    perception_reality_fingerprint: Optional[str] = None
    perception_posture: Optional[str] = None
    perception_contradiction_overall: Optional[float] = None
    perception_primary_thesis: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "decision_id": self.decision_id, "timestamp": self.timestamp, "session_id": self.session_id,
            "market_thesis_assessment_id": self.market_thesis_assessment_id, "market_regime": self.market_regime,
            "directional_bias": self.directional_bias, "volatility_environment": self.volatility_environment,
            "premium_environment": self.premium_environment,
            "candidate_strategies": list(self.candidate_strategies), "rejected_strategies": list(self.rejected_strategies),
            "selected_strategy": self.selected_strategy, "selection_reason": list(self.selection_reason),
            "confidence": self.confidence,
            "trade_constructed": self.trade_constructed, "trade_legs_summary": list(self.trade_legs_summary),
            "entry_timestamp": self.entry_timestamp, "exit_timestamp": self.exit_timestamp,
            "outcome_direction": self.outcome_direction, "realized_pnl": self.realized_pnl,
            "learning_tag": self.learning_tag,
            "steps": list(self.steps), "supporting_assessment_ids": list(self.supporting_assessment_ids),
            "provenance": self.provenance, "schema_version": self.schema_version,
            "governor_selected_strategy": self.governor_selected_strategy,
            "governor_trend_regime": self.governor_trend_regime,
            "governor_volatility_regime": self.governor_volatility_regime,
            "governor_confidence": self.governor_confidence, "governor_reasoning": self.governor_reasoning,
            "perception_snapshot_id": self.perception_snapshot_id,
            "perception_reality_fingerprint": self.perception_reality_fingerprint,
            "perception_posture": self.perception_posture,
            "perception_contradiction_overall": self.perception_contradiction_overall,
            "perception_primary_thesis": self.perception_primary_thesis,
        }

    @staticmethod
    def from_dict(d: dict) -> "DecisionArtifact":
        return DecisionArtifact(
            decision_id=d["decision_id"], timestamp=d["timestamp"], session_id=d.get("session_id"),
            market_thesis_assessment_id=d.get("market_thesis_assessment_id"), market_regime=d.get("market_regime"),
            directional_bias=d.get("directional_bias"), volatility_environment=d.get("volatility_environment"),
            premium_environment=d.get("premium_environment"),
            candidate_strategies=tuple(d.get("candidate_strategies", ())),
            rejected_strategies=tuple(d.get("rejected_strategies", ())),
            selected_strategy=d.get("selected_strategy"), selection_reason=tuple(d.get("selection_reason", ())),
            confidence=d["confidence"],
            trade_constructed=d["trade_constructed"], trade_legs_summary=tuple(d.get("trade_legs_summary", ())),
            entry_timestamp=d.get("entry_timestamp"), exit_timestamp=d.get("exit_timestamp"),
            outcome_direction=d.get("outcome_direction"), realized_pnl=d.get("realized_pnl"),
            learning_tag=d["learning_tag"],
            steps=tuple(d.get("steps", ())), supporting_assessment_ids=tuple(d.get("supporting_assessment_ids", ())),
            provenance=d["provenance"], schema_version=d["schema_version"],
            governor_selected_strategy=d.get("governor_selected_strategy"),
            governor_trend_regime=d.get("governor_trend_regime"),
            governor_volatility_regime=d.get("governor_volatility_regime"),
            governor_confidence=d.get("governor_confidence"), governor_reasoning=d.get("governor_reasoning"),
            perception_snapshot_id=d.get("perception_snapshot_id"),
            perception_reality_fingerprint=d.get("perception_reality_fingerprint"),
            perception_posture=d.get("perception_posture"),
            perception_contradiction_overall=d.get("perception_contradiction_overall"),
            perception_primary_thesis=d.get("perception_primary_thesis"),
        )
