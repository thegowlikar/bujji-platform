# Phase 19.8 — Market State Graph Audit

**Audit only, performed before any implementation, per this phase's own explicit instruction to search
`market_state`, `market_regime_memory`, `ontology`, `phenomena`, `trading_brain`, `market_perception` first.**

## Finding: `bujji/decision_context/models.py` already defines the exact transition taxonomy this phase asks for — 4 of 6 values, verbatim

Phase 19.4 already built `TransitionType` (`COMPRESSION_TO_EXPANSION`, `RANGE_TO_TREND`, `TREND_TO_RANGE`,
`NORMAL_TO_EVENT_RISK`, `UNKNOWN`) and `MarketStateTransition`/`detect_transition()` — a documented,
exhaustive posture-pair mapping table over two `MarketIntelligenceSnapshot`s' own `posture` field. This is
**not a coincidence to route around** — it is the exact 4-of-6-name overlap this phase's own taxonomy
requests. **Reused directly**: this phase's `transitions.py` imports `bujji.decision_context.transition.detect_transition()`
and `TransitionType` rather than reimplementing posture-transition detection a second time. The two new
values this phase needs (`LIQUIDITY_NORMAL_TO_STRESS`, `LIQUIDITY_STRESS_RECOVERY`) are added as this
phase's own new detector, since Phase 19.4's `TransitionType` enum has no liquidity-shaped member and is a
frozen, already-shipped model not modified by this phase (same "never retrofit a shipped model" discipline
already followed for `DecisionContext` in Phase 19.6).

## `bujji/market_regime_memory/` — different scope, already audited in Phase 19.5, still holds

`RegimeMemoryState`/`evaluate()` tracks regime stability/transition PROBABILITY for one dimension
(regime alone), per-session. Narrower than this phase's cross-dimensional, cross-session state graph.
Nothing new to add beyond Phase 19.5's own finding — not reusable as a model, no conflict.

## `bujji/market_state/` and `bujji/market_state_builder/` — a different, older MSI-pipeline concept, same name collision risk already known

`bujji/market_state/models.py` defines `MarketState`/`MarketDirectionSummary` — built from the MSI
Production Intelligence family (PSI/MSSI/MDI), the same older pipeline `msi_market_phenomena` sits on
(Phase 19.7's own audit finding). Structurally unrelated to `MarketIntelligenceSnapshot`/
`DecisionIntelligenceSnapshot` (this session's 19.x chain). **Confirms, rather than creates, a known naming
tension**: `bujji.market_state.models.MarketState` (MSI pipeline) and this phase's new
`bujji.market_state_graph.models.MarketStateNode` (19.x pipeline) are deliberately named to NOT collide —
`MarketStateNode`, not `MarketState`, and a distinct top-level package (`market_state_graph`, not a
sub-module added to `market_state`) — precisely because `bujji/market_state/` is already a real, in-use
name for a different concept. Nothing in `bujji/market_state/` or `bujji/market_state_builder/` is touched
or imported.

## `bujji/core/state_machine.py` — a different kind of state entirely, no conflict

`StateMachine`/`IllegalTransition` is the trading SESSION lifecycle FSM
(`WAITING → READY → CONFIRMED → IN_POSITION → EXITING → DONE_FOR_DAY`) — legal-transition enforcement for
the *runtime's own operating mode*, not market condition. Different concern entirely (this project's own
module docstring: "No trading logic lives here — it only guards which state we may move to next"). Not
reusable as a model. Its *discipline* (explicit legal-transition table, `IllegalTransition` raised rather
than silently allowing anything) is not needed here — this phase's transitions are observations
("conditions changed from A to B"), not a guarded state machine the caller must obey; nothing here decides
what Bujji is "allowed" to do next.

## `bujji/market_phenomena/` (Phase 19.7) — reused directly as this phase's own evidence source

The liquidity-transition detectors this phase adds (`LIQUIDITY_NORMAL_TO_STRESS`/
`LIQUIDITY_STRESS_RECOVERY`) are built directly on Phase 19.7's own `PHENOMENON_LIQUIDITY_STRESS`
detection — comparing whether that phenomenon was present in a `previous` `MarketPhenomenaAssessment`
versus the current one. No new liquidity classification logic is written; this phase only compares two
already-real `MarketPhenomenaAssessment` outputs.

## `bujji/trading_brain/ontology/` — re-confirmed empty of phenomenon/state-graph concepts

Re-grepped for "transition"/"state_graph"/"MarketStateNode" — zero matches beyond files already accounted
for above. Nothing to reuse or conflict with.

## `bujji/market_perception/` — data acquisition layer, no state-graph concept

Confirmed again (same finding as Phase 19.7's audit): acquisition, not interpretation. Nothing to reuse.

## Conclusion

One real precedent found and reused directly (`decision_context.detect_transition()`/`TransitionType` for
4 of 6 transition types), one real precedent found and reused directly as an evidence source
(`market_phenomena`'s own liquidity-stress detection, for the 2 new transition types), one deliberate
naming-collision avoidance confirmed and acted on (`MarketStateNode` vs. the pre-existing, unrelated
`bujji.market_state.MarketState`). No duplicate state vocabulary was created. Proceeding to implementation.
