# Phase 19.20 — Market Observation Upgrade: Design Document

**Status: DESIGN ONLY. Nothing in this phase has been built or deployed. No line of the Phase 19.19 commissioned Shadow OS has been touched.**

## 0. Mandate

> Current 5-minute observation is sufficient for intelligence research but insufficient for future live trading readiness. Do not modify the existing Shadow OS. Preserve the Phase 19.19 baseline. First priority is trustworthy market sensing, not strategy or execution.

This document proposes an architecture for two new, strictly additive capabilities:

1. **A high-frequency capture layer** — observes the market at a finer resolution than today's 5-minute EOD-oriented capture.
2. **A data integrity / reconciliation layer** — proves that what was captured is trustworthy: no silent gaps, no duplicate/corrupted ticks, and agreement with an independent authoritative source (NSE bhavcopy) at end of day.

Both are designed to sit **beside** the Phase 19.19 pipeline, not inside it.

---

## 1. Why 5-Minute Resolution Is Not Enough for Live Trading

**In trader language:** a 5-minute bar tells you where the market *ended up* over 5 minutes, not what it *did* inside that window. For research and for classifying the shape of a trading day, that's plenty. But if this system is ever going to inform real entries, exits, or risk decisions, it needs to see the market's actual texture: how fast price moved, whether a spike reverted in 20 seconds or held, how option premiums actually reacted tick-by-tick to a spot move, and whether the data feed itself ever silently dropped a moment of the day. A 5-minute bar cannot answer any of those questions after the fact — the information inside that window is already gone.

**In technical language:** the currently commissioned pipeline (`bujji.shadow_runtime.daily_session` → `run_live_intelligence_cycle` → `build_market_reality_snapshot(resolution=RESOLUTION_FIVE_MINUTE)`) captures and persists at 5-minute granularity only, once per day, end-of-day. This is correct and sufficient for Reality/Intelligence/Phenomena/State/Environment composition — the intelligence-research use case this system was built and commissioned for. It is insufficient for anything that would eventually need microstructure: slippage estimation, fill-quality modeling, intraday volatility-of-volatility, or premium-reaction latency to spot moves — all of which require sub-5-minute, ideally tick-level, data that today simply isn't captured or stored anywhere.

---

## 2. Audit: What Already Exists (Reuse, Don't Rebuild)

Before proposing anything new, the codebase was audited for pre-existing infrastructure. The finding: **most of what Phase 19.20 needs already exists**, built in earlier phases for the live-trading runtime, tested, and simply never wired into the Phase 19.x Shadow OS commissioning path.

| Capability needed | Existing component | State |
|---|---|---|
| Real-time tick ingestion from FYERS | `bujji.broker.fyers_ws.FyersTickFeed` | **Exists.** WebSocket feed with reconnect handling, `TickSilenceWatchdog`, subscription-state tracking, `tick_age_seconds()`, `connect_count()`. Already used by the live-trading runtime (`run_live_shadow.py`). Read-only market data — no order-placement surface. |
| Tick → candle aggregation at any interval | `bujji.market_timeseries.aggregator.CandleAggregator` | **Exists** (Phase 15Q). Configurable `interval`, not hardcoded to 5-minute. Produces closed `Candle`s and a live `FormingCandle`. |
| Persisted candle storage | `bujji.market_timeseries.store` | **Exists** (Phase 15Q), SQLite-backed. |
| A resolution vocabulary finer than 5-minute | `bujji.market_observation.taxonomy` | **Exists.** Already defines `RESOLUTION_TICK`, `RESOLUTION_ONE_MINUTE`, `RESOLUTION_FIVE_MINUTE`, `RESOLUTION_FIFTEEN_MINUTE`, `RESOLUTION_HOURLY`, `RESOLUTION_DAILY`, `RESOLUTION_WEEKLY`, `RESOLUTION_EVENT` — richer than the two resolutions (`RESOLUTION_DAILY`, `RESOLUTION_FIVE_MINUTE`) actually used by the Reality builder today. |
| A store that can hold finer-than-5-minute data without a schema change | `bujji.historical_reality.store.HistoricalObservationStore` | **Already resolution-agnostic.** `resolution` is a plain `TEXT` column inside the natural key — the schema places no constraint on which resolution strings are valid. Storing `RESOLUTION_ONE_MINUTE` or `RESOLUTION_TICK` rows requires no migration. |
| Gap detection / completeness / quality scoring, per resolution | `bujji.market_observation.engine` (Market Observation Contract, "MOF v1") | **Exists**, and is exactly the shape of a reconciliation primitive: pure functions, `SeriesGap` detection with per-resolution nominal-interval arithmetic (`_RESOLUTION_SECONDS`), `ValidationResult`, `ObservationQualityMetadata` (completeness/freshness/confidence). Currently unused by the Phase 19.x pipeline. |
| An authoritative, independent EOD source to reconcile against | `data/bhavcopy/*.csv` (NSE Bhav Copy, F&O segment) | **Already being fetched** by existing tooling (`tools/pre_market_supplementary_checks.py`, `futures_observation`, `options_observation` packages reference it). Never used as a reconciliation source against the Shadow OS's own captured data. |

