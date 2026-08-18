# Phase 19.5 — Market Understanding Memory Implementation

## Objective

Give Bujji memory and pattern recognition — "when has the market behaved like this before, and what
happened afterward?" — without predictions, signals, or execution. This phase moves Bujji from "current
market classification" (Phase 19.3/19.4) to "current market classification + historical understanding."

Audit performed first, per this phase's own explicit requirement — see
[docs/PHASE_19_5_MARKET_UNDERSTANDING_MEMORY_AUDIT.md](PHASE_19_5_MARKET_UNDERSTANDING_MEMORY_AUDIT.md).
**No duplicate memory system was created.** Two real precedents were reused directly
(`outcome_memory`'s identity/persistence discipline, `market_understanding/similarity.py`'s explainability
discipline); the generic `EventStore` primitive is used unmodified; nothing in
`market_understanding/{structure,similarity,timeline}.py`, `outcome_memory/`, `reality_memory/`,
`market_regime_memory/`, or `journal/` was touched.

## New files, all in `bujji/market_understanding/` (the audit's own conclusion: same package as the
existing 17J-series similarity engine, since that is architecturally where "market situation similarity"
already lives — but new, separate modules, never modifying the existing three files)

- `memory_models.py` — `MarketMemoryEntry`, `MarketOutcomeObservation`, `market_memory_id_for()`
- `memory_similarity.py` — `SimilarityContribution`, `SimilarityExplanation`, `explain_similarity()`,
  `find_similar_memories()`
- `memory_engine.py` — `build_market_memory_entry()`, `record_market_memory()`,
  `record_outcome_observation()`, `hydrate_market_memory()`
- `memory_query.py` — `find_similar_memories_as_of()`, the point-in-time, no-look-ahead query surface

## 1. Memory Identity — reused, not reinvented

`MarketMemoryEntry.intelligence_snapshot_id` is `MarketIntelligenceSnapshot.intelligence_snapshot_id`
verbatim (Phase 19.3). `intelligence_fingerprint` is the **same value** — by Phase 19.3's own construction,
`.fingerprint()` always equals `.intelligence_snapshot_id`. Kept as an explicit second field only because
the phase spec names it separately, documented rather than silently duplicating a value under a second
name without explanation. `market_memory_id_for(intelligence_snapshot_id, underlying)` follows the
identical deterministic-MD5 convention `outcome_memory.models.memory_id_for()` already established —
reused, not reinvented. No new `snapshot_id` was introduced anywhere.

`underlying` is passed explicitly by the caller at record time — `MarketIntelligenceSnapshot` itself
carries no instrument field (Bujji's Intelligence Core is currently single-instrument, NIFTY-scoped, with
no symbol field anywhere in the six Reading classes). Rather than fabricate a fake field on an upstream
object, this phase records the real fact honestly: the underlying is context the *caller* supplies, not
something the snapshot itself claims to know.

## 2. Similarity Engine — new code, reused discipline

`market_understanding/similarity.py`'s `compare()` (Phase 17J.2) established the discipline this phase's
`explain_similarity()` follows exactly: a missing dimension is never scored as a match or mismatch, no ML,
fully explainable, deterministic. The **code** could not be reused directly — `compare()` operates over
`SituationFeatureVector`'s 14 structure-classification dimensions + VIX band, a different feature space
than `MarketMemoryEntry`'s six Intelligence Core dimensions (regime, volatility richness, structure
proximity, liquidity tightness, event environment, evidence confidence).

### The weighted, explainable breakdown (matching the phase spec's own worked example exactly)

| Dimension | Same | Similar | Different | Missing |
|---|---|---|---|---|
| Regime | +20 | — | 0 | 0 |
| Volatility (richness) | +20 | — | 0 | 0 |
| Structure (proximity) | +15 | +7 (both `NEAR_*`, different side) | 0 | 0 |
| Liquidity (tightness) | +10 | — | 0 | 0 |
| Event (expiry proximity + VIX regime) | +10 | — | **-5** | 0 |
| Evidence (confidence band: LOW/MODERATE/HIGH) | +10 | — | 0 | 0 |

`MAX_POSSIBLE_SCORE = 85` (every dimension matching exactly). `similarity_pct = round(100 * max(0, raw_score) / 85, 1)`.
Event is the one dimension with an explicit negative contribution on mismatch — per the phase's own worked
example ("Event: different = -5") — event risk genuinely changes a situation's character more than a
same-magnitude miss elsewhere. Every `SimilarityExplanation.breakdown` entry alone reproduces the final
percentage — never a black box a caller has to trust without being able to verify.

## 3. Observation vs. Inference — structurally separated

`MarketMemoryEntry` (the observation: "what Bujji's Intelligence Core concluded at this moment") and
`MarketOutcomeObservation` (the later fact: "what it concluded N sessions afterward") are two distinct,
independently-recorded objects. Neither ever contains a prediction, a probability-of-future-movement
statement, or a recommendation — `MarketOutcomeObservation` records only what a **later, real**
`MarketIntelligenceSnapshot` actually showed (`regime_after`, `volatility_richness_after`, `posture_after`),
never a forecast. `status` is honestly `NOT_YET_OBSERVED` until a real later snapshot is actually supplied
— never guessed or interpolated in the meantime.

## 4. Outcome Memory — factual only, never converted to a signal

