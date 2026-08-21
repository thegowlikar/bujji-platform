# Phase 16J — Market Memory & Learning Capability Audit

**Audit only. No code, no implementation, no new storage. Regression unchanged at 5,167 / 0 failed.**

---

## 0. THE HEADLINE

> **Bujji already has a complete knowledge-promotion architecture. It has no similarity retrieval and no population.**

`bujji/msi_market_learning/` (MLE) — 8 modules, tested, **zero internal importers** — implements a 12-stage Knowledge Candidate lifecycle:

```
OBSERVED → REPEATED → EVIDENCE → KNOWLEDGE → ENGINEERING_CANDIDATE
→ APPROVED → IMPLEMENTED → REPLAY_VALIDATED → PRODUCTION_VALIDATED
→ ACTIVE → DECAYING → ARCHIVED
```

Forward-only, with two disclosed exceptions (`ACTIVE→DECAYING/ARCHIVED`, and `DECAYING→ACTIVE` because *"decaying knowledge can recover"*).

Its own First Principle, from the source:

> *"MLE is an EVIDENCE ENGINE, not a learning/adaptive system… No thresholds here are ever tuned against real trading outcomes — they are declared once, disclosed, and never fit to data."*

**This is the research→production promotion gate I designed in Track B without knowing it existed.** Seventh reuse finding this session.

---

## 1. Existing memory capabilities

| System | Files | Tests | Scope | Searchable by |
|---|--:|--:|---|---|
| `outcome_memory` (15N) | 5 | 10 | **cross-session**, durable | session · strategy_family · entry_regime · entry_direction · underlying_symbol |
| `msi_market_learning` (MLE) | 8 | 2 | knowledge candidates | lifecycle stage, evidence tier |
| `market_regime_memory` | 3 | 4 | regime continuity | `RegimeMemoryState` → snapshot |
| `market_state_builder.ObservationMemory` | — | — | within-session observations | recovery-oriented (15D) |
| `context_window` | 3 | 2 | intra-session horizons | short/medium/session |
| `market_narrative` | 3 | 3 | narrative text | — |
| `market_episode` | — | — | episodes | deliberately non-interpretive |

### What is genuinely searchable today

| Store | Query surface |
|---|---|
| Trade history | `outcome_memory.filter_records()` — 5 exact-match fields |
| Entry snapshots | **stored verbatim, not indexed** (`EntrySnapshot.entry_greeks`, `entry_premium_behaviour`) |
| Market snapshots | JSONL, **linear scan only** |
| Evidence graph | `evidence_ids` resolve (115/115 verified) — but **no index** |
| Phenomena history | not persisted as a series |
| Regime history | `RegimeMemoryState` — current state, not a searchable history |

**Pattern: capture is good, indexing is absent.** Almost everything needed is *stored*; almost nothing is *queryable* along the dimensions that matter.

### MLE's five evidence dimensions — deliberately unblended

```python
repeatability: int      # real occurrence_count
consistency: str
causal_validity: bool   # every citation passed the contemporaneous-evidence check
replay_support: bool    # ≥1 real replay run corroborates
market_diversity: int   # DISTINCT market-day classifications observed under
```

> *"never a single blended score (that would hide which dimension is actually weak)"*

This is the right call and worth preserving under pressure: a single "confidence 0.83" would conceal that a pattern was seen 40 times on one market day and never replayed.

`earliest_causal_timestamp` — *"the earliest real timestamp at which this pattern's evidence existed"* — is **no-look-ahead applied to knowledge itself**, which I had not seen anywhere else in the codebase.

---

## 2. Historical question capability

> *"When similar market conditions occurred before, what happened?"*

**Answer: no.** Not for any of the seven dimensions.

| Dimension | Captured | Searchable | Blocker |
|---|:--:|:--:|---|
| Underlying regime | ✅ | ✅ | `query_regime_performance` exists |
| Volatility regime | ⚠️ in snapshot | ❌ | not extracted to a query field |
| Options positioning | ⚠️ in snapshot | ❌ | not extracted |
| Futures structure | ❌ | ❌ | never captured |
| Liquidity state | ⚠️ sparse | ❌ | 1/174 cycles had both ATM bid/ask |
| **Time of day** | ✅ in timestamps | ❌ | no derived field |
| **Expiry proximity** | ✅ `expiry_date` present | ❌ | DTE never computed as a query field |

**Only one of seven is answerable today** — and only by exact regime match, not similarity.

`context_window` is admirably honest about this:

```python
HistoricalContext(available=False, similar_sessions_found=0, outcomes=None,
    reason="no prior-session index wired yet -- this session's own record only")
```

> *"No cross-session index exists yet — honestly disclosed as unavailable rather than fabricated."*

The capability was designed, its absence measured, and the honest `available=False` shipped rather than a fabricated answer. That is the correct behaviour, and it is also a precise statement of the gap.

**Time of day and expiry proximity are the cheapest wins.** Both are already present in captured timestamps; neither has been promoted to a query dimension. For an options desk, DTE is arguably the single most conditioning variable there is — a 0DTE setup and a 21DTE setup are different markets.

---

## 3. Missing memory dimensions

| | |
|---|---|
| **Captured** | trade outcomes · lifecycle · attribution · entry Greeks/premium behaviour · regime · evidence IDs · option chain OI · verbatim snapshots |
| **Derived, not stored as series** | IV · Greeks over time · phenomena · liquidity · premium velocity |
| **Searchable** | session · strategy_family · entry_regime · entry_direction · underlying_symbol — **5 fields, exact match only** |
| **Impossible to reconstruct** | per-tick bid/ask · depth · futures · intra-30s path · exchange-time ordering |

