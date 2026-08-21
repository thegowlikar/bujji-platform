# BUJJI CHIEF ENGINEER MASTER PLAN

**Date:** 2026-08-19 · **Baseline commit:** post-8523e26 · **Author:** Chief Engineer (Claude), operator saigowlikar
**Evidence base:** 13-agent forensic reconnaissance (26-row capability matrix, 22 findings dispositioned, 4 vision docs validated, adversarial critic), the 2026-08-18 full-tree audit (958 files), and live production evidence from 2026-08-14 through this morning's session.

The rule this plan is built on: **a capability is real only when caller → construction → runtime invocation → real input → real output → consumer → persisted result is demonstrated.** Every claim below cites that chain or says UNVERIFIED.

---

## A. CURRENT TRUTH — what Bujji actually does today (2026-08-19)

**What runs, scheduled, every trading day (7 timers):**

| IST | Unit | What it really does |
|---|---|---|
| 08:45 | token-preflight | Decodes JWT vs every timer's next fire. **Proven today**: predicted the 09:10 death at 08:45, was right. |
| 09:10 | shadow-decision-campaign | 5-min cycles: VIX + spot → mic_v0 regime → DecisionObservation JSONL. 150 obs/day. No execution. |
| 09:16 | daily-intelligence | Spawns spot/VIX/futures capture (layer0) + option-chain capture (SQLite) concurrently, then EOD intelligence + completeness. **Has never completed successfully; today is the first credible attempt.** |
| 09:17 | futures-depth-poller | 60s 5-level depth → layer0. First scheduled run TODAY (running now). |
| 09:22:30 | options-os-trading | The trading organism: thesis → regime → strategy table → (risk → construct → PaperBroker). **Both live sessions ended NO_TRADE in ≤12s. Zero orders, fills, positions ever.** |
| 09:27:30 | paper-intelligence-campaign | Thesis + DecisionArtifact per cycle. **Structurally deadlocked** by its own guard against daily-intelligence's all-day lock (refused its first fire today). |
| nightly | — | **Nothing. No backup exists.** |

**What is real (full chain proven):** option-chain capture (180k+ rows, 3 live days, ltp/bid/ask/vol/OI per contract); OI capture; VIX decision-input (150 persisted readings/day); certified whole-book SPAN margin (live ₹220,455.74/lot quote); lot size from instrument master (65, fail-closed); declared simulated capital (₹5L, disclosed); spot-from-chain capture (certified 09:17 today, 0.06 bps mean divergence, rows flowing 09:21); spot/futures/VIX layer0 capture (restored 09:49 today after the EROFS fix); token pre-flight; regime derivation from market evidence (`regime_source: market_thesis` — no human input on the autonomous path).

**What is fabricated or severed on the trading path:** LiveTickProvider wraps the PaperBroker random walk (`rng.uniform(-3,3)` seeded 120.0) while logging "live quotes" — every future position would be managed against noise; the runner's intelligence sees exactly ONE snapshot (no warmup config) so trend is structurally UNKNOWN → NO_TRADE forever; `capital_check.assess_capital` — documented as the sole margin ALLOW/VETO authority — is invoked NOWHERE in the production entry chain; the whole-book margin projection receives empty leg maps; OutcomeMemoryRecord dies at process exit (no durable write); AdaptiveRiskMemory is constructed empty every session; nothing reads any prior outcome; charges are computed on every paper fill and then never subtracted from P&L; no emergency close exists between 5-minute passes; no alerting exists (three units failed this morning, observed only because I was watching); heartbeats don't beat (rows_captured_today froze at 0 while 1,200+ rows landed).

**Operational reality, measured:** the last three trading days each began with a failure in this class: root-owned lock (08-18, killed the trading fire), root-owned instrument-master tmp (08-18, killed spot/VIX capture), unpromoted token (08-19, killed the 09:10 campaign), sandbox-readonly layer0 (08-19, 26 min silent capture loss). All four were invisible until a human read journalctl. **The organism's #1 measured failure mode is not trading logic — it is silent operational death.**

## B. VISION GAP

Vision: continuous tick-driven perceive→understand→decide→trade→manage→learn loop.
Today: batch REST perception (60s–5min, real), single-shot morning decision (12s, honest but blind), zero execution history, write-only-then-discarded memory, unreachable learning, no self-healing ops.

