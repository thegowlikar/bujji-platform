# Phase 17H.2 — Historical Market Reality Layer (HMRL): Schema Design

**Status: DESIGN ONLY. No code. No ingestion. No indicators. No signals.
No strategy logic. No live capture code modified.**

Builds on [PHASE_17H1_HISTORICAL_SOURCE_AUDIT.md](PHASE_17H1_HISTORICAL_SOURCE_AUDIT.md)
(live-verified 2026-08-13). Every design decision below is grounded in a
real, checked fact about this repository or the real FYERS API — each
tagged **VERIFIED** (checked this session), **EXISTING** (already in the
codebase), or **OPEN** (a genuine unknown, not papered over).

---

## Part 0 — Four findings that change the proposed design

Before the schema, four things found while checking the proposal against
the real repository. Each changes something concrete.

### 0.1 A candle model already exists, and it is richer than the proposal — EXISTING

`bujji/market_timeseries/models.py` defines `Candle` (schema 1.2.0), with
a `CandleStore` (SQLite, WAL, `synchronous=FULL`) already built and
tested (Phase 15Q/17F). It carries far more than the proposed
`market_candle` table: `tick_count`, `source_observation_ids`,
`materializer_id`, `calc_version` (content hash, part of the PK),
`first_event_time`/`last_event_time` (real observed span vs. nominal
window), `knowledge_boundary` (bitemporal), `capture_event_overlap`
(which blind spots overlapped this bar), `transformation_history`.

**But it is a DERIVED record** — its whole design assumes the bar was
folded from N Layer 0 ticks that Bujji itself observed. A FYERS daily
bar for 1998 has no ticks behind it, no capture events, no
`tick_count`. Forcing historical bars into that model would mean
writing `tick_count=0`, `source_observation_ids=()`,
`capture_event_overlap=()` on every row — meaningless placeholders,
exactly the failure mode this project already identified once (Layer 0's
`build_raw_observation()` hardcoding MOC's quality fields to
placeholders, documented in `PHASE_17H2_0_3_...md`).

**Decision: a separate `HistoricalCandle` model, sharing vocabulary but
not the record shape.** Same reasoning that produced 17H2.0.3's
conclusion (raw observations + source capability profiles, **not** one
universal schema). Section 2 defines it.

### 0.2 Layer 0's taxonomy already supports historical candles — EXISTING

Three vocabulary items needed for the user's stated principle
("historical enters through the same Reality discipline") already exist,
unused:

| Constant | Where | Status |
|---|---|---|
| `KIND_CANDLE = "CANDLE"` | `market_reality/taxonomy.py:57` | Defined, with `REQUIRED_PAYLOAD_FIELDS[KIND_CANDLE] = ("open","high","low","close")` and full `capture.py` mappings (`TYPE_PRICE`, `VALUE_KIND_OHLC`) — **never used by any collector** |
| `ORIGIN_HISTORICAL_RECONSTRUCTION` | `market_observation/taxonomy.py:154` | Defined in `ALL_ORIGINS` — **never used** |
| `RESOLUTION_DAILY` / `_WEEKLY` / `_HOURLY` / `_FIFTEEN_MINUTE` / `_FIVE_MINUTE` / `_ONE_MINUTE` | `market_observation/taxonomy.py:93-100` | All defined — only `TICK`/`ONE_MINUTE` ever used |

**No taxonomy change is required for this phase.** The vocabulary was
built for exactly this and left dormant. This is a real, load-bearing
finding: the "same Reality discipline" principle is not just
philosophically right, it is already mechanically supported.

### 0.3 Zero analytical dependencies are installed — VERIFIED

```
duckdb    NOT INSTALLED
pyarrow   NOT INSTALLED
pandas    NOT INSTALLED
polars    NOT INSTALLED
```

`requirements.txt` in full: `pydantic`, `PyYAML`, `pytest`,
`pytest-asyncio`, `fyers-apiv3`, `requests`. Six dependencies, none
analytical. Every store in this project (`journal.py`,
`mil_next/snapshot_journal.py`, `CandleStore`, `FuturesStatsStore`) is
SQLite — and `CandleStore`'s own docstring states the precedent
explicitly: *"SQLite is already an established storage technology in
this codebase... so a `.db` file introduces no new dependency and
follows existing precedent rather than inventing a pattern."*

