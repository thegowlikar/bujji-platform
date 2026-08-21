"""Paper Intelligence Mode — BUJJI Options OS v3, Phase 5 (Nervous
System Integration).

Task 1's "Paper Intelligence Mode": run live market sessions, generate
Decision Artifacts only, no execution. This module is the missing
wire connecting `IntelligenceCycleRecorder.last_evidence` (real,
already-built MDI/PSI/MSSI/VSB/MPPI/Consensus/Liquidity/
PremiumBehaviour, confirmed by direct trace of `record_cycle()`'s own
body) through the exact chain Task 2 specifies:

    MSI assessments -> market_thesis -> intelligence_orchestrator
    -> TradingSessionGovernor

STRUCTURALLY NO EXECUTION IS POSSIBLE HERE -- not just disabled by a
flag. This module never imports `PaperBroker`, `TradingSessionGovernor`
itself, `msi_trade_construction`, or any order/broker type -- confirmed
by this file's own import list. "Connect to TradingSessionGovernor"
means calling its own real, already-tested, already-protected local
decision function (`production_runtime.trading_session_governor.
strategy_selector.select_strategy()`) directly -- the same real
function `TradingSessionGovernor.select_and_lock_strategy()` itself
calls -- WITHOUT touching the Governor's own stateful session/lock
lifecycle (that belongs to Paper TRADING Mode, a later milestone).

WHY intelligence_orchestrator's OWN strategy evaluation is honestly
NO_TRADE here: `evaluate_strategies()` requires a real Trading Brain
v3 `MarketStateAssessment` (built via `evidence_interpreter` from MIC
v2 layers) -- a genuinely different, unrelated evidence source that
`IntelligenceCycleRecorder`'s pipeline does not produce (confirmed,
Phase 1's own architecture audit). Rather than fabricate one, this
module passes `market_state_assessment=None` -- `intelligence_
orchestrator` already handles that honestly (skips evaluation, real
NO_TRADE). `orchestrate()`'s real market-thesis narration and
composed evidence are still fully real and useful for the artifact.

PHASE 6 ADDITION -- `perception_snapshot` (optional): a real
`MarketIntelligenceSnapshot`. NOT routed through market_thesis or
CycleEvidence -- confirmed by direct trace this phase that its own
build path (`shadow_runtime.intelligence_pipeline_adapter.
build_intelligence_heartbeat_cycle()`) imports ONLY the legacy
RegimeBrain/StructureBrain/VolatilityBrain/GreeksBrain/EventBrain
lineage, never MDI/PSI/MSSI/VSB/MPPI/Consensus/msi_trade_thesis --
there is no real MSI-shaped data inside it to honestly extract into a
`CycleEvidence`. It is instead attached to the artifact as an
independent, disclosed perception cross-reference (see decision_
artifact's own docstring) -- exactly what "MarketIntelligenceSnapshot
remains the perception layer, do not merge it" means in practice.
Both objects ARE guaranteed to describe the same real cycle when
supplied together: `intelligence_pipeline_enabled` structurally
requires `intelligence_cycle_enabled` (confirmed via `ShadowSession
Runner.__init__`'s own docstring), so whenever a real
`MarketIntelligenceSnapshot` exists for a cycle, the SAME cycle's real
`CycleEvidence` exists too.
"""
from __future__ import annotations

from datetime import datetime
from typing import Callable, Optional

from bujji.decision_artifact import DecisionArtifact, build_decision_artifact
from bujji.intelligence_orchestrator import DecisionContext, orchestrate
from bujji.market_state.cycle_evidence import CycleEvidence
from bujji.market_thesis import assess as assess_market_thesis
from bujji.production_runtime.market_thesis_regime_provider import MarketThesisRegimeProvider
from bujji.production_runtime.regime_provider import MissingRegimeInputError
from bujji.production_runtime.trading_session_governor.strategy_selector import select_strategy

Clock = Callable[[], datetime]


def _real_clock() -> datetime:
    return datetime.now()


def run_cycle(
    evidence: CycleEvidence, *, session_id: Optional[str] = None, clock: Clock = _real_clock,
    perception_snapshot=None,
) -> DecisionArtifact:
    """One full Paper Intelligence Mode cycle -- pure composition of
    already-real engines, no new intelligence, no execution.
    `evidence`: `IntelligenceCycleRecorder.last_evidence` after a real
    `record_cycle()` call (or an equivalent hand-built `CycleEvidence`
    in a test). `perception_snapshot` (optional): a real
    `MarketIntelligenceSnapshot` from the SAME cycle (e.g.
    `ShadowSessionRunner`'s own `_previous_intelligence_snapshot` at
    the point this function is called) -- attached to the artifact for
    cross-reference only, never merged into the thesis (see module
    docstring)."""
    thesis = assess_market_thesis(
        psi=evidence.psi, mssi=evidence.mssi, mdi=evidence.mdi, mppi=evidence.mppi,
        vsb=evidence.vsb, consensus=evidence.consensus, liquidity=evidence.liquidity,
        timestamp=evidence.timestamp,
    )

    context = DecisionContext(
        timestamp=evidence.timestamp, psi=evidence.psi, mssi=evidence.mssi, mdi=evidence.mdi,
        mppi=evidence.mppi, vsb=evidence.vsb, consensus=evidence.consensus, liquidity=evidence.liquidity,
        premium_behaviour=evidence.premium_behaviour, market_state_assessment=None,
    )
    trace = orchestrate(context, clock=clock)

    governor_selection = None
    volatility_regime = evidence.vsb.volatility_regime if evidence.vsb is not None else None
    provider = MarketThesisRegimeProvider(thesis, volatility_regime)
    try:
        trend, vol = provider.get_regime()
        governor_selection = select_strategy(trend, vol, clock)
    except MissingRegimeInputError:
        governor_selection = None

    return build_decision_artifact(
        trace, thesis=thesis, ranked=None, governor_selection=governor_selection,
        perception_snapshot=perception_snapshot, session_id=session_id,
    )
