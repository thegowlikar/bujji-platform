# Bujji Paper Trading Deployment Blueprint v1.0

Read-only audit, Phase 7. No code was written this phase. Every claim below is
traced against the real code on the VPS (`/opt/bujji/app`), not inferred from
naming or memory. File:line citations are preserved from the underlying
investigation; treat this document as the single source of truth for what
exists today and what remains to wire.

## 0. Headline finding

**Nothing runs the full chain live today, and the single biggest gap is not
missing code — it's a missing `systemctl enable`.** The one script that
already produces real, live, MSI-grounded evidence and a real strategy
decision (`scripts/run_paper_intelligence_campaign.py`, built last phase) has
a working systemd unit sitting in `deploy/` that was **never installed** on
the VPS. Separately, the one script that already reaches a real PaperBroker
order (`bujji_options_os_runner.py`) runs against a historical EOD bhavcopy
file and a human-typed regime, not live FYERS data. These two real, tested
halves have never been connected.

---

## 1. Canonical intelligence path — decisions

### 1.1 MSI evidence completeness

**Q: Are MDI/PSI/MSSI/VSB/MPPI/Consensus/Liquidity/Volatility Intelligence
producing a complete real-time evidence object during live market hours?**

**A: No — not because any piece is missing, but because nothing schedules the
component that already assembles them.**

`IntelligenceCycleRecorder.record_cycle()` (`bujji/market_state/intelligence_cycle_recorder.py`)
already builds all of MDI/PSI/MSSI/MPPI/VSB/Consensus/LiquidityReading/
PremiumBehaviour in one call, with no internal None-fallback beyond what each
upstream bridge itself returns. It is reachable in exactly two places, both
via `ShadowSessionRunner(intelligence_cycle_enabled=True)`:
- `scripts/run_shadow_live_observatory.py` — self-documented deprecated,
  manual-only.
- `scripts/run_paper_intelligence_campaign.py` — the current script, real MSI
  evidence, real strategy decision via `select_strategy()`.

Systemd reality on the VPS today:
- `bujji-daily-intelligence.timer` — **enabled**, 09:00 IST daily. Runs a
  **structurally different pipeline** (`live_intelligence_cycle.py` →
  `build_intelligence_heartbeat_cycle()`) that never touches
  `IntelligenceCycleRecorder` or `market_thesis`.
- `bujji-shadow-decision-campaign.timer` — **enabled**, 09:10 IST daily. Runs
  Cycle 1's own lineage (`mic_v0`/`strategy_intelligence`), also never
  touching MDI/PSI/MSSI/VSB/MPPI/Consensus.
- `deploy/bujji-paper-intelligence-campaign.{service,timer}` — **present in
  the repo, never installed** (`systemctl is-enabled` → not-found).

**Exact missing connector:** install and enable
`bujji-paper-intelligence-campaign.{service,timer}` on the VPS. No new code.

### 1.2 Market Thesis canonicalization

**Q: `msi_trade_thesis` vs `market_thesis` — which becomes canonical?**

**A: `market_thesis.assess()` — because it already is, structurally.** Traced
in full: `market_thesis/engine.py:152` calls `derive_trade_thesis()`
internally and adds `premium_environment`/`positioning_environment`/
`liquidity_environment`/strategy-family suitability on top. It is a strict
superset, not a competing implementation. Two other direct callers of
`derive_trade_thesis()` remain — `trade_thesis_bridge.py` (feeds
`IntelligenceCycleRecorder`) and `live_pipeline_bridge.py` (feeds legacy
`run_live_shadow.py`/`run_daily_observation.py`) — these are real, still-used
legacy paths, not dead code, but neither is reached by either currently
-enabled systemd entrypoint.

```
Evidence (MDI/PSI/MSSI/VSB/MPPI/Consensus/Liquidity)
        ↓
market_thesis.assess()          <- canonical, wraps msi_trade_thesis internally
        ↓
Strategy suitability (SSF, already embedded)
```

**Decision:** `market_thesis` is canonical wherever thesis generation is
invoked going forward. `msi_trade_thesis.derive_trade_thesis()` remains as an
internal implementation detail `market_thesis` wraps — not a second thesis
concept a caller should ever invoke directly for new work.

### 1.3 MarketIntelligenceSnapshot architecture

