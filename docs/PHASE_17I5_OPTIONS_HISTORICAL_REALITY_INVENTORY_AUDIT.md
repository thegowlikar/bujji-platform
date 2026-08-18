# Phase 17I.5 — Options Historical Reality Inventory Audit

**Status: AUDIT ONLY. No code, no schemas, no integration, no files
modified.** Full repository-wide discovery, every source read directly,
nothing assumed from memory of prior audits (including this thread's
own 17I.4, whose options finding this audit supersedes with a much more
precise result).

---

## 1. Repository-wide options data discovery

Filename search across every keyword in the instruction
(`option`/`bhavcopy`/`greeks`/`strike`/`NFO`, plus targeted content
greps for `IV`/`OI`/`derivatives`/`option snapshot`): **21 real files**
found, none previously fully catalogued together in one place.

| File | Role |
|---|---|
| `bujji/options_observation/` (8 files: taxonomy/models/config/engine/serialization/query/runner/journal) | **The primary find** — a full Reality-adjacent observation domain, Engineering Series 73C |
| `bujji/replay/option_chain_ingestion.py` | Bhavcopy → `HistoricalSessionRecord` parser (Series 59/64) |
| `bujji/replay/historical_session.py` | The `HistoricalSessionRecord` model this feeds |
| `bujji/market_perception/option_chain_adapter.py` | Live, per-cycle Shadow Campaign adapter (Intelligence-tier) |
| `bujji/market_state_builder/option_observation_bridge.py` | Bridges `options_observation` into the live MSI cycle |
| `bujji/intelligence/greeks_brain.py`, `bujji/market_perception/greeks_adapter.py` | Live Black-Scholes Greeks computation (Intelligence-tier) |
| `data/bhavcopy/*.csv` (4 files, 2026-07-27..30) | Real raw NSE bhavcopy source files |
| `data_certification/fyers_option_chain_certification.json`, `fyers_option_chain_discovery_20260813.json` | Live-quote certification/discovery (17F.5/17F.7.1) |
| `scripts/discover_option_chain_premium_fields.py`, `tools/find_nifty_options.py` | Discovery/utility scripts |
| `docs/OPTIONS_OBSERVATION_DOMAIN.md`, `docs/PHASE_16I_OPTIONS_INTELLIGENCE_INTEGRATION_AUDIT.md`, `docs/PHASE_15E_GREEKS_PREMIUM_BEHAVIOUR_REPORT.md`, `docs/OPTIONS_OS_RUNNER.md` | Prior design/audit documents |
| 8 test files (`test_options_observation_domain.py` 521 lines, `test_option_chain_ingestion.py` 104 lines, `test_greeks_brain.py`, `test_msi_greeks.py`, etc.) | Real, passing test coverage |

**No parquet files found. No sqlite database of persisted options
observations found anywhere** — confirmed by direct `find` across
`data/` for any options-named `.db`/journal file: none exist. Every
capability below is real, tested CODE with, at most, 4 real raw source
files on disk — never a populated historical corpus.

## 2. Classification of every discovered source

| Source | Exists | Data Type | Reality Quality |
|---|---|---|---|
| `bujji/options_observation/` | Yes (code); No (persisted data — journal file never written) | Wraps MOC `Observation`: strike, expiry, option_type, underlying (identity) + OHLC, settlement, volume, OI, change-in-OI, underlying_price, bid/ask/bid-qty/ask-qty (schema-present, always `None` from Bhavcopy) | **Reality-grade** — literal facts only, verified by direct contamination scan (§5) |
| `bujji/replay/option_chain_ingestion.py` → `HistoricalSessionRecord` | Yes (code + 4 real CSVs) | Strike, expiry, OI, change-in-OI, real bhavcopy row values | **Reality-grade** — same standard, own module docstring: "never fabricates a strike, an expiry... every value... read directly from a real bhavcopy row" |
| `bujji/market_perception/option_chain_adapter.py` | Yes (code, live-cycle only, no historical persistence) | Per-leg bid/ask/spread, per-strike OI, live | **Reality-grade facts, Intelligence-tier lifecycle** — the facts themselves are literal, but this module is a live per-cycle Shadow Campaign input, not a Reality-tier historical store |
| `bujji/intelligence/greeks_brain.py`, `greeks_adapter.py` | Yes (code) | Computed delta/gamma/theta/vega, IV solved via Newton-Raphson | **Derived/Intelligence-grade** — correctly named, correctly separate, never claims to be Reality |
| `data_certification/fyers_option_chain_certification.json` | Yes | One certified live quote+historical probe for one contract | **Reality-grade** (a certification artifact, same discipline as spot/futures/VIX certs) but proves ACCESS, not a populated corpus |

