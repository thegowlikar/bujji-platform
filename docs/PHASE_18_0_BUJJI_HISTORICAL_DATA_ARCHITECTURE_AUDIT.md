# Phase 18.0 — Bujji Historical Data Architecture Audit

**Status: AUDIT ONLY. No code written, no storage created, no
architecture modified.** Every claim below is backed by a direct
query, `grep`, or file read against the real VPS repository and its
real databases — evidence, not assumption, per this phase's own rule.

---

## 1. Data Lineage Audit

**Verdict: strong for spot/futures/VIX, one real gap for options.**

`HistoricalLineage` (`bujji/historical_reality/models.py`) carries, on
every record: `source`, `access_method`, `source_epoch`,
`source_symbol`, `raw_artifact_ref`, `ingestion_run_id`, `retrieved_at`,
`certification_status`, `certification_ref`, `continuity_method`,
`schema_version`. This answers every one of the audit's five questions
— source, producer, capture time, market-valid time (via the paired
`Observation.timestamp`), and reproducibility — **for every record that
has a real `raw_artifact_ref`.**

Checked directly (`grep raw_artifact_ref` across all 7 historical
writers): **6 of 7 populate a real path** into
`data/historical_reality/raw_artifacts/` (49MB on disk, confirmed) —
spot, futures, VIX, daily and intraday. **The 7th, options
(`capture_options_reality_session.py:196`), hardcodes
`raw_artifact_ref=""`** — the raw FYERS `optionchain` JSON response is
never persisted anywhere. `find ... -iname '*option*'` in the raw
artifacts directory returned zero files, confirmed.

**Consequence**: options records satisfy 4 of 5 lineage questions
(source, producer, capture time, market time) but **cannot be
independently re-verified against the original API response** — if a
transformation bug is later suspected in how a payload field was
mapped, there is no raw artifact to replay it against, unlike every
other instrument. This is a real, addressable gap, not a structural
flaw (the mechanism — `raw_artifact_ref` — already exists and works;
options simply never wrote to it).

## 2. Point-In-Time Correctness Audit

**Verdict: no fabrication risk found, but two real precision/process
gaps identified.**

- **Conflict handling protects against silent future leakage**:
  `HistoricalObservationStore.write()` raises
  `ConflictingHistoricalObservationError` (confirmed at
  `store.py:134`) rather than silently overwriting a value under the
  same natural key. This means a same-day intraday-candle revision
  from FYERS is never silently swapped in later — but it also means
  **Bujji has no revision-tracking mechanism**: whichever value is
  captured first is permanent; a later, more-final print for the same
  minute is rejected as a conflict (logged as an error) rather than
  recorded as a correction. For backtesting, this is actually the
  *safer* failure mode (no silent look-ahead), but it means Bujji
  cannot currently distinguish "provisional intraday print" from
  "settled/finalized value" — worth a future decision, not urgent.
- **`market_reality/replay.py` already implements bitemporal,
  no-look-ahead bounds** (`as_of_event_time`, `as_of_knowledge_time`,
  Phase 17F.1.2) for the Layer 0 event stream — this is exactly the
  machinery point-in-time correctness needs, and it already exists,
  live-verified in code, not proposed. It only covers Layer 0's raw
  event stream, not the Historical Reality store or
  `MarketRealitySnapshot` (see §3) — the bound-checking concept is
  proven but not yet extended to every store.
- **Options intra-cycle timestamp coarsening**: `capture_ts` is
  computed **once** at the start of `_capture_one_cycle` and reused
  for all 18 expiries × ~2,190 contracts, even though the real FYERS
  calls for those 18 expiries execute sequentially and can span real
  wall-clock seconds. Every row in one 5-minute cycle carries an
  identical nominal timestamp regardless of its true fetch moment.
  Not a leakage risk (all fetches complete well within the 5-minute
  window), but a real precision limit if a future backtest ever needs
  sub-5-minute intra-cycle ordering across expiries.
