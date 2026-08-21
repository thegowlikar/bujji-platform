# Phase 19.9.5 — Bujji Intelligence Runtime Integration Audit

**Audit and architecture-alignment phase only. No code was written or modified. No new campaign,
recorder, observation model, scheduler, or storage was created.**

Every finding below was verified directly against the live VPS (`root@139.59.76.137:/opt/bujji/app`) —
running processes, real file mtimes, real database row counts and timestamps, real source code — not
assumed from prior phase docs.

## 1. Current Runtime Map

### 1.1 What is actually running right now

```
ps aux | grep python
```

confirmed exactly one long-lived Bujji process on the box:

```
PID 787565  root  Jul 31 (13d ago)  /opt/bujji/.venv/bin/python run_live_shadow.py --live
            --bhavcopy data/bhavcopy/BhavCopy_..._20260730_F_0000.csv
            --bhavcopy-day 2026-07-30 --log-file logs/lsq1_day1.log
```

**This process has been running continuously since 2026-07-31 — 13+ days.** `run_live_shadow.py` is
documented (its own module docstring) as a single-trading-day orchestrator: pre-market checklist → auth →
WebSocket → wait for open → run → market close → generate reports → shutdown. A 13-day-old still-alive
instance of a "one trading day" process is a real anomaly — either genuinely hung, or the box has not been
restarted/cleaned up since. This is flagged as a reliability finding (§5), not assumed to be intentional.

Two other short-lived `bujji`-owned processes were also caught mid-run (pre-market checklist scripts,
`tools/pre_market_supplementary_checks.py` / `tools/lsq_day1_premarket.py`), both against `Jul 29`/`Jul 30`
Bhavcopy files — consistent with `run_live_shadow.py`'s own pre-market checklist step, not a second
independent runtime.

