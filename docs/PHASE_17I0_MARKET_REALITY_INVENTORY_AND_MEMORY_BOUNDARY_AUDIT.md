# Phase 17I.0 — Market Reality Inventory & Memory Boundary Audit

**Status: AUDIT ONLY. No indicators, no regimes, no similarity, no
Memory, no Intelligence built in this phase.** Every number below was
queried live against the real VPS stores on 2026-08-13, not carried
over from memory of prior phases.

---

## 1. What does Bujji know today? — Historical Reality inventory

All figures are live `SELECT` results against
`data/historical_reality/normalized/historical_observations.db`.

| Instrument | Resolution | Rows (store, deduped) | Date range |
|---|---|---|---|
| NSE:NIFTY50-INDEX (spot) | DAILY | 7,041 | 1998-05-04 → 2026-08-13 |
| NSE:NIFTY50-INDEX (spot) | FIVE_MINUTE | 168,119 | 2017-07-17 → 2026-08-13 |
| NSE:INDIAVIX-INDEX | DAILY | 4,524 | 2008-04-17 → 2026-08-13 |
| NSE:INDIAVIX-INDEX | FIVE_MINUTE | 168,082 | 2017-07-17 → 2026-08-13 |
| NIFTY_FUT_CONTINUOUS | DAILY | 2,131 | 2018-01-02 → 2026-08-13 |
| NIFTY_FUT_CONTINUOUS | FIVE_MINUTE | 157,889 | 2018-02-01 → 2026-08-13 |

**Total: 507,786 historical observations**, all `RUN_STATUS_OK`
ingestion runs, zero `RUN_STATUS_ERROR` runs on record.

### Data quality — rejected rows (real, from `ingestion_runs`)

| Instrument | Resolution | Rejected | Cause (verified) |
|---|---|---|---|
| Spot | FIVE_MINUTE | 153 | Real-time revision drift: a candle fetched near-live can be revised slightly before settlement; the store correctly refused to silently overwrite a prior value under the same key (Phase 17H.9). |
| VIX | FIVE_MINUTE | 44 | Same revision-drift class. |
| VIX | DAILY | 3 | A real `-1.0` OHLC sentinel artifact from FYERS itself (Phase 17H.6), correctly rejected by `_validate_row()`. |
| Futures | FIVE_MINUTE | 29 | Same revision-drift class. |
| Spot | DAILY | 0 | — |
| Futures | DAILY | 0 | — |

No row was ever silently repaired, estimated, or overwritten — every
rejection is logged with a reason and the pre-existing value was kept.

### Known limitations (carried forward from 17H.1/17H.8/17H.9, reconfirmed live)

- **Intraday depth is not a subset of daily depth.** Spot/VIX intraday
  starts 2017-07-17 (same real epoch for both — a platform-wide
  cutoff), ~9-19 years shallower than their daily depth. Futures
  intraday (2018-02-01) is bounded by the continuous series' own
  origin, not independently shallower.
- **Open Interest is never present** in any FYERS historical candle,
  at any resolution, for any instrument — a structural source
  limitation, not a Bujji gap (confirmed live, 17H.1 §1.6, reconfirmed
  17H.8).
- **100-day/request cap on intraday, 366-day/request cap on daily** —
  chunking artifacts of the source API, invisible to the stored data
  itself (chunk boundaries do not affect row correctness).
- **`ingestion_runs.rows_accepted` is a write-attempt counter, not a
  unique-row counter** — it increments on idempotent re-writes too.
  The authoritative count is always the store's own row count, not the
  sum of `rows_accepted` across runs (verified: summing 17H.9's spot
  intraday `rows_accepted` across all 36 runs gives 176,202, while the
  deduped store holds 168,119 — the gap is re-ingestion overlap runs
  colliding on already-held identical rows, not lost data).

---

## 2. Live Reality inventory

### `RawObservationStore` (Layer 0, `layer0_data/raw_observations.jsonl`)

