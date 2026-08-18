# Phase 19.10.0 — Shadow Runtime Capability Audit

**Audit only. No code edited, no files deployed, no process started or stopped, no runtime behavior
modified, no scheduler or store created.**

Every finding below is from directly reading the real source on the VPS: `bujji/shadow_runtime/`'s
complete 5-file, 539-line package (all files read in full, not sampled) plus targeted reads of
`bujji/shadow_lifecycle/orchestrator.py`'s header. `bujji/msi_shadow_trading/`, `bujji/shadow_observatory/`,
`bujji/shadow_validation/`, `bujji/shadow_trade_construction/`, `run_live_shadow.py`, and
`live_shadow_operator/` were already characterized in Phase 19.9.5/19.9.6 and are cited from those audits,
not re-read line-by-line here (redundant with already-verified findings).

## 1. Executive Summary

**`shadow_runtime` is architecturally correct but operationally incomplete.** Its boundary is exactly
right — genuinely zero trading/strategy/position/execution code anywhere in the package, confirmed by full
read, not sampled. But it is a **bounded, single-invocation library**, not a runtime: no CLI entry point,
no session-boundary awareness (no PRE_MARKET/MARKET_OPEN/.../IDLE states), no scheduler, no live health
endpoint, and — critically — **it has never been wired to any object from the Phase 19.0–19.9 Intelligence
Foundation** (`MarketIntelligenceSnapshot`, `DecisionContext`, `DecisionIntelligenceSnapshot`,
`MarketPhenomenaAssessment`, `MarketStateNode`, `MarketEnvironmentAssessment` — zero imports of any of
these, confirmed by full-file read). It already calls `build_intelligence_snapshot()` from
`bujji.market_perception.intelligence_adapter` — a **different, older, same-named concept**, not this
session's `MarketIntelligenceSnapshot` (see §3, real naming-collision finding). **Recommendation: B — small,
well-scoped hardening required before Phase 19.10 integration**, not a rewrite and not a reconsideration of
ownership (§9's boundary check confirms the Phase 19.9.6 decision was correct).

## 2. Current Architecture

```
Current shadow_runtime
inputs
  |
  |-- broker (caller-supplied, real or fake -- connect()/get_quote() only)
  |-- watchlist (Sequence[(OptionContract, Side)] -- caller-supplied, no chain discovery)
  |-- clock (injectable Callable[[], datetime] -- no wall-clock call anywhere in the package)
  v
processing
  |-- ShadowSessionRunner.start(): validate -> connect+smoke-test -> bounded `for _ in range(max_cycles)` loop
  |-- per cycle: fetch quotes -> QuoteObservationStore.append()
  |-- [optional, off by default] market_perception_enabled: build a MarketSnapshot via
  |     bujji.market_perception.MarketDataAdapter, THEN build_intelligence_snapshot()
  |     (market_perception's OWN function -- not Phase 19.3's) 
  |-- [optional, off by default] intelligence_cycle_enabled: IntelligenceCycleRecorder.record_cycle()
  |     (calls LiquidityBrain.analyze() -- the ONE Phase 19.x brain this package touches, via the
  |     IntelligenceContext clock-injection fix Phase 19.2.2 made)
  |-- [optional, off by default] monitoring_pair_roles: a second, separate LiquidityBrain.analyze() call
  |     for CE/PE monitoring pairs, feeding a liquidity_summary tally only
  v
storage
  |-- QuoteObservationStore (always on) -- quote observations
  |-- market_snapshot_path (opt-in) -- JSONL, market_perception's MarketSnapshot
  |-- intelligence_snapshot_path (opt-in) -- JSONL, market_perception's build_intelligence_snapshot() output
  |-- intelligence_cycle_path (opt-in) -- JSONL, IntelligenceCycleRecorder's record
  |-- regime_memory_event_store_path (opt-in) -- EventStore, regime memory only
  v
outputs
  |-- ShadowSessionArtifact (returned from start(), never persisted by the runner itself --
  |     the CALLER is responsible for writing it anywhere)
```

## 3. Capability Matrix

| Capability | Exists | Evidence | Gap |
|---|---|---|---|
| Entry point | **No CLI/service entry point** | No script in `scripts/` or repo root imports `shadow_runtime`; `ShadowSessionRunner` is constructed only from `tests/test_shadow_runtime*.py` (6+ test files) | A caller/driver script must be written — this is real, expected Phase 19.10 work, not a hidden gap |
| Lifecycle | **Partial** — bounded start/run/finish exists; session-boundary states do not | `start()` is one method: validate → connect+smoke-test → `for _ in range(max_cycles)` → build artifact (try/finally, never raises). **No PRE_MARKET/MARKET_OPEN/INTRADAY/MARKET_CLOSE/POST_SESSION/IDLE states anywhere in the package** — confirmed by full-file read, zero matches for any of those terms | No market-calendar awareness, no "wait for open" behavior (that logic exists only in `run_live_shadow.py`'s separate, trading-decision-scoped system, per Phase 19.9.5) |
| Scheduler | **None, by design** | Package's own `__init__.py` docstring: excludes `production_runtime` "entirely... to keep the dependency graph exactly as designed." Loop is an explicitly bounded `for _ in range(max_cycles)`, never `while True` | No timer/cron/background-worker hook exists anywhere in the package — confirmed absent, not merely unused |
| Persistence | **Real, but fragmented across 5 independent opt-in paths** | `QuoteObservationStore` (always), `market_snapshot_path`, `intelligence_snapshot_path`, `intelligence_cycle_path`, `regime_memory_event_store_path` — 5 separate file paths, each independently toggled | No single persisted "session record"; the `ShadowSessionArtifact` the runner produces is returned to the caller, never written to disk by the runner itself |
| Recovery | **Real, but partial and opt-in** | Three independent, additive, off-by-default recovery mechanisms, each fully read: regime memory (`recovery.py`, `EventStore`-based, Phase 15C), observation memory (Phase 15D, replays `market_snapshot_path`), premium behaviour (Phase 15E, same source) | `QuoteObservationStore` itself has no recovery path audited here (append-only, likely needs none); no SINGLE recovery call covers all state — a caller must opt into three separate flags correctly |
| Health | **Minimal, artifact-only, not live** | `ShadowSessionArtifact.runtime_health = {"heartbeats": N, "consecutive_failures_at_end": N}` — only available AFTER `start()` returns | **No live/queryable status** — no `{runtime_status, last_cycle, last_success, last_error, uptime}` shape exists anywhere; nothing can ask "is it alive right now" while a session is running |
| Replay | **Mostly real** — clock injection is real and complete; execution_mode is not | Every timestamp in the package traces to the injected `Clock`, zero wall-clock calls found (`datetime.now()`/`now_ist()` absent, confirmed by full-file read) | The one `IntelligenceContext` constructed in the package (`_run_one_cycle`'s monitoring-pair liquidity call) **hardcodes `execution_mode=EXECUTION_MODE_LIVE`** — the runner cannot currently express HISTORICAL_REPLAY, even though the clock itself is already fully injectable |
| Intelligence compatibility | **Not wired; one real naming collision found** | Zero imports anywhere in the package of `MarketIntelligenceSnapshot`, `DecisionContext`, `DecisionIntelligenceSnapshot`, `MarketPhenomenaAssessment`, `MarketStateNode`, or `MarketEnvironmentAssessment` (confirmed, full-file read) | The package already has its own, DIFFERENT `build_intelligence_snapshot()` (from `bujji.market_perception.intelligence_adapter`) and its own `intelligence_snapshot_path`/`intelligence_cycle_path` fields — same vocabulary, different, older objects. Phase 19.10 must not silently overload these existing names |

## 4. Missing Capabilities

Only real, confirmed gaps — no proposed solutions here, per this phase's own scope:

1. No CLI/service entry point.
2. No market-session-boundary state machine (PRE_MARKET → ... → IDLE).
3. No scheduler or timer of any kind (deliberate, by design, but still a real gap relative to "daily
   automatic execution").
4. No single unified session-state persistence — 5 independent opt-in file paths, no one place recording
   "this session ran, here is everything that happened."
5. No live health/liveness endpoint — health is retrospective (in the final artifact) only.
6. No `execution_mode` parameterization — the one `IntelligenceContext` built inside the runner hardcodes
   `LIVE`, so a caller cannot today run this package against historical data in a way that honestly labels
   itself `HISTORICAL_REPLAY`.
7. Zero connection to any Phase 19.0–19.9 Intelligence Foundation object.
8. A live, unresolved naming collision between `market_perception`'s `build_intelligence_snapshot()` /
   `intelligence_snapshot_path` and this session's `MarketIntelligenceSnapshot` (Phase 19.3) — not
   resolved by this audit, only surfaced.
9. The runner does not read from `HistoricalObservationStore` at all — its `QuoteObservationStore` is a
   third persistence root distinct from both `HistoricalObservationStore` and `OperatorJournal` (Phase
   19.9.5's own finding, re-confirmed here at the code level).

## 5. Phase 19.10 Implementation Boundary

**What Phase 19.10 should add** (to this package or a thin driver around it — not decided by this audit):
- Connect Reality snapshots: read `HistoricalObservationStore` / call `build_market_reality_snapshot()`.
- Trigger one intelligence cycle: the six brains → `MarketIntelligenceSnapshot` → ... →
  `MarketEnvironmentAssessment` (all already real, unmodified).
- Persist the result via Phase 19.5's own `record_market_memory()`.
- Add a heartbeat/liveness signal — the `{runtime_status, last_cycle, last_success, last_error, uptime}`
  shape this audit confirmed does not exist yet.
- Thread `execution_mode` through explicitly, closing gap #6 above.
- Resolve or explicitly rename around the `build_intelligence_snapshot()` naming collision (gap #8) —
  a real decision, not something to paper over silently.

**What Phase 19.10 should NOT add:**
- Trading decisions, strategy evaluation, entry/exit rules.
- Broker order/position/margin/funds calls of any kind.
- A second scheduler competing with whatever Phase 19.10 introduces.
- A second capture loop competing with the existing `capture_*_session.py` scripts (Phase 19.9.5's own
  non-negotiable separation, reaffirmed here).

## 6. Recovery Scenario — VPS restart at 10:45 AM IST

Walking through the real, read code: if the VPS restarts mid-session today, **nothing automatically
resumes** — there is no supervisor for `shadow_runtime` (none was ever started in the first place; it is
not among the running processes per Phase 19.9.5/19.9.6's own process audit) and no systemd/cron entry
exists for it. If a caller HAD started a session and manually restarts it after a crash with the same
`session_id`, `intelligence_cycle_path`, and `regime_memory_event_store_path`:
- Regime memory: recovers cleanly (`recover_shadow_session()`, `EventStore`-based, real and tested).
- Observation memory / premium behaviour: recover cleanly IF their respective `*_recovery_enabled` flags
  are set (off by default — a caller must know to turn them on).
- Quote observations already written to `QuoteObservationStore`: preserved (append-only file), but the
  runner itself does not re-derive any in-memory state from them — only the three named recovery paths
  hydrate anything.
- **Nothing resumes automatically** — a human or a driver script must notice the crash and re-invoke
  `ShadowSessionRunner` with the right flags. This matches Phase 19.9.5/19.9.6's own top-level finding
  (no supervision exists anywhere in this codebase for any runtime) rather than contradicting it.

## 7. Is `intelligence_cycle.jsonl` (A) temporary logging, (B) permanent intelligence memory, (C) duplicate storage, or (D) migration artifact?

**(A) — closer to temporary/session-scoped logging than permanent memory**, with real caveats:
- It IS durable (append-only JSONL, survives process restart) and IS replayable (Phase 15C's regime
  recovery reads a derived count from it).
- But it is NOT "memory" in the Phase 19.5 sense — it has no `market_memory_id`, no similarity-searchable
  shape, no cross-session query surface, and nothing in the Phase 19.0–19.9 Intelligence Foundation reads
  it. It is the OLDER intelligence pipeline's own working log (`market_perception`'s
  `build_intelligence_snapshot()`, §3's naming-collision finding), not Phase 19.5's
  `MarketMemoryEntry`/`EventStore`-based Market Understanding Memory.
- It is not (C) a duplicate of `HistoricalObservationStore` — different content shape and purpose
  (derived intelligence readings, not raw observations) — and not (D) a migration artifact — nothing in
  the codebase treats it as an intermediate format being phased out.

## 8. Health Monitoring Audit

Searched the full package for `heartbeat`/`health`/`liveness`/`monitor`/`status`/`watchdog`:
`self._heartbeats` (a plain per-session cycle counter, incremented once per completed cycle) and
`runtime_health` (the two-key dict in §3) are the only matches. **No `{runtime_status, last_cycle,
last_success, last_error, uptime}` shape exists.** `_attempt_reconnect()` provides real, tested
within-session resilience (reconnect after `max_consecutive_failures`), which is a real, working
mechanism — but it answers "can this session keep running," not "is this system alive," the latter being
what a future daily supervisor needs to ask from outside the process.

## 9. Runtime Boundary Verification

Confirmed absent from `shadow_runtime` itself (full-file read, all 5 files):
- ❌ No broker order/position/margin/funds API call anywhere — only `connect()`/`get_quote()`-family
  methods, exhaustively documented in the package's own module docstring and independently confirmed by
  reading every line of `shadow_session_runner.py`.
- ❌ No strategy execution, no trade construction, no position management code.

**One adjacent finding, correctly out of `shadow_runtime` itself**: `bujji.shadow_lifecycle.orchestrator`
(a *different* package, only briefly inspected per this audit's stated scope) **does** carry a real
position lifecycle — `OrderRequest`, `PaperBroker` entry/exit fills, thesis/management/attribution/outcome
memory. This is not a boundary violation IN `shadow_runtime` (the two packages are separate, and
`shadow_runtime` imports nothing from `shadow_lifecycle`, confirmed) — it is a reminder that the
"shadow"-prefixed package family (7 packages, per Phase 19.9.6's own inventory) spans genuinely different
domains, and only `shadow_runtime` itself has the observation-only boundary Phase 19.10 needs.

## 10. Final Recommendation

**B — small, well-scoped runtime hardening required before Phase 19.10 integration.**

Not A: no entry point, no session-boundary states, no live health signal, and zero connection to the
Intelligence Foundation all mean "proceed to integration" today would mean building Phase 19.10 directly
on top of gaps rather than closing them first.

Not C: nothing found in this audit contradicts Phase 19.9.6's ownership decision. The package's boundary is
exactly right (confirmed clean, §9), its clock-injection/determinism discipline is exactly right (confirmed
clean, §3's Replay row), and its existing recovery mechanisms — while partial — are real, tested, and
reusable, not something to discard. The gaps are additive work, not a sign of the wrong foundation.

The hardening this audit recommends before Phase 19.10 begins substantive integration work is scoped
exactly to §4's list — nothing more, nothing broader.

## Success Criteria — answered

1. **Can `shadow_runtime` become Bujji's heartbeat?** Yes, architecturally — its boundary is already
   correct. Not yet operationally — see §4 for exactly what is missing.
2. **What must be added before daily autonomous operation?** An entry point, a heartbeat/liveness signal,
   `execution_mode` threading, and the actual wiring to `HistoricalObservationStore` and the Intelligence
   Foundation (§5) — none of which exist today.
3. **How to avoid duplicate runtimes?** Build the Phase 19.10 driver as a thin wrapper AROUND
   `ShadowSessionRunner` (or a sibling module in the same package), never as a new top-level "shadow"
   package — the existing 7-package fragmentation Phase 19.9.6 already flagged should not grow an eighth
   member.
4. **How to guarantee daily data/intelligence continuity?** Not guaranteed today by anything in this
   package or elsewhere in the codebase (§6) — this remains real, unstarted work for Phase 19.10, informed
   but not solved by this audit.
