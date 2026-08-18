# Bujji OS v1.0 — Interface Map

**The architecture contract every future phase plugs into.** Written to prevent the exact failure mode this engagement already found once: three intelligence lineages (Cycle 1, Phase 19.x, MSI/Trading Brain) built independently, each internally coherent, never unified. This document exists so that never happens a fourth time.

**Bujji is not missing a brain. It has three.** The job from here is connecting organs, not growing a new one.

---

## 1. The canonical pipeline

Every future phase's code must be traceable to exactly one stage below. A phase that doesn't fit any stage is either mis-scoped or belongs in a stage this document needs to be revised to add — never bolted on sideways.

```
Market Data
    ↓
Observation Schema
    ↓
MIC (Market Intelligence Core)
    ↓
Market Memory
    ↓
Strategy Intelligence
    ↓
Opportunity Engine
    ↓
Decision Brain
    ↓
Risk Governor Bridge
    ↓
Execution Intelligence
    ↓
Shadow Result
    ↓
Learning Update
```

Note the one deliberate reordering from the roadmap's original feedback-loop diagram: **Market Memory sits between MIC and Strategy Intelligence**, not after Decision. Memory is a real-time *input* to how a strategy's confidence gets modified (exactly like MIC context already is, per Cycle 1's own "MIC modifies confidence, never evidence" invariant) — not only a post-hoc record. Phase 20.15.1 (below) exists specifically to make this true.

## 2. Stage-by-stage: owner, current implementation, contract

| Stage | Owning lineage today | Real module(s) | Contract this stage must honor |
|---|---|---|---|
| **Market Data** | Cycle 1 (Phase 20.13) | `bujji.broker.fyers.FyersBroker` (read-only methods only), `bujji.historical_reality.store.HistoricalObservationStore` | Real only. No fabricated candle, VIX, or quote ever crosses this boundary — every prior phase's own anti-fabrication tests enforce this already; nothing here changes it. |
| **Observation Schema** | Cycle 1 + Phase 19.x (parallel, not yet merged) | `bujji.core.models.Candle`, `bujji.market_reality_snapshot` (Phase 19.x) | The schema a downstream stage receives must be the same shape regardless of which lineage produced it. Not true today — this is the first concrete unification target, not yet phased. |
| **MIC** | Cycle 1 | `bujji.mic_v0` (session-level, Phase 20.1), `bujji.mic_v0_validation.intraday_validation` (intraday, Phase 20.1C, validated but not yet the live source) | Classification only — regime/volatility/risk, never a prediction, never a decision. Already enforced by `test_safety_boundary.py` at every layer. |
| **Market Memory** | Phase 19.x (`market_regime_memory`, `outcome_memory`, `reality_memory`, Phase 19.5's `MarketMemoryEntry`) — **not yet connected to Cycle 1's live path** | Phase 20.15.1 is the phase that makes this connection real (§3). | Memory may only ever modify confidence/context, exactly like MIC — never fabricate or override the evidence-based score Phase 20.5 computes. Same invariant, extended to a second input. |
| **Strategy Intelligence** | Cycle 1 | `bujji.strategy_intelligence` (Phase 20.5) | `evidence_score` is provably context-independent (existing test: `test_mic_context_never_changes_evidence_score`); Phase 20.15.1 must add the equivalent test for memory. |
| **Opportunity Engine** | Cycle 1 | `bujji.opportunity_intelligence` (20.6), `bujji.opportunity_ranking` (20.7) | Unchanged by this document — already evidence-gated, already tested. |
| **Decision Brain** | Cycle 1 | `bujji.decision_orchestration` (20.10) | Unchanged — `FinalDecision` remains the single canonical decision object every downstream stage consumes. This is the object the Risk Governor Bridge (next stage) receives; it is never recomputed by any stage after this one. |
| **Risk Governor Bridge** | **New, Phase 20.17** — bridges Cycle 1 into the real MSI risk governor | Real target: `bujji.trading_brain.risk_governor` (`capital_safety_governor.py`, `risk_budget_governor.py`, `portfolio_limits.py`, `defined_risk.py`) | Reads a `FinalDecision`, never recomputes qualification/ranking/allocation-class; produces a real risk-governor-reviewed sizing recommendation. Still never places an order. This is the highest-leverage, and highest-risk-of-scope-creep, stage in the whole map — its own phase report must show the exact read boundary (what it reads from Cycle 1, what it reads from the real governor, what crosses neither direction). |
| **Execution Intelligence** | **New, design-first** — bridges the risk-governed recommendation toward (never into) real order flow | Real target: `msi_trade_construction`, `msi_margin_bridge`, `msi_execution_planning`, `bujji.broker.*` | **Explicit build/delay split** (this session's own refinement, see §4) — broker abstraction, order lifecycle model, reconciliation, failure handling, and a real execution *simulator* are BUILD-NOW; real order placement, capital exposure, and autonomous execution are DELAY, gated on the criterion in §4. |
| **Shadow Result** | Cycle 1 | `bujji.shadow_decision_runtime` (20.11), `bujji.shadow_market_campaign` (20.12), `bujji.live_shadow_runner` (20.13) | Already built, tested, live-feed-wired. No change required by this document. |
| **Learning Update** | **New, Phase 20.15 (feeds back into Market Memory) + 20.16/20.17/20.21+ (feeds back into Strategy Intelligence / Risk Governor)** | — | Every "improvement" this stage makes must cite the specific real shadow sessions that justified it (per the roadmap's §C gating) — never a threshold change with no cited evidence. |

## 3. Phase 20.15.1 — Market Memory Foundation (inserted before strategy expansion, per this session's own correction)

**Sequencing change from the original roadmap draft:** Phase 20.15 (memory *bridge* — plumbing shadow output into existing memory stores) now splits into two:

- **20.15 — Memory write path**: shadow campaign output → real `MarketRegimeMemory`/`OutcomeMemory`/`MarketMemoryEntry` stores. (Unchanged from the original roadmap.)
- **20.15.1 — Memory read path, inserted BEFORE Phase 20.16 (strategy expansion)**: the missing half — real memory becomes a genuine *input* to Strategy Intelligence's confidence computation, with a similarity query ("has Bujji seen conditions like this before, and what happened") and a disclosed, bounded confidence adjustment, exactly mirroring how `MarketContext` already modifies confidence today. Required proof, on real data: an example of the shape

  ```
  Similar conditions found: N real historical/shadow occurrences
  Outcome distribution: [real counts, never estimated]
  Confidence adjustment: disclosed magnitude and direction, capped like every other confidence modifier in this codebase
  ```

  This is the compounding-advantage layer — Bujji "teaches itself how markets behave" rather than "acquires more strategies." It must exist and be proven on real data **before** Phase 20.16 (strategy expansion) — a new strategy family evaluated without a working memory layer would only ever see MIC's confidence modifier, missing the whole point of building memory first.

## 4. Execution boundary — locked, explicit, revisited only by a future, separately-scoped phase

**Build now** (Phase 20.18/20.19, still design/scaffolding — no code path to a live order):
- ✅ Broker abstraction (already exists — `bujji.broker.base.Broker`, `FyersBroker`, `PaperBroker`, `HybridPaperBroker`, `disable_live_execution` guard, all live-verified in Phase 20.13)
- ✅ Order lifecycle model (states, transitions — design target: audit `msi_trade_construction`/`msi_execution_planning` first, per the roadmap's own reuse-first discipline)
- ✅ Reconciliation (audit `position_group_fill_reconciliation.py`, already real, in `trading_brain.risk_governor`)
- ✅ Failure handling (same "never crash, honest degradation" discipline every phase since 20.11 has used)
- ✅ Execution simulator (a `PaperBroker`-backed path that exercises the full lifecycle model without ever touching `FyersBroker`'s execution methods — those stay behind `disable_live_execution()`)
- ✅ Audit trail (extends Phase 20.13's own append-only artifact persistence, never a new mechanism)

**Delay, explicitly, until the criterion below is met:**
- ❌ Real order placement
- ❌ Capital exposure
- ❌ Autonomous execution

**Gating criterion, stated as a chain, per this session's own framing:**

```
Shadow → Risk Governor → Paper → Human Approval
```

Concretely: the Risk Governor Bridge (Phase 20.17) must be producing real, reviewed sizing recommendations from real shadow-observed decisions; the execution simulator (Phase 20.18/19, paper-only) must have exercised the full lifecycle against those recommendations without a structural safety-boundary violation; and a human must explicitly authorize the specific, narrow step of allowing any real order — this document does not authorize it, no future phase's own report may authorize it unilaterally, and the existing `disable_live_execution()` guard remains in force on every broker instance until that authorization is explicit, scoped, and separately requested.

## 5. Milestone alignment

This document does not replace `docs/BUJJI_OS_ROADMAP_V2_CONTINUOUS_SHADOW.md`'s own "Complete Bujji OS v1.0" milestone definition (§E there) — it is the interface contract that milestone's condition 1 ("every component... reachable from one coherent chain") is checked against. A future phase claiming milestone completion must show its code changes map onto exactly one row of §2's table above, with no stage skipped and no sideways connection outside this map.

---

*Every future phase (20.14 onward) must state, in its own audit section, which row(s) of §2 it touches and confirm it does not create a new, undocumented connection between stages. This is the concrete mechanism that prevents lineage #4.*