- **Futures rollover / continuity is a real, structural point-in-time
  risk** — detailed fully in §5, because it is fundamentally an
  identity problem, not a timestamp problem: Bujji's only historical
  futures series is FYERS's own **synthetic continuous contract**
  (`cont_flag=1`), never the literal price of the actual traded expiry
  contract on that date.

## 3. Historical Replay Capability Audit

**Verdict: partial. Two real reconstruction engines already exist —
neither can currently answer the full question this phase poses.**

Two genuine, working reconstruction paths were found (not proposed —
read directly):

- **`market_reality_snapshot/reconstruction.py` + `builder.py`**
  (Phase 17H.5/17H.7): `build_market_reality_snapshot(date)` combines
  historical + live Reality into one `MarketRealitySnapshot`. Read
  `builder.py` directly: it covers exactly **three** instruments —
  `SPOT_SYMBOL = "NSE:NIFTY50-INDEX"`, `VIX_SYMBOL =
  "NSE:INDIAVIX-INDEX"`, `FUTURES_CONTINUOUS_IDENTITY =
  "NIFTY_FUT_CONTINUOUS"` — at **`RESOLUTION_DAILY` only**. No options,
  no futures depth/OI, no intraday granularity anywhere in this path.
- **`market_reality/replay.py`**: deterministic Layer 0 event replay
  with bitemporal bounds — but Layer 0 (`layer0_data/raw_observations.jsonl`)
  currently holds only **841 real rows total**, spanning just
  2026-08-13 to today (confirmed by reading the first record's
  timestamp), and is a flat, unindexed, append-only JSONL file — no
  timestamp index, no query API faster than a full linear scan.

**Running this phase's own example** — "NIFTY market state at 10:35 on
14-Aug-2026" — against what actually exists today:

| Component | Available at 10:35 IST resolution? | Evidence |
|---|---|---|
| Spot price/OHLC/volume | 🟡 5-min candles exist (`NSE:NIFTY50-INDEX FIVE_MINUTE`, live-queried, 168,119 rows back to 2017-07-17) but no reconstruction engine currently reads at 5-min resolution — only the daily-only snapshot builder exists | Real data present; query path missing |
| Futures price/OI | 🟡 Same — 5-min continuous-contract candles exist (157,889 rows, 2018-02-01 onward) but reflect a *synthetic* series, not the real traded contract (§5) | Real caveat, not fabrication |
| Futures depth/liquidity | 🔴 Only in Layer 0's 841-row JSONL, today's date only, no historical retention proven | Confirmed by direct row count |
| Options chain/strikes/premium/OI | 🟡 Real 5-min data exists (162,150 rows, 2,190 contracts) but **only from 2026-08-14 onward** — 10:35 on 14-Aug-2026 IS covered (today), but no date before that is | Confirmed live query |
| Options bid/ask | 🟢 Present in the same rows | Confirmed |
| India VIX | 🟡 5-min candles exist (168,082 rows, 2017-07-17 onward) — same query-path gap as spot | Confirmed |

**Honest answer**: for **today's date specifically**, every raw fact
needed for a 10:35 reconstruction physically exists in the databases.
For **any date before 2026-08-14**, options are structurally absent
(historically unobtainable, per Phase 17I.6's own finding, correctly
not fabricated). And for **no date, including today**, does a single
query interface currently stitch spot+futures+options+VIX together at
5-minute resolution — the only working reconstruction engine
(`market_reality_snapshot`) is daily-only and 3-instrument-only. This
is a real, load-bearing gap for §3's own worked example.

## 4. Data Model Audit

**Verdict: mostly compatible, one confirmed hidden semantic-debt
pattern, and one genuinely serious architectural duplication.**

