"""Intelligence Cycle Recorder -- Shadow Campaign v2, Continuous
Intelligence Observatory.

Orchestrates ONE complete pass of the existing understanding pipeline
per cycle -- MarketStateAssessment -> MarketDirection -> Volatility
Structure -> MarketState -> Consensus -> Opportunity -> Trade Thesis ->
Strategy Eligibility -- and serializes the raw result for persistence.

Calls EVERY existing bridge exactly as already built (Phases 3A-6B),
never reimplemented: MarketStateBuilder, market_state.direction_bridge,
market_perception.msi_adapter, market_perception.intelligence_adapter,
market_state.synthesizer, market_state.domain_view_adapter,
msi_consensus.compute_consensus, msi_decision_synthesis.synthesize,
market_state.trade_thesis_bridge, market_state.strategy_eligibility_bridge.

Also calls, as of 2026-08-06, three further DESCRIPTION-only MSI
engines directly (no adapter needed -- each already accepts the real
objects this recorder builds above): msi_strategy_selection_foundation.
assess_all_families(), msi_strategy_selector.select_strategy(), and
msi_trade_intent.determine_trade_intent(). All three produce records of
what Bujji WOULD do -- a suitability read, a single selected family
with full alternative-rejection reasoning, and a trade intent
description (market bias, volatility bias, invalidation conditions) --
never an action. None of the three new calls requires a new broker
request: every input (psi/mssi/mdi/mppi/vsb/consensus/opportunity/
eligibility) is already built earlier in this same cycle.

Phase 9 follow-up (Liquidity Intelligence Bridge): also computes a real
`LiquidityReading` each cycle via `bujji.intelligence.liquidity_brain.
LiquidityBrain`, from the ATM CE/PE bid/ask already present on
`snapshot.option_chain` -- zero new broker calls, mirrors exactly how
`vsb` was already threaded into `assess_all_families()`. Passed through
to `assess_all_families()` so `DOMAIN_LIQUIDITY` (SYNTHETIC, IRON_CONDOR,
IRON_FLY) resolves to a real SUITABLE/UNSUITABLE read instead of an
unconditional INSUFFICIENT_EVIDENCE. Persisted on the record as
`"liquidity"` for transparency, independent of whether any family
actually required it this cycle.

Trade Construction, Risk Governor, and Execution remain explicitly OUT
OF SCOPE and are not imported anywhere in this module or reachable from
it -- there is no code path here that constructs an option leg, sizes
a position, allocates capital, or calls a broker order/position/margin
method. Broker access overall is limited to the same read-only calls
already used by market_perception/market_state_builder (get_spot/
get_vix/get_option_chain/get_futures_quote/get_recent_candles), all via
existing adapters.

HONEST, DISCLOSED LIMITATION: MarketOpportunityAssessment is built with
previous_assessment=None every cycle (no cross-cycle Opportunity
threading yet) -- this phase persists each cycle's raw, independently-
computed Opportunity read rather than adding new cross-cycle state,
per the explicit instruction to keep bridges thin and avoid migrating
business logic into this layer. Analysis-time diffing of consecutive
persisted records is expected to answer "what changed" questions, not
this recorder.

Never fabricates, never infers, never reinterprets: every None/UNKNOWN/
AMBIGUOUS/empty-tuple result from a real engine is serialized exactly
as produced.

Phase 10 (Market Memory Integrity Upgrade): persists a `"memory_health"`
telemetry block every cycle (`bujji.market_state_builder.memory_health.
compute_memory_health`) -- episode_count, active_events (this cycle's
delta, unchanged meaning), historical_events_available, historical_
events_referenced, unresolved_event_references, history_resolution_
ratio. Purely observational -- computed from data this recorder already
has (MarketStateAssessment.episodes/.events plus the builder's own
accumulated event_history), never fed back into any decision. Exists so
Bujji can honestly state whether PSI/MSSI reasoned over complete or
partial memory this cycle, instead of that being silently invisible.

Phase 11 (Intelligence Depth Upgrade): persists `"regime_memory"`
(`bujji.market_regime_memory` -- how long the current market_state.regime
has persisted, empirical transition context, sample-size-aware
confidence; threaded cross-cycle exactly like ObservationMemory) and
`"narrative"` (`bujji.market_narrative` -- a rule-based, no-LLM sentence
built only from fragments that resolved with real evidence this cycle).
Both are read-only: neither is imported by, or influences, any decision
engine. Market Context Window, the expanded Intelligence Completeness
Engine, and the Shadow Intelligence Report (Upgrades 3/4/7) are NOT
wired in here -- they operate over already-persisted records as
standalone analytical tools, the same way the Phase 9 Completeness
Engine was kept separate from the live recorder.
"""
from __future__ import annotations

