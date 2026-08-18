"""Decision Artifact engine — BUJJI Options OS v3.

`build_decision_artifact()` composes five already-real objects into
one immutable record. It computes NOTHING beyond `learning_tag` (a
small, disclosed, four-branch classification over an already-real
`outcome_direction` value) and `trade_legs_summary` (a human-readable
string rendering of already-real `StrikeLeg` fields, never a new
number). Every other field is a direct pass-through:

  - `intelligence_orchestrator.DecisionTrace` (Phase 4) -- decision
    metadata, outcome, full narration.
  - the real `MarketThesisAssessment` (`bujji.market_thesis`, Phase 3)
    that `generate_thesis()` produced alongside the trace -- the trace
    itself only stores its `assessment_id`, not the object, so the
    caller supplies it directly here rather than this module parsing
    it back out of `trace.steps`' human-readable narration text (an
    earlier draft of this function did exactly that string-parsing and
    it was replaced -- fragile, and unnecessary when the real object
    is trivially available at every real call site).
  - the real `RankedCandidates` object `intelligence_orchestrator.
    evaluate_strategies()` itself already returned alongside the trace
    (same reasoning -- the trace only stores its ID).
  - an optional real `TradeConstructionAssessment` (msi_trade_
    construction, protected, unmodified) -- when a trade was actually
    constructed.
  - an optional real `OutcomeMemoryRecord` (Phase 15N) -- once the
    position this decision led to has closed.
  - an optional real `StrategySelectionResult` (Phase 5 -- Trading
    SessionGovernor's own local, narrow strategy_selector.
    select_strategy()) -- kept in SEPARATE governor_* fields rather
    than overwriting selected_strategy/confidence above: these are two
    real, distinct decisions from two different lineages (the
    11-registry Trading Brain v3 view vs. the 2-strategy premium-
    selling-only view that actually executes), and conflating them
    would misrepresent which one the system will actually act on.
  - an optional real `MarketIntelligenceSnapshot` (Phase 6 --
    `bujji.intelligence.market_intelligence_snapshot`) -- kept in
    SEPARATE perception_* fields, never merged into market_regime/
    directional_bias above. This snapshot's own `thesis` field is a
    genuinely independent read, composed from a different brain
    lineage (RegimeBrain/StructureBrain/VolatilityBrain/GreeksBrain/
    EventBrain) than market_thesis's own (MDI/PSI/MSSI/VSB/MPPI/
    Consensus/msi_trade_thesis) -- recording both, never reconciling
    them into one, is the whole point of keeping perception and
    interpretation as two separate, never-merged layers.
"""
from __future__ import annotations

import hashlib
from typing import Optional, Tuple

from bujji.outcome_attribution.models import OUTCOME_BREAKEVEN, OUTCOME_LOSS, OUTCOME_PROFIT

from .models import DecisionArtifact

PROVENANCE = "bujji.decision_artifact.engine.build_decision_artifact"
SCHEMA_VERSION = "1.0.0"

_OUTCOME_TO_LEARNING_TAG = {OUTCOME_PROFIT: "WIN", OUTCOME_LOSS: "LOSS", OUTCOME_BREAKEVEN: "BREAKEVEN"}


def _derive_learning_tag(trade_constructed: bool, outcome) -> str:
    if outcome is None:
        return "OPEN" if trade_constructed else "NO_TRADE"
    return _OUTCOME_TO_LEARNING_TAG.get(outcome.outcome_direction, "UNKNOWN")


def _leg_summary(construction) -> Tuple[str, ...]:
    if construction is None or not getattr(construction, "constructed", False):
        return ()
    return tuple(f"{leg.side} {leg.option_type}{int(leg.strike)}" for leg in construction.legs)


def _artifact_id(fields: Tuple[str, ...]) -> str:
    seed = "###".join(fields)
    return "DA-" + hashlib.md5(seed.encode()).hexdigest()[:24]


def _perception_posture(perception_snapshot) -> Optional[str]:
    if perception_snapshot is None or perception_snapshot.posture is None:
        return None
    return getattr(perception_snapshot.posture, "value", None) or str(perception_snapshot.posture)


