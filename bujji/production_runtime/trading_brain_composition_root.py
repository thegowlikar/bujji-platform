"""Trading Brain Composition Root -- BUJJI Options OS v3, Gate F.1.0.

PURPOSE: one dependency graph, one frozen object, exactly mirroring
the existing `production_runtime.composition_root.CompositionRoot`'s
own role for the legacy `run_shadow()` chain -- but wiring the NEW
Trading Brain path (MSI Trade Construction -> E.1 -> E.2 -> E.3 ->
D.1-D.6 -> D.4 -> PaperBroker) instead. This is a SEPARATE, additive
composition root: it does not modify or replace the existing
`CompositionRoot`, and the legacy `run_shadow()` chain is left
completely untouched (verified via AST-import-check tests: this
module never imports `production_runtime.runtime`/`composition_root`).

Every field here is an ALREADY-EXISTING class from a completed gate
(D.1-D.6, E.1-E.3, F.0) or already-existing runtime infrastructure
(PaperBroker, EventBus, Position Group Journal) -- this file
constructs nothing new of its own, it only bundles references,
exactly matching E.2/E.3's own "assembles references, owns none of
them" discipline.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Optional

from bujji.broker.paper import PaperBroker
from bujji.core.event_bus import EventBus
from bujji.journal.position_group_journal import PositionGroupJournal

from bujji.trading_brain.risk_governor.capital_safety_governor import CapitalSafetySnapshot, CapitalSafetyThresholds
from bujji.trading_brain.risk_governor.portfolio_risk_aggregator import PortfolioRiskThresholds
from bujji.trading_brain.risk_governor.risk_budget_governor import RiskPolicy
from bujji.trading_brain.risk_governor.position_lifecycle_intelligence import PositionHealthThresholds
from bujji.trading_brain.risk_governor.adaptive_risk_memory import AdaptiveRiskMemory
from bujji.trading_brain.risk_governor.margin_calibration_runner import MarginCalibrationStore
from bujji.trading_brain.risk_governor.live_risk_context_provider import LiveRiskContextProvider

from .runtime_state_machine import RuntimeStateMachine
from .shadow_trade_timeline import ShadowTradeTimeline

Clock = Callable[[], datetime]


@dataclass(frozen=True)
class TradingBrainCompositionRoot:
    # Terminal execution layer -- PaperBroker only, see module docstring
    # and the runtime's own AST-verified "no live broker" safety test.
    broker: PaperBroker

    # Infrastructure, all reused verbatim -- never redesigned.
    event_bus: EventBus
    timeline: ShadowTradeTimeline
    runtime_state_machine: RuntimeStateMachine
    journal: PositionGroupJournal
    clock: Clock

    # E.3's own live provider (already wires E.2's GovernorContextBuilder
    # + E.1's strategy adapter internally -- reused, never re-implemented).
    live_risk_context_provider: LiveRiskContextProvider

    # Governor configuration -- caller-owned, matching D.1-D.4's own
    # "Optional thresholds, illustrative defaults inside each governor"
    # discipline established since Gate D.1.
    capital_safety_thresholds: Optional[CapitalSafetyThresholds]
    portfolio_risk_thresholds: Optional[PortfolioRiskThresholds]
    risk_policy: RiskPolicy
    position_health_thresholds: Optional[PositionHealthThresholds]

    # Contract-bridge configuration -- see trading_brain_runtime.py's
    # own docstring for why quantity = leg.ratio * exchange_lot_size *
    # approved_lots is computed explicitly there, not inferred here.
    underlying: str
    exchange_lot_size: int
    instrument_type: str
    product_type: str


def build_trading_brain_composition_root(
    broker: PaperBroker,
    journal: PositionGroupJournal,
    margin_provider: Any,
    capital_snapshot_provider: Callable[[], CapitalSafetySnapshot],
    memory: AdaptiveRiskMemory,
    clock: Clock,
    underlying: str,
    exchange_lot_size: int,
    instrument_type: str = "OPTIDX",
    product_type: str = "MARGIN",
    market_regime_provider: Optional[Callable[[], Optional[str]]] = None,
    calibration_store: Optional[MarginCalibrationStore] = None,
    cache_ttl_seconds: Optional[float] = None,
    capital_safety_thresholds: Optional[CapitalSafetyThresholds] = None,
    portfolio_risk_thresholds: Optional[PortfolioRiskThresholds] = None,
    risk_policy: Optional[RiskPolicy] = None,
    position_health_thresholds: Optional[PositionHealthThresholds] = None,
    initial_state=None,
) -> TradingBrainCompositionRoot:
    """Pure construction/wiring -- no decision, no validation beyond
    what each already-existing constructor performs on its own."""
    from .runtime_state_machine import RuntimeState

    event_bus = EventBus()
    timeline = ShadowTradeTimeline()
    timeline.attach(event_bus)
    runtime_state_machine = RuntimeStateMachine(
        event_bus, clock, initial=initial_state if initial_state is not None else RuntimeState.INITIALIZING,
    )
    live_risk_context_provider = LiveRiskContextProvider(
        journal=journal, margin_provider=margin_provider, capital_snapshot_provider=capital_snapshot_provider,
        memory=memory, market_regime_provider=market_regime_provider, calibration_store=calibration_store,
        cache_ttl_seconds=cache_ttl_seconds,
    )
    return TradingBrainCompositionRoot(
        broker=broker, event_bus=event_bus, timeline=timeline, runtime_state_machine=runtime_state_machine,
        journal=journal, clock=clock, live_risk_context_provider=live_risk_context_provider,
        capital_safety_thresholds=capital_safety_thresholds, portfolio_risk_thresholds=portfolio_risk_thresholds,
        risk_policy=risk_policy or RiskPolicy(), position_health_thresholds=position_health_thresholds,
        underlying=underlying, exchange_lot_size=exchange_lot_size, instrument_type=instrument_type,
        product_type=product_type,
    )