Already resolved and implemented in Phase 6, reconfirmed here: `MarketIntelligenceSnapshot`
is built from a structurally separate lineage (`RegimeBrain`/`StructureBrain`/
`VolatilityBrain`/`GreeksBrain`/`EventBrain` — zero MDI/PSI/MSSI/VSB/MPPI/
Consensus imports, confirmed by direct trace). It cannot be honestly merged
into `market_thesis`. It stays a **perception cross-reference**, attached to
`DecisionArtifact` in separate `perception_*` fields, never merged into
`market_regime`/`directional_bias`. Both are guaranteed to describe the same
cycle because `ShadowSessionRunner`'s `intelligence_pipeline_enabled`
structurally requires `intelligence_cycle_enabled`. No change from Phase 6's
documented architecture.

```
MarketIntelligenceSnapshot          CycleEvidence (MDI/PSI/MSSI/VSB/MPPI/Consensus)
   (perception, RegimeBrain              (perception, MSI lineage)
    lineage)                                    ↓
        |                                market_thesis.assess()
        | perception_* fields                   ↓
        └──────────────→ DecisionArtifact ←──────┘
              (both attached, never merged)
```

---

## 2. Canonical runtime choice

Four candidates audited: `ShadowSessionRunner`, `TradingSessionGovernor`,
Live Shadow Campaign Runner ("Cycle 1"), Paper Intelligence Campaign Runner.
**None reaches the full chain today.** The gap splits cleanly in two:

| | Real MSI evidence → decision | Real PaperBroker + position lifecycle |
|---|---|---|
| Paper Intelligence Campaign Runner | ✅ yes | ❌ deliberately absent |
| TradingSessionGovernor (via `bujji_options_os_runner.py`) | ❌ regime is human-typed/caller-supplied | ✅ yes |

**Decision: `TradingSessionGovernor` becomes "Bujji Paper Trading Runtime
v1."** It is the only candidate that already touches real `PaperBroker`
order flow (`attempt_entry`→`trading_brain_runtime.process_entry_cycle()`)
and real position-lifecycle/exit enforcement
(`evaluate_and_enforce_exit`→`PositionLifecycleRuntime`→mandatory-exit
execution) — the exact session-discipline machinery (one strategy/day,
mandatory exits) this project's own V1.1 milestone was built to guarantee.
Rebuilding that discipline around a different runtime would be redesign, not
wiring — explicitly out of scope.

