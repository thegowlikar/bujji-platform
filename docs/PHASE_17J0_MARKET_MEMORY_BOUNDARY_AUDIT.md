# Phase 17J.0 — Market Memory Boundary Audit

**Status: AUDIT ONLY. No code.** Defines what "Market Memory" means,
what already exists under similar names, what representation a "market
situation" needs, and one real architectural fork this document
surfaces rather than resolves silently.

---

## 1. Naming collision audit — "Memory" is already a heavily overloaded word here

Two existing, unrelated concepts already use "Memory" in this codebase.
Neither is what Phase 17J means by it:

| Existing name | Package | What it actually is |
|---|---|---|
| `ObservationMemory` | `bujji.market_state_builder.market_state` | A **session-scoped** live-tick buffer feeding `MarketStateBuilder` — resets with the process, holds ticks for the CURRENT session only. Intelligence-tier plumbing, not historical recall. |
| `RegimeMemoryState` / `market_regime_memory` | `bujji.market_regime_memory` (Phase 11) | **Session-scoped** regime-transition statistics ("what has `previous_regime` actually transitioned to, this session") — empirical, honest, sample-size-aware, but bounded to one running session, not a multi-year archive. Intelligence-tier. |

**What Phase 17J means by "Market Memory" is neither of these**: a
**durable, cross-year** capability answering "has a situation like
today's occurred before, across the full 1998–2026 (or resolution-
bounded) Reality corpus." Real-time-session scope vs. multi-year-archive
scope is the load-bearing difference — reusing "Memory" bare would be
exactly the collision class this engagement has caught and renamed
around repeatedly (`MarketState`/`MarketRealitySnapshot`, 17H.5;
`HistoricalCandle`/`HistoricalObservation`, 17H.3).