The gap, ranked by what blocks the next gate — not by architectural distance:
1. **Priced perception on the decision path** (tick source is fabricated).
2. **Temporal evidence** (warmup absent → trend UNKNOWN → no strategy can ever be selected).
3. **Safety spine unwired** (margin veto, defined-risk, emergency close not invoked in production).
4. **Evidence not persisted** (thesis internals, 13-family selection, IV — computed then discarded; the first trade would be unauditable).
5. **Outcome loop severed at the last 5 lines** (record exists in memory, dies at exit).
6. **Ops immune system missing** (no alerts, no backup, no cross-process rate budget, heartbeat staleness).
7. Websocket tick nervous system, continuous re-decision, hedge/roll transformations, statistically-governed learning — real vision items, all sequenced AFTER a trustworthy paper session exists to feed them.

## C. CANONICAL ARCHITECTURE (one organism; everything else classified)

```
FYERS REST (paced, certified)                      [future: FYERS websocket — certified, unwired]
  ├─ CAPTURE plane (evidence, all-day):
  │    daily-intelligence → capture_market_reality (spot/fut/VIX → layer0)
  │                       → capture_options_reality (chain+spot → historical_observations.db)
  │    futures-depth-poller (depth → layer0)
  └─ DECISION plane (trading runner, 09:22:30):
       LiveChainProvider (fyers_live) + LiveTickProvider(FYERS data leg — TO FIX)
         → market_state_builder + ObservationMemory (warmup — TO ENABLE)
         → market_thesis.assess                      [THESIS AUTHORITY]
         → strategy table (binding today)            [SELECTION AUTHORITY — see D/ask]
         → trade construction (trading_brain order_construction)
         → RISK SPINE: fyers_span_margin (certified) → capital_check.assess_capital [VETO AUTHORITY — TO WIRE]
                       → capital_safety_governor → position sizing (real proposal-derived — TO FIX)
         → PaperBroker (factory NORMAL profile)      [EXECUTION SURFACE]
         → position_lifecycle → 5-min management passes → EOD close
         → lifecycle_outcome_bridge → OutcomeMemoryRecord → EventStore [TO WIRE, ~5 lines]
         → (Gate 5+) outcome_memory.query → decision_intelligence memory_matches
```

**Classification of every competing lineage:**

| Lineage | Class |
|---|---|
| Trading runner + market_thesis + trading_brain risk chain + PaperBroker(factory) + position_lifecycle | **CANONICAL** |
| shadow-decision-campaign (mic_v0), paper-intelligence-campaign (MIC brains), daily-intelligence EOD intelligence | **OBSERVATION ONLY** (three parallel state stacks — tolerated as instruments, never authorities) |
| msi 13-family selector + msi_decision_synthesis | **OBSERVATION ONLY** today; candidate canonical (§D, operator gate) |
| broker_boundary + execution_intelligence fill model | **OBSERVATION/VALIDATION** (second fill model — collapse before Gate 4) |
| bujji/capital (CapitalManagementEngine) | **FROZEN** (margin role superseded by fyers_span_margin; funds role by fyers_funds_capital) |
| core/orchestrator (ORB-VWAP), run_live_shadow.py | **LEGACY** |
| HumanSuppliedRegimeProvider | **FORBIDDEN in autonomous mode** — silent-default hole to close (§N-7) |
| learning_update, self-improvement pipeline | **FROZEN until Gate 5** |

## D. CRITICAL PATH — to the first trustworthy autonomous paper session (Gate 3)

Adopted from the adversarial critic, amended by chief engineer; dependency-ordered:

| # | Intervention | Size | Status |
|---|---|---|---|
| 1 | Hygiene: test roots out of `data/`, kill 0-byte decoy store, root-litter sweep + prevention (tests run as bujji) | S | items 1a (EROFS/layer0) FIXED today; rest open |
| 2 | Token ritual: document refresh+promote as ONE ritual; candidate auto-promote in pre-flight (operator decision) | S | pre-flight proven today |
| 3 | **Tick source**: hand execution-neutered FYERS data broker to LiveTickProvider (runner:569); fix the "live quotes" log lie | S | open — top code fix |
| 4 | **Enable `providers.regime.warmup`** (config block; code+tests exist). Accept NO_TRADE days; do NOT tune the gate to force entries | S | open |
| 5 | **Persist decision evidence**: thesis internals + 13-family selection + record_cycle dict → session artifacts | M | open |
| 6 | **Safety spine**: real legs into SPAN projection; `capital_check.assess_capital` wired pre-approval; loss hard-limit inside the 5-min loop (emergency close); settle ONE close constant (operator: 15:15?) | M | open |
| 7 | Broker realism: set_quote/set_depth from live chain + set_capital(5L, SPAN) before each place_order | M | open |
| 8 | **Outcome durability**: persist OutcomeMemoryRecord (reuse shadow_lifecycle EventStore writer); pass real fees/slippage into close_position | S | open |
| 9 | Failure visibility: OnFailure= alert on every unit; heartbeat staleness rule | S | open |
| 10 | **Nightly off-box backup** (sqlite .backup + layer0 + journals) — before the first trade creates unrecreatable data | S | open |

