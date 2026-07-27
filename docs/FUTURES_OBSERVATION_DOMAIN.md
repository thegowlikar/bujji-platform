# Futures Observation Domain v1 — Engineering Series 73B

## 1. Philosophy

**This module performs zero reasoning.** Long build-up, short build-up,
short covering, long unwinding, basis interpretation, and trend
classification belong to future MSI brains, never here.

`bujji/futures_observation/` is the first concrete domain built on top
of the domain-neutral Market Observation Contract
(`bujji/market_observation/`, Engineering Series 73A / MOC v1). It
records observed Futures market facts — one contract's OHLC, volume,
open interest, change in open interest, settlement price, and a
derived basis number — exactly as published in NSE's own Bhavcopy, with
every genuine gap disclosed and nothing fabricated. It classifies
nothing, labels nothing "bullish"/"bearish", and never decides whether
a build-up is "long" or "short".

## 2. Relationship to MOF (Series 72) / MOC (Series 73A)

`docs/MOF_V1_FOUNDATION.md` (Series 72) defines four layers:
Observation → Derived Evidence → Intelligence → Decision. MOC v1
(Series 73A, `bujji/market_observation/`) implements ONLY the
Observation layer, domain-neutrally: `Observation`, `ObservationSeries`,
identity/quality/provenance/value, validation, gap detection —
none of it aware of what a "Futures" concept even is.

Series 73B is the first domain to actually populate that generic
contract with real futures facts. Per
`docs/MARKET_OBSERVATION_CONTRACT.md` Section 5 (Extension rules),
`bujji/futures_observation/` **wraps** MOC's `Observation` /
`ObservationSeries`, it never subclasses or mutates them, and it never
mints its own `observation_id` — only
`bujji.market_observation.engine.build_observation` does that, exactly
as before this sprint.

## 3. Observation lifecycle for this domain

1. **Acquisition** (outside this module): a real NSE Bhavcopy F&O CSV
   file already sitting on disk (e.g. `/tmp/m1/BhavCopy_NSE_FO_*.csv`).
2. **Parsing / Normalization** (`runner.parse_futures_bhavcopy_csv`,
   `runner.build_futures_observation_from_row`): select rows where
   `FinInstrmTp` is a futures type (`STF`/`IDF`) for the requested
   underlying, parse each field to a plain float (or `None` if blank —
   never a fabricated default).
3. **Observation** (`engine.build_futures_observation` →
   `bujji.market_observation.engine.build_observation`): mint one
   `Observation` (MAPPING-shaped payload) per futures row, wrapped as a
   `FuturesObservation`.
4. **Validation** (`engine.validate_futures_observation`): MOC's own
   structural checks (identity/timestamp/schema/quality-range) plus
   two futures-domain checks (`expiry`, `instrument_symbol` non-empty).
   Never validates whether a price/OI value is "reasonable".
5. **Assembly** (`engine.new_futures_series` /
   `engine.append_futures_observation` /
   `runner.ingest_futures_observations_from_bhavcopy` /
   `runner.ingest_all_futures_series_from_bhavcopy`): observations for
   one (underlying, expiry) contract are assembled, in file order, into
   a `FuturesObservationSeries`, with MOC's own ordering/gap logic
   (`bujji.market_observation.engine.append_observation`) doing all the
   work — never re-implemented here.
6. **Journaling** (optional, `journal.FuturesObservationJournal`):
   append-only JSONL record of every observation/series built, plus
   validation failures, duplicates, schema mismatches, and ordering
   anomalies.

## 4. Real schema and fields

### `taxonomy.py` — `FuturesObservationField` vocabulary (plain string
constants, matching this project's established closed-vocabulary
convention — see `bujji/market_observation/taxonomy.py`,
`bujji/runtime_safety/taxonomy.py` — not `enum.Enum`):

| Field | Source Bhavcopy column | Mandatory? |
|---|---|---|
| `OPEN` | `OpnPric` | yes |
| `HIGH` | `HghPric` | yes |
| `LOW` | `LwPric` | yes |
| `CLOSE` | `ClsPric` | yes |
| `VOLUME` | `TtlTradgVol` | yes |
| `OPEN_INTEREST` | `OpnIntrst` | yes |
| `CHANGE_IN_OPEN_INTEREST` | `ChngInOpnIntrst` | yes |
| `SETTLEMENT_PRICE` | `SttlmPric` | yes |
| `BASIS` | derived: `SETTLEMENT_PRICE - UndrlygPric` | no (optional) |

`observation_type` reuses MOC's own existing
`taxonomy.TYPE_FUTURES` constant (Series 73A's own 17-domain
`ObservationType` vocabulary already had a `FUTURES` entry) — this
module does not invent a parallel `ObservationType` enum.

### `models.py` — design decisions

