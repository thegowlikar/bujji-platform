# Phase 16C — Decision Lineage & Evidence Audit

**Regression: 5,146 passed / 0 failed** (5,137 + 9 look-ahead safety tests).
No frozen architecture touched. No TickStore, fabric, watermark, storage-tier, topology, or Stack A migration.

---

## THE HEADLINE

> **Bujji can prove WHAT evidence produced a decision. It cannot prove WHICH CODE turned that evidence into that decision.**

The evidence graph is **complete, populated and walkable**. The version graph is **entirely absent**. That single asymmetry explains Phase 15P's 684/708 divergence — there was no version stamp for anything to compare against, so nothing detected that the archived corpus had been produced by different code.

This is materially better than my earlier assessment (I previously reported "source_event_ids fails" — **that was wrong**, see §3).

---

## 1. Current lineage graph

| Transition | Output id | Evidence refs | `as_of` | Versions | Epistemic |
|---|---|---|:--:|:--:|---|
| Observation → Domain assessment | `OBS-*`, `MSA-*` | — | ❌ | schema only | per-domain `confidence` |
| Domain → Consensus | `b6f11cd9…` | ✅ `supporting_assessment_ids` (4) | ❌ | schema only | `consensus_level`, `evidence_sufficiency` |
| Domain → Opportunity | `MOA-*` | ✅ **`evidence_ids` (115)** | ❌ | schema only | `confidence_level`, `opportunity_quality` |
| {Opportunity, Consensus} → Eligibility | `7168ab8e…` | ✅ `supporting_assessment_ids` (2) | ❌ | schema only | `eligibility_confidence` |
| Eligibility → Trade Intent | `assessment_id` | ✅ `supporting_assessment_ids` | ❌ | schema only | `readiness` |
| Intent → Candidate | `candidate_id` | ✅ `source_cycle_id` | ⚠️ `as_of_date` (only one in repo) | schema only | `construction_confidence` |
| Candidate → Position | `position_id` | ✅ `candidate_id`, `source_cycle_id` | ❌ | schema only | — |
| Position → Management | — | ✅ `position_id`, `cycle_id` | ❌ | schema only | `evidence_confidence` |
| Position → P&L | `structured_exit` | ✅ per-leg `leg_id` | ❌ | schema only | `pnl_status` |
| Position → Attribution | — | ✅ `position_id` | ❌ | schema only | `readiness`, `strength` |
| Attribution → Memory | `memory_id` | ✅ **verbatim snapshots** | ❌ | schema only | 4-state epistemic |

**Verified on real persisted data** (`SHADOW-OBSERVATORY-2026-08-06`, cycle 100):

```
opportunity.assessment_id            : MOA-89d9d432c0c0eeb62052bbf7
opportunity.evidence_ids             : 115 ids  (MSA-*, OBS-*)
consensus.supporting_assessment_ids  : 4        (MDA-*, MPPA-*)
eligibility.supporting_assessment_ids: 2 → [MOA-89d9…, b6f11cd9…]
```

**Evidence closure: 115/115 resolvable** within the same session's `intelligence_cycle.jsonl`. The DAG is connected, not merely annotated.

---

## 2. Timestamp vocabulary

Measured across the decision chain's models:

```
timestamp              41 uses     ← one undifferentiated field
entry_timestamp         4
source_cycle_id         2
recorded_at             2
opened_at / closed_at   2
acquisition_timestamp   1  (market_observation only)
normalization_timestamp 1  (market_observation only)
as_of_date              1  (shadow_trade_construction only)

as_of · event_time · exchange_time · receive_time ·
processing_time · decision_time         →  0 occurrences
```

| Concept | Where represented | Status |
|---|---|---|
| Event / exchange time | `timestamp` (conflated) | ⚠️ **implicit** |
| Receive time | — | ❌ **missing** |
| Processing time | — | ❌ **missing** |
| Decision time | `timestamp` (conflated) | ⚠️ **implicit** |
| `as_of` | `epistemics.Lineage` (new) + `as_of_date` (1 place) | ❌ **absent from every persisted model** |
| Acquisition / normalization | `ObservationProvenance` | ✅ **the only 2-way split in the repo** |

**Canonical vocabulary established** (implementation deferred, watermark untouched):