Parallel, off-spine: campaign lock conflict (operator), cross-process rate budget, 08-14 value_kind re-normalization, .gitignore runtime outputs, VIX/spot store unification.

**Gate 3 definition:** first timer-fired session that selects a strategy, opens/manages/closes a position on real ticks, with the persisted thesis→selection→margin-veto→fills→outcome chain intact and alerts armed.

## E. CAPABILITY MATRIX (recon verdicts, full evidence in tasks/wh4o5azey.output)

| Capability | Status | Reachable | Blocking gap (abridged) |
|---|---|---|---|
| Live ticks | **BROKEN** | YES | PaperBroker random walk behind "live quotes" label (runner:569) |
| Option chain capture | **REAL** | YES | — |
| OI capture | **REAL** | YES | — |
| Volatility (VIX+IV) | PARTIAL | YES | IV computed-vs-silently-None indistinguishable; persist readings |
| Liquidity (bid/ask/depth) | PARTIAL | PARTIAL | depth first prod run TODAY; LiquidityBrain unpersisted |
| Microstructure | DORMANT | NO | needs sub-minute source (websocket certified, unwired) |
| Market state builder | PARTIAL | YES | single snapshot without warmup config |
| Regime understanding | PARTIAL | PARTIAL | structurally UNKNOWN-trend without warmup |
| Historical memory retrieval | DORMANT | NO | memory_matches hardcoded empty (adapter:184) |
| Market thesis | PARTIAL | YES | internals never persisted |
| Warm-up gate | DORMANT | NO | one config block away; never run in prod |
| Decision synthesis | PARTIAL | YES | record_cycle return discarded (runner:767) |
| Strategy selection | PARTIAL | YES | binding 2-family table; 13-family computed+discarded |
| Strategy validation | DORMANT | NO | validators exist, run only by hand |
| Trade construction | PARTIAL | YES | never executed live (needs regime to resolve) |
| Margin (whole-book SPAN) | PARTIAL | PARTIAL | proposal legs not fed into projection (empty maps, runner:847) |
| Capital verification | PARTIAL | YES | ProposedTradeEffect from YAML constants, not the proposal |
| Risk Governor | PARTIAL | YES | assess_capital not invoked in production chain |
| Position sizing | PARTIAL | YES | sizes a fictitious ₹5000 constant risk |
| Paper execution | PARTIAL | YES | realism inputs (quote/depth/capital) have zero prod callers; 1cr fabricated broker-internal defaults |
| Order lifecycle | DORMANT | PARTIAL | cancel has no production caller (partial-fill path) |
| Position lifecycle | DORMANT | YES | net P&L permanently None (fees never passed) |
| Broker abstraction | PARTIAL | PARTIAL | two disagreeing fill models |
| Continuous monitoring | PARTIAL | YES | 5-min cadence real; priced off fabricated ticks |
| MFE/MAE | PARTIAL | PARTIAL | computed; evaporates at exit |
| Management actions | PARTIAL | PARTIAL | HOLD/EXIT real-if-entered; HEDGE/ADJUST/ROLL vocabulary only |
| Emergency close | **MISSING** | NO | nothing between 5-min passes; blind cycles disable stops |
| EOD square-off | PARTIAL | YES | complete chain, zero runtime history with a position |
| Outcome attribution | DORMANT | YES | blocked upstream (no trade ever) |
| Outcome Memory persistence | **BROKEN** | NO | ~5 lines: durable write missing at _close_canonical_lifecycle |
| Outcome retrieval | DORMANT | NO | hydrate/query tests-only |
| Learning modules | DORMANT | PARTIAL | AdaptiveRiskMemory fresh+empty each session; no writer |
| Self-improvement governance | MISSING | NO | design exists (§K); nothing scheduled |
| Monitoring/health honesty | PARTIAL | YES | heartbeat doesn't beat; no staleness rule |
| Crash recovery/reconciliation | **MISSING** | NO | no Restart=, no alert, no resume; by explicit past design |
| Rate limiting | PARTIAL | YES | per-process 8.3/s × up to 6 processes vs 10/s account ceiling |
| Observability consumers | PARTIAL | PARTIAL | no automated consumer of any health signal |
| Auditability | PARTIAL | YES | legacy synthetic journals inside data/ pollute audit surface |
| Data safety | **BROKEN** | NO | zero backups of the only two live capture days in existence |