import dataclasses
from datetime import datetime
from typing import Any, Callable, Optional

from bujji.intelligence.context import EXECUTION_MODE_LIVE, IntelligenceContext
from bujji.intelligence.liquidity_brain import LiquidityBrain
from bujji.market_perception.intelligence_adapter import build_intelligence_snapshot, fetch_spot_candles
from bujji.market_perception.models import MarketSnapshot
from bujji.market_perception.greeks_adapter import build_greeks_assessment
from bujji.market_perception.msi_adapter import build_volatility_structure_assessment
from bujji.market_narrative.engine import build_narrative
from bujji.premium_behaviour.engine import evaluate as evaluate_premium_behaviour
from bujji.premium_behaviour.models import PremiumBehaviourState, PremiumObservation
from bujji.market_regime_memory.engine import evaluate as evaluate_regime_memory
from bujji.market_regime_memory.models import RegimeMemoryState
from bujji.market_state_builder.market_state import MarketStateBuilder, ObservationMemory
from bujji.market_state_builder.memory_health import compute_memory_health
from bujji.msi_consensus.engine import compute_consensus
from bujji.msi_decision_synthesis.engine import synthesize
from bujji.msi_strategy_selection_foundation.engine import assess_all_families
from bujji.msi_strategy_selector.engine import select_strategy
from bujji.msi_trade_intent.engine import determine_trade_intent, determine_trade_intent_from_selection

from .direction_bridge import build_market_direction
from .domain_view_adapter import build_domain_assessment_views, build_domain_signals
from .strategy_eligibility_bridge import build_strategy_eligibility
from .synthesizer import build_market_state
from .trade_thesis_bridge import build_trade_thesis

from .cycle_evidence import CycleEvidence

Clock = Callable[[], datetime]


def _to_dict(obj: Any) -> Optional[dict]:
    """None passes through as None (never fabricated); any frozen
    dataclass is serialized field-for-field via dataclasses.asdict --
    no reshaping, no reinterpretation."""
    if obj is None:
        return None
    return dataclasses.asdict(obj)


def _atm_bid_ask(snapshot: MarketSnapshot):
    """Same extraction `market_perception.intelligence_adapter._atm_bid_ask`
    already performs for the dashboard-facing intelligence snapshot --
    duplicated here (rather than imported, since it's a private helper)
    so this recorder can hand LiquidityBrain a real dataclass reading,
    not the dashboard dict `build_intelligence_snapshot` already
    produces. Same real ATM CE/PE top-of-book bid/ask, same snapshot,
    zero additional broker calls."""
    if snapshot.option_chain is None:
        return None, None, None, None
    atm = snapshot.option_chain.atm_strike
    ce = snapshot.option_chain.leg(atm, "CE")
    pe = snapshot.option_chain.leg(atm, "PE")
    return (
        ce.bid if ce else None, ce.ask if ce else None,
        pe.bid if pe else None, pe.ask if pe else None,
    )


def _atm_mid_premiums(snapshot: MarketSnapshot):
    """Phase 15E: mid = (bid+ask)/2, only when BOTH sides are real --
    same convention as `market_perception.greeks_adapter._atm_mid_premiums`
    (itself mirroring `msi_adapter.py`'s own helper) -- duplicated here
    for the same reason `_atm_bid_ask` above is duplicated rather than
    imported: a small private helper, not a cross-module import surface."""
    if snapshot.option_chain is None:
        return None, None
    atm = snapshot.option_chain.atm_strike
    ce = snapshot.option_chain.leg(atm, "CE")
    pe = snapshot.option_chain.leg(atm, "PE")
    ce_mid = (ce.bid + ce.ask) / 2.0 if ce and ce.bid is not None and ce.ask is not None else None
    pe_mid = (pe.bid + pe.ask) / 2.0 if pe and pe.bid is not None and pe.ask is not None else None
    return ce_mid, pe_mid


_liquidity_brain = LiquidityBrain()


