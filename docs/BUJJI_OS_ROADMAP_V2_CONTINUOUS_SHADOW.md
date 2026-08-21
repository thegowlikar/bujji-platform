# Bujji OS Roadmap v2 — Continuous Build + Continuous Shadow

**Supersedes the "build → freeze → collect 20-30 sessions → resume" sequencing.** New principle: build Bujji end-to-end as if it will run real capital, while a daily live shadow campaign runs permanently, in parallel, never blocking development.

**Revision note:** amended after review to (a) insert Phase 20.15.1 — Market Memory Foundation — before strategy expansion, since memory (not more strategies) is the compounding advantage, and (b) sharpen the Execution Intelligence boundary into an explicit build-now/delay split with a stated gating criterion. See `docs/BUJJI_OS_V1_INTERFACE_MAP.md` for the resulting locked interface contract every phase below must plug into.

---

## 0. The fact this roadmap has to start from: three lineages exist, not one

Before proposing new phases, the single most important finding from auditing every prior phase (20.0–20.13, and the Phase 19.x / MSI-series audits nested inside them) has to be stated plainly, because it changes what "Phase 20.14 onward" even means:

**This codebase already contains three separate, non-overlapping intelligence/execution lineages**, built at different times, never unified:

| Lineage | What it has | What it's missing |
|---|---|---|
| **Cycle 1 (Phase 20.x, this engagement)** — MIC v0 → Strategy Intelligence → Opportunity → Ranking → Capital Intelligence (allocation *class*, not size) → Portfolio → Decision Orchestration → Shadow Decision Runtime → Shadow Market Campaign → Live Shadow Runner | The only lineage with real, validated, evidence-based strategy research (Phase 20.4's real 8.5-year backtest: Trend Following passed, Mean Reversion failed) and a fully tested, honest, anti-fabrication decision chain. Now live-feed-wired (Phase 20.13). | No risk governor, no margin/capital *sizing*, no order construction, no broker execution, no position/P&L tracking, no market memory beyond the current cycle. Two strategies only. |
| **Phase 19.x Intelligence Foundation** — MarketRealitySnapshot → MarketIntelligenceSnapshot → DecisionContext → DecisionIntelligenceSnapshot → MarketPhenomena → MarketStateGraph, plus `market_regime_memory`, `outcome_memory`, `reality_memory`, and the `shadow_runtime` operational scaffolding (`DailySessionRuntime`, heartbeat, completeness, systemd pattern) | Real market *understanding* depth (phenomena detection, state-graph transitions, cross-session memory) and real operational reliability engineering (the daily lifecycle Phase 20.13 already reuses). | Never wired to Cycle 1's own validated evidence — its decisions are structurally real but epistemically ungrounded (no strategy has ever been backtested against this lineage's own outputs). |
| **MSI / Trading Brain series ("BUJJI Options OS v3")** — `msi_strategy_selector`, `msi_trade_construction`, `msi_margin_bridge`, `msi_execution_planning`, `trading_brain.risk_governor` (portfolio limits, capital safety governor, risk budget governor, real margin engines, position-group lifecycle), `production_runtime.trading_brain_runtime` terminating in real `PaperBroker.place_order` | The **only** lineage with real risk governance, real margin calculation, real trade construction, and a real (paper) execution path. This is genuinely substantial, working infrastructure — not a prototype. | Never validated against real, evidence-backed strategy research. Its strategy selection has no equivalent of Phase 20.4's honest PASS/FAIL finding. |

**The strategic implication:** "Complete Bujji OS v1.0" is not a from-scratch execution/risk build. It is **a bridging problem** — connecting Cycle 1's validated intelligence output to the MSI series' real risk governor and execution path, informed by Phase 19.x's market memory. Every phase below is scoped with this in mind: audit-first, reuse-first, exactly as every phase in this engagement has already required.

---

## A. Updated Phase 20.14-onward roadmap

Each phase pairs one **system capability** milestone with one **shadow data quality** milestone (requirement 3), and each starts with the same mandatory audit-before-code discipline already established.