No cron entries exist for `root` or the invoking user (`crontab -l` empty both). No relevant `systemd`
unit exists (`/etc/systemd/system/` has only an unrelated `bujji-orb-vwap-legacy.service`, not touched or
investigated further — out of this audit's scope). No `at` jobs queued.

**Yet `data/historical_reality/normalized/historical_observations.db` has a real row as recent as
`2026-08-14T15:35:00+05:30`** (yesterday's market close, 670,163 total rows) — and
`logs/fyersRequests.log` was last written `Aug 14 17:05` under the `bujji` user (not `root`, i.e. NOT
`run_live_shadow.py`, which runs as `root`). **Conclusion: real daily data capture into
`HistoricalObservationStore` IS happening, but not via any automated scheduler found on this box** — it is
run manually, per-day, by the human operator (consistent with this session's own memory of a standing
reminder to manually refresh the FYERS token and trigger capture scripts each morning). This is the
single most important reliability finding in this audit (§5.1).

### 1.2 Component-by-component map

| Component | Purpose | Starts how | Runs when | Stores where | Currently running? |
|---|---|---|---|---|---|
| `scripts/capture_market_reality_session.py` (Phase 17I.6) | Continuous NIFTY Spot/Futures/VIX capture loop | Manual invocation | Whenever the operator runs it | `HistoricalObservationStore` (`data/historical_reality/normalized/historical_observations.db`) — the canonical Layer-0 store | Not running now |
| `scripts/capture_options_reality_session.py` (Phase 17I.10) | Full NIFTY option-chain capture, 5-min cadence, all expiries | Manual invocation | Whenever the operator runs it | Same `HistoricalObservationStore`, reused unmodified per its own 17I.9 audit | Not running now |
| `scripts/run_futures_depth_poller.py` | Futures market-depth polling | Manual invocation | Whenever the operator runs it | Same `HistoricalObservationStore` | Not running now |
| `scripts/run_shadow_live_observatory.py` | Entry point for `shadow_observatory` (below) | Manual invocation | Whenever the operator runs it | `shadow_observatory`'s own session artifact store (not investigated further — out of scope, no evidence it competes with `HistoricalObservationStore`) | Not running now |
| `scripts/run_shadow_validation_pass.py` | Entry point for `shadow_validation` (below) | Manual invocation | Post-hoc, against already-captured data | Reads existing artifacts; writes validation reports | Not running now |
| `run_live_shadow.py` / `bujji.live_shadow_operator` (Sprint 107) | Full live shadow **trading-decision** day: real auth, real WebSocket, runs the frozen Series 73-106 decision chain in shadow mode (orders structurally blocked via `bujji.broker.guard`) | Manual invocation (`python run_live_shadow.py --live ...`) | Was started 2026-07-31, still alive | `OperatorJournal` → `data/live_shadow_journal/operator_journal.jsonl` — **a completely separate store from `HistoricalObservationStore`** | **Running now (PID 787565, 13 days old)** |
| `bujji.shadow_runtime` (Phase-5) | Minimal, standalone, live-capable **observation-only** runtime (no trade/strategy/position concept) — the package Phase 19.2.2's own clock-injection fix touched (`shadow_session_runner.py`) | Not wired to any script found in `scripts/` or the repo root during this audit | Unclear — no invocation found | Its own `market_snapshots.jsonl`/`intelligence_cycle.jsonl` (per Phase 15O/19.2.2's own prior docs) | Not running now |
| `bujji.msi_shadow_trading` (Series 100) | Post-hoc shadow **position** tracking from `Decision Auditor`-approved decisions, repriced against later real Bhavcopy | Library, invoked by an offline/batch driver (not found running) | Batch/offline, not continuous | Its own position-tracking store (not investigated further) | Not running now |
| `bujji.shadow_observatory` (Gate V.0) | Passive black-box recorder — "what did Bujji know/decide/do" — for the Trading Brain Shadow Runtime | Via `scripts/run_shadow_live_observatory.py` | Manual invocation | Its own session artifact store | Not running now |
| `bujji.shadow_lifecycle` (`orchestrator.py`) | Not read in full this audit — name suggests position/session lifecycle orchestration, likely related to `msi_shadow_trading` or `production_runtime.shadow_session_controller` | Unclear | Unclear | Unclear | Not running now |
| `bujji.shadow_trade_construction` | Builds `ShadowTradeCandidate`s (Phase 14's own deliverable, confirmed from this session's much earlier context) | Library, called by other components | N/A (library) | N/A (library) | N/A |
| `bujji.shadow_validation` | Health metrics, memory validation, strategy-reasoning validation, session summaries — the Phase 12 series deliverables this session's own earlier context built | Via `scripts/run_shadow_validation_pass.py` | Manual, post-hoc | Reads/writes its own report artifacts | Not running now |
| `bujji.production_runtime` | A large, real, apparently-mature orchestration scaffold: `RuntimeScheduler`, `startup.py`/`shutdown.py`, `composition_root.py`, `trading_brain_runtime.py`, `shadow_session_controller.py`, circuit breaker, rate limiter, health checks | Not invoked by anything found running or by `run_live_shadow.py` (confirmed: `live_shadow_operator` does not import it) | Unclear — appears to be a designed-but-not-currently-deployed runtime | Unclear | **Not running; no evidence it is wired to anything currently live** |
| `bujji.runtime` (`recovery_coordinator.py`) | Single-file package, name suggests crash-recovery coordination | Unclear | Unclear | N/A | Not running |
| `bujji.market_state.intelligence_cycle_recorder` | The ONLY place in the entire codebase, confirmed by direct grep this phase, that calls `LiquidityBrain.analyze()` outside `runner.py` — records `intelligence_cycle.jsonl` | Called by `shadow_runtime.shadow_session_runner` and (separately) by whatever drives `market_state` cycles | Only when its caller runs | `intelligence_cycle.jsonl` (append-only) | Not running (its only known caller, `shadow_runtime`, is not running) |

## 2. Duplicate System Findings

### 2.1 Confirmed: the ENTIRE Phase 19.0–19.9 Intelligence Foundation has zero live runtime wiring today

Direct grep across `bujji/live_shadow_operator/*.py` (the one process actually running) for
`HistoricalObservationStore`, `historical_reality`, `intelligence_cycle_recorder`,
`market_intelligence_snapshot`, `decision_intelligence`, `market_phenomena`, `market_state_graph`,
`market_environment` — **zero matches, every single term.** The live shadow operator that has been running
for 13 days does not call, import, or know about any of this session's Phase 19.x work. This is not a bug
in Phase 19.x — every phase since 19.2.2 explicitly deferred live-runtime wiring — but it is the central,
now-confirmed fact this audit exists to establish: **intelligence processing is not yet a consumer of
anything in the live path.**

### 2.2 Confirmed: two structurally separate "live capture" stores, not one

- `HistoricalObservationStore` (`data/historical_reality/normalized/historical_observations.db`) — the
  canonical, FROZEN (Phase 18.15) Layer-0 store. Fed by `capture_market_reality_session.py`,
  `capture_options_reality_session.py`, `run_futures_depth_poller.py`. 670,163 real rows, current through
  yesterday's close.
- `OperatorJournal` (`data/live_shadow_journal/operator_journal.jsonl`) — fed exclusively by
  `run_live_shadow.py`/`live_shadow_operator`. Records trading-decision cadence, end-of-day outcomes,
  portfolio state snapshots — a genuinely different kind of record (decisions/outcomes, not raw market
  observations), so this is **not a true duplicate of `HistoricalObservationStore`'s content**, but it IS
  a second, disconnected persistence root the operator must know about separately. Not a finding requiring
  removal — a finding requiring the unified architecture (§3) to explicitly name both roles rather than
  conflating them.

### 2.3 Real fragmentation across at least 7 "shadow"-named packages

`live_shadow_operator`, `msi_shadow_trading`, `shadow_observatory`, `shadow_runtime`, `shadow_lifecycle`,
`shadow_trade_construction`, `shadow_validation` — each has a genuinely distinct, real, documented purpose
(confirmed by reading each package's own docstring, not assumed from the name alone), **but the sheer
number of similarly-named packages is itself a real onboarding/maintenance cost**, and this audit could
not, in the time available, fully trace which of these actively depend on which others versus which are
now orphaned relative to the currently-running `live_shadow_operator` path. `bujji.production_runtime` in
particular — a large, mature-looking orchestration scaffold with its own scheduler, circuit breaker, and
startup/shutdown lifecycle — shows no evidence of being wired to anything currently live. This is flagged
as a **candidate for a dedicated follow-up audit** (not resolved here — this phase's own scope is
architecture alignment, not a full dependency-graph trace of every shadow package), specifically to answer:
is `production_runtime` a superseded, safe-to-archive design, or the intended eventual replacement for
`live_shadow_operator`'s ad hoc orchestration? This audit does not have enough evidence to say.

### 2.4 No duplicate observation models found

`HistoricalObservationStore`'s `HistoricalObservation` model (Phase 17H+) remains the single real
observation shape every capture script targets. No second, competing observation dataclass was found.

### 2.5 No duplicate schedulers found running

`production_runtime.runtime_scheduler.RuntimeScheduler` exists in code but is not invoked by anything
currently running. No cron/systemd/at mechanism exists on the box. There is, in the concrete sense of
"something that actually runs on a schedule," **zero schedulers currently active** — capture is 100%
manually triggered today (§5.1).

## 3. Proposed Unified Runtime Architecture

```
                     Market Open (manual trigger today — see §5.1)
                              |
                              v
                     Data Capture Layer
        (capture_market_reality_session.py, capture_options_reality_session.py,
         run_futures_depth_poller.py — ALL ALREADY REAL, ALREADY WORKING,
         UNCHANGED by this audit)
                              |
                              v
                  HistoricalObservationStore
                 (already frozen, Phase 18.15 — UNCHANGED)
                              |
                              v
              Reality Reconstruction Layer
        (build_market_reality_snapshot() — already real, UNCHANGED)
                              |
                              v
              Intelligence Processing Layer
   (bujji.intelligence's 6 brains -> MarketIntelligenceSnapshot [19.3]
    -> DecisionContext [19.4] -> DecisionIntelligenceSnapshot [19.6]
    -> MarketPhenomenaAssessment [19.7] -> MarketStateNode [19.8]
    -> MarketEnvironmentAssessment [19.9] — ALL ALREADY REAL,
    CURRENTLY ZERO LIVE CALLERS, per §2.1)
                              |
                              v
              Intelligence Observations
     (Market Understanding Memory [19.5] — EventStore-based,
      already real, UNCHANGED)
                              |
                              v
          Reports / Evaluation / Memory
    (a NEW, thin daily-report generator is the only genuinely new
     component this architecture implies — see §4)
```

### Non-negotiable separation — verified already true today, not just proposed

The capture scripts (§1.2, first three rows) **already** have zero import-time or runtime dependency on
`bujji.intelligence`, `bujji.decision_context`, `bujji.decision_intelligence`, `bujji.market_phenomena`,
`bujji.market_state_graph`, or `bujji.market_environment` — confirmed by the same grep that produced §2.1's
finding (it found nothing in `live_shadow_operator`, and separately, none of Phase 19.0–19.9's own six
phases ever imported a capture script). **The correct `Capture → Storage (+→ Intelligence)` shape the user
specifies is therefore already the reality on disk, by accident of how these were built in
sequence — not yet by deliberate wiring.** The task ahead is connecting the arrow from Storage to
Intelligence (currently absent), never rerouting Capture through Intelligence.

## 4. Implementation Plan (Phase 19.10, not this phase)

Only a plan — nothing below was built this phase.

1. **A single daily driver script** (e.g. `scripts/run_daily_intelligence_cycle.py`) that: reads a date's
   already-captured `HistoricalObservationStore` rows, calls `build_market_reality_snapshot()` (unchanged),
   threads the result through the six brains → `MarketIntelligenceSnapshot` → ... →
   `MarketEnvironmentAssessment` (all unchanged), and appends the result to Phase 19.5's own
   `MarketMemoryEntry` store via the already-real `record_market_memory()`. This is the ONLY genuinely new
   code Phase 19.10 should write — a consumer, never a second capture loop.
2. **A thin daily report renderer** reading the day's `MarketEnvironmentAssessment` + `MarketStateNode`
   sequence and producing a plain-text/markdown summary — "what kind of environment did Bujji observe
   today, and why." No new storage; reads what §4.1 already wrote.
3. **Explicit non-goal, restated**: this driver must NEVER be invoked by, or invoke, `run_live_shadow.py`
   or any trading-decision path. It is a pure consumer of `HistoricalObservationStore`, exactly like
   `reality_memory/catalog.py` already is.
4. **Scheduling remains manual, initially** — per §5.1's finding, there is no automated scheduler on this
   box today; introducing one is a real, separate infrastructure decision (cron vs. systemd timer vs.
   something else) the user should make explicitly, not something to default into silently.
5. **The `production_runtime`/`shadow_lifecycle` disposition question (§2.3) is out of this plan's scope**
   — flagged for a dedicated follow-up audit, not resolved or acted on here.

## 5. Reliability Audit

### 5.1 Data Collection Reliability

- **Restart/crash recovery**: `HistoricalObservationStore` is append-only with a documented
  `ConflictingHistoricalObservationError` guard (Phase 17H+, confirmed in multiple earlier phases of this
  session) — a re-run of a capture script for an already-captured window fails closed rather than
  silently double-writing. Verified as still true by inspecting the frozen store's own governance docs
  referenced throughout this session; not re-derived from scratch this audit.
- **Duplicate prevention**: same mechanism as above — content-based conflict detection, not a new finding.
- **Missing-session handling**: not verified this audit (would require simulating a missed day against
  `MarketCalendar` — out of this audit's read-only scope).
- **Timezone correctness**: every capture script and every Phase 19.x brain uses `IST`
  (`bujji.core.clock`) consistently — confirmed by this session's own exhaustive work through Phase 19.2.2
  onward; no new drift found.
- **Market holiday handling**: `bujji.market_calendar.MarketCalendar` exists and is imported by
  `run_live_shadow.py`; not independently re-verified this audit.
- **Partial capture handling**: `capture_options_reality_session.py`'s own docstring documents fail-closed
  behavior via `CertificationGate` — refuses to write until certification succeeds. Not re-verified live
  this audit (would require a real failure injection, out of scope).
- **The real, concrete gap this audit found**: capture is **not automated**. No cron, systemd, or `at`
  mechanism triggers `capture_market_reality_session.py`/`capture_options_reality_session.py` on this box.
  Yesterday's real data (through 2026-08-14T15:35 IST) exists because the human operator ran something
  manually (confirmed by the `bujji`-user-owned `fyersRequests.log` timestamp, distinct from the `root`-owned
  `run_live_shadow.py` process). **If the operator does not run capture on a given day, no data is
  captured that day — there is no automatic fallback.** This is the single most actionable finding in this
  audit.

### 5.2 Intelligence Runtime Reliability

- **Deterministic rebuild**: proven exhaustively across Phase 19.2.2 (six brains), 19.3
  (`MarketIntelligenceSnapshot`), 19.6 (`DecisionIntelligenceSnapshot`), 19.7 (`MarketPhenomenaAssessment`),
  19.8 (`MarketStateNode`), and 19.9 (`MarketEnvironmentAssessment`) — every one of those phases' own test
  suites includes a same-inputs-same-fingerprint test, all currently passing (5,870 passed as of Phase
  19.9's own regression run).
- **Idempotency**: `market_memory_id_for()` (Phase 19.5) and every other `*_id_for()` convention across
  these phases is deterministically content-derived — replaying the same inputs never produces a duplicate
  record, verified by test in every phase since 19.5.
- **Replay capability**: `execution_mode` (LIVE vs. HISTORICAL_REPLAY) is proven to produce byte-identical
  output at every layer, Phase 19.2.2 through 19.9, each independently tested.
- **Failure isolation**: not yet meaningfully testable — since §2.1 confirms zero live callers exist yet,
  there is no real failure-isolation behavior to audit. This becomes a real question only once Phase 19.10
  wires a live/daily caller.

## Success Criteria — answered

1. **Where does Bujji's daily market data enter?** `capture_market_reality_session.py` /
   `capture_options_reality_session.py` / `run_futures_depth_poller.py`, run manually by the operator.
2. **Where is it stored?** `HistoricalObservationStore`
   (`data/historical_reality/normalized/historical_observations.db`) — confirmed current through
   2026-08-14T15:35 IST, 670,163 rows.
3. **How does intelligence consume it?** It does not yet, live — confirmed zero live callers (§2.1). The
   full Phase 19.0–19.9 chain is real, tested, and deterministic, but currently only exercised by test
   suites and manual smoke tests, never by a scheduled or continuous process reading real captured data.
4. **What process keeps it alive?** No automated process does. The operator's manual daily trigger is the
   only thing keeping data flowing (§5.1) — the actual single point of failure in the entire system today.
5. **How do failures recover?** Capture-layer failures fail closed and are conflict-guarded
   (§5.1); a missed manual trigger has no automatic recovery. Intelligence-layer failure isolation is not
   yet a real question (§5.2) since nothing live calls it yet.
6. **What single runtime should own future daily operations?** Neither `run_live_shadow.py` (a
   trading-decision orchestrator, wrong layer for this) nor any of the "shadow" packages audited in §2.3
   (undetermined disposition) — a **new, thin, single-purpose daily driver** (§4.1), reading already-captured
   `HistoricalObservationStore` data and calling the already-real Phase 19.x chain, is the only piece that
   does not already exist and is not a duplicate of anything found in this audit.
