# Phase 19.20 — Market Observation Upgrade: Final Implementation Plan

**Status: PLAN ONLY. No code written. Phase 19.19 untouched.**

This plan incorporates your approved decisions (1-minute primary granularity + per-minute microstructure metadata, no raw tick storage, dedicated isolated store, full-session continuous capture, ATM±5 CE/PE scope, classify-never-repair reconciliation) and is grounded in a second, deeper audit of the exact reusable components below.

---

## 1. Deeper Audit Findings (beyond the Phase 19.20 design doc)

- **`bujji.market_timeseries.aggregator.CandleAggregator`** already supports `interval="ONE_MINUTE"` — it's in `_INTERVAL_SECONDS` today (`{"ONE_MINUTE": 60, "FIVE_MINUTE": 300}`), just never used with that value in production. `ingest(instrument, timestamp, price, kind=..., volume=..., open_interest=...)` returns a closed `Candle` when a window rolls over, `None` otherwise. A window with **zero ticks closes silently and produces nothing** — gaps are preserved as gaps, never fabricated. This "no synthetic bar" discipline is exactly what the integrity layer needs to be able to trust.
- **`bujji.live_observation.models.AggregationWindow`** holds the raw `window.ticks: List[Tick(timestamp, price, volume)]` for the window before it closes — this is the exact source needed to compute the requested per-minute microstructure fields (tick count, max tick silence gap, average tick interval, largest price movement) without storing every tick permanently. They're computed once at window-close time, then the raw tick list is discarded — satisfying "no raw tick storage" while still deriving real texture metadata from real ticks.
- **`bujji.broker.fyers_ws.FyersTickFeed`** is read-only market data (`latest()`, `tick_age_seconds()`, `subscribe()`, `on_connect`/`on_disconnect` hooks) — no execution surface exists on this class at all (not even something to strip via `disable_live_execution`; it structurally has no order methods).
- **`bujji.broker.base.Broker.resolve_atm_contract()`** and **`get_option_chain()`** are the existing, already-used methods for identifying ATM ± N strikes — reused, not reimplemented, for selecting the CE/PE symbols to subscribe to.
- **`bujji.market_observation.engine`** (Market Observation Contract / "MOF v1") has real, tested gap-detection arithmetic (`_RESOLUTION_SECONDS`, `SeriesGap`), `ValidationResult`, and `ObservationQualityMetadata` (completeness/freshness/confidence) — the reconciliation engine's intraday-validation half (missing-minute detection, stale-data detection) is a direct reuse of this, not a reimplementation.
- **`tests/test_market_observation_contract.py`** already has the exact AST-based import-boundary test pattern this project uses everywhere (`ast.parse` + `ast.walk`, checking `Import`/`ImportFrom` nodes against a forbidden-module list: `bujji.mic_replay`, `bujji.production_runtime`, `bujji.trading_brain`). Phase 19.20's own safety test will follow this identical pattern.
- **`bujji.core.process_lock.ProcessLock`** (Phase 19.12) is reused as-is for the new services' single-instance guarantee — no new locking primitive needed.

**Conclusion holds from the design doc: this phase is overwhelmingly integration of existing, tested primitives, plus a genuinely new reconciliation engine and two genuinely new persistence schemas.**

---

## 2. Files/Modules To Create (all new, none touch Phase 19.19 files)

### 19.20.2 — High-frequency capture