**Decision: the new concept is named `RealityMemoryEvent`, in a new
package `bujji/reality_memory/`.** Not `MarketMemory` (too close to
`market_state`/`market_regime_memory`'s existing territory), not
`ObservationMemory` (already taken, different scope).

## 2. What already exists, Memory-adjacent, inside Reality — reused, not rebuilt

- **`MarketRealityTimeline`/`RealityQuery`** (17H.6) — a condition
  *filter* over stored `MarketRealitySnapshot` rows ("find days where
  VIX was 10–13"). This already IS a form of retrieval-across-time —
  per the Reality/Memory boundary drawn in 17I.0 ("Memory begins the
  moment two or more Reality facts are retrieved together for
  comparison, even with zero computation"), `MarketRealityTimeline`
  sits **on** that boundary, correctly built Reality-only (raw filters,
  no derived fields).
- **`RealityCoverageIndex`** (17I.4) — an availability manifest ("what
  do we have for date X"), not a comparison tool at all.
- **Neither does similarity/pattern-matching across multiple
  dimensions at once** — that capability does not exist yet anywhere in
  this codebase. This is the actual gap Phase 17J exists to close.

## 3. What is a "market situation"? — the representation question

A `RealityMemoryEvent` needs a defined granularity and a defined field
set before anything else can be built.

### Granularity — recommend: daily, matching existing certified coverage

Three options, evaluated against what's actually populated today
(17I.0/17I.4 inventory):

| Option | Real data support today |
|---|---|
| Single trading day | `MarketRealitySnapshot` is already exactly this shape (17H.5) — 252 rows already exist, spot/futures/VIX OHLC, COMPLETE/PARTIAL/EMPTY completeness already tracked. |
| Multi-day window (e.g. "the last 5 days") | No existing Reality-tier structure spans multiple days as one unit. Would require new composition logic (not a new store) over already-existing daily rows — real but not-yet-built work. |
| Intraday moment (a specific 5-min bar or a live tick) | 5-min Reality exists (17H.9, 175K+ rows/instrument) and live depth is pending its first certified evidence (17I.3, not yet run). Structurally poorer coverage (2017/2018→today vs. 1998/2008/2018→today for daily) and the microstructure leg isn't proven live yet. |

**Recommendation: start at daily granularity**, directly reusing
`MarketRealitySnapshot`'s existing shape and 252 already-populated
rows, deferring multi-day-window and intraday-moment representations as
later, explicitly separate extensions — not because they're
unimportant, but because daily is the only granularity with full,
certified, already-verified coverage back to 1998/2008/2018.

### Field set — Reality facts actually available for a daily situation (per 17I.0/17I.4)

| Field | Source | Available today |
|---|---|---|
| Spot OHLC | `MarketRealitySnapshot.spot` | Yes, 1998→today |
| Futures OHLC | `MarketRealitySnapshot.futures` | Yes, 2018→today |
| VIX OHLC | `MarketRealitySnapshot.vix` | Yes, 2008→today |
| Futures OI | Depth capture (17I.2), pending live evidence (17I.3) | Not yet certified live |
| Bid/ask depth | Depth capture (17I.2), pending live evidence (17I.3) | Not yet certified live |
| Completeness | `MarketRealitySnapshot.completeness` | Yes, already tracked |
| Lineage / certification refs | `MarketRealitySnapshot.source_observation_ids` | Yes, already tracked |

Everything in this table is a **stored, literal fact** — no
transformation applied. This is the uncontroversial part of the field
set.

## 4. The fork this audit surfaces, not resolves

**A `RealityMemoryEvent` built from literal facts alone may not be
comparable in a meaningful way.** "VIX closed at 14.5" is a fact; but
whether that is HIGH or LOW depends on VIX's own historical range —
comparing raw levels across a 28-year corpus that includes both a
sub-10 VIX regime and a 70+ VIX crash regime, with no normalization,
risks matching on coincidence rather than genuine similarity. This is
precisely why the earlier informal sketch of "a market situation"
(this conversation, prior turn) reached for `% down today`, `futures
basis in points`, `drawdown from recent high` — none of which are
literal stored facts; all are simple arithmetic over stored facts.

This is a real fork, not a rhetorical one:

**Option A — Reality-only `RealityMemoryEvent`** (strict, matches
17H.6's precedent exactly): store only literal OHLC/VIX-level/OI values
per day. Comparison/similarity logic (whatever normalizes or transforms
these) becomes Understanding-tier, built in a later, separate phase.
`RealityMemoryEvent` itself needs no new store beyond composing
existing `MarketRealitySnapshot` rows — this option requires **zero
new persistent storage**.

**Option B — Memory carries minimal derived arithmetic** (% change,
basis-in-points, distance-from-N-day-high): directly useful for
comparison without a separate Understanding-tier pass first, but
crosses a line this project has held firm since `PHASE_17H2` ("No:
RSI, EMA, MACD... Reality must remain immutable") — and Memory has
never previously been allowed to compute anything either (17H.6's own
explicit "Reality-only now" decision). Adopting this would be the
**first computed/derived value ever persisted above the Reality tier**
in this engagement, and would need its own new store (Understanding-tier
data has never been durably persisted before — `market_narrative`/
`market_regime_memory` are session-scoped and never written to disk as
a corpus).

Both are legitimate, defensible designs. Neither was silently assumed.

**Decision (2026-08-14): Option A — Reality-only.** `RealityMemoryEvent`
stores literal facts only, sourced directly from `MarketRealitySnapshot`.
No new store. Any normalization/comparison logic is explicitly deferred
to a separate, later, deliberately-scoped Understanding-tier phase
(17J.2+), not built alongside 17J.1.

## 5. What Phase 17J.1+ would build, once the fork is resolved

Regardless of which option is chosen:
- `bujji/reality_memory/models.py` — `RealityMemoryEvent` dataclass,
  immutable, sourced from `MarketRealitySnapshot` rows (no new
  observation identity system).
- A read-only index/store composing already-existing
  `MarketRealitySnapshotStore` rows across the full available date
  range — reused, not duplicated, following the same discipline as
  `RealityCoverageIndex` (17I.4).
- Similarity/matching logic is explicitly OUT of 17J.1's scope either
  way — that is Phase 17J.2 (Understanding-tier), a separate, later,
  deliberate decision, not built here.

## 6. Restrictions carried forward, unchanged

No RSI/EMA/MACD/Supertrend, no trend/regime labels, no similarity
scoring, no ML, in this phase or the next. The only question genuinely
open is the narrow one in §4: literal facts only, or literal facts plus
simple arithmetic (% change / basis points / distance-from-high) — not
whether indicators or classification belong here (they do not, in
either option).