**Conclusion:** Phase 19.20 is primarily an **integration and reconciliation** effort, not a from-scratch build. The riskiest new work is the reconciliation logic itself (comparing two independently captured views of the same day) and the daily orchestration that runs the high-frequency capture safely alongside — never inside — the commissioned Phase 19.19 pipeline.

---

## 3. Proposed Architecture

```
                     ┌─────────────────────────────────────────┐
                     │   EXISTING, UNTOUCHED (Phase 19.19)      │
                     │   bujji-daily-intelligence.timer          │
                     │   → DailySessionRuntime (09:00, once/day) │
                     │   → RESOLUTION_FIVE_MINUTE capture        │
                     │   → Reality → Intelligence → ... →         │
                     │     DailyIntelligenceArtifact              │
                     └─────────────────────────────────────────┘

                     ┌─────────────────────────────────────────┐
                     │   NEW, ADDITIVE (Phase 19.20)             │
                     │                                            │
                     │   bujji-high-freq-capture.timer/.service   │
                     │   (separate unit — market-hours window,    │
                     │    NOT the 09:00-once/day daily unit)      │
                     │        ↓                                   │
                     │   FyersTickFeed (reused, read-only)         │
                     │        ↓                                   │
                     │   CandleAggregator (reused, interval=       │
                     │   ONE_MINUTE, configurable)                 │
                     │        ↓                                   │
                     │   HistoricalObservationStore                │
                     │   (SAME store, ADDITIVE rows only —         │
                     │    resolution=RESOLUTION_ONE_MINUTE,        │
                     │    never touches existing                   │
                     │    RESOLUTION_FIVE_MINUTE rows)             │
                     │                                            │
                     │   bujji-eod-reconciliation.timer/.service   │
                     │   (fires once, AFTER market close)          │
                     │        ↓                                   │
                     │   Reconciliation Engine (new, reuses        │
                     │   market_observation.engine's gap/quality   │
                     │   primitives)                                │
                     │     - cross-checks 1-min series vs.          │
                     │       5-min series for the same day          │
                     │       (aggregated 1-min should reproduce     │
                     │       the 5-min bars within tolerance)       │
                     │     - cross-checks EOD close/settlement       │
                     │       vs. NSE bhavcopy (independent source)  │
                     │     - runs SeriesGap detection over the      │
                     │       1-min series (silent-drop detection)   │
                     │        ↓                                   │
                     │   ReconciliationReport (new, persisted,      │
                     │   append-only, never overwrites)              │
                     │        ↓                                   │
                     │   Extends bujji_campaign_status.py with a    │
                     │   read-only "data integrity" section          │
                     └─────────────────────────────────────────┘
```

**Why two separate systemd units, not one:**
- The existing `bujji-daily-intelligence.timer` is `Type=oneshot`, fires once at 09:00, and completes a single bounded intelligence cycle. It must not be turned into a long-running process — that was an explicit Phase 19.14.1 fix (a prior restart-loop defect).
- High-frequency capture is fundamentally different in shape: it needs to run continuously *during* market hours (09:15–15:30 IST), not once at open. That's a distinct lifecycle, and conflating it with the daily intelligence unit would reintroduce exactly the kind of restart-loop risk Phase 19.14.1 fixed.
- Reconciliation is a third distinct shape again: it must run once, *after* market close, once both the 5-minute EOD capture and the high-frequency intraday capture are known to be complete for the day.
- Keeping these three units separate preserves the Phase 19.19 unit completely untouched (satisfies "preserve the baseline" literally, not just in spirit) and keeps each unit's failure mode isolated and independently diagnosable.

---

## 4. Data Model Decisions (proposed, for approval)