| File | Purpose |
|---|---|
| `bujji/market_microstructure/__init__.py` | New package |
| `bujji/market_microstructure/models.py` | `MinuteObservation` dataclass: instrument, kind, window_start/end, OHLC, tick_count, max_tick_silence_seconds, avg_tick_interval_seconds, max_price_move, max_premium_move (options only), open_interest |
| `bujji/market_microstructure/microstructure_aggregator.py` | Wraps `CandleAggregator` (interval=`ONE_MINUTE`) + reads `window.ticks` at close-time to derive the extra metadata fields before discarding the raw ticks. Never reimplements window mechanics — pure derivation on top of the existing close event. |
| `bujji/market_microstructure/capture_session.py` | Owns one `FyersTickFeed` instance, subscribes to the approved instrument set (spot, futures, VIX, ATM±5 CE/PE resolved via `resolve_atm_contract`/`get_option_chain` at session start), feeds ticks into the aggregator, hands closed `MinuteObservation`s to the store. Contains the 09:15–15:30 IST session-window logic (start/stop boundaries) and reconnect/backoff delegation to the existing `FyersTickFeed`/`TickSilenceWatchdog`. |
| `scripts/run_high_frequency_capture.py` | Entrypoint script — constructs `capture_session`, acquires its own `ProcessLock` (separate lock file from Phase 19.19's), handles SIGTERM/SIGINT gracefully (same pattern as `run_daily_intelligence_session.py`), never imported by anything in the Phase 19.19 path. |

### 19.20.3 — Microstructure storage

| File | Purpose |
|---|---|
| `bujji/market_microstructure/store.py` | `MicrostructureStore` — dedicated, isolated SQLite store, own file (`data/market_microstructure/microstructure_observations.db`), own WAL mode, own natural-key idempotent `write()`/`write_many()` (same pattern as `HistoricalObservationStore`, deliberately not the same class — separate blast radius). Never opens or touches `historical_observations.db`. |

### 19.20.4 — Integrity / reconciliation engine

| File | Purpose |
|---|---|
| `bujji/market_data_integrity/__init__.py` | New package |
| `bujji/market_data_integrity/models.py` | `IntegrityIssue` (severity: `ADVISORY`/`WARNING`/`FAILED`, category, description, evidence), `DailyIntegrityReport` (capture_completeness, missing_intervals, duplicate_records, feed_interruptions, eod_reconciliation, quality_score, status: `GREEN`/`WARNING`/`FAILED`) |
| `bujji/market_data_integrity/intraday_checks.py` | Missing-minute detection (reuses `market_observation.engine`'s gap arithmetic against `RESOLUTION_ONE_MINUTE`), duplicate detection (natural-key collision scan), timestamp validation, stale-data detection (reuses `tick_age_seconds`-style staleness math), feed-interruption detection (reads `FyersTickFeed`/`TickSilenceWatchdog` counters recorded during the session) |
| `bujji/market_data_integrity/eod_reconciliation.py` | Compares `MicrostructureStore`'s day against (a) `HistoricalObservationStore`'s existing Phase 19.19 5-minute rows for the same day — read-only comparison, and (b) `data/bhavcopy/*.csv` for that date. OHLC/close consistency checks, missing-data checks. **Never writes back to either source store.** |
| `bujji/market_data_integrity/report_store.py` | Append-only persistence for `DailyIntegrityReport` (`EventStore`-based, same pattern as `cycle_artifact.py`/`daily_intelligence_artifact.py` — never overwrites a prior day's report) |
| `scripts/run_eod_reconciliation.py` | Entrypoint — runs once after market close, reads both stores + bhavcopy, produces and persists one `DailyIntegrityReport`, never mutates source data. |

### 19.20.5 — Monitoring extension

| File | Purpose |
|---|---|
| `bujji_campaign_status.py` (existing file — **additive edit only**, does not touch Phase 19.19's own fields) | Add a new, clearly-labeled "Market Data Integrity" section to the ASCII box, reading the latest `DailyIntegrityReport`. Additive: existing fields/behavior for the Phase 19.19 campaign status are unchanged. |

### Deploy artifacts (not installed until 19.20.6 approval)

| File | Purpose |
|---|---|
| `deploy/bujji-high-frequency-capture.service` | `Type=simple` (long-running, market-hours process — NOT oneshot), `User=bujji`, restart policy tuned for "crash → retry with backoff," explicit `ReadWritePaths` scoped to `data/market_microstructure/` and its own lock/log files only |
| `deploy/bujji-high-frequency-capture.timer` | Fires shortly before 09:15 IST, weekdays, `OnCalendar` — the *service* itself exits at 15:30 (session-window logic in `capture_session.py`), the timer's job is only to start it each morning |
| `deploy/bujji-eod-reconciliation.service` | `Type=oneshot`, mirrors the Phase 19.19 service's shape exactly (no `[Install]`, no `Restart=`) |
| `deploy/bujji-eod-reconciliation.timer` | Fires once, after 15:30 IST + a buffer for late data settlement (e.g. 16:00 IST) |

---

## 3. Existing Modules Reused (no modification to any of these)

| Module | Used for |
|---|---|
| `bujji.broker.fyers_ws.FyersTickFeed`, `TickSilenceWatchdog` | Tick ingestion, reconnect, silence detection |
| `bujji.broker.base.Broker.resolve_atm_contract`, `.get_option_chain` | ATM±5 CE/PE symbol resolution |
| `bujji.broker.guard.disable_live_execution` | Applied to any broker instance this layer constructs, identical to Phase 19.19's usage — belt-and-braces even though `FyersTickFeed` itself has no execution surface |
| `bujji.market_timeseries.aggregator.CandleAggregator`, `window_bounds` | Tick→1-minute window mechanics (interval=`ONE_MINUTE`, already supported) |
| `bujji.live_observation.models.AggregationWindow`, `Tick` | Raw per-window tick access for microstructure metadata derivation |
| `bujji.market_observation.engine`, `.taxonomy` | Gap detection arithmetic, resolution vocabulary, quality-metadata shape |
| `bujji.core.process_lock.ProcessLock` | Single-instance guarantee for both new services |
| `bujji.market_calendar.MarketCalendar` | Trading-day gate for both new services (reused exactly as Phase 19.16 wired it into the daily entrypoint) |
| `bujji.core.event_store.EventStore` (or equivalent used by `cycle_artifact.py`) | Append-only `DailyIntegrityReport` persistence pattern |

---

## 4. Database Schema Proposal

### 4.1 `data/market_microstructure/microstructure_observations.db` (new, isolated)

```sql
CREATE TABLE IF NOT EXISTS minute_observations (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    instrument          TEXT NOT NULL,
    kind                TEXT NOT NULL,        -- SPOT | FUTURES | VIX | OPTION_CE | OPTION_PE
    session_date        TEXT NOT NULL,        -- YYYY-MM-DD
    window_start         TEXT NOT NULL,        -- ISO 8601
    window_end           TEXT NOT NULL,
    open                REAL NOT NULL,
    high                REAL NOT NULL,
    low                 REAL NOT NULL,
    close               REAL NOT NULL,
    tick_count          INTEGER NOT NULL,
    max_tick_silence_seconds  REAL,            -- largest gap between consecutive ticks in this minute
    avg_tick_interval_seconds REAL,
    max_price_move      REAL,                  -- largest single-tick-to-tick move within the minute
    max_premium_move    REAL,                  -- options only; NULL for spot/futures/VIX
    open_interest       REAL,                  -- options only
    strike              REAL,                  -- options only
    option_type         TEXT,                  -- CE | PE | NULL
    source               TEXT NOT NULL,         -- e.g. "fyers_ws"
    natural_key         TEXT NOT NULL UNIQUE    -- (instrument, window_start) — idempotent writes, same discipline as HistoricalObservationStore
);
CREATE INDEX IF NOT EXISTS idx_minute_obs_natural_key ON minute_observations (natural_key);
CREATE INDEX IF NOT EXISTS idx_minute_obs_session_date ON minute_observations (session_date, instrument);

CREATE TABLE IF NOT EXISTS capture_session_log (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    session_date        TEXT NOT NULL,
    started_at          TEXT NOT NULL,
    stopped_at          TEXT,
    connect_count       INTEGER,
    disconnect_events   INTEGER,
    last_error          TEXT
);
```

Deliberately separate from, and structurally incapable of colliding with, `historical_observations.db` — a different file, different directory (`data/market_microstructure/` vs `data/historical_reality/normalized/`), different Python class (`MicrostructureStore`, not `HistoricalObservationStore`).

### 4.2 Integrity report persistence

Reuses the existing `EventStore` append-only-JSONL pattern (as `cycle_artifact.py`/`daily_intelligence_artifact.py` already do) — `data/market_integrity_reports.jsonl`, one event per day, keyed by `session_date`, never overwritten, exactly mirroring the "never silently fix, always disclose" convention already established for `DailyIntelligenceArtifact`.

---

## 5. Service Architecture (as approved, unchanged from your spec)

```
EXISTING — UNTOUCHED
bujji-daily-intelligence.timer → bujji-daily-intelligence.service → DailySessionRuntime → Phase 19.19 pipeline

NEW
bujji-high-frequency-capture.timer (fires ~09:10 IST, weekdays)
  → bujji-high-frequency-capture.service (Type=simple, runs 09:15–15:30, exits itself)
    → FyersTickFeed → MicrostructureAggregator (interval=ONE_MINUTE) → MicrostructureStore

bujji-eod-reconciliation.timer (fires ~16:00 IST, weekdays, after settlement buffer)
  → bujji-eod-reconciliation.service (Type=oneshot)
    → IntegrityEngine (intraday_checks + eod_reconciliation)
    → DailyIntegrityReport (append-only)
```

Three independent units, three independent lock files, three independent failure domains. None share code paths with `run_daily_intelligence_session.py`.

---

## 6. Testing Strategy

Matching this project's standing convention (unit tests with synthetic fixtures first, then real-production-data validation, then full regression):

**19.20.2 (capture, isolated):**
- `MicrostructureAggregator` unit tests using synthetic tick sequences (reuses the exact `Tick`/`AggregationWindow` fixtures style already used in `tests/` for `CandleAggregator`): correct OHLC, correct tick_count, correct max-silence-gap and avg-interval arithmetic against hand-computed expected values, correct handling of a zero-tick minute (must produce nothing, never a fabricated bar — this is the single most important property to test, matching the aggregator's own existing discipline).
- ATM±5 strike resolution tested against a synthetic option chain fixture (reusing the existing `resolve_atm_contract`/`get_option_chain` test fixtures already in the test suite for those methods).
- AST-level import-boundary test for `bujji.market_microstructure` — forbidden: `bujji.trading_brain`, `bujji.production_runtime`, `bujji.mic_replay`, plus (new, specific to this package) any `place_order`/`modify_order`/`cancel_order` reference at all — mirroring `test_market_observation_contract.py`'s exact pattern.

**19.20.3 (storage):**
- Idempotent-write tests (duplicate natural key → no-op, matching `HistoricalObservationStore`'s own `ConflictingHistoricalObservationError`-style behavior).
- Restart-recovery test: write N rows, simulate process restart (new `MicrostructureStore` instance against the same file), confirm all N rows still present and a fresh write continues correctly (same shape as the Phase 15B/15C crash-recovery test suite).
- WAL/integrity-check verification (same `PRAGMA journal_mode`/`PRAGMA integrity_check` proof pattern used in every prior phase's filesystem verification).

**19.20.4 (integrity engine) — explicitly required scenarios:**
- Clean day: full, complete, non-duplicated minute series → `GREEN`.
- Missing data: deliberately omit several minutes from a fixture → `WARNING`/`FAILED` (by count/severity threshold) with the exact missing intervals named, not just a count.
- Duplicate data: deliberately insert a colliding natural key → detected and reported, never silently deduplicated away without disclosure.
- Corrupted timestamps: out-of-order, non-ISO, or future timestamps → rejected/flagged, never silently accepted.
- EOD reconciliation against a synthetic bhavcopy fixture: matching close → pass; deliberately mismatched close → `FAILED` with the exact discrepancy reported.
- **Anti-fabrication test** (matching Phase 15P's own established pattern): assert the reconciliation engine never writes to `MicrostructureStore` or `HistoricalObservationStore` under any tested scenario — read-only proof, not just an assumption.

**19.20.6 (final):**
- Full regression, 0 failures (standing requirement, unchanged).
- Real-market validation: run the capture service read-only against a real live session (market hours, no systemd install — foreground/manual invocation only, same discipline as every prior phase's real-data proof) and confirm real `MinuteObservation`s with plausible tick counts/OHLC are produced, then run reconciliation against that real day.
- Confirm via `md5sum`/mtime that no Phase 19.19 file changed at any point during this phase (same verification technique already used in Phase 19.19's own re-verification).

---

## 7. Risks and Mitigations

| Risk | Mitigation |
|---|---|
| A bug in the new capture service somehow writes to `historical_observations.db` | Structural: `MicrostructureStore` is a distinct class with its own file path, never constructed with the Phase 19.19 store's path anywhere in the new code; enforced by an explicit test asserting the two file paths are never equal and by AST-checking that `market_microstructure`/`market_data_integrity` never import `bujji.historical_reality.store.HistoricalObservationStore` for writing (read-only import for reconciliation's comparison step is the one deliberate, explicit exception — reviewed, not incidental). |
| Long-running `Type=simple` service (09:15–15:30) crashes or hangs mid-session, unlike the existing bounded `oneshot` | `ProcessLock` + the same `TickSilenceWatchdog`/reconnect discipline already proven in the live-trading runtime; systemd `Restart=on-failure` with backoff (mirroring `bujji-orb-vwap-legacy.service`'s own already-audited restart policy, not inventing a new one); the service exits cleanly at 15:30 regardless of tick activity, so a hang has a bounded worst case of one session, not indefinite. |
| FYERS WebSocket subscription limits / rate limits from adding ~12 new symbols (spot, futures, VIX, 10 option legs) | Bounded, small, known subscription count (ATM±5 = 10 option symbols + 3 underlyings = 13 total) — well within typical WS subscription limits; sized explicitly in 19.20.2's real-data validation step before any systemd install. |
| Reconciliation engine silently "fixing" bad data instead of reporting it | Explicit anti-fabrication test (Section 6) plus the same code-review discipline applied to every prior phase's completeness/gate logic — `eod_reconciliation.py` and `intraday_checks.py` are read-only by construction (no `write()` call to any source store appears anywhere in either module — verified by test, not just design intent). |
| bhavcopy unavailability or format drift breaks EOD reconciliation | Reconciliation classifies bhavcopy-unavailable as its own explicit status (not `FAILED` for the whole day, not silently skipped) — matches the existing 5-way honesty discipline (`NON_TRADING_DAY`/`SESSION_COMPLETE`/`INCOMPLETE`/`SESSION_FAILED`/`MISSING`) already established in `campaign_continuity.py`. |
| Two new always-on-ish services increase VPS resource usage (memory, disk, CPU) beyond the currently-commissioned footprint | Sized and measured explicitly during 19.20.6's real-data validation (disk growth rate from 1-minute-not-tick storage, memory footprint of the WS client) before proposing systemd installation — matching the disk-space check already performed in every prior commissioning gate. |
| Scope creep — this phase quietly growing into strategy/decision work | Structural AST import-boundary test (Section 6) is the enforcement mechanism, not just a stated intention — the same mechanism this project has relied on since Phase 19.7 to keep `market_phenomena` free of trading-brain imports. |

---

## 8. What Happens Next

Per your Section 8 implementation order, the next step (19.20.2) is to build the high-frequency capture module in isolation, with unit tests against synthetic ticks, and no systemd deployment. I have not started that yet — this document is 19.20.1's deliverable. Confirm this plan (or flag anything you want changed) and I'll proceed to 19.20.2.
