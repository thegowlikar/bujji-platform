# Phase 17H.9 — Intraday Historical Reality: Architecture Audit

**Status: AUDIT ONLY (this document). Implementation follows in this
same turn, per your instruction, but is reported separately below.**

---

## 1. Can existing `HistoricalObservation` represent intraday candles without modification?

**Yes — confirmed empirically, not just by reading code.** Live-minted
four real `build_historical_observation()` calls this session:

```
daily,       2020-03-23T09:15:00+05:30  -> OBS-482a8aa51c90a00d0b845e2c
5min@09:20,  2020-03-23T09:20:00+05:30  -> OBS-31d8305c38c3af39d581f91c
5min@09:25,  2020-03-23T09:25:00+05:30  -> OBS-0fc4951e94801f6c2a061cbe
5min@09:20 (re-minted, identical inputs) -> OBS-31d8305c38c3af39d581f91c  (same id)
```

All three distinct records got distinct ids; the re-minted duplicate
got the identical id. Zero changes needed to `HistoricalObservation`,
`HistoricalLineage`, or `build_historical_observation()`.

## 2. Does `resolution` already exist?

**Yes.** `market_observation.taxonomy.RESOLUTION_FIVE_MINUTE = "FIVE_MINUTE"`
already exists, alongside `RESOLUTION_ONE_MINUTE`/`RESOLUTION_FIFTEEN_MINUTE`/
`RESOLUTION_HOURLY` — defined since this taxonomy module's creation,
simply never exercised until now. No taxonomy change needed.

## 3. Does timestamp identity need extension?

**No — this is the "important architectural rule" question, and the
answer is it's already handled correctly, proven above.**
`build_observation()`'s identity hash seed is
`(observation_type, instrument, exchange, segment, timestamp,
resolution, source, schema_version)` — **`timestamp` is the full
ISO8601 datetime already, never truncated to a date, and `resolution`
is a separate hash input.** A 2020-03-23 daily bar and a 2020-03-23
09:20 five-minute bar were never at risk of colliding — the mechanism
that would prevent it already existed, unused, since Phase 17H.3.

`HistoricalObservationStore`'s natural key
(`instrument_identity|resolution|timestamp|source`) independently
confirms the same thing at the storage layer — resolution and full
timestamp are both already part of the natural key, not just the
content-hash.

## 4. Does `MarketRealitySnapshot` need changes?

**No, and it should NOT be touched this phase.** Its builder
(`market_reality_snapshot/builder.py`) explicitly queries
`moc_taxonomy.RESOLUTION_DAILY` rows only, on a fixed `_day_bounds()`
window. Intraday 5-min rows will live in the **same**
`HistoricalObservationStore` table (same schema, no new store) but are
structurally invisible to the existing daily snapshot builder — this is
correct, not a gap: a `MarketRealitySnapshot` is a one-row-per-day
Reality-tier concept, and building an intraday-aware snapshot/query
layer is a genuinely separate, later concern (unscoped here, not
implemented).

## 5. Any duplicate storage models being proposed accidentally?

**No.** `HistoricalObservationStore` is reused completely unchanged —
same SQLite file, same schema, same `write()`/`range()`/
`record_ingestion_run()` methods. Intraday rows are just more rows in
the same table, distinguished by `resolution` and `timestamp`, both
already part of the schema and the natural key (§3).

---

## Data source audit (live-verified this session, 2026-08-13)

### Per-request range limit

Bisected precisely across 1/5/15/60-minute resolutions, confirmed
**uniform**: **100 days/request** (100d = ok, 101d = error, code -50),
vs daily's 366 days. Not resolution-specific — the same 100-day cap
applies to all four intraday resolutions tested.

### Earliest available dates — bisected, not assumed

| Instrument | Earliest intraday (5-min, live-bisected) | Compare: daily depth |
|---|---|---|
| Spot | **2017-07-17** (epoch 1500262800, confirmed identical at both 1-min and 5-min) | 1998-05-04 |
| VIX | **2017-07-17** (same exact epoch as spot) | 2008-04-17 |
| Futures (continuous) | ~2018-01-02, bounded by the continuous series' own existence | 2018-01-02 |

