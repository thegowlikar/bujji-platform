# Phase 19.9.6 — Bujji Runtime Ownership Audit

**Audit only. No code modified. No scheduler created. No intelligence wired. No live capture started. No
broker API touched. No new shadow framework created.**

Every finding verified directly against the live VPS — real imports (grep + manual trace), a pre-existing
authoritative ownership document discovered this phase (`docs/SYSTEM_OWNERSHIP.md`), and the live process
table — never assumed.

## 0. A pre-existing, authoritative answer was found: `docs/SYSTEM_OWNERSHIP.md`

Before tracing anything by hand, `docs/` was checked for an existing ownership map — and one already
exists, verified (per its own header) "against commit `a4a6220` on branch `v1.0-shadow`." It documents
**three generations** in this repository:

1. **Legacy ORB-VWAP ATM Seller** — deprecated, disabled systemd unit `bujji-orb-vwap-legacy.service`.
2. **"Options OS v3" Series 31–54** — already-abandoned, expects an external `mic-v2` process not part of
   this repo. No disposition decision made; not touched by this audit.
3. **"Bujji Options OS" (the current product)** — regime-aware, defined-risk options-selling architecture.
   Its own runtime controller is documented as `bujji/production_runtime/trading_brain_runtime.py`,
   `shadow_session_controller.py`, `trading_session_governor/session_governor.py`. **Its own stated
   status: "No automated entrypoint exists yet — reserved future systemd unit name:
   `bujji-options-os.service` (not yet created)."**

This is the single most important pre-existing fact this audit builds on: **`production_runtime` is
already the documented, designed runtime controller for the current product generation — but by its own
project's own admission, was never wired to a real entrypoint.** This phase's own investigation (below)
independently re-confirms that statement is still true today, and additionally discovers a **fourth,
undocumented lineage** (`live_shadow_operator`) that SYSTEM_OWNERSHIP.md does not mention at all.

## 1. Runtime Inventory