def build_decision_artifact(
    trace, thesis=None, ranked=None, construction=None, outcome=None, governor_selection=None,
    perception_snapshot=None, *, session_id: Optional[str] = None,
) -> DecisionArtifact:
    """`trace`: a real `intelligence_orchestrator.models.DecisionTrace`.
    `thesis`: the real `MarketThesisAssessment` `generate_thesis()`
    produced in the same `orchestrate()` call (optional -- when
    omitted, the thesis-summary fields are honestly None rather than
    guessed). `ranked`: the real `RankedCandidates` object from the
    same `evaluate_strategies()` call (optional, same reasoning).
    `construction`/`outcome`: optional real objects, supplied once
    each stage has actually happened. `governor_selection`: an
    optional real `StrategySelectionResult` (TradingSessionGovernor's
    own local `strategy_selector.select_strategy()`) -- stored in
    separate governor_* fields, never merged into selected_strategy/
    confidence above (see module docstring). `perception_snapshot`: an
    optional real `MarketIntelligenceSnapshot` -- stored in separate
    perception_* fields, never merged into market_regime/
    directional_bias above (see module docstring)."""
    candidate_strategies: Tuple[str, ...] = ()
    rejected_strategies: Tuple[str, ...] = ()
    if ranked is not None:
        candidate_strategies = tuple(r.strategy_id for r in ranked.rankings)
        rejected_strategies = ranked.rejected_ineligible

    trade_constructed = construction is not None and getattr(construction, "constructed", False)
    learning_tag = _derive_learning_tag(trade_constructed, outcome)

    artifact_id = _artifact_id((
        trace.decision_id, trace.outcome.decision_status, trace.outcome.selected_strategy or "NONE",
        "TRUE" if trade_constructed else "FALSE", learning_tag,
        getattr(governor_selection, "selected_strategy", None) or "NONE",
    ))

    return DecisionArtifact(
        decision_id=artifact_id, timestamp=trace.timestamp, session_id=session_id,
        market_thesis_assessment_id=trace.market_thesis_assessment_id,
        market_regime=getattr(thesis, "market_regime", None),
        directional_bias=getattr(thesis, "directional_bias", None),
        volatility_environment=getattr(thesis, "volatility_environment", None),
        premium_environment=getattr(thesis, "premium_environment", None),
        candidate_strategies=candidate_strategies, rejected_strategies=rejected_strategies,
        selected_strategy=trace.outcome.selected_strategy, selection_reason=trace.outcome.reasons,
        confidence=trace.outcome.confidence,
        trade_constructed=trade_constructed, trade_legs_summary=_leg_summary(construction),
        entry_timestamp=getattr(outcome, "entry_timestamp", None), exit_timestamp=getattr(outcome, "exit_timestamp", None),
        outcome_direction=getattr(outcome, "outcome_direction", None), realized_pnl=getattr(outcome, "realized_pnl", None),
        learning_tag=learning_tag,
        steps=trace.steps, supporting_assessment_ids=trace.supporting_assessment_ids,
        provenance=PROVENANCE, schema_version=SCHEMA_VERSION,
        governor_selected_strategy=getattr(governor_selection, "selected_strategy", None),
        governor_trend_regime=getattr(governor_selection, "trend_regime", None),
        governor_volatility_regime=getattr(governor_selection, "volatility_regime", None),
        governor_confidence=getattr(governor_selection, "confidence", None),
        governor_reasoning=getattr(governor_selection, "reasoning", None),
        perception_snapshot_id=getattr(perception_snapshot, "intelligence_snapshot_id", None),
        perception_reality_fingerprint=getattr(perception_snapshot, "reality_fingerprint", None),
        perception_posture=_perception_posture(perception_snapshot),
        perception_contradiction_overall=(
            perception_snapshot.contradiction.overall
            if perception_snapshot is not None and perception_snapshot.contradiction is not None else None
        ),
        perception_primary_thesis=(
            perception_snapshot.thesis.primary_thesis
            if perception_snapshot is not None and perception_snapshot.thesis is not None else None
        ),
    )