| Model | A/B/C/D |
|---|---|
| `RawObservation` (Layer 0) | A — compatible, live-verified single source of truth for real-time capture |
| `HistoricalObservation` | A — compatible, reused unmodified across 8 real writers (spot/futures/VIX × daily/intraday + options), same `build_observation()` identity minting throughout |
| `MarketRealitySnapshot` | A/D — compatible as far as it goes, but §3 shows it is missing options and intraday resolution as concepts, not just as unfilled data — a real "D" (missing important concepts), not just an empty result |
| `MarketObservation` (MOC, `market_observation/models.py`) | A — the shared canonical schema every store above serializes through; no duplication found |
| Option observation models | **C — conflicting/duplicated, see finding below** |
| Futures depth models | A — Layer 0 `MARKET_DEPTH` type (schema 1.1.0, added Phase 17E) is the only futures-depth model; no duplicate found |
| Market state models | A — not independently re-audited in depth this phase (out of the historical-data-lake scope this phase asks for); no contradiction surfaced in the areas actually touched |

**Confirmed hidden semantic debt, same pattern as Phase 17I.11's
`value_kind=OHLC` finding, found by re-running the identical audit
technique**: `bujji/options_observation/` (the pre-existing, MOC-wrapped,
49/49-tested but never-persisted options domain from Series 73C,
audited in Phase 17I.5) and `bujji/historical_reality/` +
`capture_options_reality_session.py` (this project's actual, live,
persisted options writer, Phase 17I.10) are **two structurally separate
option observation models that were never reconciled** — 17I.9's own
readiness audit explicitly chose to bypass `options_observation`
entirely rather than resolve the duplication. This was a disclosed,
deliberate choice at the time (documented in `PHASE_17I9`), not a new
finding — but it remains real, live semantic debt today: a future
engineer searching for "the" options model will find two, and only one
is actually wired to real storage.

