# Phase 19.8 — Market State Graph Foundation

## Objective

Build a temporal understanding layer connecting `MarketIntelligenceSnapshot` (Phase 19.3),
`DecisionIntelligenceSnapshot` (Phase 19.6), `MarketPhenomenaAssessment` (Phase 19.7), and Market
Understanding Memory (Phase 19.5) into an evolving market state timeline. Answers "where are we in the
market's evolution" — not strategy selection, not prediction.

Audit performed first, per this phase's own explicit instruction — see
[docs/PHASE_19_8_MARKET_STATE_GRAPH_AUDIT.md](PHASE_19_8_MARKET_STATE_GRAPH_AUDIT.md). **Two real
precedents were found and reused directly**: `bujji.decision_context.TransitionType`/`detect_transition()`
(Phase 19.4) already defines 4 of this phase's own 6 requested transition types — reused verbatim, never
reimplemented — and `bujji.market_phenomena`'s own `PHENOMENON_LIQUIDITY_STRESS` detection (Phase 19.7) is
the evidence source for the 2 new liquidity-shaped transitions, rather than a new liquidity classifier.
One deliberate naming-collision avoidance was also confirmed: `bujji.market_state.MarketState` already
exists (a different, older MSI-pipeline concept) — this phase's own type is named `MarketStateNode`, in a
distinct top-level package (`market_state_graph`), specifically to not collide with it.

## New package: `bujji/market_state_graph/`

- `models.py` — `MarketStateNode`, `StateTransitionEdge`, the confidence scale
- `transitions.py` — the 6-value transition taxonomy (4 reused, 2 new) + `detect_posture_transition_type()`
  (delegates to Phase 19.4) + `detect_liquidity_transition_type()` (built on Phase 19.7)
- `engine.py` — `build_market_state_node()`, the one entry point
- `memory.py` — extends Phase 19.5's `EventStore`-based persistence: `record_market_state_node()`,
  `hydrate_market_state_graph()`, `nodes_as_of()`, `build_state_sequence()`

## Core Model — `MarketStateNode`

Every field is copied verbatim from an already-validated upstream layer — this object never recomputes a
metric, only links and sequences what already exists:

```python
state_id: str                              # content fingerprint, same two-step provisional-then-hash pattern as every prior phase
timestamp: str                             # snapshot.as_of_time.isoformat() -- never wall-clock
regime, volatility_state, liquidity_state  # copied verbatim from MarketIntelligenceSnapshot's own Reading fields
phenomena: Tuple[str, ...]                 # phenomenon_type strings from MarketPhenomenaAssessment (Phase 19.7)
event_state: str                           # "EVENT_RISK" or "NORMAL" -- from market_phenomena's own EVENT_RISK detection, not re-derived
decision_posture: str                      # DecisionIntelligenceSnapshot.recommended_posture.value (Phase 19.6)
evidence_bundle: Dict[str, Any]            # decision_intelligence's own evidence_bundle + phenomena, verbatim
previous_state_id: Optional[str]           # links to the prior node -- an explicit, caller-supplied predecessor, never inferred
transition: Optional[StateTransitionEdge]
```

## State Transition Model — observation, never prediction

Verified structurally (`test_no_prediction_shaped_transition_types`): none of the 6 real transition type
names contains a future-tense or prediction word (`expected`, `predicted`, `will_`, `forecast`,
`probability`). `RANGE_TO_TREND` describes a transition that **already occurred**, per this phase's own
explicit example (`BREAKOUT_EXPECTED` — wrong; `RANGE_TO_TREND` — correct).

### Taxonomy — 4 reused, 2 new

| Type | Source |
|---|---|
| `COMPRESSION_TO_EXPANSION` | `decision_context.TransitionType` (Phase 19.4), reused verbatim |
| `RANGE_TO_TREND` | `decision_context.TransitionType` (Phase 19.4), reused verbatim |
| `TREND_TO_RANGE` | `decision_context.TransitionType` (Phase 19.4), reused verbatim |
| `NORMAL_TO_EVENT_RISK` | `decision_context.TransitionType` (Phase 19.4), reused verbatim |
| `LIQUIDITY_NORMAL_TO_STRESS` | new — built on `market_phenomena`'s own `PHENOMENON_LIQUIDITY_STRESS` (Phase 19.7) |
| `LIQUIDITY_STRESS_RECOVERY` | new — same source, inverse direction |
| `UNKNOWN` | `decision_context.TransitionType` (Phase 19.4), reused verbatim — "no named transition matched," including "no change" |

### Priority when more than one real transition is detected in the same cycle

Documented, deterministic: `NORMAL_TO_EVENT_RISK` (highest-stakes real change) > liquidity stress
onset/recovery > posture-shaped transitions (`COMPRESSION_TO_EXPANSION`/`RANGE_TO_TREND`/`TREND_TO_RANGE`).
Verified live in the phase's own end-to-end smoke test: a cycle with simultaneous event risk, liquidity
stress onset, and a regime change correctly reported `NORMAL_TO_EVENT_RISK` as the single `transition`
field — `MarketStateNode` carries exactly one transition per node, per the phase's own spec, never a list.

### No fabricated transitions

Two calm, unchanged cycles produce `transition=None` — verified by
`test_no_transition_reported_when_nothing_real_changed` — never a forced `UNKNOWN`-shaped edge just to
have something to report. The node is still correctly linked via `previous_state_id` either way.

## Temporal Memory — extends Phase 19.5, same persistence primitive

