# Options Observation Domain v1 — Engineering Series 73C

## 1. Philosophy

**This module performs zero reasoning.** PCR (put-call ratio), max
pain, gamma exposure, delta, theta, vega, IV rank, OI buildup/
unwinding, dealer positioning, liquidity intelligence, support/
resistance, and trend classification belong to future MSI brains,
never here.

`bujji/options_observation/` is the second concrete domain built on
top of the domain-neutral Market Observation Contract
(`bujji/market_observation/`, Engineering Series 73A / MOC v1). It
records observed Options market facts — one contract's OHLC,
settlement, volume, open interest, change in open interest, underlying
price, and (schema-present but, from this source, always-absent)
bid/ask/bid-quantity/ask-quantity — exactly as published in NSE's own
Bhavcopy, with every genuine gap disclosed and nothing fabricated. It
classifies nothing, computes no ratio, and never decides whether an
option chain is "bullish" or "bearish".

## 2. Relationship to MOF (Series 72) / MOC (Series 73A) / Futures Observation Domain (Series 73B)

`docs/MOF_V1_FOUNDATION.md` (Series 72) defines four layers:
Observation → Derived Evidence → Intelligence → Decision. MOC v1
(Series 73A, `bujji/market_observation/`) implements ONLY the
Observation layer, domain-neutrally — none of it aware of what an
"Option" concept even is.

`bujji/futures_observation/` (Series 73B) was the first concrete
domain to populate that generic contract, and is this module's direct
structural template: same 8-file layout (`taxonomy.py`, `models.py`,
`config.py`, `engine.py`, `serialization.py`, `query.py`, `runner.py`,
`journal.py`), same house conventions (plain string-constant
taxonomies, never `enum.Enum`; wrap-never-subclass; MAPPING-shaped
payload for a multi-field row; one series per contract;
`instrument` = contract-specific symbol).

Per `docs/MARKET_OBSERVATION_CONTRACT.md` Section 5 (Extension rules),
`bujji/options_observation/` **wraps** MOC's `Observation` /
`ObservationSeries`, exactly as 73B does — it never subclasses or
mutates them, and it never mints its own `observation_id` — only
`bujji.market_observation.engine.build_observation` does that.

## 3. Observation lifecycle for this domain

1. **Acquisition** (outside this module): a real NSE Bhavcopy F&O CSV
   file already on disk (`/tmp/m1/BhavCopy_NSE_FO_*.csv`).
2. **Parsing / Normalization** (`runner.parse_options_bhavcopy_csv`,
   `runner.build_option_observation_from_row`): select rows where
   `FinInstrmTp` is an option type (`STO`/`IDO`) for the requested
   underlying, parse each field to a plain float (or `None` if blank —
   never a fabricated default). Bid/ask/bid-quantity/ask-quantity are
   always passed as `None` from this path — see Section 5.
3. **Observation** (`engine.build_option_observation` →
   `bujji.market_observation.engine.build_observation`): mint one
   `Observation` (MAPPING-shaped payload) per option row, wrapped as an
   `OptionObservation`.
4. **Validation** (`engine.validate_option_observation`): MOC's own
   structural checks (identity/timestamp/schema/quality-range) plus
   options-domain checks (`strike`, `expiry`, `option_type`,
   `underlying`, `instrument_symbol` non-empty; `option_type` must be
   one of `taxonomy.ALL_OPTION_TYPES`). Never validates whether a
   price/OI/strike value is "reasonable".
5. **Assembly** (`engine.new_option_series` /
   `engine.append_option_observation` /
   `runner.ingest_option_observations_from_bhavcopy` /
   `runner.ingest_all_option_series_from_bhavcopy`): observations for
   one (underlying, strike, expiry, option_type) contract are assembled,
   in file order, into an `OptionObservationSeries`, with MOC's own
   ordering/gap logic (`bujji.market_observation.engine.append_observation`)
   doing all the work — never re-implemented here.
6. **Journaling** (optional, `journal.OptionsObservationJournal`):
   append-only JSONL record of every observation/series built, plus
   validation failures, duplicates, schema mismatches, and ordering
   anomalies.

## 4. Identity vs. Value design decision

This is the single most important design decision in this module.