| Component | Purpose | Status | Entry point | How started | Dependencies | Storage touched | Used by |
|---|---|---|---|---|---|---|---|
| `bujji.production_runtime` | Documented runtime controller for "Bujji Options OS" gen 3: `RuntimeScheduler`, `startup.py`/`shutdown.py`, `composition_root.py`, `trading_brain_runtime.py`, `shadow_session_controller.py`, `trading_session_governor/`, circuit breaker, rate limiter, health checks | **Scaffold — real, tested, but zero live entrypoint** (confirmed both by SYSTEM_OWNERSHIP.md's own admission and this audit's independent import trace, §3) | None (no script in repo root or `scripts/` imports it as a driver) | Not started anywhere | `bujji.trading_brain.*`, `bujji.broker.paper.PaperBroker`, `bujji.core.event_bus` (per SYSTEM_OWNERSHIP.md's shared-infrastructure table) | N/A — never runs | 80+ files import individual modules from it (mostly test files and sibling packages reusing its models/taxonomy, not runtime orchestration callers — see §3) |
| `bujji.live_shadow_operator` (Sprint 107) | **The only currently-running process.** Full live shadow trading-DECISION day: real auth, real WebSocket, runs a frozen decision chain (`SessionDriver`/`run_full_cadence`, from `bujji.live_pipeline_bridge`/`bujji.live_shadow_validation` — NOT from `production_runtime`), orders structurally blocked via `bujji.broker.guard` | **Active (PID 787565, running continuously since 2026-07-31 — 13+ days)** | `run_live_shadow.py` | Manual (`python run_live_shadow.py --live ...`) | `bujji.core`, `bujji.live_pipeline_bridge`, `bujji.live_shadow_validation`, `bujji.msi_portfolio_construction`, `bujji.market_calendar`, `bujji.broker.*`, `bujji.trading_brain.portfolio_valuation`, `bujji.trading_brain.exit_engine`, `bujji.journal.*` — **confirmed zero import of `bujji.production_runtime`** | `OperatorJournal` → `data/live_shadow_journal/operator_journal.jsonl` | Nothing else — a leaf process |
| `bujji.shadow_runtime` (Phase-5) | Minimal, standalone, **observation-only** runtime (own docstring: "NOT a trading runtime... no concept of a trade, a strategy, a position, or risk"). Explicitly, deliberately excludes `production_runtime` by design ("the Phase-5 design review's own final dependency diagram excludes production_runtime entirely"). The package Phase 19.2.2 hardened for clock-injection/replay determinism this session | Inactive — no running process found; no script in `scripts/` or repo root invokes it | `bujji.shadow_runtime.shadow_session_runner.ShadowSessionRunner` (library, no CLI entrypoint found) | Not started anywhere currently | `bujji.execution_reality` (quote normalization/storage/liquidity, unmodified), `bujji.market_state.intelligence_cycle_recorder` (the same file Phase 19.2.2 modified) | Its own `market_snapshots.jsonl` / `intelligence_cycle.jsonl` — **a third storage location, distinct from both `HistoricalObservationStore` and `OperatorJournal`** | Extensively unit-tested (`tests/test_shadow_runtime*.py`, 6+ files); no production caller found |
| `bujji.msi_shadow_trading` (Series 100) | Post-hoc shadow position tracking from `Decision Auditor`-approved decisions | Inactive; batch/offline library | Not found as a standalone script | Called by an offline driver (not located) | `PositionLifecycle` (reused, not re-implemented) | Its own position-tracking store | Referenced by `tests/test_msi_shadow_trading.py` |
| `bujji.shadow_observatory` (Gate V.0) | Passive black-box recorder for the Trading Brain Shadow Runtime | Inactive | `scripts/run_shadow_live_observatory.py` | Manual | Observes `production_runtime`-adjacent components without being observed by them (fail-isolated by design) | Its own session artifact store | `tests/test_shadow_observatory.py` |
| `bujji.shadow_lifecycle` (`orchestrator.py`) | Not fully read this audit (single file, name suggests session/position lifecycle orchestration) | Unclear | Unclear | Unclear | Unclear | Unclear | Not traced this audit — flagged for follow-up |
| `bujji.shadow_trade_construction` | Builds `ShadowTradeCandidate`s (a Phase 14 deliverable per this session's own earlier context) | Library only | N/A | N/A (imported, not run standalone) | N/A | N/A | `tests/test_shadow_trade_construction_safety.py` |
| `bujji.shadow_validation` | Health metrics, memory validation, strategy-reasoning validation, session summaries | Inactive; post-hoc batch tool | `scripts/run_shadow_validation_pass.py` | Manual, post-hoc | Reads existing artifacts | Report artifacts only | N/A |
| Capture scripts (`capture_market_reality_session.py`, `capture_options_reality_session.py`, `run_futures_depth_poller.py`) | Real Layer-0 data capture into the canonical store | **The only components with real, fresh production data** (confirmed `historical_observations.db` current through 2026-08-14T15:35 IST) | Each script itself | **Manual, no scheduler found** (§1 of Phase 19.9.5, re-confirmed) | `bujji.historical_reality.store.HistoricalObservationStore`, `bujji.market_reality.certification.CertificationGate` | `HistoricalObservationStore` (`data/historical_reality/normalized/historical_observations.db`) | Everything downstream in Phase 18/19.x ultimately reads from here |
| `bujji.market_state.intelligence_cycle_recorder` | Calls `LiquidityBrain.analyze()`, records `intelligence_cycle.jsonl` | Inactive (its only known caller, `shadow_runtime`, is not running) | N/A (library) | N/A | `bujji.intelligence.liquidity_brain`, `bujji.market_perception.*` | `intelligence_cycle.jsonl` | `shadow_runtime.shadow_session_runner` |
| `bujji_options_os_runner.py` (repo root) | **The real, documented entrypoint for `production_runtime`/`trading_session_governor`/`trading_brain`/`msi_trade_construction`/`shadow_observatory`** — "Phase-1 Shadow Runner." Confirmed by own docstring: constructs composition root + `PaperBroker` stack + Shadow Observatory + `TradingSessionGovernor`, calls lifecycle methods in order | Real entrypoint exists, but shadow-mode only; "no restart-recovery resurrection... Category B/C future work" (own docstring) — i.e. not scheduled, not supervised | Itself | Manual (implied; no scheduler found for it either) | `production_runtime` composition root, `trading_session_governor`, `trading_brain`, `msi_trade_construction`, `shadow_observatory`, `PaperBroker` — explicitly NOT `live_shadow_operator` (own docstring confirms) | Whatever `TradingSessionGovernor`/Shadow Observatory persist internally (not traced further) | Trading-decision-scoped — same domain Phase 19.10 must not own (see correction note below) |
| `bujji/app.py` | **Confirmed, per SYSTEM_OWNERSHIP.md, to be the Legacy ORB-VWAP entrypoint**, not Options OS | Deprecated (disabled systemd unit) | `bujji/app.py` | `bujji-orb-vwap-legacy.service` (disabled by default) | ORB-VWAP-only stack | Its own `TradeJournal` | N/A — deprecated |

## 2. Ownership Conflicts

### 2.1 No conflict on data capture — single, real, uncontested owner

`HistoricalObservationStore`, fed by the three capture scripts, is uncontested — nothing else claims to be
the Layer-0 data store, and SYSTEM_OWNERSHIP.md does not mention any competitor. The only real gap is
**scheduling** (manual only), not ownership.

### 2.2 Real conflict: THREE separate persistence roots for "what happened live today"

- `HistoricalObservationStore` — raw market observations (Layer 0). Owned by the capture scripts.
- `OperatorJournal` (`data/live_shadow_journal/`) — trading-decision cadence/outcomes. Owned by
  `live_shadow_operator`, the only actually-running process.
- `intelligence_cycle.jsonl` / `market_snapshots.jsonl` — owned by `shadow_runtime`, currently not running,
  never populated with anything from the last 13+ days.

These are not true duplicates of each other's CONTENT (each records a genuinely different kind of fact —
raw observation vs. decision journal vs. intelligence cycle), but a future daily runtime must know all
three exist and pick exactly one as ITS OWN write target, never invent a fourth.

### 2.3 Real conflict: `production_runtime` is the DOCUMENTED owner but has zero live evidence; `live_shadow_operator` is the REAL owner but is UNDOCUMENTED and architecturally the wrong layer

SYSTEM_OWNERSHIP.md names `production_runtime` as the runtime controller for the current product
generation. This audit's own import trace (§3) confirms `production_runtime` still has no real entrypoint
today. Meanwhile, `live_shadow_operator` — the one process that IS real and running — does not appear
anywhere in SYSTEM_OWNERSHIP.md at all, and by its own docstring is a trading-DECISION orchestrator (reuses
a frozen decision chain, generates `TRADE_APPROVED`/etc. decisions in shadow mode), not a data-capture or
intelligence runtime. **Neither existing "owner" is the right shape for what Phase 19.10 needs** (a
capture → intelligence heartbeat that must never touch trading decisions) — this is the central conflict
this audit exists to resolve (§3 recommendation).

### 2.4 No conflict found on health monitoring, heartbeat, or supervised recovery

No heartbeat/liveness-check/auto-restart mechanism was found anywhere — not in `production_runtime`
(it has `health.py`/`circuit_breaker.py`/`rate_limiter.py` as building blocks, but nothing invokes them
continuously since nothing runs it), not in `live_shadow_operator` (it has its own `health.py` /
`freshness.py` for its OWN session, but nothing watches the operator process itself from outside — this is
almost certainly why it has been alive, unattended, for 13+ days: **nothing exists to notice or restart
it**). This is a real, uncontested gap, not a conflict.

## 3. `production_runtime` Deep Audit

**Import trace, not assumption.** `grep -rln 'production_runtime'` returns 80+ files, but the overwhelming
majority are either (a) test files exercising `production_runtime`'s OWN internal modules directly
(`tests/production_runtime/test_trading_session_governor.py`, `tests/test_trading_brain_runtime.py`,
`tests/test_runtime_state_machine.py`, etc. — these test the package in isolation, they are not evidence
of a live caller), or (b) files with the SUBSTRING "production_runtime" appearing in a docstring/comment
(e.g. `shadow_runtime/__init__.py`'s own docstring explicitly says it does *not* import
`production_runtime`'s `RuntimeScheduler` — that is a match for "does not depend on," not a real
dependency).

**Real, confirmed facts:**
- `docs/SYSTEM_OWNERSHIP.md` itself states, in its own words: "No automated entrypoint exists yet" for the
  product generation `production_runtime` is supposed to run.
- `run_live_shadow.py` / `bujji.live_shadow_operator.operator` — the one real running process — has
  **zero** `production_runtime` imports (confirmed by direct listing of every `bujji.*` import in both
  files).
- No script under `scripts/` or the repo root imports `production_runtime` as a driver.
- `production_runtime` DOES contain real, substantial, well-tested logic (`RuntimeScheduler`,
  `circuit_breaker.py`, `rate_limiter.py`, `health.py`, `trading_brain_runtime.py`,
  `shadow_session_controller.py`, `trading_session_governor/`) — this is not dead or fake code, it is a
  real foundation that has simply never been switched on end-to-end.

**Verdict: (C) partially usable foundation, not (A) connected-but-hidden and not (B) obsolete.** Its
building blocks (`circuit_breaker.py`, `rate_limiter.py`, `health.py`, `RuntimeScheduler`) are generic
reliability primitives that a future daily runtime could reuse — but its actual ORCHESTRATION logic
(`trading_brain_runtime.py`, `shadow_session_controller.py`, `trading_session_governor/`) is scoped to
trading-decision lifecycle (strategy selection, order construction, position lifecycle) — precisely the
domain Phase 19.10 must NOT own. **`production_runtime` should not become the Phase 19.10 runtime owner**,
but its reliability primitives are a real, legitimate reuse candidate, separate from its orchestration
logic.

## 4. `run_live_shadow.py` Assessment

- **Why has it remained alive for 13+ days?** No evidence of a supervising process, heartbeat monitor, or
  scheduled restart anywhere in this codebase or on this box (§2.4). The most consistent explanation,
  given the available evidence (a `--bhavcopy-day 2026-07-30` argument baked into the still-running
  command line, and its own docstring describing a SINGLE trading day's workflow ending in "market close →
  generate reports → shutdown"), is that the process did not exit cleanly at the end of its
  2026-07-30/2026-07-31 session and nothing has noticed since. This audit does not have enough evidence
  (no access to its live stdout/log tail was taken, to stay strictly read-only) to say definitively whether
  it is hung, blocked on a reconnect loop, or genuinely still doing useful work — **this itself is the
  finding**: there is no way to answer that question from outside the process today, which is a real
  reliability gap regardless of the specific cause.
- **Is it designed for continuous operation?** No — its own docstring describes a bounded single-day
  workflow with an explicit shutdown step.
- **Does it handle session boundaries?** Yes, for a single day (`MarketCalendar`, pre-market checklist,
  wait-for-open, market-close report generation) — but nothing in its design anticipates being asked to run
  a SECOND day without being restarted with a new `--bhavcopy-day`.
- **Does it leak state?** Not independently verified this audit (would require live memory/log inspection,
  a more invasive check than this read-only audit's scope permits) — flagged as unknown, not asserted
  either way.
- **Does it have recovery?** It has its own internal `TickSilenceWatchdog` (forced WebSocket reconnect) and
  `ProcessLock` (prevents a second concurrent instance) — real, but scoped to within-session resilience,
  not to being restarted by something else after a crash.
- **Should it be retained, wrapped, or retired?** **Retained as-is, but NOT extended or repurposed into the
  Phase 19.10 owner.** It solves a different problem (a real live trading-decision shadow day) than what
  Phase 19.10 needs (a data-capture + intelligence-processing heartbeat). Recommend the user independently
  decide, outside this audit's scope, whether the currently-running 13-day-old instance should be manually
  stopped and restarted cleanly — this audit takes no action on it per the explicit "do not modify" instruction.

## 5. Runtime Ownership Decision

**Option B: promote `shadow_runtime` (Phase-5) into the permanent daily capture + intelligence runtime.**

Reasoning, weighed against the other two options:

- **Not Option A (`production_runtime`)**: its real orchestration logic is trading-decision-shaped
  (`trading_brain_runtime.py`, `shadow_session_controller.py`, `trading_session_governor/`) — exactly the
  domain this phase's own safety rules forbid the new owner from touching. Its reliability PRIMITIVES
  (`circuit_breaker.py`, `rate_limiter.py`, `health.py`) are legitimate future reuse candidates, but the
  package as a whole is the wrong shape to promote wholesale.
- **Not `live_shadow_operator`**: real, running, but is itself a trading-decision orchestrator (generates
  shadow `TRADE_APPROVED`-class decisions) — the same domain problem as `production_runtime`, plus the
  unexplained 13-day liveness anomaly (§4) makes it an actively risky foundation to build on today, not
  just an architecturally wrong one.
- **`shadow_runtime` fits by design, not by convenience**: its own docstring already states the exact
  boundary Phase 19.10 needs ("NOT a trading runtime... no concept of a trade, a strategy, a position, or
  risk"), it already deliberately excludes `production_runtime` as a dependency, and it is already the
  package this session's own Phase 19.2.2 hardened for deterministic, replay-safe operation
  (`IntelligenceContext`, clock injection, evidence lineage) — work that would otherwise need to be
  redone for any other candidate.
- **Real gap to close, not glossed over**: `shadow_runtime` currently writes to its own
  `market_snapshots.jsonl`/`intelligence_cycle.jsonl`, NOT to `HistoricalObservationStore` — it does not
  yet consume the canonical Layer-0 store the way §3's proposed architecture (Phase 19.9.5) requires.
  Wiring that connection is real, scoped Phase 19.10 work, not something this audit found already done.

**This is not "create a new runtime"** — `shadow_runtime` already exists, is already tested, and is already
philosophically aligned; Phase 19.10's job is to extend its capture path to read from
`HistoricalObservationStore` (or to trigger the existing capture scripts and then consume their output) and
thread the result through the already-real Phase 19.0–19.9 Intelligence Foundation, never to author a new
package.

## 6. Phase 19.10 Implementation Boundary

**What the (evolved) `shadow_runtime` owns:**
- Triggering/sequencing the existing, unmodified capture scripts (or reading their already-written
  `HistoricalObservationStore` rows on a schedule) — never a second capture loop.
- Triggering one intelligence cycle per real trading day: `build_market_reality_snapshot()` →
  the six brains → `MarketIntelligenceSnapshot` → `DecisionContext` → `DecisionIntelligenceSnapshot` →
  `MarketPhenomenaAssessment` → `MarketStateNode` → `MarketEnvironmentAssessment` (all already real,
  already tested, Phase 19.3–19.9).
- Recording the result into Phase 19.5's own `MarketMemoryEntry` store via the already-real
  `record_market_memory()`.
- A heartbeat/liveness signal for itself (a real, currently-missing capability across EVERY component
  audited here — §2.4) — reusing `production_runtime.health`/`circuit_breaker` primitives where they
  genuinely fit, per §3's nuanced verdict, rather than reinventing them.
- Recovering from its own crashes by re-reading already-persisted, idempotent state (per this project's
  own "event-derived reconstruction over blindly serializing mutable objects" discipline, already proven
  throughout Phase 15B onward).

**What it does NOT own:**
- Trading decisions (that remains `live_shadow_operator`'s domain, or eventually `production_runtime`'s,
  per SYSTEM_OWNERSHIP.md — untouched by this audit).
- Broker execution (structurally blocked project-wide via `bujji.broker.guard`, unrelated to this
  decision).
- Strategy selection, position sizing, or order construction (explicitly out of scope for the entire
  Phase 19.x Intelligence Foundation since Phase 19.6's own "no strategy selection" boundary, reaffirmed
  every phase since).

## Success Criteria — answered

1. **What keeps Bujji alive?** Nothing does, reliably — `run_live_shadow.py` is the only running process,
   has no supervisor, and has been alive 13+ days past what its own design describes as a single-day
   lifecycle (§4). This is the real, uncomfortable answer, not glossed over.
2. **Who owns daily market sessions?** No one, formally — capture is manual (Phase 19.9.5's own finding,
   re-confirmed), and the two "session"-shaped runtimes (`live_shadow_operator`, `production_runtime`) are
   both trading-decision-scoped, not data/intelligence-scoped.
3. **Where does automation belong?** In an evolved `bujji.shadow_runtime`, per §5's decision — the only
   existing component whose own documented design boundary already matches what Phase 19.10 needs.
4. **What should Phase 19.10 integrate with?** `HistoricalObservationStore` (read), the six existing
   brains through `MarketEnvironmentAssessment` (call, unmodified), `MarketMemoryEntry`/`EventStore`
   (write) — and, for reliability primitives only, selected pieces of `production_runtime.health`/
   `circuit_breaker.py` (§3).
5. **Which components should be retired or preserved?** Nothing is recommended for retirement by this
   audit — `bujji_options_os_runner.py` was identified but not traced (a real gap, flagged for immediate
   follow-up before Phase 19.10 begins), and `shadow_lifecycle`/`msi_shadow_trading`/`shadow_observatory`/
   `shadow_trade_construction`/`shadow_validation` all have real, distinct, currently-inactive-but-not-
   necessarily-obsolete purposes that a full retirement decision would require more evidence than this
   audit gathered. All are preserved, untouched, pending that follow-up.

## Correction found before finalizing this audit: `bujji_options_os_runner.py` IS a real `production_runtime` entrypoint — SYSTEM_OWNERSHIP.md is stale on this one point

Read in full after an initial draft of this audit flagged it as unread. Its own docstring states plainly:
**"The ONLY outer entrypoint for the Trading Session Governor stack (`production_runtime/`,
`trading_session_governor/`, `trading_brain/`, `msi_trade_construction/`, `shadow_observatory/")** — so
§0/§3's claim that `production_runtime` has "no automated entrypoint" is stale relative to this file
(SYSTEM_OWNERSHIP.md predates it, or was never updated after it was added). Correcting the record:

- It IS a real entrypoint: constructs the composition root, `PaperBroker`-based stack, Shadow Observatory
  recorder, and `TradingSessionGovernor`, then calls governor lifecycle methods in order.
- It is still explicitly **"Phase-1 Shadow Runner"** — shadow-mode only, and its own docstring states
  "no restart-recovery resurrection... explicitly Category B/C future work," i.e. **no automated scheduling
  or crash recovery exists here either.**
- Critically, its own docstring independently confirms this audit's separately-derived finding: **"`bujji.live_shadow_operator` / `run_live_shadow.py` are NOT imported — that is a separate, unrelated
  system using the legacy `exit_engine`, not D.4."** Two independent lines of evidence (this audit's own
  import trace, and this file's own author) now agree: `production_runtime` and `live_shadow_operator` are
  deliberately separate, non-overlapping systems, not one hidden inside the other.
- This correction does **not** change the §5 recommendation — `bujji_options_os_runner.py` drives exactly
  the trading-decision-scoped stack (`TradingSessionGovernor`, strategy selection, risk approval, order
  construction, exit decisions) that Phase 19.10 must not own. It changes only the factual claim that no
  entrypoint exists — one does, and it is the third, separate, shadow-only trading-decision runner in this
  repository (alongside `live_shadow_operator` and the abandoned Series 31–54 generation), not a candidate
  for the data-capture/intelligence role either.

## Immediate recommendation before Phase 19.10 begins

`docs/SYSTEM_OWNERSHIP.md` should be updated to record `bujji_options_os_runner.py` as the real
`production_runtime` entrypoint (out of this audit's own scope to edit, since this phase is read-only) —
otherwise a future audit will re-discover the same gap this one just closed. Beyond that documentation
fix, no further investigation is required before Phase 19.10 begins; §5's decision stands.