```
exchange_time   authoritative for windowing/ordering/analytics
receive_time    latency, staleness, feed health
processing_time lineage only
as_of           the instant a query is answered "as if"  — REQUIRED on reads
decision_time   when a decision was committed (an authoritative event)
```

---

## 3. Provenance inventory — **and a correction**

**I previously reported that `source_event_ids` fails. That was wrong.** The decision chain carries real evidence references throughout (`evidence_ids`, `supporting_assessment_ids`, `assessment_id`), and they resolve. Correcting it here rather than defending it.

| Shape | Count | Grade |
|---|--:|---|
| `provenance: str` (free-text) | **50 packages** | **DESCRIPTIVE** — names the producing *function* (`"msi_consensus.engine.compute_consensus"`). No source, temporal or calculation identity. |
| `ObservationProvenance` | 1 (market_observation) | **AUTHORITATIVE-GRADE** — originating_source, acquisition_timestamp, normalization_timestamp, origin, version, transformation_history |
| 8 other `*Provenance` dataclasses | 8 | **MIXED** — mostly Stack A / peripheral |
| `evidence_ids` / `supporting_assessment_ids` | decision chain | **AUTHORITATIVE for source identity** — references, never copies |
| `schema_version` | 171 files | **STRUCTURE version only**, not calculation version |

**Sufficient for deterministic replay?**
- Source identity: ✅ **yes**
- Temporal identity: ⚠️ partial (one conflated `timestamp`)
- Calculation identity: ❌ **no**

**Recommendation: adapt, do not replace.** `epistemics.Lineage.from_observation_provenance()` already adapts the richest existing shape. The 50 free-text `provenance` strings become the `code_path` component of `Lineage` — they are genuinely useful, just insufficient alone. **No provenance subsystem is created.**

---

## 4. Look-ahead findings

### Structural guarantee — verified, not asserted

**Zero wall-clock reads across 10 of 11 decision-path packages** (AST-verified, now a permanent test):

```
msi_consensus · msi_decision_synthesis · msi_strategy_eligibility ·
msi_strategy_selector · msi_trade_intent · position_lifecycle ·
position_intelligence · position_management · outcome_attribution ·
portfolio_intelligence                                    → 0 each
```

Every engine takes its timestamp as an explicit argument. **A function that cannot observe the present cannot observe the future** — this is the strongest look-ahead guarantee available short of the Market Data Fabric.

### The one exception — a real, bounded replay-divergence vector

`shadow_trade_construction/engine.py:181`:
```python
as_of_date = source_cycle_id[:10] if source_cycle_id else datetime.now(timezone.utc)...
```
Normal path uses the source cycle's own event time. The fallback fires only when `source_cycle_id` is empty — but if it does, replay would date construction to **replay time**, potentially selecting a different expiry. Now pinned by test to exactly one occurrence so it cannot spread.

### Memory isolation — intact

`outcome_memory` imports **zero** decision-path modules; the decision path imports **zero** outcome modules. Both directions now asserted.

### Not yet enforceable

| Vector | Status |
|---|---|
| Later candle influencing earlier decision | ⚠️ **no `as_of` on any store read** — enforcement point is the future `TickStore`/`CandleStore` signature |
| Session-end info influencing intraday | ⚠️ same |
| Replay using post-decision info | ⚠️ same |
| P&L/outcome influencing decisions | ✅ blocked (import isolation) |

---

## 5. Decision reproducibility matrix

| # | Decision | Verdict | Why |
|---|---|---|---|
| **A** | Opportunity | **PARTIALLY_REPRODUCIBLE** | Evidence complete (115/115 resolve) + engine is pure. But **no `calc_version`** — replaying with today's code gives a *different* answer and nothing flags it. **Phase 15P is the proof.** |
| **B** | Trade Intent | **PARTIALLY_REPRODUCIBLE** | `supporting_assessment_ids` resolve; same version gap. |
| **C** | Risk Approval | **NOT_REPRODUCIBLE** | **No risk layer exists in Stack B.** Nothing to reproduce. |
| **D** | Position Opening | **REPRODUCIBLE** | `position_id` deterministic from (session, candidate, entry_time); EventStore replay proven byte-identical (15G/15K/15L). |
| **E** | Position Management | **REPRODUCIBLE** | Same reducer live and replay; assessments persisted as events (15I). |
| **F** | Position Closing | **REPRODUCIBLE** | `structured_exit` + per-leg lineage; P&L re-derivable from `pnl.py` (15K); proven across restart/torn/duplicate. |