Spot and VIX intraday data start on the **identical real epoch** —
strong evidence of one platform-wide intraday cutoff, not a
per-instrument limit. This means VIX intraday is **9 years shallower**
than VIX daily — a real, previously-undiscovered asymmetry (found in
Phase 17H.8's own audit, reconfirmed here).

### Continuous futures / `cont_flag` behaviour at intraday resolution

**Confirmed working identically to daily.** The 2018 intraday probe
used `cont_flag="1"` with the current live contract symbol
(`NSE:NIFTY26AUGFUT`) and returned real 2018 data despite that specific
contract not existing then — the same continuous-stitching mechanism
already locked for daily (Phase 17H.3/17H.6) works at 5-minute
resolution too, not re-derived, directly observed.

### Identity rule — unchanged, reaffirmed

Historical continuous futures rows are stored under
`instrument_identity = "NIFTY_FUT_CONTINUOUS"`, never
`NSE:NIFTY26AUGFUT` (the request symbol) — the exact same binding rule
from Phase 17H.3 Part 2.4, applied identically to intraday rows. No new
identity concept needed.

---

## Certification — new, distinct access-method value required

**Confirmed necessary, your instinct is correct.** `CertificationGate`
is keyed by `(instrument, access_method)` only — it has no concept of
resolution. If intraday certification reused
`"direct_sdk_fyers_historical_rest"` (the existing daily historical
access method), then `gate.status_for("direct_sdk_fyers_historical_rest",
INSTRUMENT_SPOT)` would **already** return `CERTIFIED_AVAILABLE` for
intraday access that was never actually tested — the exact collision
class already found and fixed twice in this engagement (17G.0
REST-vs-websocket; 17H.3 live-vs-historical).

**Decision: `access_method = "direct_sdk_fyers_historical_intraday_rest"`**
— a third, distinct value, parallel to the existing
`"direct_sdk_fyers_broker_py"` (live) and
`"direct_sdk_fyers_historical_rest"` (historical daily). Same cert-key
mapping (`INSTRUMENT_TYPE_TO_CERT_KEY`) reused unchanged — only the
access-method value is new.

Three new dated artifacts, same filename convention as every prior
certification:
```
fyers_nifty_spot_intraday_historical_certification_<YYYYMMDD>.json
fyers_nifty_future_intraday_historical_certification_<YYYYMMDD>.json
fyers_india_vix_intraday_historical_certification_<YYYYMMDD>.json
```

Not market-hours gated, same reasoning as every historical
certification script before it. Fail-closed: ingestion scripts refuse
to run unless `CERTIFIED_AVAILABLE`.

---

## What's genuinely new this phase (not a duplication of 17H.4/17H.6)

- `CHUNK_DAYS = 100` instead of `366`.
- **Real per-candle timestamps**, not a fixed `T09:15:00` session marker
  — daily's fixed-time convention was correct for daily (one nominal
  time per bar) but wrong for intraday. Reuses `bujji.core.clock.epoch_to_ist()`
  (already correctness-verified elsewhere in this codebase, explicit
  about avoiding naive `datetime.fromtimestamp()`'s host-timezone bug)
  to convert each real candle epoch to its own real IST timestamp.
- `resolution = moc_taxonomy.RESOLUTION_FIVE_MINUTE` instead of `RESOLUTION_DAILY`.
- A distinct `access_method` (above).
- Otherwise: identical chunking loop, identical raw-artifact-before-
  interpretation discipline, identical OHLC validation rules, identical
  conflict/idempotency guarantees — all reused from 17H.4/17H.6, not
  reinvented.

## Volume estimate (stated before ingestion, not after)

~75 candles/session × ~250 sessions/year × ~9 years (2017/2018 → 2026)
≈ **~170,000 rows per instrument**, ~500,000 total across three
instruments. SQLite/WAL handles this without difficulty — same
conclusion as Phase 17H.8's own estimate, restated as a pre-ingestion
expectation to check the real run against.