### Phase 20.14 — Continuous Shadow Campaign (infrastructure, not a new intelligence layer)
Turn Phase 20.13's entrypoint into a permanent, unattended daily process: install the Phase 19.12 systemd unit pattern (or equivalent), point it at the now-live FYERS feed, and let it run every real NSE trading day going forward, indefinitely. No new intelligence code — this phase is the literal start of the "shadow campaign never blocks development" principle. Output: `CampaignSession`/`HealthReport` artifacts accumulating daily, monitorable via a simple status check (reusing `bujji.shadow_runtime.status`'s pattern).

### Phase 20.15 — Market Memory Bridge: write path (audit + connect, not rebuild)
Audit `bujji.market_regime_memory`, `bujji.outcome_memory`, `bujji.reality_memory`, and Phase 19.5's `MarketMemoryEntry` against Cycle 1's own `MarketState`/`DecisionObservation` shapes. Build the minimal adapter so every day's real shadow campaign output is *also* written into real, cross-session memory (not just a per-day JSONL) — closing requirement 4's `Memory` node in the feedback loop.

### Phase 20.15.1 — Market Memory Foundation: read path (inserted before strategy expansion)
**The compounding-advantage layer, and the reason it must come before Phase 20.16, not after.** The original vision was "teach Bujji how markets behave," not "give Bujji more strategies" — a new strategy family evaluated before memory exists would only ever see MIC's confidence modifier, missing the point of building memory first. This phase makes real memory (written by 20.15) a genuine *input* to Strategy Intelligence's confidence computation: a similarity query over real historical/shadow-observed conditions, a real outcome distribution (never estimated), and a disclosed, bounded confidence adjustment — the same "modifies confidence, never evidence" invariant Phase 20.5's own `MarketContext` already proves, extended to a second input. See `docs/BUJJI_OS_V1_INTERFACE_MAP.md` §3 for the full contract and required real-data proof shape.

### Phase 20.16 — Strategy Family Expansion (evidence-gated, not premature)
Per requirement "do not add random strategies": any third strategy family only enters Cycle 1's chain after passing Phase 20.4's own PASS/FAIL discipline (train/validation/out-of-sample stability) on real data — exactly as Trend Following did and Mean Reversion didn't. This phase is a research phase, not a build phase; its deliverable is either a new validated `StrategyEvidence` or an honest "still FAIL/INCONCLUSIVE" finding, either way real.

### Phase 20.17 — Risk Governor Bridge (the highest-leverage phase in this roadmap)
Audit `bujji.trading_brain.risk_governor`'s real components (`capital_safety_governor.py`, `risk_budget_governor.py`, `portfolio_limits.py`, `defined_risk.py`) against Cycle 1's `AllocationAssessment`/`RiskAllocationClass`. Design and build the bridge that lets a Cycle-1 `FinalDecision` become an *input* to the real risk governor pipeline — still never executing, but now producing a real, risk-governor-reviewed sizing recommendation instead of Phase 20.8's coarse 5-tier class. This is where "risk governor integration" (requirement 1) actually happens, by connection, not reimplementation.

### Phase 20.18 — Execution Intelligence (explicit build-now / delay split, no live orders)
Audit `msi_trade_construction`, `msi_margin_bridge`, `msi_execution_planning`. Unlike the original draft, this phase is not purely design-only — it has a locked boundary (full detail in `docs/BUJJI_OS_V1_INTERFACE_MAP.md` §4):

**Build now:** broker abstraction (already exists, live-verified), order lifecycle model, reconciliation, failure handling, a `PaperBroker`-backed execution simulator, and audit trail persistence.
**Delay, explicitly:** real order placement, capital exposure, autonomous execution — gated on `Shadow → Risk Governor → Paper → Human Approval` all being real and proven, not on a calendar date.

Output is both a design doc AND real, tested, paper-only scaffolding — `place_order`/`modify_order`/`cancel_order` remain behind `disable_live_execution()` on every path this phase builds.