The middle row is the important one: **IV, Greeks and phenomena are recomputable — but only if raw inputs were captured at sufficient resolution.** Today they were not. That converts a "derived, so recompute later" position into "permanently unavailable."

---

## 4. Similarity model design

**Verified absent:** `cosine` 0 · `euclidean` 0 · `knn` 0 · `embedding` 0 · `similarity` 0. The `nearest` hits (15 files) are all *nearest expiry/strike* — contract selection, not retrieval.

### Recommendation: **rule-based + regime-conditioned first. Not feature-vector similarity.**

| Approach | Verdict |
|---|---|
| **Rule-based similarity** | ✅ **start here** — extends existing exact-match filtering with tolerance bands (DTE ±2, IV percentile bucket, regime match). Explainable, testable, no new dependency. |
| **Regime-conditioned memory** | ✅ **natural fit** — `market_regime_memory` + `query_regime_performance` already exist; conditioning is the established idiom |
| **Feature-vector similarity** | ⚠️ **premature** — requires a stable, versioned feature set. Features have no `calc_version` in any persisted record yet (Phase 16C finding). Vectors built from unversioned features would silently mix definitions. |
| **Nearest-neighbour retrieval** | ⚠️ **later** — needs both a vector space and a population |

**The sequencing argument matters more than the technique.** A distance metric over features whose definitions can silently change produces confident nonsense. `calc_version` stamping (Phase 16D built the mechanism, no producer stamps yet) is a genuine prerequisite, not a nicety.

### The sample-size trap

Conditioning on regime × volatility × positioning × DTE × time-of-day *feels* like rigour. It is actually **exponential cohort shrinkage**. With zero records today and realistically a few hundred after months of shadow trading, five-way conditioning yields cells of size 0–2.

`MIN_SAMPLE_SIZE=3` and honest `INSUFFICIENT_HISTORY` already exist in `outcome_memory.query`. **They must survive this pressure** — the temptation will be to relax them precisely when a beautiful-looking cohort has two members.

---

## 5. Two documentation-drift findings

| Finding | Status |
|---|---|
| `models.py:51` cites *"enforced structurally, see isolation.py"* | ❌ **`isolation.py` does not exist** |
| `__init__.py:9` cites *"verified by tests/test_mle_isolation.py"* | ✅ **that file DOES exist** |

**The isolation property itself is real:** `engine.py` imports only `hashlib`, `typing` and its own siblings — no production write path. Verified by import inspection *and* by `test_mle_isolation.py`.

So this is stale documentation, not a safety gap. Worth correcting because a docstring pointing at a non-existent enforcement file is exactly how a real guarantee gets assumed rather than checked.

*(I nearly mis-reported this: my first grep for MLE tests used a pattern that missed `test_mle_isolation.py`. Verified before concluding.)*

---

## 6. Final recommendation

### EXISTING — reuse unchanged

| | |
|---|---|
| 12-stage knowledge promotion lifecycle | `msi_market_learning` |
| 5 unblended evidence dimensions | `EvidenceDimensions` |
| Causal-validity / `earliest_causal_timestamp` | MLE |
| Cross-session durable outcome memory | `outcome_memory` (15N) |
| Regime memory + regime-conditioned query | `market_regime_memory` + `query_regime_performance` |
| Honest `HistoricalContext(available=False)` | `context_window` |
| Sample-sufficiency gating | `MIN_SAMPLE_SIZE` / `INSUFFICIENT_HISTORY` |
| Verbatim snapshots (raw material for future indexing) | `outcome_memory` |
| Structural production isolation | MLE + `test_mle_isolation.py` |

### EXTENSION REQUIRED

| # | Extension | Size |
|---|---|---|
| 1 | **DTE + time-of-day as query dimensions** | small — **highest value/effort ratio** |
| 2 | Volatility-state + positioning query extracts | small — data already in snapshots |
| 3 | Rule-based tolerance-band matching | medium |
| 4 | Connect MLE (0 importers today) | medium |
| 5 | Phenomena history as a persisted series | medium |
| 6 | Fix the two stale MLE doc references | trivial |

### NEW CAPABILITY

| # | Capability | Prerequisite |
|---|---|---|
| 1 | Cross-session index | — |
| 2 | Similarity retrieval | stable versioned features |
| 3 | Feature-vector space | `calc_version` stamping |
| 4 | **A real population** | Gate 1 → shadow trading |

---

## 7. Verdict

**Bujji's learning architecture is more complete than its learning capability.**

The promotion path (OBSERVED→ARCHIVED), the anti-overfitting discipline (unblended dimensions, thresholds never fit to data), the causal-validity check, the production isolation, and the honest unavailability signalling all exist and are tested.

What is missing is narrower than expected:
1. **Query dimensions** — DTE, time-of-day, volatility, positioning (data captured, not indexed)
2. **Similarity retrieval** — genuinely absent
3. **A population** — zero records

**The binding constraint is still population, not architecture.** Every retrieval and learning capability above will return `INSUFFICIENT_HISTORY` until real sessions run — which remains Gate-1-gated.

### Recommended next, none requiring Gate 1

1. Add DTE + time-of-day query dimensions (cheapest, highest value for an options desk)
2. Add volatility/positioning query extracts
3. Fix the two stale MLE doc references
4. Design rule-based tolerance-band matching
5. Design the MLE ← `outcome_memory` connection

---

**No code written. Frozen untouched: TickStore · storage backend · watermark value · ingestion topology · Stack A. Memory packages diff: 0 lines. Regression 5,167 / 0 failed.**