- **456 raw observations** currently on disk (append-only JSONL,
  event-sourced — this is a live capture buffer, not a long-horizon
  archive; distinct from Historical Reality's SQLite store).
- Breakdown: 152 spot, 152 futures, 152 VIX — evenly split, one
  `QUOTE` tick per instrument per cycle.
- Real timestamp range: **2026-08-13T11:47:13 → 2026-08-13T15:29:04**
  — this is the most recent capture session's window (today), not a
  running multi-day archive. Confirms Layer 0's raw JSONL is a
  session-scoped buffer, not itself a durable historical store — that
  role belongs to Historical Reality (§1) and `MarketRealitySnapshot`
  (below).
- Each observation carries full lineage: `observation_id`,
  `provenance.originating_source`, `provenance.transformation_history`
  (`["RAW_CAPTURE"]`), `quality.completeness/freshness/confidence`
  (honest placeholders, never fabricated), `certification` fields tied
  to `access_method="direct_sdk_fyers_broker_py"`.

### `MarketRealitySnapshotStore` (`market_reality_snapshots.db`)

- **252 rows** — one row per trading day the builder has processed
  (historical-first, live-fallback per 17H.5/17H.7).
- `is_final`-aware conflict rule: today's row (in-progress) upserts
  freely; settled days get full conflict detection — reused unmodified
  from 17H.6.

### Shadow campaign data (`data/live_shadow_journal/`)

- `operator_journal.jsonl` — 49MB, real per-cycle decision/session
  telemetry from the Shadow Market Campaign (V1.1 Trading Session
  Governor era onward). This is Intelligence/Strategy-tier telemetry
  (decisions, session state), **not** Reality data — noted for
  completeness, out of this audit's scope to re-classify.
- `portfolio_valuation.jsonl` — 6.1MB, same tier.
- These are real, substantial datasets but belong to the
  Strategy/Position-Intelligence layers audited in Phases 15E–15O, not
  to Market Reality.

### Certification dependencies (all currently on disk, `data_certification/`)

| access_method | Scope | Status |
|---|---|---|
| `direct_sdk_fyers_broker_py` | Live quotes/websocket (spot, VIX, futures, options) | CERTIFIED_AVAILABLE |
| `direct_sdk_fyers_historical_rest` | Historical daily (spot, VIX, futures) | CERTIFIED_AVAILABLE |
| `direct_sdk_fyers_historical_intraday_rest` | Historical 5-min (spot, VIX, futures) | CERTIFIED_AVAILABLE (17H.9) |

Every write path in both Historical Reality and Layer 0 fails closed
if its `(instrument, access_method)` pair is not `CERTIFIED_AVAILABLE`
— no observation has ever been written without a real, dated
certification artifact behind it.

---

## 3. Data dimensions currently available

### Price

- **OHLC**: present for all three instruments, both resolutions.
- **Volume**: present for futures (real, non-zero); spot and VIX are
  **structural volume=0 indices** — stored as `None`, never coerced to
  a fake zero (17H.1 finding, reconfirmed 17H.4).
- **Timestamps**: daily rows carry the nominal `T09:15:00+05:30`
  session-open marker (correct for daily — one nominal time per bar);
  intraday rows carry each candle's own real time via `epoch_to_ist()`
  (17H.9).

### Market context

- Spot (NSE:NIFTY50-INDEX), Futures (NIFTY_FUT_CONTINUOUS, identity
  never the literal contract symbol), VIX (NSE:INDIAVIX-INDEX) — all
  three present at both resolutions, all cross-readable via
  `MarketRealitySnapshot`/`reconstruct_market_reality()`.

### Resolution

- Daily and 5-minute only. **1-minute, 15-minute, 60-minute are NOT
  ingested** — certified as available at the API level (17H.8) but
  never built, by deliberate scope decision (17H.9 spec: "5-minute
  candles only").

### Missing Reality fields (documented, not assumed required)

| Field | Currently captured? | Where evidence exists |
|---|---|---|
| Open Interest | **No, structurally absent from source.** FYERS historical candles never carry OI at any resolution (confirmed 17H.1, every ingestion cert since). Live futures depth polling (`run_futures_depth_poller.py`) DOES see `oi`/`pdoi`/`oiflag` fields in the raw broker payload (confirmed live, `fyers_depth_discovery_20260813.json`) — but this live OI is not currently normalized into any Reality store. |
| Option chain (strikes, premiums) | **Certified as accessible** (`fyers_option_chain_certification.json`, `NIFTY_OPTION_CE` CERTIFIED_AVAILABLE) but **not ingested into any store** — Phase 17F.7.1 built discovery/certification only. |
| Greeks (delta, gamma, theta, vega, IV) | **Not captured anywhere.** No certification artifact exists for a Greeks source; would require either FYERS providing them directly or Bujji computing them (the latter is explicitly Intelligence/Understanding-tier work, out of Reality's scope). |
| Bid/ask (top-of-book) | **Certified as accessible for futures** (`fyers_depth_discovery_20260813.json` shows `bids`/`ask` in the raw poll payload) but **not normalized/stored** as a Reality fact anywhere. |
| Market depth (multi-level order book) | Same as bid/ask — accessible via the depth poller's raw payload, not stored as Reality. |

None of the above are asserted to be *needed* — this section only
documents what exists versus what is captured, per the instruction not
to assume.

---

## 4. Reality integrity audit

| Property | Verified? | Evidence |
|---|---|---|
| Identity uniqueness | Yes | `observation_id` content-hash + store natural key both include full timestamp + resolution; empirically proven distinct across daily/intraday/different-intraday-timestamps (17H.9). |
| Observation lineage | Yes | Every `HistoricalObservation` carries `HistoricalLineage` (source, access_method, source_epoch, source_symbol, raw_artifact_ref, ingestion_run_id, retrieved_at, certification_status/ref, continuity_method). Every raw Layer 0 observation carries `provenance`/`transformation_history`. |
| Duplicate handling | Yes | Three-outcome discipline: new→insert, identical→idempotent no-op, different-under-same-key→`ConflictingHistoricalObservationError`. Proven live this session (17H.9 backfill: 226 real conflicts correctly caught across 3 instruments, zero silent overwrites). |
| Conflict handling | Yes | Same as above — never resolved automatically, always logged and left for explicit review. |
| Restart survival | Yes | SQLite/WAL; proven live by re-running an identical ingestion window and confirming zero row-count growth (17H.9). |
| Certification boundaries | Yes | `CertificationGate` keyed by `(instrument, access_method)`; three genuinely distinct access_method values now exist (live, historical daily, historical intraday) specifically to prevent false-positive certification collisions — this exact class of bug has been proactively avoided three times across 17G.0/17H.3/17H.9. |
| Immutable storage rules | Yes | No `UPDATE` statement exists anywhere in `HistoricalObservationStore` or `RawObservationStore` — every write is either an insert or a rejection. Raw artifacts are written byte-for-byte before any interpretation. |

**No integrity gap was found.** This is the strongest section of the
audit — every mechanism was already correct by design from prior
phases; this audit re-verified rather than discovered.

---

## 5. Memory boundary definition

| Layer | Definition | Example |
|---|---|---|
| **Reality** | A single, immutable, certified fact about what the market did, with full lineage. No comparison across time, no aggregation beyond the candle it describes. | "NIFTY closed at 24,342.55 on 2026-08-13." "VIX's 09:20 five-minute bar on 2020-03-23 had close=41.2." "A candle exists at timestamp T with this OHLC." |
| **Memory** | Reality facts retrieved, filtered, or juxtaposed *without computation* — recall, not derivation. Answers "what happened before," never "what does it mean." | "This VIX level (10–13) has occurred on N prior dates." "These three facts (VIX>25, futures basis negative, spot down >2%) co-occurred on these specific dates." Already partially built: `MarketRealityTimeline`/`RealityQuery` (17H.6-Reality-only) is a Memory-adjacent query layer over Reality rows — it recalls, it does not classify. |
| **Understanding** | A label or classification derived FROM Reality/Memory via computation — trend detection, regime classification, statistical summarization. | "Trend day." "Range day." "Volatility expansion." "This is a Stage 2 setup." None of this exists in the Reality/Memory stores today — `bujji.market_state`/`market_regime` (Intelligence-tier, pre-existing, separate subsystem) already occupies adjacent territory and must not be confused with anything built under "Memory." |
| **Intelligence** | Probabilistic or decision-oriented output built on top of Understanding. | "70% probability of a trend day given these conditions." "Select Iron Condor." Shadow Campaign's strategy selection, position lifecycle, and outcome attribution (Phases 15E–15O) already live here. |

**Where Reality ends and Memory begins, precisely:**
Reality ends at the last `HistoricalObservation`/`RawObservation`/
`MarketRealitySnapshot` row — a fact with a timestamp, a value, and
lineage, nothing more. Memory begins the moment two or more Reality
facts are **retrieved together for comparison or pattern-matching**,
even with zero computation (e.g., "show me every day VIX was between
10 and 13" is Memory; "NIFTY closed at 24,342 on 2026-08-13" is
Reality). The boundary is **retrieval-across-time vs. a single fact**,
not "any computation vs. no computation" — `MarketRealityTimeline`
already sits exactly on this boundary today, correctly on the Memory
side, built deliberately Reality-only (raw filters, no derived fields)
per the explicit decision in Phase 17H.6.

---

## 6. Gaps, classified

| # | Gap | Classification | Notes |
|---|---|---|---|
| 1 | 1-min/15-min/60-min intraday not ingested | **A — Missing Reality data** | Certified available at the API level (17H.8); a deliberate scope decision to build only 5-min, not a discovered blocker. Immediate engineering work only if a future phase explicitly asks for it. |
| 2 | Live futures OI/bid/ask/depth seen by the depth poller but never normalized into any Reality store | **B — Missing storage capability** | The data is accessible and already certified (`fyers_depth_discovery_20260813.json`); no store currently persists it as a Reality fact. This is the single clearest immediate-engineering candidate if pursued, since it is Reality-tier, not Understanding/Intelligence. |
| 3 | Option chain (strikes/premiums) certified but not ingested | **A — Missing Reality data** (certification exists; ingestion does not) | Phase 17F.7.1 built discovery/certification only, by design (manual-execution-only script). |
| 4 | Greeks (delta/gamma/theta/vega/IV) | **F — Intelligence requirement** (if computed) or **A — Missing Reality data** (if sourced directly from a broker field) | No certification exists either way yet — undetermined which path, out of scope to decide here. |
| 5 | No query/filter layer over intraday `MarketRealitySnapshot` rows | **C — Missing query capability** | `MarketRealityTimeline` only ever queried `RESOLUTION_DAILY` rows (by design, 17H.6); a parallel intraday-aware query layer is a distinct, later, additive piece — not built, not currently needed by anything. |
| 6 | "This type of day occurred before" / fact-pattern recall across Reality rows | **D — Memory requirement** | This is exactly what Phase 17I.0 was scoped to NOT build. The boundary in §5 defines where this work would begin. |
| 7 | Trend/range/regime/volatility-expansion labeling | **E — Understanding requirement** | Not attempted here; `bujji.market_state`/`market_regime` already occupy adjacent Intelligence-tier territory and must stay separate from any future Understanding-tier work to avoid the naming collision already avoided once (17H.5). |
| 8 | Probability/prediction/strategy selection on top of Reality | **F — Intelligence requirement** | Already exists as a separate, mature subsystem (Shadow Campaign, Phases 15E–15O) — not a gap in Reality, out of this audit's scope. |

**Only Gap #2 (live depth/OI/bid-ask normalization) is a genuine
Reality-layer deficiency eligible for immediate engineering work under
this phase's own rules** — the data exists, is certified, and simply
has no store. Nothing else in this list qualifies: gaps #1 and #3 are
deliberate scope boundaries already decided elsewhere, not omissions;
#4 is undetermined pending a sourcing decision; #5–#8 are explicitly
Memory/Understanding/Intelligence, out of bounds for this phase by its
own restriction.

---

## Summary — answers to the five questions asked

1. **What does Bujji know today?** 507,786 certified, lineage-complete
   historical OHLC observations (daily 1998/2008/2018→today, 5-min
   2017/2018→today) across spot/VIX/futures-continuous, plus a
   same-day live capture buffer (456 raw ticks) and a 252-day
   cross-instrument daily snapshot store. Certified-but-unstored:
   option chain, live futures OI/depth/bid-ask.

2. **How reliable is that knowledge?** Every row carries certified
   lineage back to a specific `IngestionRun`/certification artifact.
   Zero silent overwrites (three-outcome duplicate/conflict discipline
   verified live). Known, documented limitations: OI structurally
   absent from historical candles; intraday depth is resolution-
   dependent, not a subset of daily depth; a small (<0.1%),
   well-understood rejection rate from real-time revision drift on
   very recent intraday rows.

3. **What does Bujji not know?** Greeks, market depth beyond
   top-of-book, option chain history (only current-moment certified
   access exists), 1/15/60-minute resolutions, anything above a single
   timestamped fact (no Memory, Understanding, or pattern recall over
   Reality rows exists inside the Reality layer itself — that
   capability lives one layer up, in `MarketRealityTimeline`, and even
   that stays deliberately fact-only).

4. **Which missing pieces are Reality gaps?** Only live futures
   OI/depth/bid-ask normalization (Gap #2) is a ready, certified,
   currently-unstored Reality fact. Option chain ingestion (Gap #3) is
   a Reality gap in principle but requires a separate ingestion-design
   decision, not immediate work under this audit alone.

5. **Where exactly does Reality end and Memory begin?** At the
   boundary between a single certified fact and any retrieval of two
   or more facts for comparison across time — even zero-computation
   comparison. Everything currently built stays correctly on the
   Reality side of that line, including the one component
   (`MarketRealityTimeline`) that sits right at the boundary by
   deliberate design.

No code was written in this phase. No indicator, regime, similarity,
or Memory component was built, per the phase's own restriction.