`record_outcome_observation()` appends a **separate** `MARKET_OUTCOME_OBSERVED` event — it never edits the
original `MARKET_MEMORY_RECORDED` event. `hydrate_market_memory()` applies it on replay via
`MarketMemoryEntry.with_outcome_observation()`, which itself never mutates in place (returns a new,
otherwise-identical entry via `dataclasses.replace`) — the same "no mutable historical facts" discipline
this project has followed since Phase 18.x. Verified by test: the original in-memory `MarketMemoryEntry`
object is provably untouched after an outcome is recorded and rehydrated.

No aggregate "N% of similar states experienced X" statistic was built this phase — that requires a real
population of multiple outcome-observed entries to aggregate honestly, which a from-scratch phase building
the first entries does not yet have. Computing it from a still-empty or single-entry store would either be
vacuous or misleadingly precise; left as a genuine, separate future step once real history accumulates,
not silently faked with a placeholder now.

## 5. Time Integrity — no-look-ahead, structurally enforced

`find_similar_memories_as_of(target, universe, as_of_time, top_n)` (`memory_query.py`) is the query surface
with the no-look-ahead guarantee: any candidate whose own `as_of_time` is strictly after the query's
`as_of_time` is excluded, regardless of what order it happens to sit in `universe` or when it was actually
recorded into the store. No file in this package calls `datetime.now()` or `now_ist()` — every timestamp is
either threaded in as an explicit parameter or copied verbatim from an already-real
`MarketIntelligenceSnapshot.as_of_time`. Same discipline `reality_memory/catalog.py`'s own `now`-threaded
point-in-time queries already established (Phase 17J.1), applied here to memory retrieval instead of
Reality-tier queries.

## 6. Persistence — reused, not reinvented

`memory_engine.py` is built directly on `bujji.state_persistence.store.EventStore` /
`bujji.state_persistence.models.PersistedEvent` (Phase 15B) — the already-existing, fully generic
append-only JSONL primitive `outcome_memory` itself already sits on. No new persistence mechanism was
invented; this is exactly the "if persistence exists, reuse it" branch of the phase's own instruction.
Two calls recording the same `(intelligence_snapshot_id, underlying)` produce the same `market_memory_id`
— idempotent by construction, never a duplicate memory record.

## 7. Testing

`tests/test_market_understanding_memory.py`, 12 tests, proving all 7 required properties:

1. **Same memory identity** — 2 tests: identical snapshot inputs produce identical `market_memory_id`;
   `intelligence_fingerprint` always equals `intelligence_snapshot_id`.
2. **Replay determinism** — `test_live_and_replay_produce_identical_similarity_result`: LIVE vs
   HISTORICAL_REPLAY produce byte-identical similarity scores and breakdowns, and the same
   `market_memory_id` (since Phase 19.3 already excludes `execution_mode` from the underlying fingerprint).
3. **No future leakage** — 2 tests: a query as-of an early date sees zero candidates from a universe that
   (from the caller's perspective) does not exist yet; a query as-of a middle date sees an earlier entry
   but never a later one, regardless of the universe's actual iteration order.
4. **Deterministic explanation** — 2 tests: identical inputs produce an identical `SimilarityExplanation`;
   the breakdown alone independently reproduces the reported percentage; `find_similar_memories()`'s
   ranking is stable across repeated calls.
5. **No execution vocabulary** — 2 tests: an AST-level identifier scan (order/fill/buy/sell/quantity/
   signal — function, class, variable, and argument names only, not prose) across all four new files; a
   direct check that `MarketMemoryEntry` carries no action/recommendation/signal/side-shaped field.
6. **No broker/order imports** — `test_no_broker_imports_anywhere_in_memory_package`: AST-level import
   scan confirming no file imports anything with `broker`/`fyers`/`order`/`execution_reality` in its
   module path.
7. **Operates without live market** — 2 tests: the full record → hydrate → observe-outcome → rehydrate
   cycle runs entirely against an isolated `tmp_path` `EventStore` (no network, no broker, no live
   connection — and, per this project's own standing discipline since the Phase 18.12 production-data
   incident, never the production DB); a direct test that an outcome observation is a genuinely separate,
   immutable fact rather than an in-place mutation.

## Full regression

Baseline before this phase: 5,805 passed (post Phase 19.4). After Phase 19.5's additions: **5,817 passed,
0 failed** — exactly the 12 new tests. No existing file was modified, only four new self-contained modules
in `bujji/market_understanding/` + one new test file.

## Constraints — verified honored

- ❌ No predictions — `MarketOutcomeObservation` only records what a later, real snapshot actually showed.
- ❌ No BUY/SELL/ENTRY/EXIT vocabulary anywhere — verified structurally (Testing, property 5).
- ❌ No duplicate memory system — verified via the audit doc, reusing `outcome_memory`'s identity
  convention, `market_understanding/similarity.py`'s explainability discipline, and `EventStore` directly.
- ❌ No hidden future information — verified structurally (Testing, property 3) and by no-clock-call
  discipline (property matches Phase 19.2.2's own established pattern for the Intelligence Core brains).

## Final verdict

Bujji can now retrieve, for any given `MarketIntelligenceSnapshot`, an explainable, deterministic set of
historically similar market states — each with a per-dimension breakdown of exactly *why* it is similar —
and, once real time has actually passed, a factual record of what the market's own intelligence looked
like afterward. Never a prediction, never a trade, never a signal. Per the user's own framing: "current
market classification + historical understanding," nothing more, nothing less.

Ready for Phase 19.6 (Adaptive Decision Intelligence — combining current state + memory + context) at the
user's discretion.