DuckDB + Parquet means **two new dependencies** in a six-dependency
project. That is not a veto — it is a real cost that should be paid
knowingly. Section 3 treats this as an explicit decision with a
measurable trigger, not an assumption in either direction.

### 0.4 Daily candle timestamps are UTC-midnight-anchored, not session times — VERIFIED

From the real 17H.1 captures: daily candle epoch `1786579200` decodes to
**2026-08-13 05:30:00 IST** — i.e. `00:00:00 UTC` of the trading date,
not 09:15 (open) or 15:30 (close). Confirmed across multiple years
(`1104710400` → 2005-01-03, `946857600` → 2000-01-03).

Independently corroborating: Phase 17I.6.2's live discovery found the
quote endpoint's `tt` field carrying **the identical value**,
`1786579200`, static all day — the same UTC-midnight day marker.

**Consequence**: naively applying this codebase's own `epoch_to_ist()` to
a daily bar yields `05:30 IST`, which is not a market time and would be
actively misleading if stored as a timestamp. Section 5 defines the
rule that prevents this.

---

## Part 1 — Storage layout

Engine-agnostic on-disk layout. Directory names follow the existing
`layer0_data/` / `data_certification/` convention (lowercase, snake).

```
data/
  historical_reality/

    raw_artifacts/                 <- immutable, exactly as received
      fyers/
        NSE_NIFTY50-INDEX/
          daily/
            1998.json              <- one file per (instrument, timeframe, year-chunk)
            1999.json                 mirroring the 366-day request limit
            ...
        NSE_NIFTY-CONTINUOUS-FUT/
          daily/
        NSE_INDIAVIX-INDEX/
          daily/

    normalized/                    <- validated, canonical HistoricalCandle records
      historical_candles.db        <- see Part 3 for the engine decision

    metadata/
      ingestion_runs.db            <- one row per real fetch attempt (Part 4)
      quality_reports/             <- per-run validation output (Part 6)
```

**Why `raw_artifacts/` exists as its own tier**: it is the direct
implementation of the stated principle — *Source → Raw Historical
Artifact → Validation → Normalized Reality Store → Memory Layer*. The
raw tier stores FYERS's response **byte-for-byte as received**, before
any interpretation. If a validation rule is later found wrong, or a
field is later discovered to mean something different, re-normalization
runs from the preserved artifact rather than re-hitting the API (which
may by then return different data, or none). This mirrors the
already-proven Gate B artifact discipline
(`tests/fixtures/gate_b/`, Phase 17H.2.0.1) rather than inventing a
pattern.

**Note on the proposed `futures/contracts/2026AUG/` tree**: deferred, not
adopted. Per 17H.1 §1.3, `cont_flag=1` yields a genuine continuous
series, so per-contract history is not needed for the price series that
Phase 17H.4 targets. Per-expiry contract history remains a real future
need **only** for OI (which FYERS cannot supply at all — Bhavcopy-only,
permanently). Creating the directory now would imply a capability that
does not exist yet.

---

## Part 2 — Data model

Four record types. No indicators. No derived analytics. Nothing that
could be recomputed differently tomorrow is stored.

### 2.1 `HistoricalCandle` — the core record

```
# ---- Identity (what this bar IS) ----
instrument            str    # canonical symbol, e.g. "NSE:NIFTY50-INDEX"
instrument_type       str    # reuse market_reality.taxonomy: SPOT | FUTURE | INDEX
timeframe             str    # reuse market_observation.taxonomy RESOLUTION_*: DAILY, ONE_MINUTE, ...
window_start          str    # ISO8601 IST, inclusive  -- see Part 5 for derivation
window_end            str    # ISO8601 IST, exclusive

# ---- Observed values (the reality itself) ----
open                  float
high                  float
low                   float
close                 float
volume                Optional[float]   # None when the source published none; never coerced to 0.0
open_interest         Optional[float]   # ALWAYS None from FYERS (structural); Bhavcopy-only

# ---- Lineage (Part 4) ----
source                str    # "fyers_historical" | "nse_bhavcopy"
access_method         str    # "direct_sdk_fyers_broker_py"
origin                str    # ORIGIN_HISTORICAL_RECONSTRUCTION (existing constant)
source_epoch          int    # the raw epoch exactly as the source sent it -- never discarded
raw_artifact_ref      str    # path + line/index into raw_artifacts/, so any row traces to its file
ingestion_id          str    # FK -> IngestionRun
retrieved_at          str    # ISO8601 IST -- when Bujji fetched it (NOT when it happened)
certification_status  str    # reuse market_reality.taxonomy certification vocabulary
schema_version        str
```

