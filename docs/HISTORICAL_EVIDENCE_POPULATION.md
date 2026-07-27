# Historical Evidence Population

**BUJJI Options OS v3 — Data Acquisition Sprint C**

## Status

Deployed. `bujji/replay/option_chain_ingestion.py` now populates the
Series 64 evidence fields it previously left empty, from real sources
only. Nothing downstream consumes them — confirmed by re-replaying
both the 13-session and 41-session real corpora and observing
byte-identical manifests, report IDs, and per-session outcomes to
their pre-population runs.

## Fields populated, and their real source

| Field | Source | Extraction method |
|---|---|---|
| `option_chain_liquidity[].open_interest` | NSE official Bhavcopy, `OpnIntrst` column | Read directly from the same row already parsed for strike/type/expiry/symbol |
| `option_chain_liquidity[].change_in_open_interest` | NSE official Bhavcopy, `ChngInOpnIntrst` column | Same row, same mechanism |
| `session_exchange` | NSE official Bhavcopy, `Src` column | First non-empty value across the session's rows (`"NSE"`) |
| `session_segment` | NSE official Bhavcopy, `Sgmt` column | First non-empty value across the session's rows (`"FO"`) |
| `vix` | Connected FYERS MCP, `fyers_historical`, symbol `NSE:INDIAVIX-INDEX` | Fetched separately by the caller (bhavcopy has no VIX column) and passed into `build_session_records_from_bhavcopy(..., vix=..., vix_as_of=...)` as an optional pass-through parameter |
| `vix_as_of` | Same as `vix` | Set to the session's own market-close timestamp when a real VIX value is supplied |

**Deliberately left `None`, per this sprint's own explicit non-goals:**
`option_chain_liquidity[].bid`/`.ask` (bhavcopy has no bid/ask column;
no other authorized source was ever found, unchanged conclusion since
Data Acquisition Sprint A) and any certification input (not a
market-data field at all).

## Why `option_chain_ingestion.py` never fetches VIX itself