**No source found mixes Reality facts with PCR, max pain,
support/resistance, IV rank, signals, or strategy output in the same
data record.** Every place a derived concept appears, it appears in an
explicitly Intelligence-tier module (`greeks_brain`,
`msi_participant_positioning`, etc.), never inside
`options_observation`'s own models or `HistoricalSessionRecord`.

## 3. Historical depth assessment

### `bujji/options_observation/` (via `bujji/replay/option_chain_ingestion.py`'s bhavcopy path)

```
NIFTY / stock options (schema is underlying-agnostic; real evidence
sampled from ABCAPITAL rows, same file family used for NIFTY)

Real data on disk: 2026-07-27 → 2026-07-30 (4 trading days only)
Resolution: EOD (one row per contract per day — bhavcopy is a
            settlement snapshot, never intraday)
Instruments: any NSE F&O underlying present in the bhavcopy file
Expiries available: whatever expiries traded that day (bhavcopy-native,
            not filtered)
Strikes available: whatever strikes traded that day

Fields:
  strike               ✅
  expiry                ✅
  option_type (CE/PE)    ✅
  OPEN/HIGH/LOW/CLOSE     ✅
  SETTLEMENT               ✅
  VOLUME                    ✅
  OPEN_INTEREST              ✅
  CHANGE_IN_OPEN_INTEREST     ✅
  UNDERLYING_PRICE              ✅ (present, not identity-mandatory)
  BID / ASK / BID_QTY / ASK_QTY   ❌ — confirmed absent from the bhavcopy
                                     column set itself (34-column header
                                     inspected directly, no bid/ask
                                     column exists at all)
  IV                                ❌ — never computed here, correctly
  Greeks                             ❌ — never computed here, correctly
```

**No populated multi-day/multi-year historical corpus exists** — the
code can ingest a full bhavcopy archive if one is ever backfilled, but
today only 4 real days sit on disk, and no `HistoricalSessionRecord`
database file exists anywhere (confirmed: no `.db` under
`data/bhavcopy/` or `data/replay/`).

### `bujji/market_perception/option_chain_adapter.py` (live path)

Not a historical source at all — per-cycle only, no persistence beyond
the current Shadow Campaign run's own journal (not options-specific,
mixed with all Shadow Campaign telemetry). Zero historical depth to
report.

## 4. Architecture compatibility check

**`bujji/options_observation/` is the closest-to-compatible source
found, and the comparison is precise, not hand-waved:**