**Deliberately absent**, each for a stated reason:

| Not included | Why |
|---|---|
| `candle_id` (surrogate) | The natural key `(instrument, timeframe, window_start, source)` is complete and content-addressable. A surrogate id adds a second identity to keep consistent, which this project's Layer 0 already avoids (content-hash ids only). |
| `basis`, `basis_percentage`, `days_to_expiry` | **Derived.** Correctly placed in the proposal's own §3 as "not stored initially" — this design keeps them out entirely. They are a query/materializer output (Part 8), computable from `futures.close - spot.close` at any time. Storing them freezes one definition of basis forever. |
| `change` (on VIX) | Same reason — `close - prev_close` is a one-line query, not a fact worth storing. |
| `tick_count`, `source_observation_ids`, `capture_event_overlap` | Meaningless for a source-provided bar (§0.1). Their absence *is* the honest signal that this is not a tick-folded record. |
| RSI / EMA / MACD / Supertrend | Per instruction, and per the project's standing rule that Layer 0-adjacent stores never hold derived analytics. `market_timeseries/indicators.py` already computes these on demand from a series — nothing needs storing. |

### 2.2 `FuturesIdentity` — separate, because futures have identity

```
instrument            str    # e.g. "NSE:NIFTY26AUGFUT" or the continuous pseudo-symbol
underlying            str    # "NIFTY"
contract_type         str    # CONTINUOUS | SPECIFIC_EXPIRY
expiry_date           Optional[str]   # ISO date; None for CONTINUOUS
expiry_epoch          Optional[int]   # authoritative, from InstrumentMaster (Phase 17I.5)
continuity_method     Optional[str]   # "fyers_cont_flag_1" for CONTINUOUS; None otherwise
source                str
```

`continuity_method` exists because **how** a continuous series was
stitched is itself a fact that affects interpretation. `cont_flag=1`'s
stitching rule (whether it back-adjusts prices or simply concatenates)
is **OPEN** — 17H.1 proved it stitches, not *how*. Recording the method
per-row means the day that question is answered, every affected row is
identifiable. Without it, a future correction could not find its own
scope.

### 2.3 `IngestionRun` — one row per real fetch attempt

```
ingestion_id          str    # content hash of (instrument, timeframe, range, source, started_at)
source                str
instrument            str
timeframe             str
range_from            str    # requested window (≤366 days, per 17H.1 §1.1)
range_to              str
started_at            str
completed_at          Optional[str]
status                str    # OK | NO_DATA | ERROR | PARTIAL
rows_returned         int
error_code            Optional[int]    # e.g. -50, as the API really returned it
error_message         Optional[str]
raw_artifact_path     str
```

This makes the two real API behaviours found in 17H.1 first-class,
recorded facts rather than lost console output: `s=no_data` (a genuine
"this period has no data" — e.g. NIFTY pre-1997, VIX pre-2008) and
`s=error, code=-50` (a rejected request — e.g. >366 days). **These are
different facts and must never collapse into "we got nothing."**

### 2.4 `QualityReport` — per ingestion run, per Part 6's rules

```
ingestion_id          str
rule_name             str
severity              str    # REJECT | FLAG | INFO
affected_rows         int
detail                str
```

---

## Part 3 — Storage engine decision (DuckDB / Parquet)

**Recommendation: SQLite for the normalized store now; revisit DuckDB at
a defined, measurable trigger. Parquet not adopted yet.** Presented as a
tradeoff rather than a verdict — the reasoning is below and the decision
is the operator's.