def _to_liquidity_dict(reading) -> dict:
    """LiquidityReading carries Enum (`tightness`, `data_quality`) and
    `datetime` (`as_of`) fields that `dataclasses.asdict` would leave
    non-JSON-serializable -- its own `to_log()` already exists
    specifically to produce a JSON-safe dict (enums -> `.value`,
    unchanged otherwise), so this recorder reuses that instead of
    reimplementing the same conversion."""
    return reading.to_log()


class IntelligenceCycleRecorder:
    """Owns the one piece of cross-cycle state this layer needs
    (MarketStateBuilder's ObservationMemory, for Episode/MarketEvent
    continuity) -- construct once per session, call record_cycle() once
    per ShadowSessionRunner cycle."""

    def __init__(
        self, underlying: str = "NIFTY", initial_regime_state: Optional[RegimeMemoryState] = None,
        initial_observation_memory: Optional[ObservationMemory] = None,
        initial_premium_behaviour_state: Optional[PremiumBehaviourState] = None,
    ) -> None:
        """`initial_regime_state`: Phase 15C additive recovery hook --
        when a caller has hydrated a prior session's RegimeMemoryState
        (see `bujji.shadow_runtime.recovery`), pass it here so a
        restarted recorder continues that trajectory instead of
        silently resetting to a fresh, empty one. Defaults to None,
        which preserves the original fresh-state behavior exactly.

        `initial_observation_memory`: Phase 15D additive recovery hook
        -- when a caller has hydrated a prior session's ObservationMemory
        (see `bujji.market_state_builder.recovery.hydrate_observation_memory`),
        pass it here so `MarketStateBuilder` continues that episode/event
        history instead of resetting to a fresh, empty one. Defaults to
        None, preserving the original fresh-state behavior exactly.

        `initial_premium_behaviour_state`: Phase 15E additive recovery
        hook -- when a caller has hydrated a prior session's
        PremiumBehaviourState (see
        `bujji.premium_behaviour.recovery.hydrate_premium_behaviour`),
        pass it here so the rolling premium window continues instead of
        resetting to empty. Defaults to None, preserving fresh-state
        behavior exactly."""
        self._underlying = underlying
        self._market_state_builder = MarketStateBuilder(memory=initial_observation_memory)
        self._regime_memory_state = initial_regime_state if initial_regime_state is not None else RegimeMemoryState()
        self._premium_behaviour_state = (
            initial_premium_behaviour_state if initial_premium_behaviour_state is not None
            else PremiumBehaviourState()
        )
        # Phase 15C: the RAW per-cycle regime signal (pre-advance()),
        # exposed via `last_raw_regime` so a caller (ShadowSessionRunner)
        # can persist it for future recovery without parsing this
        # recorder's returned dict -- keeps the runner decoupled from
        # this module's internal record shape, same boundary as before.
        self._last_raw_regime: Optional[str] = None
        # Phase 5 (Nervous System Integration): the real, already-built
        # objects this cycle produced, exposed the SAME way
        # `last_raw_regime` already is -- a caller (a new Paper
        # Intelligence Mode hook) needs the real psi/mssi/mdi/mppi/vsb/
        # consensus/liquidity/premium_behaviour OBJECTS to feed into
        # `bujji.market_thesis.assess()`, not their `_to_dict()`
        # serialization in the returned record. This adds a property,
        # nothing else -- record_cycle()'s own return value, control
        # flow, and every existing call site are unchanged.
        self._last_evidence: Optional[CycleEvidence] = None

    @property
    def last_raw_regime(self) -> Optional[str]:
        return self._last_raw_regime

    @property
    def last_evidence(self) -> Optional["CycleEvidence"]:
        """The real objects built during the most recent record_cycle()
        call, or None before the first cycle. See CycleEvidence's own
        docstring."""
        return self._last_evidence

    async def record_cycle(self, snapshot: MarketSnapshot, broker, clock: Clock, candles=None) -> dict:
        """Returns one complete, honest record of this cycle's
        understanding-layer conclusions. Never raises for a partial/
        missing upstream result -- every stage degrades to None exactly
        as its own bridge already does.

        `candles`: pass the already-fetched candle list from the same
        cycle's market_perception step (fetch_spot_candles is a real
        broker call -- fetching it twice per cycle needlessly doubles
        FYERS request volume, a real cause of rate-limiting observed
        2026-08-06). If None, fetched here as a fallback for standalone
        callers (e.g. tests) that do not already have candles."""
        market_state_assessment = self._market_state_builder.process(snapshot)

        # Phase 10 (Market Memory Integrity Upgrade): explicit
        # observability into whether PSI/MSSI this cycle resolved
        # against COMPLETE memory or PARTIAL memory. Computed from the
        # SAME episodes/events this recorder already has plus the
        # builder's own accumulated event_history -- no new state, no
        # broker call. unresolved_event_references should read 0 now
        # that Phase 10's hydration fix is wired into
        # MarketStateBuilder.process(); a nonzero value here would mean
        # that fix itself has a gap and is surfaced honestly, not hidden.
        memory_health = compute_memory_health(
            market_state_assessment.episodes, market_state_assessment.events,
            self._market_state_builder.memory.event_history,
        )

        mdi = build_market_direction(market_state_assessment, snapshot.timestamp)

        if candles is None:
            candles = await fetch_spot_candles(broker, self._underlying)

        vsb = build_volatility_structure_assessment(snapshot, candles, clock())

        # Phase 15E (Greeks Intelligence): a real, per-leg ATM delta/
        # gamma/theta/vega read via the SAME already-persisted ATM CE/PE
        # option chain data every other bridge this cycle already
        # consumes -- zero new broker calls. Purely observational: not
        # fed into VSB, consensus, or any decision engine yet (no
        # proven downstream consumer established this phase -- see
        # module docstring / Phase 15E report).
        greeks = build_greeks_assessment(snapshot, clock())

        # Phase 15E (Premium Behaviour Intelligence): a real, cross-cycle
        # rolling window of the SAME ATM CE/PE mid premiums, threaded
        # forward exactly like RegimeMemoryState/ObservationMemory.
        ce_mid, pe_mid = _atm_mid_premiums(snapshot)
        spot = snapshot.spot.ltp if snapshot.spot else None
        self._premium_behaviour_state = self._premium_behaviour_state.advance(
            PremiumObservation(snapshot.timestamp, ce_mid, pe_mid, spot)
        )
        premium_behaviour = evaluate_premium_behaviour(self._premium_behaviour_state)

        intelligence_snapshot = build_intelligence_snapshot(snapshot, candles, clock())

        market_state = build_market_state(intelligence_snapshot, market_state_assessment, snapshot.timestamp)

        # Phase 11 Upgrade 1 (Market Regime Memory): thread the same
        # regime string market_state.regime already carries into a
        # cross-cycle state, mirroring MarketStateBuilder's own
        # ObservationMemory pattern. A None/UNKNOWN reading never resets
        # or fabricates a transition (see RegimeMemoryState.advance()).
        self._last_raw_regime = market_state.regime
        self._regime_memory_state = self._regime_memory_state.advance(market_state.regime)
        regime_memory = evaluate_regime_memory(self._regime_memory_state)

        views = build_domain_assessment_views(
            psi=market_state_assessment.price_structure,
            mssi=market_state_assessment.market_structure,
            mdi=mdi,
            mppi=market_state_assessment.participant_positioning,
        )
        consensus = compute_consensus(views, timestamp=snapshot.timestamp)

        signals = build_domain_signals(
            psi=market_state_assessment.price_structure,
            mssi=market_state_assessment.market_structure,
            mdi=mdi,
            mppi=market_state_assessment.participant_positioning,
            vsb=vsb,
        )
        episode_ids = tuple(ep.episode_id for ep in market_state_assessment.episodes)
        # previous_assessment=None every cycle -- see module docstring's
        # disclosed limitation; no cross-cycle Opportunity threading yet.
        opportunity = synthesize(signals, None, episode_ids, timestamp=snapshot.timestamp)

        thesis = build_trade_thesis(
            psi=market_state_assessment.price_structure,
            mssi=market_state_assessment.market_structure,
            mdi=mdi,
            mppi=market_state_assessment.participant_positioning,
            vsb=vsb,
            consensus=consensus,
            timestamp=snapshot.timestamp,
        )

        eligibility = build_strategy_eligibility(opportunity, consensus, snapshot.timestamp)

        # --- Description-only additions, 2026-08-06: Suitability -> ---
        # Selection -> Intent. Every input below is already real and
        # already built above -- no new broker call, no fabrication.
        # psi/mssi/mppi are only referenced when honestly present;
        # select_strategy() requires all four as real (non-Optional)
        # objects, so it is skipped (never fabricated) when any is None.
        psi = market_state_assessment.price_structure
        mssi = market_state_assessment.market_structure
        mppi = market_state_assessment.participant_positioning

        # Phase 9 Liquidity Intelligence Bridge: real ATM CE/PE bid/ask
        # already sits on `snapshot.option_chain` (no new broker call --
        # the option chain was already fetched to build this snapshot).
        # LiquidityBrain.analyze() honestly returns tightness=UNKNOWN
        # when a quote leg is missing/invalid -- never assumed tight.
        ce_bid, ce_ask, pe_bid, pe_ask = _atm_bid_ask(snapshot)
        # snapshot.timestamp is a real ISO string (MarketSnapshot's own
        # field type), not a datetime -- parsed here rather than passed
        # raw, since IntelligenceContext.as_of_time must be a real datetime
        # for downstream Reading.as_of.isoformat() calls to work.
        intelligence_context = IntelligenceContext(
            as_of_time=datetime.fromisoformat(snapshot.timestamp), execution_mode=EXECUTION_MODE_LIVE,
        )
        liquidity_reading = _liquidity_brain.analyze(ce_bid, ce_ask, pe_bid, pe_ask, context=intelligence_context)

        suitability_assessments: tuple = ()
        if mdi is not None and mssi is not None:
            suitability_assessments = assess_all_families(
                mdi, mssi, consensus, vsb, liquidity_reading, timestamp=snapshot.timestamp,
            )

        selection = None
        if mdi is not None and psi is not None and mssi is not None and mppi is not None:
            selection = select_strategy(
                suitability_assessments, psi, mssi, mdi, mppi, consensus, vsb,
                timestamp=snapshot.timestamp,
            )

        # Phase 15O forensic finding: Phase 14B built
        # `determine_trade_intent_from_selection` (so a real, ranked
        # Strategy Selection drives the intent, instead of
        # `determine_trade_intent`'s own documented
        # `_placeholder_select_one_eligible_family`) -- but it was
        # never called from anywhere in production; confirmed by direct
        # grep. Wired here for the first time. Behaviour is strictly
        # additive: the selection-driven path is used ONLY when a real
        # selection with a real family exists; every other cycle falls
        # back to the exact pre-existing call, unchanged.
        trade_intent = None
        if opportunity is not None and eligibility is not None:
            selected_family = getattr(selection, "selected_strategy_family", None) if selection is not None else None
            if selected_family is not None:
                trade_intent = determine_trade_intent_from_selection(
                    selected_family, getattr(selection, "confidence", None), eligibility, opportunity,
                    timestamp=snapshot.timestamp,
                )
            else:
                trade_intent = determine_trade_intent(eligibility, opportunity, timestamp=snapshot.timestamp)

        record = {
            "timestamp": snapshot.timestamp,
            "market_snapshot_health": snapshot.health_status,
            "market_snapshot_missing_fields": list(snapshot.missing_fields),
            "price_structure": _to_dict(market_state_assessment.price_structure),
            "market_structure": _to_dict(market_state_assessment.market_structure),
            "participant_positioning": _to_dict(market_state_assessment.participant_positioning),
            "market_direction": _to_dict(mdi),
            "volatility_structure": _to_dict(vsb),
            "market_state": _to_dict(market_state),
            "consensus": _to_dict(consensus),
            "opportunity": _to_dict(opportunity),
            "trade_thesis": _to_dict(thesis),
            "strategy_eligibility": _to_dict(eligibility),
            "strategy_suitability": [_to_dict(s) for s in suitability_assessments],
            "strategy_selection": _to_dict(selection),
            "trade_intent": _to_dict(trade_intent),
            "liquidity": _to_liquidity_dict(liquidity_reading),
            "memory_health": memory_health.to_dict(),
            "regime_memory": regime_memory.to_dict(),
            "greeks": greeks.to_dict() if greeks is not None else None,
            "premium_behaviour": premium_behaviour.to_dict(),
        }
        # Phase 11 Upgrade 2 (Market Narrative): built from the record
        # just assembled above (+ this cycle's regime_memory) -- reads
        # only what's already real/persisted this cycle, never a second
        # broker call, never fed back into any decision.
        record["narrative"] = build_narrative(record, regime_memory.to_dict()).to_dict()

        # Phase 5 (Nervous System Integration): expose the real objects
        # this cycle already built, the same "property, not a parsed
        # dict" pattern last_raw_regime already established above.
        self._last_evidence = CycleEvidence(
            timestamp=snapshot.timestamp, psi=psi, mssi=mssi, mdi=mdi, mppi=mppi, vsb=vsb,
            consensus=consensus, liquidity=liquidity_reading, premium_behaviour=premium_behaviour,
        )
        return record