| Reality architecture requirement | `options_observation`'s actual state |
|---|---|
| Identity minted via `market_observation.engine.build_observation()` | ✅ **Already true** — confirmed by direct read of `OPTIONS_OBSERVATION_DOMAIN.md` §4 and `engine.py`: wraps MOC exactly as `futures_observation` (73B) does, never mints its own `observation_id` |
| Immutable identity, value-vs-identity separation | ✅ **Already correct** — strike/expiry/option_type/underlying/timestamp/instrument are identity; OHLC/OI/etc. are value; a value change (bhavcopy re-publish) correctly mints a NEW `observation_id`, same discipline as every Reality-tier store in this project |
| `CertificationGate` fail-closed write path | ❌ **Does not exist.** Zero references to `CertificationGate` anywhere in the package (grepped directly). Any write path built on this today would NOT be certification-gated the way `HistoricalObservationStore`/`RawObservationStore` both mandate. |
| Persistent store (`HistoricalObservationStore`-equivalent, SQLite, natural-key conflict detection) | ❌ **Does not exist.** The package has a `journal.py` (append-only JSONL, mirrors `MarketEventJournal`'s pattern) but no SQLite store, no `range()`/query-by-date-range method, and — critically — no file has ever actually been written by it (confirmed: no journal artifact exists on disk anywhere). |
| `HistoricalLineage` (source, access_method, ingestion_run_id, certification_ref) | ❌ **Does not exist in this form.** `options_observation` has its own `ObservationProvenance`/`ObservationVersion` (inherited from MOC), which is a real, disciplined provenance mechanism — but it is NOT the same shape as `HistoricalLineage`, and nothing maps one to the other today. |

**Verdict: existing options data does NOT fit into the certified
Reality architecture as-is.** It would require, at minimum: (a) wiring
a `CertificationGate` check into whatever write path is built, (b) a
real persistent store (either extending `HistoricalObservationStore`'s
pattern with a new options-shaped table, or a genuinely new store —
either way, new code), and (c) a lineage mapping from
`ObservationProvenance` to something `HistoricalObservation`-compatible,
or an explicit decision that options Reality uses its own,
differently-shaped lineage model. **None of this is designed here,
per this audit's own restriction.**

## 5. Dangerous contamination check

**Zero contamination found.** Direct grep for `PCR`, `max_pain`,
`gamma_exposure`, `dealer_position`, `signal`, `score`, `sentiment`,
`bias` inside `bujji/options_observation/*.py` returned exactly 3
matches, all three confirmed by manual inspection to be **negative
disclosures** ("no PCR/OI-aggregation/chain-calculation," "PCR...
belongs to future MSI brains, never here") — never actual computed
fields.

`bujji/market_state_builder/option_observation_bridge.py` (the one
module connecting this Reality-adjacent domain to the live
Intelligence cycle) explicitly states, in its own docstring: *"No
Greeks are invented — OptionLeg's iv/delta/gamma/theta/vega... are
simply not passed through."* Confirmed structurally, not just by
docstring claim: `build_option_observation()` has no such parameters
at all.

`bujji/intelligence/greeks_brain.py` computes real Black-Scholes
Greeks, but from live option premiums fetched at decision time, never
from a persisted "Reality" record, and is correctly named/located as
an Intelligence-tier module, not folded into `options_observation`.

**No leakage found anywhere in this inventory.**

## 6. Final Classification

| Finding | Status |
|---|---|
| Existing options Reality exists | **Partially** — real, tested, correctly-designed CODE (`options_observation`, Series 73C) exists and is Reality-grade in its data model; **no populated historical corpus exists anywhere** (0 persisted records, 4 raw source days on disk) |
| Historical depth | 4 real trading days (2026-07-27..30), EOD resolution only, no bid/ask, no IV, no Greeks (by design, correctly) |
| Quality | Reality-grade where it exists — zero fabrication, zero contamination, disclosed gaps (bid/ask absent at the source, not hidden) |
| Architecture compatibility | **Does not fit as-is** — no `CertificationGate`, no `HistoricalObservationStore`-equivalent persistent store, no `HistoricalLineage`-shaped provenance. Would require new store/certification wiring to integrate, though the identity/value model itself is already correctly MOC-compatible. |
| Suitable before v1 freeze? | **No** — insufficient depth (4 days) and missing the certification/storage machinery the rest of Reality v1.0 requires |

## Final Decision

**B — Existing options Reality exists but is isolated. Needs future
integration.**

Not **A** (there genuinely IS real, well-designed, MOC-compatible
Reality-grade code and a small amount of real data — calling this
"nothing exists" would understate what was found). Not **C** (the
depth is 4 days, there is no certification gate, and there is no
persistent store — this does not meet the bar to block the freeze;
integrating it properly is real, scoped, future work, not an emergency
gap). This finding does not change the v1.0 freeze decision already
made in `PHASE_17I4_REALITY_LAYER_V1_0_PRE_FREEZE_GAP_AUDIT.md` (HOLD,
pending the futures-depth campaign) — options remains correctly out of
v1.0's scope, now with a precise, evidence-based inventory behind that
classification instead of an assumption.

No implementation is recommended here. If options Reality is ever
prioritized, the concrete starting point this audit identifies is:
`bujji/options_observation/` already has the correct identity/value
design — the missing pieces are storage and certification, not a
redesign of the observation model itself.