### Phase 20.19 — Broker Abstraction Readiness Review
Audit `bujji.broker.base.Broker`, `FyersBroker`, `PaperBroker`, `HybridPaperBroker`, `disable_live_execution` guard (already exercised live in Phase 20.13) against what Phase 20.18 designed. Confirm the existing abstraction is sufficient, or extend it — but the guard that currently prevents any Cycle-1-derived decision from ever reaching `place_order` stays in force through this entire roadmap unless a future, explicit, separately-authorized phase removes it.

### Phase 20.20 — Monitoring, Recovery, Reporting Consolidation
Unify Phase 20.13's `HealthReport`, Phase 20.12's `CampaignMetrics`, and the Phase 19.x `DailySessionHeartbeat`/`status.py`/`campaign_continuity.py` tools into one operator-facing daily report. This closes requirement 1's `Monitoring`/`Recovery`/`Reporting` bullets using what already exists rather than a new dashboard.

### Phase 20.21+ — Repeat 20.15/20.15.1/20.16/20.17 cycles as shadow data accumulates
Once 60–90 real shadow sessions exist (see §C), revisit strategy evidence (does real intraday MIC classification, not the session-level approximation Phase 20.11/20.12 disclosed, change anything?), revisit risk governor thresholds against real observed volatility/regime distributions, and only then consider Phase 20.1C's intraday MIC classifier as the live regime source instead of the disclosed session-level approximation.

---

## B. Components still missing before Bujji can theoretically operate with real capital

Ordered by how load-bearing each gap is:

1. **A tested bridge from `FinalDecision` to the real risk governor** (Phase 20.17). Without this, no real position size has ever been computed from a Cycle-1 decision — `RiskAllocationClass` is a 5-tier label, never a quantity.
2. **Order construction and execution**, gated behind explicit authorization (Phase 20.18/20.19). Currently zero code path exists from Cycle 1 to any `place_order` call — by design, and that must stay true until a real authorization decision is made.
3. **Real intraday regime classification wired into the live decision path.** Phase 20.11/20.12/20.13 all disclosed the same approximation: session-level MIC v0 regime (TREND/RANGE/UNCLEAR) mapped onto the strategy layer's intraday-shaped vocabulary. Phase 20.1C's own separately-validated intraday classifier has never been the live source.
4. **A third, evidence-validated strategy family** (or an honest confirmation that two is enough) — requirement 4 says don't add strategies prematurely, so this is a gate, not a backlog item.
5. **Cross-session market memory wired into the live decision path** (Phase 20.15) — today's shadow campaign observes but does not yet let yesterday's observation inform today's decision.
6. **A single, unified operator report** spanning intelligence health, risk governor state, and (once it exists) execution readiness — today these are three separate tool families (Cycle 1's own, Phase 19.x's, MSI's).
7. **A real, tested authorization boundary** for the moment live orders are ever allowed — not built, not designed in detail yet (Phase 20.18 is design-only), and should not exist until the roadmap explicitly reaches it.

## C. What to build now vs. what should wait for shadow data

**Build now** (doesn't need shadow data to be correct):
- Phase 20.14 (continuous campaign infrastructure) — the sooner this runs, the sooner real data accumulates.
- Phase 20.15 (memory bridge) — plumbing, testable against historical replay exactly as every prior phase already was.
- Phase 20.17 (risk governor bridge) — the governor's own logic doesn't need shadow data to test; only its live *inputs* eventually will.
- Phase 20.18/20.19 (execution design, broker abstraction review) — design and audit work, not shadow-data-dependent.
- Phase 20.20 (monitoring consolidation) — pure engineering.

**Wait for shadow data** (would be premature or unverifiable without it):
- Any decision to trust Phase 20.1C's intraday classifier as the *live* regime source over the session-level approximation — needs real intraday shadow observations to compare against, not just the original historical validation.
- Any threshold *retuning* in the risk governor or opportunity qualification layers (`MIN_ELIGIBLE_EFFECTIVE_SCORE`, allocation tiers, etc.) — every one of these is currently a disclosed, undisclosed-as-optimized constant; retuning against live shadow data before ~60-90 real sessions exist would be fitting noise.
- Any claim about real-world decision stability/reliability beyond what Phase 20.12's own metrics (decision flips, confidence oscillation, uncertainty frequency) can show on live data — Phase 20.12 proved the *metric*, not yet a real answer.
- A third strategy family's *live-regime* validation (its historical validation can and should happen now, per Phase 20.16, but confirming it behaves as expected live is a shadow-data question).

## D. The continuous feedback loop

```
Market ──▶ Observation ──▶ Intelligence ──▶ Decision ──▶ Shadow Result ──▶ Memory ──▶ Improvement
  │            │                │              │              │              │            │
  │      FyersBroker      MIC v0 (20.1)   Decision      DecisionObs   Phase 20.15    Phase 20.16/17/21+
  │   (read-only, guard-  Strategy Intel  Orchestration  (20.11)      bridge into    revisit evidence/
  │    ed, Phase 20.13)   →Opportunity→   (20.10)        →Campaign    real cross-    thresholds using
  │                       Ranking→Capital                 (20.12)     session        accumulated real
  │                       →Portfolio                      →persisted  memory         sessions
  │                       (20.5-20.9)                      via Live                  (never premature —
  │                                                         Shadow                    gated on real
  │                                                         Runner                    session count,
  │                                                         (20.13)                   §C)
  └───────────────────────────────────────────────────────────────────────────────────────┘
                     (loop closes: today's Memory informs tomorrow's Intelligence)
```

Each arrow is already either fully built (Market→Observation→Intelligence→Decision→Shadow Result, Phases 20.1–20.13) or explicitly scheduled above (Shadow Result→Memory is Phase 20.15; Memory→Improvement is Phase 20.16/20.17/20.21+). The loop runs continuously and automatically once Phase 20.14 lands — no phase after that needs to "pause" the loop to improve the system; every future phase runs *alongside* it, per the new principle.

## E. Milestone definition: "Complete Bujji OS v1.0"

Bujji OS v1.0 is complete when **all** of the following are simultaneously true — this is a conjunction, not a checklist to partially satisfy:

1. **Every component in requirement 1's list has a real, tested implementation reachable from one coherent chain** — Market Intelligence Core, Market Memory, Observation Engine, Strategy Intelligence, Opportunity Detection, Decision Engine, Risk Governor integration, Portfolio Intelligence, Execution Intelligence (*designed and code-reviewed, execution itself still gated*), Broker abstraction, Monitoring, Recovery, Reporting — with every "wrong domain / wrong lineage" audit finding from this engagement resolved by bridging, not duplicating.
2. **The continuous shadow campaign has run uninterrupted (allowing for real NSE holidays/weekends) for at least 60 real trading sessions**, with `CampaignMetrics` showing: decision stability `LOW` in the large majority of sessions, `explanation_completeness_pct` at or near 100% throughout, and no unexplained `FAILED` runtime-health session.
3. **At least one full loop iteration of Phase 20.15→20.15.1→20.16/17→(revisit) has actually happened** — i.e., real shadow data has demonstrably informed at least one real decision made about the system (a threshold, a regime-source switch, a strategy validation, or a memory-driven confidence adjustment), not just been collected.
4. **The risk governor bridge (Phase 20.17) produces a real, reviewed position-sizing recommendation from a real `FinalDecision`** — even though no order is ever placed from it, this is the concrete evidence "operate with real capital" is *architecturally* possible, pending only the explicit authorization decision this roadmap deliberately does not make.
5. **Every safety boundary already enforced (no broker execution import outside the guarded, disclosed path; no fabricated evidence; no premature threshold optimization) still holds**, verified by the same structural `grep`/AST tests this engagement has used at every phase, now run across the unified chain.

Reaching this milestone is explicitly **not** "ready to trade real capital" — it is "the architecture and the evidence exist to have that conversation for the first time." The decision to actually authorize live execution is a separate, later, explicitly-scoped decision this roadmap does not make on the operator's behalf.