* **Wrap, never subclass** (per Extension rule 1): `FuturesObservation`
  composes one `bujji.market_observation.models.Observation` plus two
  futures-domain-only fields (`expiry`, `underlying`) that MOC's
  generic `ObservationIdentity` has no concept of. Every OHLC/volume/OI
  accessor (`.open`, `.close`, `.volume`, `.open_interest`, `.basis`,
  …) reads through to the wrapped `Observation.value.payload` — there
  is exactly one source of truth, never a second copy.
* **`instrument` = contract symbol, not bare underlying.**
  `docs/MARKET_OBSERVATION_CONTRACT.md`'s own illustrative usage builds
  an `Observation` with `instrument="NIFTY-24000-CE-2026-07-30"` — a
  contract-specific string, not the bare underlying "NIFTY". This
  module follows that same convention:
  `Observation.identity.instrument` holds the Bhavcopy `FinInstrmNm`
  value (e.g. `"BANKNIFTY26JULFUT"`), and the bare underlying
  (`"BANKNIFTY"`) is threaded through separately as
  `FuturesObservation.underlying`, since MOC itself has no
  underlying-vs-contract distinction.
* **One `Observation` per futures row, `VALUE_KIND_MAPPING` payload**
  (per Extension rule 2: never add a per-domain Identity/Value type —
  reuse MOC's closed `value_kind` set, adding one more only if
  genuinely needed). A futures row is more than an OHLC bundle — it
  also carries volume, open interest, change in open interest,
  settlement price, and an optional basis — so `VALUE_KIND_MAPPING`
  (already part of MOC's `ALL_VALUE_KINDS`) was used, with `payload` a
  `Mapping[str, Optional[float]]` keyed by
  `taxonomy.ALL_FUTURES_OBSERVATION_FIELDS`. This was chosen over
  either (a) `VALUE_KIND_OHLC` — too narrow, would leave
  volume/OI/settlement/basis with nowhere principled to live — or (b)
  eight separate scalar `Observation`s per row — which would multiply
  `observation_id`s for what is, on the wire, a single recorded fact,
  and would complicate replay/dedup.
* **`FuturesObservationSeries`** is a thin wrapper over one
  `bujji.market_observation.models.ObservationSeries`
  (`observation_type=TYPE_FUTURES`, `instrument=<contract symbol>`),
  again composing rather than duplicating storage — all ordering/gap
  state lives in the wrapped `ObservationSeries`.

## 5. Which Deliverable-2 fields were NOT available in real Bhavcopy data

Verified against real files at `/tmp/m1/BhavCopy_NSE_FO_*.csv` on the
BUJJI host (2026-07-24). Header:

```
TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,SctySrs,
XpryDt,FininstrmActlXpryDt,StrkPric,OptnTp,FinInstrmNm,OpnPric,HghPric,
LwPric,ClsPric,LastPric,PrvsClsgPric,UndrlygPric,SttlmPric,OpnIntrst,
ChngInOpnIntrst,TtlTradgVol,TtlTrfVal,TtlNbOfTxsExctd,SsnId,
NewBrdLotQty,Rmks,Rsvd1,Rsvd2,Rsvd3,Rsvd4
```

A real `IDF` (index futures) row (BANKNIFTY, `/tmp/m1/BhavCopy_NSE_FO_0_0_0_20260525_F_0000.csv`):

```
2026-05-25,2026-05-25,FO,NSE,IDF,61088,,BANKNIFTY,,2026-07-28,2026-07-28,,,
BANKNIFTY26JULFUT,55150.00,56055.20,55100.20,55914.20,56055.20,54752.80,
55293.65,55914.20,100020,10800,2328,3880637598.00,1817,F1,30,,,,,
```

* **Open/High/Low/Close/Volume/OI/ChangeInOI/SettlementPrice**: ALL
  present and populated on every real futures row inspected
  (`OpnPric`, `HghPric`, `LwPric`, `ClsPric`, `TtlTradgVol`,
  `OpnIntrst`, `ChngInOpnIntrst`, `SttlmPric`). Nothing here is
  missing or needs a disclosed-gap decision at the schema level — a
  *specific* row could still have a blank value, which is handled at
  the row level via `missing_fields`, never at the schema level.
* **Basis/Premium/Discount**: **NOT a raw column.** There is no
  `Basis`/`Premium`/`Discount` column anywhere in the Bhavcopy header.
  However, every futures row also carries `UndrlygPric` (the
  underlying/spot settlement price) on the **same row**. Decision made:
  define `BASIS = SETTLEMENT_PRICE - UNDERLYING_PRICE`, a plain
  arithmetic difference between two already-raw fields on one row — not
  a two-observation combination, not a classification, and explicitly
  **not** labeled "premium" or "discount" (that framing — *"the future
  is trading at a premium, suggesting bullishness"* — is an
  interpretation, MSI's job, out of scope here). `BASIS` is a signed
  float, included in `MANDATORY_FUTURES_OBSERVATION_FIELDS`'s
  complement (i.e. it is **optional**, not mandatory) since
  `UndrlygPric` is not guaranteed populated on every row; when absent,
  `basis` is `None`, never `0.0`.

## 6. Validation (Deliverable 5)

`engine.validate_futures_observation` composes:

* MOC's own `bujji.market_observation.engine.validate_observation` —
  identity fields present, well-formed ISO-8601 timestamp, known
  `observation_type`/`resolution`/`value_kind`/`origin`, schema-version
  compatibility, quality-metadata ranges. Never checks the market value
  itself.
* Two futures-domain checks: `expiry` non-empty, `instrument_symbol`
  (i.e. `Observation.identity.instrument`) non-empty.

Missing mandatory OHLC/volume/OI/settlement fields are **never**
silently dropped: `engine.build_futures_observation` still constructs
the `FuturesObservation`, records every missing mandatory field in
`ObservationQualityMetadata.missing_fields`, sets `completeness` to the
fraction of mandatory fields actually present, and sets
`validation_status` to `INCOMPLETE` — the row exists and is queryable,
its gaps are just disclosed. Duplicate detection is available at two
levels: identical rows deterministically produce identical
`observation_id`s (content-hash based, never `uuid4()`), and
`FuturesObservationJournal.record_duplicate` provides an explicit
audit-trail channel for callers that detect a duplicate during
ingestion. Series ordering validity is delegated entirely to MOC's own
`append_observation`/`validate_series_ordering` — out-of-order appends
are recorded as an explicit `SeriesGap` with reason
`OUT_OF_ORDER_SKIPPED`, never silently absorbed.

## 7. Replay compatibility

`serialization.py` produces deterministic, `sort_keys=True` JSON for
both `FuturesObservation` and `FuturesObservationSeries`, reusing MOC's
own `observation_to_dict`/`observation_from_dict`/`series_to_dict`/
`series_from_dict` primitives for the wrapped MOC objects. Verified on
the live host: ingesting the same real Bhavcopy file
(`BhavCopy_NSE_FO_0_0_0_20260526_F_0000.csv`) twice produced
byte-identical serialized `FuturesObservationSeries` JSON both times
(see test suite's `TestReplayCompatibility`, and an ad hoc check run
during development that printed `byte-identical replay: True`).

## 8. Extension rules (for a future domain built on top of this one)

Anything downstream of this domain (MSI, a future Decision layer) must:

1. Consume already-built `FuturesObservation`/`FuturesObservationSeries`
   objects only — never construct one directly, never mint an
   `observation_id`.
2. Never mutate a `FuturesObservation`/`FuturesObservationSeries` — they
   are frozen dataclasses; build a new one via `engine.py`.
3. Never fold interpretation (build-up classification, basis
   "premium"/"discount" labeling, trend calls) into this module. That
   belongs to a future MSI brain layered on top, consuming these
   Observations as its raw input.
4. Follow the same wrap-don't-subclass discipline this module itself
   followed relative to MOC.

## 9. Real test results (Engineering Series 73B)

Run on the BUJJI host, `/opt/bujji/app`, `source /opt/bujji/.venv/bin/activate`:

* New file alone: `python -m pytest tests/test_futures_observation_domain.py -q`
  → **36 passed** (identity determinism, real-Bhavcopy ingestion across
  3 real files, serialization round-trip, validation incl. a
  malformed/incomplete row, series ordering incl. out-of-order gap
  recording, duplicate handling, replay compatibility — same file
  ingested twice byte-identical — query helpers, zero-interpretation
  identifier check, and AST isolation firewall).
* Full suite baseline (before this sprint's changes):
  `python -m pytest -q` → **2105 passed**.
* Full suite after this sprint: `python -m pytest -q` → **2141 passed**
  (2105 + 36 new, zero regressions).

## 10. Genuine ambiguities resolved with judgment

* **Whether `observation.identity.instrument` should hold the bare
  underlying or the contract-specific symbol.** MOC's own extension
  pseudocode in `docs/MARKET_OBSERVATION_CONTRACT.md` uses a
  contract-specific string for `instrument` (a full option contract
  identifier), so this domain followed that precedent: `instrument` =
  contract symbol (`"BANKNIFTY26JULFUT"`), with the bare underlying
  threaded through as a separate futures-domain field.
* **One `FuturesObservationSeries` per contract, not per underlying.**
  A single Bhavcopy day/file has several expiries (near/mid/far month)
  per underlying, and MOC's `ObservationSeries` is single-instrument by
  design. `runner.ingest_futures_observations_from_bhavcopy` returns
  one contract's series (first expiry by default, or a caller-selected
  one); `runner.ingest_all_futures_series_from_bhavcopy` returns every
  contract's series for a day in one call, for callers that want all
  of them.
* **`FuturesObservationField` as string constants, not `enum.Enum`.**
  Despite the sprint brief's phrasing ("FuturesObservationField enum"),
  every sibling `*_observation`/`_taxonomy` module in this codebase
  (including MOC's own `bujji/market_observation/taxonomy.py`) uses
  plain string constants collected into `ALL_*` tuples specifically to
  stay trivially JSON-serializable without a codec. This domain follows
  that established, dominant convention rather than introducing the
  first `enum.Enum`-based taxonomy in the `*_observation` family.
