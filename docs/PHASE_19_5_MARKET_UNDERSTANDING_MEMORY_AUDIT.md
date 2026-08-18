# Phase 19.5 — Market Understanding Memory Audit

**Audit only, performed before any implementation, per this phase's own explicit first requirement.**

## What already exists

Direct inspection of `bujji/` for every memory/understanding/similarity/catalog/journal/ontology-shaped
package:

### `bujji/market_understanding/` (Phase 17J series) — a real, mature, explainable similarity engine, but a DIFFERENT feature space

- `structure.py` — `IntradayStructureCatalog`/`IntradayStructureRecord`, 14 already-classified price-action
  structure dimensions (trend/swing/compression/expansion/balance/support/resistance/breakout/breakdown/
  retest/rejection/etc.) from Phase 17G.A.
- `similarity.py` — `SituationFeatureVector` (those 14 dimensions + a bucketed VIX band), `compare()`
  (exact-match fraction over dimensions present on **both** sides — a missing dimension is excluded from
  the denominator, **never** scored as a match or mismatch by default), `find_similar()` (ranks candidates,
  excludes self, returns `None` rather than `0.0` when nothing is comparable).
- `timeline.py` — `EpisodeTimeline`/`compare_timelines()`, the same `compare()` averaged across fixed
  30-minute checkpoints spanning a session, for comparing session *paths* rather than single instants.

**Verdict: genuinely reusable as a design precedent, not directly reusable as code.** `compare()` operates
over `SituationFeatureVector`'s specific 14 structure dimensions + VIX band — a different, narrower feature
space than what Phase 19.5 needs (regime/volatility/liquidity/structure-proximity/event/confidence/strategy
compatibility, sourced from `MarketIntelligenceSnapshot` + `DecisionContext`, Phase 19.3/19.4). The
**discipline** this module already established — never treat a missing dimension as a match or mismatch,
exclude it from the denominator, return `None` rather than a fabricated `0.0` when nothing is comparable,
no ML, fully explainable — is reused verbatim in this phase's new similarity engine. The **code** is not
imported or modified, since it operates on a genuinely different vector shape (`IntradayStructureRecord`,
not `MarketIntelligenceSnapshot`).

Per this audit's own finding: **new files are added to this same package** (`bujji/market_understanding/`)
rather than creating a sibling top-level package, since this is exactly where "market situation similarity"
already lives architecturally — but as new, separate modules (`memory_models.py`, `memory_similarity.py`,
`memory_store.py`, `memory_engine.py`), never touching `structure.py`/`similarity.py`/`timeline.py`.

### `bujji/outcome_memory/` (Phase 15N) — trade-outcome memory, a DIFFERENT concern

`OutcomeMemoryRecord` remembers a real, already-closed **position**: `PositionLifecycle` +
`PositionOutcomeAttribution`, embedded verbatim. This requires an actual trade to have happened —
structurally inapplicable to "what did the market itself do after this state," which needs no position at
all. **Not reusable as a data model** for this phase (no position exists to attach to a market-state
observation). **Reusable as a persistence/identity precedent**:

- `memory_id_for(...)` — a deterministic MD5-based id, collision-resistant, replay-safe, independent of
  any broker-assigned id. This phase's own `market_memory_id_for()` follows the identical convention.
- Cross-session by design (`hydrate_outcome_memory` takes no `session_id` gate) — the same property this
  phase's memory needs, since "have we seen this before" must span every session, not just the current one.
- Records are immutable facts, embedded verbatim, never mutated in place, never re-derived from a lossy
  summary — the same discipline this phase's `MarketMemoryEntry` follows for its own
  `intelligence_snapshot` / `decision_context` fields.

### `bujji/reality_memory/` (Phase 17J.1) — read-only Reality projection, a DIFFERENT layer

`RealityMemoryCatalog` is a read-only projection over `HistoricalObservationStore`, recomputing
`RealityMemoryEvent` fresh on every call rather than trusting a persisted (and, per its own docstring,
found-stale) snapshot store. **Reused as a design precedent, not as code**: this phase's own catalog-style
query surface (a future `MarketMemoryCatalog`, deferred — see "What was NOT built" below) would follow the
same "recompute from source, never trust a possibly-stale persisted copy" discipline, and the same
`now`/`as_of_time`-threaded no-look-ahead pattern this file already established for point-in-time queries.

### `bujji/market_regime_memory/` (Phase 15B) — single-dimension regime memory, a DIFFERENT scope

