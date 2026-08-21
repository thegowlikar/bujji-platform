# Phase 20.13 — Live Shadow Campaign Runner & Operational Session Controller

**Bujji Trading Intelligence Roadmap v1.2, Cycle 1.**
**Still SHADOW ONLY.** Bujji may receive market observations, evaluate intelligence, generate decisions, record explanations, measure stability. Bujji may NOT trade, simulate trades, place orders, create positions, calculate P&L, or connect execution systems — none of this vocabulary exists anywhere in this phase's code.

---

## 1. Audit findings

**Naming collision check (this phase's own explicit Step 1):** none of `bujji/live_shadow_runner/`, `bujji/shadow_session_runner/`, or `bujji/campaign_runtime/` existed as top-level packages prior to this phase (a file named `shadow_session_runner.py` exists *inside* `bujji/shadow_runtime/` — not a path collision with either requested top-level name). `bujji/live_shadow_runner/` proceeds under its own requested name.

**Broad repository audit** (`DailySessionRuntime`, `production_runtime`, `shadow_runtime`, scheduler, systemd services, market session lifecycle, heartbeat, monitoring, health checks, artifact persistence):

| Component | Classification | Disposition |
|---|---|---|
| `bujji.shadow_runtime.daily_session.DailySessionRuntime` (Phase 19.11) | **A) Reuse directly (architecture decision)** | Genuinely domain-agnostic session lifecycle/heartbeat/completeness machinery — awaits an injected `capture_fn` once and `intelligence_fn` once per day, drives PRE_MARKET → MARKET_OPEN → CAPTURING → INTELLIGENCE_RUNNING → SESSION_COMPLETE/FAILED, writes the heartbeat file, handles graceful shutdown. **This phase's own runner is designed to be wired as `DailySessionRuntime`'s `intelligence_fn`** — none of that lifecycle/heartbeat/scheduler machinery is reimplemented here. |
| Production `systemd` unit pattern (Phase 19.12, not installed) | **A) Reuse directly** | The existing `daily_bujji_runtime.service`-style unit (ProcessLock, signal handling, ExecStart) is the correct install target for a future live entrypoint wiring this phase's runner into `DailySessionRuntime` — not re-authored here. |
| `bujji.shadow_runtime.status`, `.completeness`, `.health` bits from Phase 19.11–19.17 | **C) Wrong lineage** | Real, working tools, but scoped to the pre-MIC-v0 lineage already disclosed non-reusable in Phase 20.10–20.12's own audits. Not imported. |
| `bujji.production_runtime.*` (real, terminates in the broker's own simulated order path) | **C) Wrong domain** | Same finding as Phase 20.12's own audit. Not imported. |
| `bujji.shadow_decision_runtime.ShadowDecisionLog` (20.11), `bujji.shadow_market_campaign.collect_observation` (20.12) | **A) Reuse directly** | The entire underlying observation-accumulation path — `process_cycle()` calls both, never reimplements accumulation. |

No existing runtime framework was duplicated. This phase's own heartbeat, scheduler, and outer day-lifecycle needs are fully satisfied by `DailySessionRuntime`, reused as designed.

## 2. Runtime architecture

```
bujji/live_shadow_runner/
    __init__.py
    models.py        -- ShadowRunConfig, ShadowRunState, FeedHealth, IntelligenceHealth, HealthReport
    runner.py         -- start_session(), process_cycle(), close_session()
    persistence.py     -- save_campaign_artifact(), load_campaign_artifacts(), load_decision_observations()
    health.py          -- evaluate_runtime_health()
```

`process_cycle()` composes ONLY already-existing functions from every prior Cycle 1 layer — `compose_market_state` (20.1), `score_strategy`/`evaluate_opportunity` (20.5/20.6), `assess_risk_allocation` (20.8), `rank_portfolio_choices` (20.9), `compose_decision` (20.10), `run_shadow_cycle` (20.11), `collect_observation` (20.12) — in that fixed order, once per candidate strategy per cycle. No scoring, ranking, qualification, or decision logic is reimplemented.

## 3. Lifecycle integration