**The case for DuckDB (real, not dismissed)**: genuinely superior for the
stated future query shape ("all days where basis expanded >50 while VIX
fell") at scale; Parquet is an excellent columnar archival format; SQL
serves Phase 17H.6's comparison engine well; adopting now avoids a
migration later.

**The case against adopting it now**:

1. **Measured volume does not justify it.** Phase 17H.4's actual first
   target — NIFTY Spot Daily, ~28 years — is **~7,000 rows**. All three
   instruments at daily, full history: **~20,000 rows**. SQLite answers
   any query over 20K rows instantly. DuckDB's advantage materialises
   in the millions.
2. **The volume that would justify it is unverified.** 1-minute across
   28 years would be ~2.6M rows/instrument — but 17H.1 §1.5 explicitly
   flagged that **intraday range limits and intraday historical depth
   are unmeasured**. Brokers typically retain intraday for a few years,
   not decades. Adopting a storage engine sized for data whose
   availability is untested is designing for an assumption.
3. **Two new dependencies in a six-dependency project**, against an
   explicit, documented precedent (§0.3).

**Concrete trigger to revisit** — adopt DuckDB when *all three* hold:
(a) intraday historical depth is measured and confirmed available at
multi-year scale; (b) the normalized store exceeds ~5M rows; (c) a real
query on the real data is measured too slow in SQLite. Falsifiable, and
re-evaluable with evidence rather than preference.

**What makes this cheap either way**: the Part 2 schema is
engine-agnostic — plain columns, no engine-specific types. A future
DuckDB migration is a bulk read-and-reinsert, not a redesign.

---

## Part 4 — Source lineage rules

1. **Every normalized row traces to a preserved raw artifact.**
   `raw_artifact_ref` is mandatory and must resolve to a real file under
   `raw_artifacts/`. A row whose artifact is missing is not
   trustworthy reality.
2. **Every row records its retrieval time separately from its event
   time.** `retrieved_at` (2026) and `window_start` (1998) are different
   facts. This is the same bitemporal separation `Layer0Lineage` already
   enforces via `capture_timestamp` vs `event_timestamp` — reused
   deliberately, not reinvented.
3. **`source_epoch` is never discarded.** The raw integer stays on the
   row alongside the derived ISO timestamps, so any future timezone
   correction is auditable and reversible.
4. **Source is explicit per row**, never inferred from which table it
   sits in — a prerequisite for the dual-source (FYERS + Bhavcopy)
   architecture already decided in 17H2.0.2.
5. **No row is ever updated in place.** Re-ingestion of the same
   `(instrument, timeframe, window_start, source)` with identical
   content is an idempotent no-op; with *different* content it is a
   **conflict** to surface, never a silent overwrite — the same
   discipline `CandleStore.ConflictingCandleError` and
   `RawObservationStore` already implement.
6. **Certification**: `certification_status` reuses the existing
   vocabulary. **OPEN, and a real gate**: no certification artifact
   exists today for a `fyers_historical` access method — the existing
   three cover live REST only. Whether historical backfill must pass
   `CertificationGate` (and therefore needs its own certification run)
   or is governed differently is **a decision for Phase 17H.3, not
   assumed here.** Flagging rather than quietly defaulting to
   `CERTIFICATION_MISSING`, which would fail-closed and block all
   ingestion.

---

## Part 5 — Timezone and timestamp rules

Grounded in §0.4's verified finding.

1. **All stored timestamps are ISO8601 with explicit IST offset
   (`+05:30`).** Never naive, never UTC-rendered. Matches every existing
   store in this project.
2. **Daily bars**: the source epoch encodes the **trading date** at
   `00:00 UTC`. The rule is: *derive the calendar date from the epoch,
   then construct the window from the trading session* —
   `window_start = <date>T09:15:00+05:30`,
   `window_end = <date>T15:40:00+05:30`. **Never** store the raw
   `epoch_to_ist()` output (`05:30 IST`) as if it were a market time.
3. **Market-close boundary is date-dependent.** Per the NSE change
   verified this session (circular 2026-05-30, effective 2026-08-03),
   F&O close moved 15:30 → 15:40. A historical series spanning that
   date must use **15:30 for bars before 2026-08-03 and 15:40 from
   2026-08-03 onward**. A single hardcoded constant would silently
   misstate one side of that boundary. (Directly relevant: 7 files under
   `bujji/` still hold the old constant — flagged in the 17I.7 manifest,
   out of scope here.)
4. **Intraday bars**: `window_start` is the real per-bar epoch converted
   via the existing `epoch_to_ist()`; `window_end = window_start +
   timeframe`. No session-boundary reconstruction needed.
5. **DST**: none. IST has no daylight saving; a fixed `+05:30` is always
   correct for this market.

---

## Part 6 — Validation and missing-data rules

Rules run between the raw artifact and the normalized store. Severity is
either **REJECT** (row never enters normalized reality) or **FLAG**
(row enters, carrying a recorded quality note).

| # | Rule | Severity | Grounding |
|---|---|---|---|
| V1 | `high >= low`, `high >= max(open, close)`, `low <= min(open, close)` | REJECT | Same OHLC-integrity check `certify_vix_access.py` already applies |
| V2 | All of O/H/L/C strictly `> 0` | REJECT | Same script's `min(...) <= 0` check |
| V3 | No timestamp in the future relative to ingestion time | REJECT | Same script's future-timestamp check |
| V4 | No duplicate `(instrument, timeframe, window_start, source)` within a run | REJECT | Same script's duplicate check |
| V5 | Epochs strictly ascending within a chunk | FLAG | Same script's ordering check; a source reordering is worth knowing, not necessarily fatal |
| V6 | `volume == 0` on an **index** instrument (SPOT/INDEX) → store as `None`, not `0.0` | FLAG | **VERIFIED**: real index daily bars return `volume=0` (e.g. `[1104710400, 2080.0, ..., 0]`) while real futures bars return genuine volume (`2408835`). Per the existing `Candle` doctrine — *"no volume reported" and "zero volume traded" are different facts"* — an index's structural 0 must not masquerade as a measured zero |
| V7 | A trading day present in one instrument's series but absent in another's, same window | FLAG | Cross-instrument gap detection; never auto-filled |
| V8 | `open_interest` non-null on any FYERS-sourced row | REJECT | Structurally impossible (17H.1 §1.2) — its presence means a mapping bug, not real data |

**Missing data — the governing rule**: *absence is recorded, never
fabricated.* No forward-fill, no interpolation, no synthetic flat bars.
A market holiday, a `no_data` period (NIFTY pre-1997, VIX pre-2008), and
a failed fetch are **three different facts**, distinguished by the
`IngestionRun.status` that covers that window (`NO_DATA` vs `ERROR` vs a
successful run that simply contained no bar for that date). This is the
same principle already enforced in `CandleAggregator` (*"a window with
zero ticks produces NO candle... nothing here forward-fills"*) and in
Phase 17I's Layer 0 (*"Layer 0 records facts, not absence"*).

---

## Part 7 — Futures identity handling

1. **Two distinct series types**, never conflated:
   `contract_type=CONTINUOUS` (from `cont_flag=1`) and
   `contract_type=SPECIFIC_EXPIRY`.
2. **The continuous series gets its own pseudo-symbol**
   (e.g. `NSE:NIFTY-CONTINUOUS-FUT`), **not** the expiry-bearing symbol
   used to request it. This matters: 17H.1 §1.3 fetched 2025 data using
   `NSE:NIFTY26AUGFUT` — a symbol whose contract did not exist in 2025.
   Storing those 249 rows under that symbol would assert something false
   (that the Aug-2026 contract traded in January 2025). The request
   symbol is an API access detail; the stored identity must reflect what
   the data actually *is*.
3. **`expiry_date` is null for continuous rows** — a continuous series
   has no single expiry. Faking one would be fabrication.
4. **Expiry, when needed, comes from `InstrumentMaster`** — the
   authoritative source proven in Phase 17I.5 (real `expiry_epoch` from
   the live NFO CSV), never parsed from a symbol string.
5. **OPEN — the rollover-adjustment question.** `cont_flag=1` stitches
   (VERIFIED), but whether it *back-adjusts* prices across rollovers or
   simply concatenates is **not established**. This materially affects
   whether the continuous series' historical absolute prices are
   directly comparable to spot for basis reconstruction. Recorded via
   `continuity_method` (§2.2) so the answer can be applied retroactively.
   **Recommended check before Phase 17H.4 ingests futures**: fetch the
   continuous series across a known rollover date and compare against
   the individual expiring contract's own final bars — a small,
   read-only discovery, in the established Gate-B style. Not authorized
   here. (Note: Phase 17H.4's own order puts Spot and VIX before
   Futures, so this does not block the first ingestion target.)

---

## Part 8 — Compatibility with live reality, and migration to Market Memory

### 8.1 Shared vocabulary, separate physical stores

HMRL reuses `market_reality.taxonomy` (instrument types, certification
states) and `market_observation.taxonomy` (resolutions, origins)
**verbatim** — no new vocabulary, no taxonomy edits (§0.2). A query
joining live Layer 0 observations and historical candles therefore
matches on identical constants.

They remain **physically separate stores**, deliberately:

- **Different write patterns**: live is append-one-forever; historical
  is bulk-load-once-then-immutable.
- **Volume asymmetry**: the live campaign's Layer 0 will hold ~11K rows
  after 10 sessions. A historical backfill could add orders of magnitude
  more. Merging them would make the live campaign's own integrity
  metrics (Phase 17I.7's uptime/gap/duplicate measures) meaningless.
- **This does not violate the "NO PARALLEL PERSISTENCE SYSTEM" rule**
  in `market_reality/store.py` — that rule forbids inventing a *new
  durability mechanism*, and is satisfied by reusing the same proven
  storage technology and disciplines, not by forcing one physical file.

### 8.2 Why historical candles are not written into Layer 0

Layer 0 records **what Bujji itself observed, when it observed it**. A
1998 daily bar is a **third-party assertion about the past**, retrieved
in 2026. Both are legitimate reality; they are not the same *kind* of
reality, and the distinction is worth preserving structurally rather
than flattening. This is the same conclusion 17H2.0.3 reached about
sources generally — and it keeps the live campaign's evidence base
uncontaminated by backfill.

### 8.3 Migration path to the Market Memory Layer

```
raw_artifacts/                  (immutable source truth)
        |
        v
HistoricalCandle                (validated, normalized -- THIS PHASE)
        |                                        Layer 0 live observations
        |                                                  |
        +---------------------+----------------------------+
                              |
                              v
                   Multi-Timeframe Engine        (17H.5 -- aggregation only:
                              |                   1D->1W->1M; never the reverse)
                              v
                    Market Memory Layer          (17H.6+ -- similarity/comparison)
```

Three properties this schema guarantees for that path:

1. **Aggregation is always upward.** 17H.5's stated rule (raw → aggregate,
   never "downloaded 5m → create daily") is enforceable because
   `timeframe` is explicit per row and `source_epoch` is preserved — a
   1D bar built from 1m bars and a 1D bar fetched directly are
   distinguishable, never silently equivalent.
2. **Derived timeframes are themselves derived records** — they belong
   in the existing `market_timeseries` derived tier with its
   `calc_version`/`materializer_id` provenance, **not** back into
   HMRL. HMRL holds only what a source asserted.
3. **The Memory Layer reads, never writes.** Phase 17H.6's comparison
   engine consumes this store and produces its own records elsewhere.
   Reality stays immutable, exactly as the live pipeline already
   guarantees.

---

## Open items before Phase 17H.3 / 17H.4

| # | Item | Blocking? |
|---|---|---|
| 1 | Certification policy for `fyers_historical` (Part 4.6) | **Yes** — fail-closed gating would block all ingestion |
| 2 | Storage engine confirmation (Part 3) — SQLite now vs DuckDB now | **Yes** — determines what 17H.3 builds |
| 3 | `cont_flag=1` back-adjustment behaviour (Part 7.5) | No for Spot/VIX; **yes** before futures ingestion |
| 4 | Intraday historical depth + range limits (17H.1 §1.5) | No — 17H.4 targets daily |
| 5 | NSE archive retention depth; Bhavcopy downloader (17H.1 §2.2–2.3) | No — Bhavcopy is later work |

**Nothing here blocks Phase 17H.4's stated first target (NIFTY Spot
Daily)** once items 1 and 2 are decided.