`RegimeMemoryState`/`evaluate()` tracks regime STABILITY and transition probability for one specific
component (regime alone), per-session, hydrated via `EventStore` replay. Narrower than what Phase 19.5
needs (regime is only one of six dimensions this phase must remember) and per-session (this phase needs
cross-session). Not reusable as a model. Its `EventStore`-replay hydration PATTERN is the same one already
being reused from `outcome_memory` (both ultimately sit on `bujji.state_persistence.store.EventStore`), so
no second precedent needed here.

### `bujji/journal/` (23 files) — operational/audit journals, a DIFFERENT purpose

Every file here (`decision_journal.py`, `intelligence_observation_journal.py`,
`trading_ontology_journal.py`, etc.) is an operational audit trail for a specific runtime component's own
internal decisions/observations — not a market-pattern-memory system. `decision_journal.py` was read in
full: it journals the trading brain's own decision-making steps for later audit, not "what did the market
do." No conflict, nothing reusable for this phase's actual objective.

### `bujji.state_persistence.store.EventStore` / `bujji.state_persistence.models.PersistedEvent` (Phase 15B) — the real, generic append-only primitive

Fully generic: one JSONL file, one `PersistedEvent` per line (`event_id`, `event_type`, `session_id`,
`cycle_id`, `timestamp`, `schema_version`, `provenance`, `payload: Dict[str, Any]`), atomic single-line
writes (POSIX `PIPE_BUF` guarantee), malformed/truncated trailing lines skipped on read rather than
raised. **This is exactly the "minimal append-only memory store" this phase's own spec calls for if
nothing exists — and something already does.** `MarketMemoryStore` (this phase) is built directly on top of
this, exactly as `outcome_memory`'s own store already is — no new persistence mechanism invented.

## What conflicts

**Nothing conflicts.** No existing package claims ownership of "cross-dimensional
`MarketIntelligenceSnapshot`-based situation memory with outcome tracking." The closest name collision risk
was `market_understanding` itself already existing — resolved by adding new, separate, non-overlapping
files to that package rather than a competing top-level package, and by never modifying or importing from
its existing 17J-series files for anything other than citing them as precedent in this doc.

## What should remain untouched

- `bujji/market_understanding/{structure,similarity,timeline}.py` — the entire 17J-series structure-based
  similarity engine. Different feature space, already shipped, already tested. Zero lines touched.
- `bujji/outcome_memory/` — trade-outcome memory. Different concern (requires a position). Zero lines
  touched.
- `bujji/reality_memory/` — Reality-tier projection. This phase's memory reads `MarketIntelligenceSnapshot`/
  `DecisionContext` (Phase 19.3/19.4), which already carry a `reality_fingerprint` reference back to this
  layer — no need to query it directly. Zero lines touched.
- `bujji/market_regime_memory/` — single-dimension, per-session regime memory. Different scope. Zero lines
  touched.
- `bujji/journal/` — operational audit trail. Different purpose. Zero lines touched.
- `bujji/state_persistence/{models,store}.py` — the generic `EventStore`/`PersistedEvent` primitive. Reused
  as-is, imported, never modified.

## What is reusable (summary)

| Precedent | Source | Reused as |
|---|---|---|
| Explainable, no-ML, exclude-missing-dimensions comparison discipline | `market_understanding/similarity.py` | Design pattern for the new similarity engine (code not shared — different vector shape) |
| Deterministic MD5-based id convention, cross-session design, embed-verbatim-never-re-derive | `outcome_memory/models.py` | `market_memory_id_for()`, `MarketMemoryEntry`'s own verbatim-snapshot fields |
| Append-only JSONL event store | `state_persistence/store.py` `EventStore`/`PersistedEvent` | `MarketMemoryStore`, used directly, unmodified |
| `now`/`as_of_time`-threaded no-look-ahead point-in-time query | `reality_memory/catalog.py` | This phase's own `as_of_time`-required query surface |

## What was deliberately NOT built this phase

- No materialized `MarketMemoryCatalog` query index over historical dates (the `reality_memory/catalog.py`
  precedent recomputes/reads on demand; this phase's store is queried the same way — a full catalog-style
  bulk-scan convenience layer is a real, separate future step, not required to prove the core capability).
- No integration into any live runtime loop (`shadow_session_runner.py`, `intelligence_cycle_recorder.py`)
  — this phase builds and tests the capability in isolation, per the phase's own "can operate without live
  market" requirement (property 7). Wiring it into a live cycle is a future, separate decision.

## Conclusion

No duplicate memory system was created. Two real precedents (`outcome_memory`'s identity/persistence
pattern, `market_understanding/similarity.py`'s explainability discipline) were reused directly into the
new implementation; one (`reality_memory/catalog.py`'s point-in-time discipline) informed the new query
surface's contract; the generic `EventStore` primitive is used unmodified. Proceeding to implementation.
