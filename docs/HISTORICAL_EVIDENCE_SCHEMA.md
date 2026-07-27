# Historical Evidence Schema

**BUJJI Options OS v3 — Engineering Series 64**

## Status

Deployed. `HistoricalSessionRecord` (Series 59) is extended,
additively and backward-compatibly, to transport option open
interest, bid/ask, India VIX, and session metadata — the exact
evidence Qualification Data Enrichment Sprint B proved sourceable but
un-transportable. Nothing consumes these fields yet: they flow through
the pipeline and stop at `ReplayScenario` construction, exactly as
this sprint's critical principle requires.

## Historical evidence audit — before vs after

| Evidence | v1 (Series 59) | v2 (Series 64) |
|---|---|---|
| Spot price | `spot: Optional[float]` | Unchanged |
| Option chain identity (strike/type/expiry/symbol) | `option_chain_entries: Tuple[Tuple[int,str,str,str],...]` | Unchanged |
| MIC classification fields | `market_context`, `market_opinion`, `context_stability`, `calibration`, `governance`, `lifecycle`, `contract` | Unchanged |
| Holiday flag | `is_holiday: bool` | Unchanged |
| **Option open interest** | **Discarded** — present in the raw NSE Bhavcopy source (`OpnIntrst`, `ChngInOpnIntrst`) but had no field to land in | **Transportable** — `option_chain_liquidity: Tuple[OptionLiquiditySnapshot, ...]`, each carrying `open_interest`, `change_in_open_interest` |
| **Bid/ask** | **Discarded** — no source ever supplied it, and no field existed either | **Transportable** — same `OptionLiquiditySnapshot.bid`/`.ask` |
| **India VIX** | **Discarded** — confirmed sourceable (FYERS MCP) in Sprint B but had no field | **Transportable** — `vix: Optional[float]`, `vix_as_of: Optional[str]` |
| **Session metadata** (exchange/segment) | **Discarded** — implicit (always NSE/FO), never recorded explicitly | **Transportable** — `session_exchange: Optional[str]`, `session_segment: Optional[str]` |
| Schema identity | None | `schema_version: str` (new; see `schema_version.py`) |

## Where the new fields live, and why

`OptionLiquiditySnapshot` is a new, separate frozen dataclass — not an
extension of `option_chain_entries`'s existing 4-tuple shape — keyed
identically (`strike`, `option_type`, `expiry`, `contract_symbol`) so
a liquidity snapshot always matches back to its contract identity
unambiguously, and so `option_chain_entries`'s own shape (and every
line of code that unpacks it as a 4-tuple, e.g.
`observation_adapter.py`, `_record_to_scenario()`) needed zero
changes. Every missing value (`open_interest=None`, `bid=None`, etc.)
means "not supplied by the source" — never a real zero, matching the
project's "never fabricate" discipline throughout.

## Files created or modified

- **New:** `bujji/replay/schema_version.py` — `SCHEMA_VERSION_V1`
  (Series 59 original), `SCHEMA_VERSION_V2` (Series 64), and
  `CURRENT_SCHEMA_VERSION`.
- **New:** `bujji/replay/historical_session.py` — the canonical home
  of `HistoricalSessionRecord` (all v1 fields unchanged in name,
  order, type, default; six new optional fields appended) and the new
  `OptionLiquiditySnapshot`.
- **Modified** (justified — this sprint's own explicit mandate to
  evolve the schema): `bujji/replay/validator.py` — `HistoricalSessionRecord`'s
  definition moved to `historical_session.py` and is re-exported
  unchanged (`from .historical_session import HistoricalSessionRecord`),
  so every existing importer (`corpus_builder.py`, Data Acquisition
  Sprint A's `option_chain_ingestion.py`, every existing test)
  continues to work with **zero code changes**. `validate_session()`/
  `validate_corpus()`'s logic is byte-for-byte unchanged — confirmed
  by an AST-based test that the module's source never references any
  Series 64 field name.
- **Modified** (same justification): `bujji/replay/manifest.py` —
  `CorpusManifest` gains one new optional field,
  `session_schema_version: str = CURRENT_SCHEMA_VERSION`, recording
  which `HistoricalSessionRecord` schema built a given corpus,
  purely informational. `CorpusManifest.schema_version` (the
  manifest's own format version, unrelated to the session schema) is
  untouched at `"1.0.0"`. `build_manifest()` gained one new optional
  keyword argument with the same default — every existing call site
  is unaffected.
- **Not modified:** `bujji/replay/corpus_builder.py` — `build_corpus()`
  keeps full `HistoricalSessionRecord` objects through its filter/sort
  pipeline (never reconstructs them field-by-field), so the six new
  fields pass through automatically with zero code change.
  `compute_checksum()` reads only `session_id`/`trading_date`/
  `timestamp`/`spot`/`option_chain_expiries`/`option_chain_entries` —
  confirmed unaffected by the new fields (verified: a legacy and an
  enriched record with identical v1 fields produce an identical
  checksum).
- **Not modified:** `bujji/replay/option_chain_ingestion.py`,
  `bujji/mic_replay/*`, `bujji/qualification/*`, any Trading Brain,
  Runtime, or MIC v2 file.

## Pipeline propagation, stage by stage

| Stage | Propagation |
|---|---|
| Corpus Builder | New fields pass through unmodified (see above) |
| Manifest generation | New `session_schema_version` field, purely additive |
| Validator | Confirmed never reads a Series 64 field (AST-verified test) |
| MIC Replay / Publication Replay | Untouched — both operate on `observation_adapter.py`'s payload, itself built only from v1 fields (`spot`, `option_chain_entries`); Series 64 fields never reach this stage, by design |
| Trading Brain Input (`ReplayScenario`/`PipelineInput`) | Confirmed the new fields never reach `ReplayScenario` — `corpus_builder._record_to_scenario()` only reads v1 fields, and a test asserts `ReplayScenario` has no `vix`/`option_chain_liquidity` attribute |
| Qualification | `HistoricalQualificationRunner` (Series 58) never references a Series 64 field (AST-verified test) |

## Backward compatibility — verified, not assumed

- Every legacy-shaped construction (`HistoricalSessionRecord(session_id=..., spot=..., ...)`, no Series 64 keywords at all — exactly how Data Acquisition Sprint A's code has always constructed records) continues to work unchanged.
- `bujji.replay.validator.HistoricalSessionRecord is bujji.replay.historical_session.HistoricalSessionRecord` — the same class, not a copy, so `isinstance` checks and existing imports are unaffected.
- Both the original 13-session real corpus and the 41-session enriched real corpus (Qualification Data Enrichment Sprint B) were re-replayed end-to-end under the new schema. Both produced **identical** manifest checksums, **identical** report IDs, and **identical** per-session strategy/outcome/health/circuit/rate-limit decisions to their pre-Series-64 runs — see the deployment report for exact values.

## What this sprint deliberately does not do

No line of code introduced by this sprint reads `option_chain_liquidity`,
`vix`, `vix_as_of`, `session_exchange`, or `session_segment` for any
decision-making purpose — confirmed by AST-based tests over
`validator.py`, `historical_runner.py`, and (implicitly) every other
unmodified module in the chain, none of which even imports the new
symbols. Consuming this evidence — feeding VIX into MIC v2's
`ObservationInput.vix_level`, or OI into its `OptionChainLevel.ce_oi`/
`.pe_oi` — is explicitly out of scope and is the natural next step,
not taken here.