`memory.py` is built directly on `bujji.state_persistence.store.EventStore` — the same append-only
primitive `market_understanding.memory_engine` (Phase 19.5) already uses, never a new mechanism.
`build_state_sequence()` walks `previous_state_id` links backward, returning the chronological
"compression → expansion → trend" path the phase's own worked example describes — stopping honestly at the
first missing predecessor rather than fabricating a gap fill. Verified live: `hydrate_market_state_graph()`
round-trips a 2-node sequence byte-identically, and `build_state_sequence()` correctly recovers
`[node1, node2]` in chronological order from the store.

`node.timestamp` uses the identical `as_of_time.isoformat()` convention Phase 19.5's `MarketMemoryEntry`
already established — verified by `test_state_node_shares_intelligence_snapshot_identity_conventions` —
proving compatibility for a future combined "state sequence + phenomena + outcome" memory extension
without inventing a new time representation. That combined store was **not built this phase** — the
identity/timestamp compatibility is proven, but wiring phenomena+outcome into `MarketMemoryEntry` itself is
real, separate future work, not attempted without a concrete calling need.

## No-look-ahead — structurally enforced

`nodes_as_of(nodes, as_of_time)` — the same discipline `market_understanding.memory_query`'s
`find_similar_memories_as_of()` already established (Phase 19.5): any node whose own `timestamp` is
strictly after the query's `as_of_time` is excluded, regardless of iteration order or when it was actually
recorded. `build_state_sequence()` walking backward from an early node structurally can never reach a later
one — verified by `test_build_state_sequence_never_reaches_forward_in_time`.

## State Confidence — two separate fields, never combined

`StateTransitionEdge.observation_confidence` ("what we know happened" — bucketed from
`DecisionIntelligenceSnapshot.evidence_bundle["confidence"]`, Phase 19.6's own already-real min-across-brains
confidence, never a new number) and `interpretation_confidence` ("what we think the transition means" —
derived from how much real evidence backs the specific transition label) are two independently-set fields
on every `StateTransitionEdge`. Verified by `test_observation_and_interpretation_confidence_are_never_combined`
that both are real, independent string values — never merged into one score.

## Testing

`tests/test_market_state_graph.py`, 15 tests, proving all 8 required properties:

1. **Deterministic state fingerprint** — same inputs → same `state_id`, `.fingerprint()` self-consistent.
2. **Historical replay equality** — LIVE vs HISTORICAL_REPLAY produce the identical `state_id`.
3. **No future-state leakage** — 2 tests: `nodes_as_of()` excludes a later node when queried before it
   exists; `build_state_sequence()` walking backward from an early node never reaches a later one.
4. **Transition evidence validation** — 2 tests: every detected transition carries ≥1 real evidence item,
   a valid `transition_type`, and correct `from_state_id`/`to_state_id` linkage; observation and
   interpretation confidence are separate, real, independently-set values.
5. **Unknown transition handling** — 2 tests: no transition without a real `previous_node` (structurally
   impossible, same discipline as `REGIME_TRANSITION` in Phase 19.7); no fabricated transition when nothing
   real changed between two cycles.
6. **No strategy vocabulary** — 3 tests: AST identifier scan; every transition type name checked to contain
   no prediction/future-tense word; no order-shaped field on either model.
7. **Memory compatibility** — 2 tests: full record → hydrate → sequence round-trip via an isolated
   `tmp_path` `EventStore`; timestamp convention matches Phase 19.5's own.
8. **No direct datastore access** — 2 tests: `models.py`/`transitions.py`/`engine.py` (deliberately
   excluding `memory.py`, which IS the memory module) import nothing broker/`fyers`/`EventStore`/sqlite-shaped;
   no `.analyze` call or brain instantiation anywhere in the package.

One real design bug caught and fixed during this phase's own implementation, before any test ran against
it: the first version of `MarketStateNode.fingerprint_payload()` embedded
`StateTransitionEdge.to_state_id` — but `to_state_id` is always the CONTAINING node's own `state_id` by
construction, only known *after* the hash is computed, so hashing it would have made `.fingerprint()`
permanently disagree with the stored `state_id` the moment `to_state_id` was filled in. Fixed by adding a
`StateTransitionEdge.fingerprint_payload()` that excludes `to_state_id` (the same "exclude the
self-referential/audit-only field" treatment `created_at` already gets everywhere else in this project),
while `to_dict()` still includes it for full, real persistence.

## Full regression

Baseline before this phase: 5,843 passed (post Phase 19.7). After Phase 19.8's additions: **5,858 passed,
0 failed** — exactly the 15 new tests. No existing file was modified, only four new self-contained modules
in `bujji/market_state_graph/` + one new test file.

## Explicitly NOT built this phase (per the phase's own scope)

- ❌ Strategy recommendation, entry timing, direction prediction, options structure selection, execution.
- ❌ A combined "state sequence + phenomena + outcome" memory store (identity/timestamp compatibility
  proven; storage deferred to real future need, same discipline Phase 19.7 already applied to its own
  memory-integration scope).

## Final verdict

Bujji can now say, from a real, linked sequence of already-validated nodes: *"Market transitioned from
volatility compression into volatility expansion with increasing liquidity stress. The transition is
supported by rising realized volatility, wider spreads, and changing market regime"* — verified live
against exactly this combination of real constructed inputs. Per the user's own framing: Phase 19.7 gave
Bujji eyes; Phase 19.8 gives it memory of movement — the professional trader's skill of recognizing market
evolution, not just market condition, before Phase 20 ever builds a strategy on top of it.