## F. OWNERSHIP MAP

Market data acquisition: **FyersBroker (paced) + capture scripts (capture) / LiveChainProvider+LiveTickProvider (decision)** · Observation stores: **HistoricalObservationStore (SQLite) + RawObservationStore (layer0)** · Market state: **market_state_builder (decision); MIC/mic_v0 observation-only** · Thesis: **market_thesis.assess** · Decision+selection: **strategy table today; msi selector candidate (operator gate)** · Risk: **trading_brain risk_governor chain (capital_check = veto authority once wired)** · Margin: **fyers_span_margin (certified)** · Capital: **fyers_funds_capital / declared-static** · Construction: **trading_brain order_construction** · Execution: **PaperBroker via factory (NORMAL profile)** · Lifecycle: **position_lifecycle** · Outcome: **lifecycle_outcome_bridge → outcome_memory (EventStore)** · Learning: **outcome_memory.query → decision_intelligence (Gate 5)** · Scheduler: **systemd timers** · Token: **human refresh + promote script + pre-flight**.

## G. DATA ARCHITECTURE

`REST poll → RawObservation/HistoricalObservation (provenance: source=fyers|fyers_historical, certification_ref, capture_ts) → layer0 JSONL (quotes/depth, append-only, fsync, PIPE_BUF-atomic) + SQLite store (chain/spot, natural-key deduped) → observation engines (ObservationMemory in-session; stores for replay/validation) → intelligence readings (TO PERSIST per-cycle) → decisions.jsonl + session artifacts → OutcomeMemory EventStore (TO WIRE) → learning (Gate 5)`. Honesty invariants live in taxonomy: quality enum, MISSING ≠ zero, no synthetic source may masquerade (tick-source fix removes the one active violation). Retention: options ~18MB/day tiered; layer0 ~2MB/day; nightly off-box backup (D-10). Future websocket path appends a tick table beside — never replaces — the poll stores.

## H. PAPER TRADING ARCHITECTURE (target of Gate 3/4)

Single runner process/day: warmup (evidence-gated WARMING_UP→READY) → thesis+selection each entry cycle (persisted) → construction → risk spine (real margin/real capital/real sizing; VETO honest) → PaperBroker with live quote/depth/capital context → fills with charges → position_lifecycle → 5-min management (real ticks; MFE/MAE; loss hard-limit; thesis re-check) → 15:15 square-off → close_position(real fees) → attribution → durable OutcomeMemoryRecord → EOD validation pass over session artifacts. Every decision and every rejection explainable from persisted evidence alone.

## I. LIVE TRADING ARCHITECTURE

Identical runner, identical spine; the ONLY divergence is the execution surface: `broker = factory(mode)` returning FyersBroker-with-orders instead of PaperBroker, unlockable only by Gate 7 qualification + operator's written enable. Reconciliation-on-start (broker positions/orders vs journal) becomes mandatory at this boundary. No live-only strategy, decision, or management code — the charter's sameness principle is enforced by construction (one composition root).

## J. RELIABILITY ARCHITECTURE

Alerts: OnFailure=@alert unit on every bujji service (D-9). Heartbeats: beat per cycle + staleness watchdog. Rate: one cross-process token bucket (file-lock) under bujji/broker pacer — budget 8/s aggregate vs 10/s account ceiling, measured 3-process proof required (charter §18). Recovery: Restart=on-failure for capture/campaign units; trading runner stays no-auto-restart (documented choice) + alert + next-day resume; position-abandonment risk accepted only until Gate 4, then mid-session resume with broker/journal reconciliation. Backup: nightly sqlite .backup + tar layer0/journals → off-box (D-10); restore procedure tested once before Gate 4.

## K. LEARNING ARCHITECTURE (governed; frozen until Gate 5)

`OutcomeMemoryRecord (durable) → hydrate at session start → outcome_memory.query similarity → decision_intelligence memory_matches (advisory context first, never auto-mutation) → Gate-5 analysis over ≥30 sessions → hypothesis → replay_engine backtest → paper validation → statistical evidence (predeclared metrics) → human-approved, versioned, reversible config change → shadow validate → adopt`. AdaptiveRiskMemory gains a writer (real outcomes) + persistence before it may influence sizing. No self-modifying strategy code, ever.