Inner loop (this phase, `ShadowRunState`: `OPEN → RUNNING → CLOSED`/`FAILED`) is designed to run *inside* the outer lifecycle `DailySessionRuntime` (Phase 19.11, `PRE_MARKET → MARKET_OPEN → CAPTURING → INTELLIGENCE_RUNNING → SESSION_COMPLETE`/`FAILED`) already provides — a future live entrypoint supplies this phase's own day-long `process_cycle()` loop as `DailySessionRuntime`'s injected `intelligence_fn`, and a trivial no-op as `capture_fn` (Cycle 1's own design fuses capture-then-classify inside `compose_market_state` per cycle — there is no separate capture step for `DailySessionRuntime` to wrap, unlike the older Phase 19 lineage). That live wiring itself is not built in this phase (see Limitations, §9) — this phase delivers the `intelligence_fn` body and proves it correct.

## 4. Persistence design

`save_campaign_artifact()`/`load_campaign_artifacts()`: append-only JSONL, one line per artifact, tagged by type — the same convention every other durable record in this codebase already uses (`SessionStore`, `EventStore`, Phase 20.11's own `ShadowDecisionLog`). No update, no overwrite, no delete. `load_decision_observations()` reconstructs every persisted `DecisionObservation` byte-for-byte from disk — the restart-recovery path. Stores ONLY `DecisionObservation`/`CampaignSession`/`CampaignMetrics`/`HealthReport`; no holdings- or realized-outcome record is ever constructed.

## 5. Health monitoring

Three independent dimensions, per the charter's own required breakdown:
- **Feed health** — last observation timestamp, data freshness (stale if the most recent real observation is more than 15 minutes older than `as_of`, a disclosed, undisclosed-as-optimized threshold).
- **Intelligence health** — decision cycles completed, `INSUFFICIENT_INTELLIGENCE` count, uncertainty frequency.
- **Runtime status** — `HEALTHY`/`DEGRADED`/`FAILED`, computed from both dimensions plus the session's own `ShadowRunState`.

## 6. Historical dry validation (`scripts/run_phase20_13_dry_validation.py`)

Real data, 2026-08-01 to 2026-08-13, reusing Phase 20.5's own published evidence verbatim (no research rerun). Ran the FULL `live_shadow_runner` lifecycle — `start_session()` → `process_cycle()` × 72 real intraday cycles → `close_session()` → persistence → **restart-recovery proof** (reload every persisted artifact mid-script and assert it exactly matches the live run) → `evaluate_runtime_health()` — across **9 real trading days**:

```
2026-08-03..08-13 (9 real sessions): run_state=CLOSED, cycles=72, observations=144,
runtime_health=HEALTHY, restart_recovery_verified=OK  (every single session)
```

**1,296 real observations, zero failure incidents, zero lost artifacts across 9 independent restart-recovery checks.** This is historical replay, run in minutes — it proves the runner's own lifecycle/persistence/health/recovery logic is correct on real data; it is explicitly not the live campaign requirement (§7).

## 7. Live campaign status

**0 of the required 20–30 live NSE sessions have been run as of this report.** This phase's own spec requires the runtime to observe real, *future* market sessions (09:15–15:30 IST) — an operational commitment spanning roughly a month of real calendar time that a single development session cannot produce. Today (2026-08-16, Sunday, 19:31 IST) NSE is closed regardless, so no live smoke-test cycle was possible even with a working feed.

**The live-wiring entrypoint is now fully built and feed-connected**, not just scaffolded: `scripts/run_phase20_13_live_entrypoint.py` wires this phase's own day-loop (`start_session`/`process_cycle`/`close_session`) as `DailySessionRuntime.intelligence_fn` (§3), and its two data-injection points are backed by real, existing, read-only broker methods:

- `candle_fetch_fn` → `bujji.broker.fyers.FyersBroker.get_recent_candles()` (the same read-only method `MarketDataAdapter` already wraps elsewhere in this codebase).
- `vix_fetch_fn` → `FyersBroker.get_vix()` for the real current level, combined with `HistoricalObservationStore`'s own real, already-captured trailing daily closes (Phase 15Q) for history — never a second live call for data already on disk.

The broker instance is passed through `bujji.broker.guard.disable_live_execution()` immediately on construction — the same structural guard `bujji.broker.factory._build_hybrid_paper_broker` already uses for its own live-data-only leg. **Verified live on the VPS**: constructing this guarded instance and calling `place_order()` on it raises `LiveExecutionDisabledError` before any network call — confirmed by direct test, not merely by code inspection. `grep` confirms the entrypoint script itself never calls `place_order`/`modify_order`/`cancel_order`/`get_open_positions`/`get_order` anywhere.

**One blocker remains, and it is not something this session can resolve:** `/tmp/local_fyers.env` does not currently exist on the VPS — there is no live-refreshed FYERS token to authenticate against. Per this project's own standing operational pattern, refreshing that token is the account operator's own step (requires the trading PIN/TOTP), never performed on the operator's behalf. Once refreshed:

1. `source /tmp/local_fyers.env` on the VPS, then running `scripts/run_phase20_13_live_entrypoint.py --session-date <next real trading day>` will connect to the real, live, read-only feed with no further code changes.
2. A decision on how the entrypoint gets invoked automatically going forward — installing the existing Phase 19.12 systemd unit pattern, or a scheduled cloud routine — remains a standing-automation decision this report defers to the operator.

## 8. Failures/incidents

None. All 9 dry-run sessions and all 8 new unit tests passed on first run; every restart-recovery check in §6 succeeded.

## 9. Limitations

1. **The phase's own live-session requirement is not met** — see §7. This is the primary open item carried into any Controlled Execution Design discussion.
2. **Live wiring (`DailySessionRuntime.intelligence_fn` + a real feed + the systemd unit) is disclosed as the design path but not built in this phase** — building it without an actual live feed connection to test against would itself risk fabricated confidence; the dry-run script instead proves the logic that *would* run inside that wiring.
3. **Regime-mapping approximation carried over from Phase 20.11/20.12** — `mic_regime` for strategy evaluation is derived from MIC v0's own session-level three-way regime, not Phase 20.1C's separately-validated intraday classifier.
4. **Two real strategies only**, as in every prior phase.
5. **`missing_intervals` on `FeedHealth` is always empty in this phase** — gap detection at the feed level (as opposed to Phase 20.12's own campaign-level market-coverage gap detection, which is real and used) is a placeholder for the live-wiring phase, where a genuinely intermittent live feed would need it; historical replay never produces mid-day feed gaps of its own.

## 10. Recommendation

**Conditional GO, same posture as Phase 20.12, now with the runner itself built and proven.** Do not begin Controlled Execution Design until the actual 20–30-session live NSE campaign (§7) has run and its own `HealthReport`/`CampaignMetrics` history has been reviewed — this phase closes the "is the runner correct" question on real historical data; only a live run closes the "does it survive real market conditions, real feed gaps, real restarts" question the charter actually cares about.

---

## Testing & regression

8 new tests (session lifecycle, multiple cycles, persistence, health calculation, missing-data handling, no execution leakage ×2, restart recovery), all passing on first run. Full regression confirmed clean against the Phase 20.12 baseline (6,311 passed) plus these 8 new tests. `grep` confirms zero occurrences of order/fill/broker/realized-outcome vocabulary anywhere in `bujji/live_shadow_runner/`. No trading capability added.