**A more serious, previously undocumented finding**: `bujji/replay/`
(`historical_session.py`, `corpus_builder.py`, `option_chain_ingestion.py`)
plus `bujji.qualification.historical_runner` form a **second, entirely
separate historical replay pipeline** — an older one (Series 46–64,
from the pre-Trading-Brain "qualification campaign" era per this
project's own memory), built on its own schema
(`HistoricalSessionRecord` → `ReplayScenario`), completely disconnected
from the Reality-tier architecture (`HistoricalObservation`,
`MarketRealitySnapshot`) built afterward in Phase 17H+. Confirmed via
`grep`: **zero references** to `bujji.replay` or
`HistoricalQualificationRunner` exist anywhere in
`bujji/trading_brain/`, `bujji/shadow_runtime/`, or
`bujji/production_runtime/` — this pipeline is structurally orphaned
relative to everything the project currently runs live. This is a real
architectural risk for Phase 18+: **two parallel, non-interoperating
"replay" concepts exist in the same codebase**, with different schemas,
different assumptions, and no bridge between them. Any future
backtesting engine must explicitly choose one (the Reality-tier one —
see §9) rather than accidentally building on top of, or alongside, the
orphaned one.

## 5. Identity Integrity Audit

**Verdict: options and spot/VIX are solid; futures carries a real,
structural identity risk.**

- **Spot** (`NSE:NIFTY50-INDEX`): single, stable, unchanging identity
  string for the index itself — no rollover/expiry concept applies,
  confirmed no special-casing needed anywhere.
- **Options**: identity is `underlying|expiry_iso|strike|option_type`
  — deliberately excludes `fyToken`/broker symbol (Phase 17I.10),
  confirmed live to survive new-strikes-appearing (14 new identities
  observed intraday on 2026-08-14, §3's own live-capture evidence) and
  structurally cannot suffer "rollover" ambiguity, because each
  contract is terminal by design (no continuity concept needed,
  confirmed in Phase 17I.9). **Not yet tested against an actual live
  expiry event** (no contract in the captured set has expired yet as
  of this audit) — an inference, not a directly observed fact, exactly
  as Phase 17I.9 itself already disclosed.
- **Futures — the one real structural risk found this phase**:
  `INSTRUMENT_IDENTITY = "NIFTY_FUT_CONTINUOUS"`
  (`ingest_nifty_futures_daily_historical.py:43`), built via FYERS's
  `cont_flag=1`. This is confirmed, by the FYERS SDK's own documented
  behavior and this project's own code comment
  (`ingest_nifty_futures_daily_historical.py:7-14`), to be a
  **broker-computed synthetic series spliced/adjusted across
  rollovers — not the literal price of any single real, traded
  contract**. Bujji's historical futures store contains **only** this
  synthetic series (2,131 daily rows since 2018-01-02; 157,889 5-min
  rows since 2018-02-01, both live-queried) — **it has never captured
  a real, individually-identified expiry contract's own historical
  price series.** Meanwhile, live futures depth (Phase 17I.2) captures
  against the actual current front-month contract symbol in real time
  — a **different identity model** than the historical continuous
  series. A future backtest spanning past-into-present would silently
  stitch together two structurally different representations of
  "the future" (broker-adjusted synthetic history vs. a real traded
  contract going forward) with no documented reconciliation — this is
  a genuine, not-yet-addressed risk for any strategy whose PnL depends
  on the literal price level a real contract traded at (vs. a
  regime/direction read, which the continuous series serves fine).

## 6. Resolution Audit

**Verdict: 5-minute is a reasonable default for regime/direction
strategies; genuinely insufficient for several named use cases, and
"5-minute" already understates real coverage for spot/VIX/futures.**

Directly queried, real coverage (not the phase prompt's own
approximation):

| Instrument | Daily | 5-min |
|---|---|---|
| Spot | 1998-05-04 → today (7,041 rows) | 2017-07-17 → today (168,119 rows) |
| VIX | 2008-04-17 → today (4,524 rows) | 2017-07-17 → today (168,082 rows) |
| Futures (continuous) | 2018-01-02 → today (2,131 rows) | 2018-02-01 → today (157,889 rows) |
| Options | none | 2026-08-14 only (one session, 162,150 rows) |

5-minute is genuinely **insufficient**, confirmed against the specific
use cases this phase names:

- **Scalping / ORB (Opening Range Breakout)**: both need candle-close
  granularity inside the first 5–15 minutes of the session; a single
  5-minute bar cannot resolve an ORB break that happens mid-candle.
- **VWAP**: technically computable from 5-min bars but materially less
  accurate than tick/1-min — VWAP is defined as a volume-weighted
  average across trades, and 5-min OHLCV bars discard the actual trade
  sequence within each bar.
- **Options execution modeling**: bid/ask at 5-min intervals cannot
  reconstruct realistic slippage/fill simulation — a real fill depends
  on the depth ladder and spread at the literal moment of an order,
  not a 5-minute-old snapshot.
- **Liquidity analysis**: needs the depth ladder itself (5-level
  bid/ask, already captured live for futures at 385 cycles/day — real,
  but not yet historically retained beyond Layer 0's short JSONL
  buffer, per §3).

## 7. Commercial Backtesting Readiness

**Verdict: not ready. No mechanism found for any of the seven
capabilities audited; this is the least-built area of the entire
pipeline.**

Checked directly for evidence of each capability — none found:

- **User-selected dates**: no query interface accepts an arbitrary
  date range today; `market_reality_snapshot` takes a single `date`
  but only surfaces daily, 3-instrument data (§3).
- **User-selected strategies / reproducible results / no future
  leakage**: the bitemporal bound machinery in `market_reality/replay.py`
  is the right primitive for "no leakage," but it is wired to Layer 0
  only, not the Historical Reality store most backtest data would
  actually come from.
- **Multi-user**: no `user_id`/tenant concept found anywhere in any
  store schema audited this phase (`HistoricalObservationStore`,
  `RawObservationStore`, `MarketRealitySnapshot`) — every store is
  single-tenant by construction today.
- **Strategy comparison / equity curve generation / trade replay**:
  `bujji.replay` + `bujji.qualification.historical_runner` (§4) is the
  only code in the repository that resembles a strategy-replay runner,
  and it is structurally orphaned from the current live architecture
  — not a usable foundation without a deliberate integration decision.

**This is expected, not alarming**: nothing in Phases 17H–17I.11 was
ever scoped to build commercial multi-tenant infrastructure — Reality
capture was always the stated, narrower goal. This section exists to
make the real distance explicit before Phase 19+ assumes otherwise.

## 8. Market Data Lake Recommendation

No implementation designed — classification and boundary definition
only, per this phase's own restriction.

| Layer | Status | Basis |
|---|---|---|
| **Reality Layer** (raw capture, lineage, certification) | 🟢 for spot/futures/VIX; 🟡 for options (missing raw artifacts, §1); 🔴 for futures depth history (Layer 0 only, no durable retention proven, §3) | Direct row counts and file checks throughout this audit |
| **State Layer** (point-in-time reconstruction, "what did the market look like") | 🟡 — real, working machinery exists (`market_reality_snapshot`, `market_reality/replay.py`'s bitemporal bounds) but covers only 3 instruments at daily resolution; no 5-min, no options, no depth | §2, §3 |
| **Feature Layer** (research-ready derived features for strategies) | 🔴 — not audited in depth this phase (out of scope: this phase covers Reality→State, not Understanding→Intelligence), and no evidence of a feature layer purpose-built for backtesting (as opposed to live decisioning) was found | Scope boundary, stated explicitly |
| **Backtest Layer** (the future consumer) | 🔴 — the one candidate implementation (`bujji.replay`) is orphaned from the current architecture (§4); no live-architecture-integrated backtest runner exists | §4, §7 |

**Boundary definition, based on what was actually found working
today**: Reality ends at `HistoricalObservation`/`RawObservation` —
literal, lineage-complete facts, one store per instrument-shape,
already proven at real scale (500k+ rows across spot/futures/VIX,
verified live). State begins at `MarketRealitySnapshot` — the first
point where multiple Reality sources are combined into "what the
market looked like," and is the correct, already-chosen home for
point-in-time reconstruction; it should be extended (options, 5-min
resolution, depth) rather than replaced. Feature and Backtest layers
do not yet exist as Reality-tier-integrated concepts — building them
directly against `bujji.replay`'s orphaned schema would repeat the
`value_kind=OHLC` and options-model-duplication mistakes at a much
larger scale; they should be built as new consumers of
`MarketRealitySnapshot`, once §3's coverage gaps are closed, not as
descendants of the older pipeline.

## 9. Final Decision

**B — minimum data architecture work must happen first.** Not because
the foundation is weak (it is not — the lineage discipline, bitemporal
replay bounds, and reuse-not-duplicate architecture proven across
17H–17I.11 are genuinely institutional-grade where they've been
applied), but because three specific, concrete gaps would each cause a
real, silent correctness failure if a backtesting engine were built
today without addressing them first:

1. **Futures identity mismatch** (§5) — a backtest computing PnL off
   the continuous series would be pricing against a synthetic,
   broker-adjusted number, not a real contract's real price. This is
   the single highest-risk finding in this audit.
2. **State-layer coverage gap** (§3, §8) — no existing reconstruction
   engine can answer this phase's own worked example
   ("NIFTY at 10:35 on 14-Aug") across all five required instrument
   groups at intraday resolution; extending `market_reality_snapshot`
   (options + 5-min + depth) is a bounded, well-scoped piece of work
   building on a design that already works for 3 of 5 instruments.
3. **Two orphaned/duplicated model pipelines** (§4) — `bujji.replay`
   vs. the Reality tier, and `options_observation` vs.
   `historical_reality` — must be explicitly resolved (deprecate the
   orphaned one, or document why it's kept) before a backtest engine
   is built on top of either, so the choice is deliberate rather than
   accidental.

None of this blocks continued Reality-layer work (e.g. closing the
options raw-artifact gap from §1) — it specifically blocks starting a
**backtesting engine**, which is what this phase was asked to gate.