1. **Same `HistoricalObservationStore` file, new resolution value.** Reuse `historical_observations.db`, write rows with `resolution=RESOLUTION_ONE_MINUTE` (imported from `bujji.market_observation.taxonomy`, additively — not `bujji.market_reality_snapshot`'s narrower `moc_taxonomy`). No schema migration needed (see Section 2). This keeps one canonical observation ledger rather than fragmenting historical data across multiple files — but is an open decision (Section 7) since a dedicated store would isolate blast radius further.
2. **1-minute, not tick, as the default capture granularity.** Tick-level storage is what `FyersTickFeed`/`CandleAggregator` can technically produce, but persisting every tick for the full session, every day, indefinitely, is a meaningfully larger storage and I/O commitment than 1-minute bars, and 1-minute is very likely sufficient to answer the microstructure questions in Section 1 (slippage, reaction latency) without the operational cost of raw tick retention. This is also an open decision (Section 7) — tick-level can be added later, additively, once 1-minute proves itself.
3. **Reconciliation reports are append-only, never overwrite.** Each day's `ReconciliationReport` is a new, immutable record — matching this project's standing "never silently overwrite, always disclose" discipline (the same pattern `campaign_continuity.py` and `DailyIntelligenceArtifact` already follow).
4. **Reconciliation classifies, never "fixes."** If the 1-minute aggregate disagrees with the 5-minute bar, or the captured close disagrees with bhavcopy, the reconciliation layer's job is to report the discrepancy honestly (with a severity classification) — never to silently correct, interpolate, or discard data. Matches the project's anti-fabrication discipline established since Phase 15D/17F.

---

## 5. What This Phase Explicitly Does NOT Do

- Does not modify `daily_session.py`, `run_daily_intelligence_session.py`, `completeness.py`, or any file inside the Phase 19.19 commissioned path.
- Does not change the existing `RESOLUTION_FIVE_MINUTE` default anywhere in the Reality/Intelligence/Completeness pipeline.
- Does not install, enable, or start any new systemd unit without the same explicit operator-approval discipline used for Phase 19.19.
- Does not add strategy, decision, or execution logic. `FyersTickFeed`/`CandleAggregator` reuse here is strictly for market-data ingestion — the same AST-level import boundary that keeps `historical_reality`/`market_reality_snapshot`/`shadow_runtime` free of broker/order/position/strategy imports applies here too.
- Does not touch the FYERS broker's execution surface — the new capture layer only ever needs a read-only tick subscription, never `place_order`/`modify_order`/`cancel_order` (already structurally disabled via `disable_live_execution` wherever this layer would construct a broker).
- Does not claim the resulting higher-frequency data makes Bujji "ready" for live trading. It only closes the *observation* gap identified in Section 1. Strategy, execution, and risk-management readiness (Section 9 of the Phase 19.19 current-state document) remain separate, deferred, unstarted work.

---

## 6. Proposed Phased Build Plan (for future approval, not started)

Matching this project's standing audit-first, narrow-scope, test-verified discipline:

- **19.20.1** — Confirm this design (this document) and resolve the open decisions in Section 7.
- **19.20.2** — Build the high-frequency capture layer in isolation: wire `FyersTickFeed` + `CandleAggregator` into a new, standalone capture script, writing `RESOLUTION_ONE_MINUTE` rows to `HistoricalObservationStore`. Unit-tested against synthetic ticks first, then real-data-verified against one live market session (read-only, no systemd install).
- **19.20.3** — Build the reconciliation engine: 1-min vs. 5-min cross-check, bhavcopy cross-check, gap detection (reusing `market_observation.engine`). Tested against both a clean day and deliberately corrupted/gapped fixtures to prove it catches real discrepancies, not just passes on clean data.
- **19.20.4** — Extend `bujji_campaign_status.py` with a read-only data-integrity section (additive, same pattern as the existing campaign box).
- **19.20.5** — Full regression (0 failures, matching every prior phase's standing requirement), real-production-data validation, phase doc.
- **19.20.6** — Only after 19.20.1–19.20.5 are individually approved and verified: propose the two new systemd units (capture + reconciliation) for manual operator installation — never auto-installed, matching the Phase 19.18/19.19 precedent exactly.

No work beyond this design document has been started.

---

## 7. Open Decisions Requiring Your Approval

1. **Capture granularity:** 1-minute bars (Section 4.2's recommendation) vs. raw tick storage vs. both. Tick storage answers more microstructure questions but costs materially more disk/IO for an as-yet-unused capability.
2. **Storage location:** append `RESOLUTION_ONE_MINUTE` rows into the existing `historical_observations.db` (single canonical ledger) vs. a dedicated new store file (stronger isolation from the Phase 19.19-critical existing data, at the cost of a second store to manage).
3. **Capture window:** full market hours (09:15–15:30 IST) vs. a narrower pilot window first (e.g. first hour only) to prove reliability before committing to the full session.
4. **Instruments in scope:** spot + futures + VIX only (matching today's Reality builder's required set) vs. also capturing the options chain at 1-minute granularity (materially higher API call volume against FYERS rate limits — needs explicit sizing before committing).
5. **Reconciliation failure severity model:** what should count as a hard `FAILED` reconciliation (a real defect worth investigating) vs. a soft `ADVISORY` note (e.g. sub-paise rounding differences against bhavcopy) — needs your judgment on acceptable tolerance bands before the reconciliation engine's thresholds can be built.

I have not started building anything against this design. Once you've weighed in on Section 7, I'll produce a revised, final design (or proceed directly to 19.20.2 if you'd rather approve the whole plan now) — your call.