The precise gap to close: today `TradingSessionGovernor.select_and_lock_strategy()`
takes `trend_regime`/`volatility_regime` as caller-supplied strings
(`bujji_options_os_runner.py` gets them from `HumanSuppliedRegimeProvider`,
a human typing a value). `MarketThesisRegimeProvider` — built in Phase 5,
already tested, already used in `paper_intelligence_mode/engine.py` — derives
this exact regime pair from real `market_thesis.assess()` output. Swapping
which `RegimeProvider` implementation `bujji_options_os_runner.py` constructs
is the entire wiring task; the abstraction boundary was deliberately built
for exactly this swap (`regime_provider.py`'s own docstring says so).

**Disposition of the other three:**
- **ShadowSessionRunner** — frozen as shared infrastructure. It is
  intentionally decision-free by design and correctly reused by multiple
  runners; it is not a runtime candidate in its own right and needs no change.
- **Live Shadow Campaign Runner ("Cycle 1")** — deprecated/frozen for the
  paper-trading purpose. It is actively enabled and running
  (`bujji-shadow-decision-campaign.timer`), but on a structurally different
  evidence lineage (`mic_v0`/`strategy_intelligence`, not MSI) and is
  shadow-only by explicit design. Leave it running for its own observation
  continuity; do not extend it toward execution.
- **Paper Intelligence Campaign Runner** — becomes the **evidence/regime
  source feeding v1**, not a separate runtime. Its own systemd unit stays
  uninstalled as an execution path; instead, `bujji_options_os_runner.py`
  is what gets extended to consume live evidence, using the same
  `on_cycle_evidence`-shaped wiring this script already proved.

---

## 3. Strategy selector ownership — decision

Three real selectors exist, plus one scoring layer that is not a fourth
selector:

| Selector | Universe | Acted on by a real order today? |
|---|---|---|
| Trading Brain v3 (`bujji/trading_brain/strategy_selector`) | 11 strategies | Mechanically yes, via `production_runtime/runtime.py`'s `run_shadow` — but that pipeline is only invoked from `bujji/qualification/historical_runner.py` and `tools/*`, never a live entrypoint. No session discipline (no daily lock, no mandatory-exit enforcement) wraps it. |
| TradingSessionGovernor local (`trading_session_governor/strategy_selector.py`) | 2 strategies (IRON_CONDOR, IRON_FLY) | **Yes — the only selector reached by the sole live-capable PaperBroker path**, inside the session-disciplined `TradingSessionGovernor`. |
| MSI selector (`msi_strategy_selector`) | 13 families | No — computed and logged inside `record_cycle()`, the daily-intelligence service explicitly forbids trading. |
| SSF (`msi_strategy_selection_foundation`) | n/a — suitability scoring | Feeds the MSI selector as an input, not an independent decision. |

**Decision: `TradingSessionGovernor`'s local 2-strategy selector remains the
ONE authoritative selector for Paper Trading Runtime v1.** This is a
deliberate, safety-first choice, not a defer-to-whichever-is-richest call:
Trading Brain v3's 11-strategy registry is real and has its own proven order
path, but that path has never been wrapped in `TradingSessionGovernor`'s
session-discipline machinery (one-strategy-lock, mandatory exit enforcement).
Adopting it now would mean re-deriving that safety layer around a second
selector before the first live paper session — exactly the kind of
expansion-before-experience the user is asking to stop doing.

```
MSI evidence (MDI/PSI/MSSI/VSB/MPPI/Consensus)
        ↓
market_thesis.assess()  (canonical thesis, §1.2)
        ↓
MarketThesisRegimeProvider  (already built, Phase 5 — the missing wire, §2)
        ↓
TradingSessionGovernor.strategy_selector.select_strategy()   <- ONE owner, IRON_CONDOR/IRON_FLY only
        ↓
msi_trade_construction  (already wired into trading_brain_runtime)
        ↓
Risk Governor pipeline  (already wired, real capital/margin/portfolio checks)
        ↓
PaperBroker
```

Trading Brain v3's 11-strategy selector and its `run_shadow` order path
become explicitly **frozen/research** — a real, tested capability reserved
for a future, deliberate strategy-universe expansion once v1 has real
session-level operating history, not adopted now. MSI selector + SSF remain
exactly what they are today: an upstream evidence/suitability feed that
already exists and needs no change — `MarketThesisRegimeProvider` already
consumes their downstream product (`market_thesis`) correctly.

---

## 4. Exact remaining blockers (the missing arrows)

No new module, no redesign — four wiring gaps, in dependency order:

1. **`deploy/bujji-paper-intelligence-campaign.{service,timer}` not
   installed.** (§1.1) Lowest-effort, highest-leverage: this alone gets real
   MSI evidence + real strategy decisions flowing on a schedule, observation
   -only, immediately.
2. **No live `MarketDataProvider` backed by `FyersBroker` exists for
   `bujji_options_os_runner.py`.** It constructs `ReplayChainProvider`
   (bhavcopy file) today. This is a genuine small implementation gap, not
   pure wiring — `run_shadow_live_observatory.py` already shows the exact
   `FyersBroker` construction/connection pattern to reuse; what's missing is
   an adapter implementing whatever protocol `bujji_options_os_runner.py`'s
   `_startup()` expects from its chain provider.
3. **`HumanSuppliedRegimeProvider` → `MarketThesisRegimeProvider` swap.**
   (§2) `MarketThesisRegimeProvider` already exists, already tested, already
   derives real regime from `market_thesis.assess()`. This is the smallest
   task in the whole blueprint: change which `RegimeProvider` subclass
   `bujji_options_os_runner.py` constructs.
4. **`build_outcome_memory_record()` is never called by any production
   runtime.** Its only caller, `ShadowLifecycleOrchestrator`, is never
   constructed outside tests. Once (2) and (3) land and real trades start
   closing, this needs a real call site — likely inside
   `bujji_options_os_runner.py`'s own exit path, following the same pattern
   `ShadowLifecycleOrchestrator` already demonstrates.

---

## 5. Paper Trading Safety Checklist — current state

✅ real and wired · ⚠️ real but not reachable/scheduled in the live path ·
❌ missing entirely

**Entry** (all real, all inside the `TradingSessionGovernor`→`trading_brain_runtime`
→ risk-governor chain that blocker #2/#3 will make live):
✅ duplicate prevention · ✅ order idempotency (`client_order_id`) ·
✅ partial fill handling · ✅ margin check · ✅ capital check · ✅ risk veto
(CAPITAL/PORTFOLIO stages gate order construction, not just log it)

**Position:**
✅ live MTM (`portfolio_reality_engine.revalue_all()`) ·
⚠️ stop-loss (`exit_policy.py` real threshold logic, but only exercised by
the single-shot EOD-replay runner, never a live intraday process) ·
⚠️ adjustment (`REDUCE_SIZE` genuinely executes; roll *assessment* exists but
`msi_dynamic_management` is not imported by the executor, so it never
reaches an order) ·
⚠️ hedge (`TradeLifecycleExecutor._execute_hedge` is real and order-placing,
but `TradingSessionGovernor` never calls it for `ACTION_ADD_HEDGE` — only for
forced mandatory-exit reduce) ·
❌ roll execution (advisory-only; no order-construction branch exists anywhere)

**Exit:**
⚠️ EOD square-off (real hard-limit logic, `mandatory_exit_time`, but lives
only inside the single-shot replay runner — no live, scheduled, intraday
process enforces it today) ·
⚠️ crash recovery (`hydrate_paper_broker`/`record_paper_state` real and
tested, zero real callers) ·
❌ restart recovery at the runtime level (`bujji_options_os_runner.py`'s own
docstring: "no restart-recovery resurrection") ·
⚠️ FYERS token expiry (`FyersTokenManager.refresh()` real and working, but no
live script calls it from a mid-session retry path — the operating
assumption today is human-refreshes-before-market-open, not graceful
mid-session degrade)

**Learning** (`OutcomeMemoryRecord`):
✅ market regime · ✅ thesis · ✅ strategy chosen · ✅ entry price/time ·
✅ exit price/time · ✅ realized PnL ·
❌ reason other strategies were rejected (the fields exist elsewhere —
`decision_artifact.rejected_strategies`, `market_thesis.rejected_strategy_families`
— but are never joined into the outcome record) ·
⚠️ MFE/MAE (fields exist, always `None` today — no per-cycle valuation
history captured yet, known since Phase 5) ·
❌ mistake classification (no field, no vocabulary anywhere in the repo) ·
❌ improvement suggestion (same)

**Top 5 gaps ranked by severity for a live (even paper) session:**
1. No restart/crash recovery wired into the live runtime
2. EOD square-off is not actually live/scheduled anywhere
3. No demonstrated FYERS mid-session auth-error recovery
4. Hedge and roll never reach execution (real recommendations, structurally unreachable)
5. Rejected-strategy reasoning / mistake / improvement fields absent from learning (lowest urgency — honestly absent, not silently wrong)

None of these block *starting* Phase A (below) — Phase A's whole purpose is
surfacing exactly these gaps under real conditions before Phase B trusts any
strategy-evidence conclusion.

---

## 6. PaperBroker realism

Read in full (`bujji/broker/paper.py` + `bujji/broker/simulation/`). The
realism machinery already exists — it is simply never turned on at any
production construction site (`broker/factory.py`, `production_runtime/composition_root.py`
both construct `PaperBroker()` with no override, defaulting every mode to
off/zero).

| Dimension | Modeled today? |
|---|---|
| Slippage | Machinery real (`FIXED_TICK`/`PERCENTAGE`/`VOLATILITY_ADJUSTED`), defaults to `ZERO` everywhere live |
| Bid/ask spread | Not modeled — `MarketSnapshot.bid`/`.ask` fields exist, never read; both sides fill at one reference price |
| Liquidity depth | Not modeled — fill price is size-blind; `available_depth`/`liquidity_score` fields exist, never consumed |
| Partial fills | Config-driven only, never triggered by real order-size-vs-depth conditions |
| Latency | Computed as metadata, never actually delays which quote a fill uses |

**Do not build a full exchange simulator.** Minimum realistic additions,
each a single additive function, ranked by leverage:
1. **Default `PaperBroker()` to a non-zero `SlippageConfig`** instead of
   requiring every caller to opt in. Matters most here because a 4-leg
   IRON_CONDOR/IRON_FLY pays this cost on every leg, every trade, and
   premium-selling is inherently high trade-count.
2. **Read the already-existing `bid`/`ask` fields** to fill BUY-at-ask/
   SELL-at-bid when present, falling back to today's synthetic path
   otherwise. Matters because the illiquid OTM wings of a condor/fly have a
   proportionally wider real spread than the ATM leg — a flat slippage
   percentage can't capture that asymmetry.
3. **Let `available_depth`/`liquidity_score` (already write-only fields)
   widen applied slippage as order size approaches depth.** Multi-leg
   premium-selling is naturally sized larger and the far-OTM legs are
   typically thin — today's broker can't ever show a strategy is unfillable
   at real size.
4. **Wire the already-built `execution_profiles/profiles.py` realistic
   config as the actual default** at the two live construction sites
   (`broker/factory.py:27,41`, `production_runtime/composition_root.py:90`).
   This requires zero new logic — the realism layer already exists and is
   simply disconnected from every real call site today.

---

## 7. First 30 Session Experiment

### Phase A — 5 sessions, system stability only

**Explicit non-goal:** no strategy optimization, no interpretation of win
rate. **Goal:** does the wired runtime survive a real trading day without
silent failure?

Preconditions before session 1:
- Blockers #1–#3 (§4) landed and deployed.
- FYERS token freshly refreshed (`source /tmp/local_fyers.env` — separately,
  today's live check found `bujji-daily-intelligence.service` failing on an
  expired refresh token; this must be resolved before Phase A, independent
  of this blueprint).
- `bujji_options_os_runner.py` running against live FYERS data (blocker #2),
  live-derived regime (blocker #3), still paper-only (`PaperBroker`, no live
  capital).

Measure every session, no judgment applied yet:
- Uptime: did the process run start-to-mandatory-exit without crashing?
- Errors: count and classify every entry in the session's own error log —
  broker call failures, risk-governor rejections, exceptions.
- Decision quality (structural, not P&L): did a `DecisionArtifact` get
  produced for every cycle? Did the selected strategy's `governor_reasoning`
  cite real evidence (non-empty `market_regime`/`directional_bias`)?
- Artifacts generated: `DecisionArtifact` count, `OutcomeMemoryRecord` count
  (should be ≥0, and >0 once §4 blocker #4 lands), health-heartbeat
  completeness.

Exit criterion for Phase A: 5 sessions with zero unrecovered crashes and a
`DecisionArtifact` produced every scheduled cycle. If not met, do not advance
to Phase B — fix the specific failure, repeat Phase A, do not add scope.

### Phase B — 25 sessions, strategy evidence collection

**Only begins after Phase A's exit criterion is met.**

Measure, and only now start interpreting:
- Win rate, expectancy, drawdown per strategy (IRON_CONDOR vs IRON_FLY —
  the only two in play, per §3's deliberate scope decision).
- Strategy suitability accuracy: did `market_thesis`'s `preferred_strategy_families`
  actually match which strategy the session selected and how it performed?
- Thesis accuracy: for cycles with an `OutcomeMemoryRecord`, did
  `directional_bias`/`volatility_environment` at entry match what actually
  happened by exit (using `entry_regime` vs. realized price action already
  captured in `PositionLifecycle`)?

No strategy-selection logic changes during Phase B. This phase produces
evidence for a *future* decision, not a live one.

---

## 8. Success criteria before live capital deployment

Not proposed lightly, and explicitly out of scope to act on now — this is
the bar for a *future* decision, listed so it's visible from day one:

1. Phase A's stability bar met with zero exceptions to investigate.
2. Phase B complete: 25 real sessions, real `OutcomeMemoryRecord`s for every
   closed position (blocker #4 landed and proven, not just wired).
3. §5's Top-5 safety gaps closed or explicitly accepted in writing — crash/
   restart recovery and EOD square-off are the two that matter most once
   capital is real, not paper.
4. PaperBroker realism additions (§6) landed — a strategy that looks
   profitable against a frictionless fill model has not been evidence-tested
   for its actual edge.
5. A second, independent audit (not the same session/agent lineage that
   built the feature) re-verifies the above four before any capital-mode
   flag is ever introduced — this document does not authorize that step; it
   only defines what would need to be true first.

---

## 9. What this phase explicitly did NOT do

Per the standing instruction: no code was written, no new module was
created, no existing intelligence layer was modified or extended. This is
audit and decision-recording only. §4's four blockers are the complete list
of implementation work remaining before Bujji can run one live, evidence
-grounded, paper-only session end to end — nothing else is required, and
nothing beyond that should be built before Phase A actually runs.
