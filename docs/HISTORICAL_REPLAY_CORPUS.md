# Historical Replay Corpus

**BUJJI Options OS v3 — Engineering Series 59**

## Status

Deployed. `bujji/replay/{corpus_builder.py, validator.py, manifest.py}`
build a canonical, versioned replay corpus from raw historical session
records, producing exactly the `(scenarios, timestamps)` shape Series
58's `HistoricalQualificationRunner.run_corpus()` already consumes —
unmodified. This sprint builds data, not trading logic: it makes no
trading decision, computes no P&L, and never touches the Trading
Brain, Runtime, Health Aggregator, Circuit Breaker, Rate Limiter, or
Qualification Runner.

## Package location

`bujji/replay/` already existed before this sprint — a pre-existing,
unrelated legacy candle-replay/backtest module (`engine.py`'s
`ReplayEngine`/`ReplayResult`, `broker.py`'s `ReplayBroker`,
`__main__.py`), predating the Trading Brain entirely. Confirmed via a
name-collision check (`grep '^class \|^def '` across all three
existing files) before adding anything: no name in `corpus_builder.py`,
`validator.py`, or `manifest.py` collides with anything already in the
package. Nothing in the existing three files was read for reuse or
modified — this sprint's new files are additive only.

## Sourcing real historical data is explicitly out of scope

The specification is clear that "the corpus" is what this sprint
*builds the pipeline for*, and this sprint's environment has no
authorized path to fetch real historical NIFTY spot/option-chain data
(no live broker credentials are exercised anywhere in this project's
qualification/replay tooling, per every prior series' own "never
connect to a live broker" discipline). `HistoricalSessionRecord`
(defined in `validator.py`) is the input shape a future data-loading
layer — sourcing real exchange history, however that is obtained —
would produce; this sprint's own tests construct `HistoricalSessionRecord`
instances directly (synthetic, deterministic, fixture-shaped) to
exercise the pipeline, exactly as Series 46's own qualification tests
already do for the deterministic pipeline itself.

## Pipeline stages, in order

1. **Holiday exclusion.** A record is excluded from the corpus if its
   own `is_holiday` flag is set, or its `trading_date` appears in the
   caller-supplied `holidays` tuple. Excluded session IDs are recorded
   in `CorpusBuildResult.excluded_holiday_session_ids` — never silently
   dropped.
2. **Validation** (`validator.validate_corpus`). Every remaining
   record is checked independently: a deterministic `session_id`, a
   parseable ISO-8601 `timestamp`, a `trading_date`, a positive `spot`,
   and a non-empty, internally consistent `option_chain_entries` (every
   entry's `expiry` must appear in `option_chain_expiries`, every
   `option_type` must be `CE`/`PE`, every `strike` positive, every
   entry must carry a `contract_symbol`). Invalid sessions are excluded
   from the emitted corpus but remain fully visible, with their exact
   issues, in `CorpusBuildResult.validation_report.session_results` —
   this validator never repairs, interpolates, or guesses a missing
   value.
3. **Chronology restoration.** Surviving records are sorted by
   `timestamp` ascending. Input order is never assumed to already be
   chronological — the corpus preserves original market chronology by
   construction, not by trusting the order records happen to arrive
   in.
4. **Conversion.** Each surviving record becomes one Series 46
   `ReplayScenario` (via `_record_to_scenario`) and one `datetime`
   timestamp — pure data transformation, zero trading logic.
5. **Checksumming and manifest.** `compute_checksum()` hashes
   (`hashlib.sha256`) the exact, ordered set of surviving sessions —
   order-sensitive by design, since a corpus with the same sessions in
   a different order is a different corpus. `manifest.build_manifest()`
   then wraps `corpus_id` (deterministic, `hashlib.md5`-derived, never
   `uuid4()`), `source_description`, `trading_dates`, `session_count`,
   the checksum, and a `generation_timestamp` (from an injectable
   clock) into an immutable `CorpusManifest`.

## Immutability

`HistoricalSessionRecord`, `SessionValidationResult`,
`CorpusValidationReport`, `CorpusManifest`, and `CorpusBuildResult` are
all frozen dataclasses. `build_corpus()` never mutates an input record
— every transformation produces a new object.

## Series 58 compatibility, unmodified

`CorpusBuildResult.scenarios`/`.timestamps` are passed directly to
`HistoricalQualificationRunner.run_corpus(scenarios, timestamps)`
(Series 58) with no adapter, no wrapper, and no change to either
module — confirmed in
`tests/test_replay_corpus.py::test_replay_compatible_with_series_58_runner`.

## Verification before declaring completion

See the Series 59 deployment report for exact test counts, an example
manifest, a validation report for a sample corpus (including detected
corrupted sessions), and the qualification fingerprint comparison.