Bhavcopy — this module's only input — has no VIX column. Rather than
importing a second data source into this module (which would blur its
single responsibility: "converts one bhavcopy CSV into one
`HistoricalSessionRecord`"), `vix`/`vix_as_of` are accepted as
optional keyword parameters. The caller — a corpus-assembly script,
not this module — is responsible for fetching real VIX separately
(from the already-connected, already-authorized FYERS MCP) and
supplying it per session. This keeps the module's own source list
honest: it imports nothing FYERS-related (verified by an AST-based
test), and a caller that never supplies `vix` gets `vix=None`,
identical to this module's pre-Sprint-C behavior.

## Coverage statistics

| Corpus | Sessions | OI-eligible contract entries | Entries with real OI | Sessions with real VIX |
|---|---|---|---|---|
| 13-session (2026-07-06 to 2026-07-22) | 13 | 21,292 | **21,292 (100%)** | **13 (100%)** |
| 41-session (2026-05-25 to 2026-07-22) | 41 | 70,932 | **70,932 (100%)** | **41 (100%)** |

Every contract row in both real corpora carried a real `OpnIntrst`
value (even `0`, a genuine zero reading, distinguished from `None` by
construction — see `test_missing_oi_represented_as_none_not_zero`).
Every one of the 13 + 41 trading days had a real VIX daily candle
available from FYERS. **Missing-value rate for both populated
categories in this sprint's real corpora: 0%** — not because missing
values are impossible (a genuinely absent `OpnIntrst` cell parses to
`None`, never `0`, and a date FYERS has no VIX candle for would leave
`vix=None`), but because none occurred in the specific 54 real trading
days this sprint exercised.

## Files modified

- **`bujji/replay/option_chain_ingestion.py`** (justified — this
  sprint's own explicit mandate to populate the Series 64 schema from
  the historical acquisition path): `build_session_record()` now also
  builds `option_chain_liquidity` (one `OptionLiquiditySnapshot` per
  contract row, `bid`/`ask` always `None`) and extracts
  `session_exchange`/`session_segment` from each row's own `Src`/`Sgmt`
  columns; both `build_session_record()` and
  `build_session_records_from_bhavcopy()` gained optional `vix`/
  `vix_as_of` pass-through parameters, defaulting to `None` — every
  existing call site (Data Acquisition Sprint A's own usage, Series
  59/61/62 tests, Historical Qualification Campaigns v2/v2.1) is
  unaffected.
- No other file was modified. `historical_session.py`, `schema_version.py`,
  `validator.py`, `manifest.py`, `corpus_builder.py` (Series 64) are
  untouched — they already carry whatever fields a `HistoricalSessionRecord`
  has, without needing to know what those fields mean.
- No MIC v2, Trading Brain, Runtime, Qualification Framework, Contract
  Builder, Strategy Selector, or Operational Controls file was
  touched.

## Validation results

Both rebuilt corpora: **validator passes unchanged** (13/13 and 41/41
valid, 0 invalid), **manifest generation succeeds** with
`session_schema_version='2.0.0'` correctly recorded, **missing fields
are explicitly represented** (verified: a bhavcopy row with empty
`OpnIntrst`/`ChngInOpnIntrst` cells produces `open_interest=None`,
never `0` or a guessed value).

## Replay verification — byte-for-byte identical

| | 13-session corpus | 41-session corpus |
|---|---|---|
| Manifest checksum, pre-population | `5fcd01b20fb9df8a5963282f6d55d4f868c93d04586e5d6da4824a66fbab7cce` | `09673a3dc182a3677df14ec3c610b427154243a9676f3c79facd4ba563b3729e` |
| Manifest checksum, post-population | **identical** | **identical** |
| Report ID, pre-population | `QREPORT-bbe021ca1cfcc16f` | `QREPORT-1f479265179511b0` |
| Report ID, post-population | **identical** | **identical** |
| Completed sessions | 0 (unchanged) | 2 (`IRON_CONDOR`, 2026-07-09 & 2026-07-16, unchanged) |
| Strategy selections | `COVERED_CALL` on 2026-07-09 only (unchanged) | `COVERED_CALL` on 2026-05-29/06-22/06-23/06-24, `IRON_CONDOR` on 07-09/07-16 (unchanged) |
| Qualification fingerprint (per-session) | `RFP-0000000000000000` (unchanged) | `RFP-0000000000000000` (unchanged) |
| Production `baselines.json` md5 | `2328e0f77ec312eeca318946df293d91` (unchanged) | `2328e0f77ec312eeca318946df293d91` (unchanged) |

No behavioral difference of any kind was observed on either corpus.

## Verification that nothing consumes the new evidence

- `corpus_builder._record_to_scenario()` builds `ReplayScenario`
  (Series 46) from only the same seven v1 fields it always has —
  `test_scenario_construction_never_receives_new_evidence_fields`
  confirms `ReplayScenario` has no `vix`/`option_chain_liquidity`
  attribute even when built from a fully-populated record.
- `compute_checksum()` is confirmed unaffected — a record built with
  `vix=None` and the identical record built with `vix=13.29` produce
  the same checksum.
- `option_chain_ingestion.py` itself imports nothing from `fyers`/MIC
  v2/Trading Brain — verified by an AST-based test over its own
  import statements.

## Recommendation

**The evidence is now real, populated, and proven at 100% coverage
across 54 real trading days — sufficient in coverage to justify
starting a future, separately-scoped sprint on consuming Option OI and
India VIX inside MIC**, but two things should happen before that
sprint, not during it:
1. **Broaden the coverage window further** — 54 trading days spans
   roughly 2.5 months; a consumption decision (how MIC v2's
   `option_premium`/`open_interest` analyzer modules should weight OI,
   or how `vix_level`/`vix_prev_close` should influence volatility
   classification) deserves a longer, more market-regime-diverse
   window before being tuned or validated against it.
2. **That sprint must be explicitly, separately justified and scoped**
   — this sprint's own principle ("populate, do not consume") and the
   Series 64 principle before it ("expand the data contract, not the
   behavior") were both satisfied by keeping transport and consumption
   strictly apart; consuming this evidence inside MIC v2 is a genuine
   intelligence-algorithm decision, not a natural continuation of a
   data-acquisition sprint.