**Identity-shaping** (threaded through MOC's `ObservationIdentity` plus
this wrapper's own explicit fields, and therefore part of
`observation_id`'s content hash): `strike`, `expiry`, `option_type`,
`underlying`, `timestamp`, `instrument` (the contract-specific symbol,
e.g. `ABCAPITAL26JUL360PE`). An option **contract** is defined by
underlying + strike + expiry + option_type — these fields identify
*which* fact is being recorded.

**Value-shaping** (carried inside `ObservationValue.payload`, and
*also* part of the content hash, per MOC's own `build_observation`,
which hashes identity fields + value together): `OPEN`, `HIGH`, `LOW`,
`CLOSE`, `SETTLEMENT`, `VOLUME`, `OPEN_INTEREST`,
`CHANGE_IN_OPEN_INTEREST`, `UNDERLYING_PRICE`, `BID`, `ASK`,
`BID_QUANTITY`, `ASK_QUANTITY` — the observed *facts* about the
already-identified contract at the already-identified timestamp.

**Consequence, deliberately accepted**: two Observations of the same
contract at the same timestamp but with *different* values (e.g. a
revised Bhavcopy re-publish) get *different* `observation_id`s, because
value is part of the content hash. This mirrors 73B's own resolution
of the identical question for futures, and MOC's own design note
(`bujji/market_observation/models.py`): quality metadata never affects
identity, but a genuine value difference is a genuine different fact.
The correction is tracked as a new version of the same underlying
contract-fact via `ObservationProvenance.version`
(MOC's `ObservationVersion` concept), never via identity collision or
silent overwrite.

## 5. Real schema and fields — Bhavcopy evidence

Evidence gathered on 2026-07-24 via `ssh root@139.59.76.137` inspection
of `/tmp/m1/BhavCopy_NSE_FO_0_0_0_20260525_F_0000.csv` (same file
family 73B inspected for futures rows):

```
Header: TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,
SctySrs,XpryDt,FininstrmActlXpryDt,StrkPric,OptnTp,FinInstrmNm,
OpnPric,HghPric,LwPric,ClsPric,LastPric,PrvsClsgPric,UndrlygPric,
SttlmPric,OpnIntrst,ChngInOpnIntrst,TtlTradgVol,TtlTrfVal,
TtlNbOfTxsExctd,SsnId,NewBrdLotQty,Rmks,Rsvd1,Rsvd2,Rsvd3,Rsvd4
```

Two real option rows confirmed (`FinInstrmTp=STO`, `TckrSymb=ABCAPITAL`):
```
2026-05-25,...,STO,...,2026-07-28,...,360.00,PE,ABCAPITAL26JUL360PE,0.00,0.00,0.00,37.10,0.00,37.10,363.65,20.70,0,0,0,0.00,0,F1,3100,,,,,
2026-05-25,...,STO,...,2026-06-30,...,360.00,CE,ABCAPITAL26JUN360CE,16.05,18.35,15.75,16.75,16.75,14.55,363.65,16.75,899000,548700,518,604433195.00,352,F1,3100,,,,,
```

| Field | Source Bhavcopy column | Mandatory? | Available? |
|---|---|---|---|
| `STRIKE` (identity) | `StrkPric` | yes | yes |
| `EXPIRY` (identity) | `XpryDt` | yes | yes |
| `OPTION_TYPE` (identity, CE/PE) | `OptnTp` | yes | yes |
| `OPEN` | `OpnPric` | yes | yes |
| `HIGH` | `HghPric` | yes | yes |
| `LOW` | `LwPric` | yes | yes |
| `CLOSE` | `ClsPric` | yes | yes |
| `SETTLEMENT` | `SttlmPric` | yes | yes |
| `VOLUME` | `TtlTradgVol` | yes | yes |
| `OPEN_INTEREST` | `OpnIntrst` | yes | yes |
| `CHANGE_IN_OPEN_INTEREST` | `ChngInOpnIntrst` | yes | yes |
| `UNDERLYING_PRICE` | `UndrlygPric` | no | yes |
| `BID` | *(none)* | no | **NO — see below** |
| `ASK` | *(none)* | no | **NO — see below** |
| `BID_QUANTITY` | *(none)* | no | **NO — see below** |
| `ASK_QUANTITY` | *(none)* | no | **NO — see below** |

### Confirmed gap: BID / ASK / BID_QUANTITY / ASK_QUANTITY

The 34-column header above contains **no bid, ask, bid-quantity, or
ask-quantity column of any kind**. This was confirmed directly against
a real file (not assumed from memory or from 73B's futures-row
evidence alone), and it matches three independent, already-existing
findings in this codebase:

1. 73B's own equivalent evidence for futures rows in the same file
   family (`bujji/futures_observation/taxonomy.py`).
2. `bujji/replay/option_chain_ingestion.py`'s own module docstring:
   *"Bid/ask: left `None` in every `OptionLiquiditySnapshot` produced
   here — bhavcopy has no bid/ask column, and this sprint's own
   explicit non-goals forbid fabricating one."*
3. `bujji/replay/historical_session.py`'s `OptionLiquiditySnapshot`
   dataclass (Series 64/65), whose `bid: Optional[float] = None` /
   `ask: Optional[float] = None` fields are populated as `None`
   unconditionally by `option_chain_ingestion.build_session_record`
   for every real Bhavcopy-sourced snapshot — not documented there as
   an EOD-settlement approximation of a real value, but as a genuinely
   unpopulated field, for the same reason: the source has no such
   column. `OptionLiquiditySnapshot` also has no `bid_quantity` /
   `ask_quantity` fields at all (this domain's schema is a superset in
   that respect).

Reconciliation: this module's finding is fully consistent with, and
strengthens (with a second independent real-file check), the project's
existing documented position. `BID`, `ASK`, `BID_QUANTITY`, and
`ASK_QUANTITY` **exist in `OptionObservation`'s schema** (a future
live/L2 market-data source could one day populate them) but **always
resolve to `None` for Bhavcopy-sourced `OptionObservation`s**, and are
always recorded in `ObservationQualityMetadata.missing_fields` (see
`taxonomy.KNOWN_UNAVAILABLE_FROM_BHAVCOPY`) — disclosed, never
fabricated, never silently dropped from the schema. They are excluded
from `taxonomy.MANDATORY_OPTIONS_OBSERVATION_FIELDS` (and therefore do
not depress `completeness`) precisely because their absence is a known
structural property of this source, not a genuine per-row data-quality
signal.

## 6. Validation

`engine.validate_option_observation` delegates entirely to
`bujji.market_observation.engine.validate_observation` for structural
checks (identity fields, timestamp well-formedness, schema-version
match, quality-metadata ranges, closed-vocabulary membership), then
adds options-domain checks:

- `MISSING_STRIKE` — `strike` is `None`.
- `MISSING_EXPIRY` — `expiry` is empty.
- `MISSING_OPTION_TYPE` — `option_type` is empty.
- `UNKNOWN_OPTION_TYPE` — `option_type` is not `CE`/`PE`.
- `MISSING_UNDERLYING` — `underlying` is empty.
- `MISSING_INSTRUMENT_SYMBOL` — `instrument_symbol` is empty.

Additional checks exercised by `tests/test_options_observation_domain.py`:
duplicate detection (same identity+timestamp+value reproduces the same
`observation_id`, detectable via journal), series-ordering (strict
monotonic timestamp order, with out-of-order appends recorded as an
explicit `OUT_OF_ORDER_SKIPPED` gap rather than silently dropped, via
MOC's own `append_observation`), and schema compatibility
(`schema_version` mismatch surfaces as `SCHEMA_VERSION_MISMATCH`).
**Never** validates whether a strike, price, or OI value is
"reasonable" — that is Derived Evidence/Intelligence work, out of
scope here.

## 7. Non-goals — explicitly out of scope for this module

This module (and any future revision of it) will **never** compute or
derive:

- **PCR** (put-call ratio)
- **OI Walls**
- **OI Build-up** / **OI Unwinding**
- **Gamma Exposure**
- **Max Pain**
- **Dealer Positioning**
- **Liquidity Intelligence**

These, along with delta/theta/vega, IV rank, support/resistance, and
trend classification, are explicitly reserved for future MSI
(Derived Evidence / Intelligence layer) modules built *on top of* this
domain's `OptionObservation`/`OptionObservationSeries` records — never
inside `bujji/options_observation/` itself.

## 8. Extension rules (inherited from MOC / 73B, unchanged)

1. Wrap, never subclass or mutate MOC's `Observation`/`ObservationSeries`.
2. Never introduce a new per-domain Identity/Value type; if a payload
   doesn't fit `SCALAR`/`OHLC`/`MAPPING`/`TEXT`, add one more
   `value_kind` to MOC's own closed set — never a subclass.
3. Only `bujji.market_observation.engine.build_observation` may mint an
   `observation_id`.
4. Closed vocabularies are plain string constants collected into
   `ALL_*` tuples, never `enum.Enum`.
5. A genuinely missing/unavailable field is recorded in
   `ObservationQualityMetadata.missing_fields`, never fabricated, never
   silently omitted from the schema.

## 9. Replay compatibility

Ingesting the same Bhavcopy file for the same (underlying, strike,
expiry, option_type) contract twice produces byte-identical
`OptionObservationSeries` JSON (`serialization.option_series_to_json`)
— verified in
`tests/test_options_observation_domain.py::TestReplayCompatibility`.
No wall-clock read, no `uuid4()`, no unseeded randomness anywhere in
`bujji/options_observation/` (enforced by the AST isolation tests in
the same file).