**The split is sharp and informative:** everything the **EventStore** owns (D/E/F) is reproducible. Everything the **JSONL intelligence corpus** owns (A/B) is only partially reproducible. C doesn't exist.

**Root cause:** EventStore records *events* with stable ids and replays them through the *same reducer*. The JSONL corpus records *conclusions* with no version stamp.

---

## 6. Authoritative vs derived

| Artifact | Class | Reconstructable? |
|---|---|---|
| `quotes.jsonl` (bid/ask observations) | **AUTHORITATIVE** | ❌ **never** — gone if not captured |
| `market_snapshots.jsonl` (option chain) | **AUTHORITATIVE** | ❌ **never** |
| Raw ticks | **AUTHORITATIVE** | ❌ **not captured at all today** |
| `intelligence_cycle.jsonl` | **DERIVED** (recorded as conclusions) | ⚠️ only if inputs + versions retained |
| `intelligence_snapshots.jsonl` | **DERIVED** | ⚠️ same |
| EventStore lifecycle records | **AUTHORITATIVE** (real state transitions) | ✅ replayable |
| `shadow_validation.jsonl` | **DERIVED** | ✅ |
| Outcome memory records | **AUTHORITATIVE** (immutable facts) | ✅ verbatim snapshots |
| Decisions acted upon | **AUTHORITATIVE EVENT** | ✅ |
| Forming candle | **EPHEMERAL** | ✅ always |

### What becomes permanently unreconstructable without raw capture

1. **Per-tick bid/ask/size** — 30s REST snapshots cannot recover intra-interval quotes
2. **True OHLC high/low** — sampled snapshots systematically understate wicks
3. **Microstructure** — spread expansion, liquidity withdrawal, premium acceleration
4. **OI intra-cycle movement** — only endpoints observed
5. **Exchange-time ordering** — never persisted at all

**These are the irreversible losses.** Every day without raw capture is a day of history that cannot be recovered later.

---

## 7. Missing contracts

| Missing | Impact | Enforcement point |
|---|---|---|
| `calc_version` on derived artifacts | **A/B not reproducible** | producer side — cheap now |
| `code_version` | can't detect code drift (15P's blind spot) | producer side |
| `config_version` tied to decisions | config drift invisible | producer side |
| `as_of` on store reads | look-ahead unpreventable | **future store signatures** |
| Explicit 5-way timestamps | event/arrival conflated | model side |
| Risk layer (L10) | decision C doesn't exist | Stack A harvest |

---

## 8. Safe changes we can make now

| Change | Why safe |
|---|---|
| ✅ **Look-ahead safety tests (DONE, 9 tests)** | AST + corpus only |
| Attach `Lineage` to *new* derived artifacts | additive; epistemics is stdlib-only |
| Stamp `calc_version`/`code_version` at producers | additive field |
| Adapter: free-text `provenance` → `Lineage.code_path` | no rewrite |
| Split `timestamp` → explicit fields on **new** models | additive |
| Fix the `shadow_trade_construction` fallback | 1 line, pinned by test |

## 9. Must wait for Gate 1 / Fabric

`as_of` on store reads · tick-level lineage · exchange-time ordering · candle→tick lineage · watermark · replay parity over raw ticks.

**Reason:** every one attaches to a store signature that does not exist yet, and inventing it now would bake in an assumption Gate 1 exists to remove.

---

## 10. Exact next recommended phase

**Phase 16D — Version Identity Stamping (producer-side, no fabric).**

Close the *one* asymmetry this audit found: evidence graph complete, version graph absent.

**Scope:** stamp `calc_version` (content hash), `code_version` (git sha), `config_version` on decision-chain producers; adapt free-text `provenance` into `Lineage`; fix the `shadow_trade_construction` fallback; add a replay-divergence detector that compares stamped versions instead of silently diverging.

**Why this next:** it is the highest-value work that needs no Gate 1 evidence, it upgrades A/B from PARTIALLY_REPRODUCIBLE toward REPRODUCIBLE, and it would have caught Phase 15P's stale-corpus problem automatically instead of costing a forensic investigation.

**Explicitly not in scope:** TickStore, fabric, watermark, storage tier, ingestion topology, Stack A migration.

---

**Working tree uncommitted at `b148e39`. Gate 1 still blocked on token refresh + non-expiry session.**