## L. DEPLOYMENT GATES — current position

**Gate 0 Integrity: ~85%.** Done: repo reproducible (1,016-file audit, clean tree, pushed), ownership sweep, canonical runtime declared (§C). Open: test/prod data separation, decoy removal, backup (also G-0 exit criterion), root-litter prevention.
**Gate 1 Live Perception: ~70%, first credible full day IN PROGRESS TODAY.** Chain+OI+bid/ask REAL (3 days); spot certified+flowing (today); VIX/futures layer0 restored 09:49 today; depth first scheduled run today. Open: gap-detection formalization, IV observability, one full EOD completeness PASS. Websocket = Gate-1.5 extension, not blocker.
**Gate 2 Live Intelligence: ~40%.** Regime derives from evidence; thesis runs; three observation campaigns cycle. Open: warmup (D-4), evidence persistence (D-5), multi-session record.
**Gate 3 Autonomous Paper Trading: BLOCKED** by D-3..D-8. This is the plan's target.
**Gate 4 Paper Stability (30 sessions):** needs Gate 3 + alerting + backup + rate budget + fill-model collapse. **Gate 5 Learning Validation:** needs the 30-session dataset. **Gate 6 Shadow Live / 7 Qualification / 8 Real Money:** sequenced; Gate 8 additionally requires funding the real account (currently ₹0.00 — measured).
**Selector decision (operator gate before Gate 4):** persist both selectors' outputs from D-5 onward; after ≥10 divergence observations, decide canonical selection authority together.

## M. RISK REGISTER

| Sev | Risk | State |
|---|---|---|
| CRIT | Fabricated ticks manage first-ever position (stops/MFE/MAE on noise) | Open — D-3 |
| CRIT | No margin/defined-risk VETO invoked at entry | Open — D-6 |
| CRIT | Only 2 live capture days exist; zero backups | Open — D-10 |
| CRIT | Silent operational death (4 incidents / 3 days; no alerting) | Partially closed (pre-flight); D-9 |
| HIGH | No emergency close between 5-min passes; blind cycles disable stops | Open — D-6 |
| HIGH | Outcome loop severed → Gate 4/5 dataset never accumulates | Open — D-8 |
| HIGH | Cross-process FYERS budget unowned (≤6 × 8.3/s vs 10/s account) | Open — J |
| HIGH | Root/test litter recurring in prod tree (incl. by my own audits) | Open — D-1 |
| MED | human_supplied regime silently reachable via config typo | Open — explicit-error fix |
| MED | Structural NO_TRADE (≈8% stability pass-rate) misread as health | Documented; expectation set |
| MED | 08-14 rows misclassified value_kind=OHLC, never re-normalized | Open |
| MED | Heartbeat freshness lies (RUNNING while dead; 0 while capturing) | Open — D-9 |
| MED | Two fill models can diverge in learning data | Open — pre-Gate-4 |
| LOW | Token ritual is 2 manual steps; today proved half gets skipped | Mitigated (pre-flight); auto-promote = operator call |

## N. IMPLEMENTATION ROADMAP (dependency order)

**Phase CP-A "The day survives" (D-1,2,9,10):** hygiene + token ritual + alerts + backup. Exit: a failed unit reaches the operator within minutes; data survives droplet loss; a test run cannot break a market day.
**Phase CP-B "A trade is possible and honestly priced" (D-3,4):** real tick source + warmup enabled. Exit: one session logs READY with multi-snapshot evidence and real-priced management path (even if NO_TRADE).
**Phase CP-C "A trade is trustworthy" (D-5,6,7,8):** evidence persistence + safety spine + broker realism + outcome durability. Exit: **Gate 3 session** — possibly after several honest NO_TRADE days; we do not tune gates to force entries.
**Phase CP-D "Sessions accumulate" :** rate budget proof, fill-model collapse, campaign-lock resolution, heartbeat watchdog, 08-14 re-normalization → Gate 4's 30-session run.
**Phase CP-E "It learns" :** hydration + retrieval influence (advisory) + Gate-5 analysis machinery.
**Phase CP-F "Toward live":** websocket nervous system, continuous re-decision cadence, hedge/roll as real transformations, reconciliation-on-start, Gate 6/7 harnesses.

**Standing operator gates (charter §23):** selection authority swap · any safety-guard weakening · close-constant meaning · capital limits · real-money enable · irreversible data ops.

*Ledger of every action: BUJJI_ENGINEERING_LEDGER.md. Full recon evidence: 13-agent workflow output archived in session tasks.*
