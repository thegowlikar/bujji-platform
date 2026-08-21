# Phase 17I.11 — Options Reality Semantic Classification Fix

**Status: AUDITED, IMPLEMENTED, TESTED, VALIDATED.** Reality-tier only.

---

## Audit findings

### 1. Origin of `value_kind` assignment

`bujji/historical_reality/capture.py:68`, inside `build_historical_observation()`
— the single shared entry point every historical ingestion script uses
(spot, futures, VIX daily/intraday, and, since Phase 17I.10, options).
It called `build_observation(..., value_kind=moc_taxonomy.VALUE_KIND_OHLC,
...)` **unconditionally, with no parameter to override it** — every
caller, regardless of what shape its payload actually was, got `OHLC`
stamped on its record.

### 2. Existing `value_kind` enums/schemas/validators

`bujji/market_observation/taxonomy.py:170-179` already defines a
**complete, four-member enum**:

```python
VALUE_KIND_SCALAR = "SCALAR"
VALUE_KIND_OHLC = "OHLC"
VALUE_KIND_MAPPING = "MAPPING"
VALUE_KIND_TEXT = "TEXT"
ALL_VALUE_KINDS = (VALUE_KIND_SCALAR, VALUE_KIND_OHLC, VALUE_KIND_MAPPING, VALUE_KIND_TEXT)
```

**`VALUE_KIND_MAPPING` was already the correct classification and was
already in real, live use elsewhere in this exact codebase**, for the
exact same *kind* of payload options carries (ltp/bid/ask/oi, not a
candle):

- `bujji/market_reality/capture.py:44-46` — `KIND_QUOTE`,
  `KIND_MARKET_DEPTH`, and **`KIND_OPTION_CHAIN` all already map to
  `VALUE_KIND_MAPPING`**, not OHLC. Only `KIND_CANDLE` maps to OHLC
  (line 47).
- `bujji/options_observation/engine.py:123` — the pre-existing,
  never-persisted options domain (Series 73C, audited in 17I.5) also
  already uses `VALUE_KIND_MAPPING` for its option payloads.
- `bujji/futures_observation/engine.py:101` — futures' own
  quote/basis data (a non-candle shape) also uses `VALUE_KIND_MAPPING`.

This means the bug was narrowly localized: **every other observation
path in the project already correctly classifies option/quote/depth
data as MAPPING. Only the historical-reality path, because it hardcoded
OHLC with no override, was the one inconsistent writer** — introduced
when 17I.10 reused `build_historical_observation()` (correctly, per its
own reuse mandate) without noticing the hardcoded kind.

`build_observation()` (`bujji/market_observation/engine.py:190`)
validates `value.value_kind` only against membership in
`taxonomy.ALL_VALUE_KINDS` — it does not enforce any relationship
between `value_kind` and payload shape. `VALUE_KIND_MAPPING` was
already a valid, accepted member; **no schema change of any kind was
required.**

### 3. Compatibility check — adding/using a new semantic type

| Component | Inspects `value_kind`? | Impact |
|---|---|---|
| `HistoricalObservationStore` | No — `payload`/`record` are opaque JSON-blob TEXT columns, no per-kind logic | None |
| `RawObservationStore` | No — same generic JSON storage pattern | None |
| `CertificationGate` | No — keyed only by `(instrument_type, access_method)`, has no concept of value_kind | None |
| Existing spot/futures/VIX historical records | Unaffected — see §4 below | None |
| Existing tests | `grep`-confirmed: no existing test in `tests/` asserted `value_kind == VALUE_KIND_OHLC` for a historical-reality record; nothing to break | None |
| `observation_id` hash (`market_observation/engine.py:58`) | **Yes** — the identity seed is `identity_fields + value_kind + repr(payload)` | Real but harmless: changes the *content* of the hash for option records (which is correct — the record's true classification is now included), not a structural risk. Re-verified live in Test §5 below (idempotency/conflict still hold). |

**Conclusion: reuse `VALUE_KIND_MAPPING` — no new enum member, no new
schema, no new validator needed.** The "preferred direction" named in
this phase's own instructions (`OPTION_CHAIN_STATE` /
`OPTION_MARKET_OBSERVATION`) was evaluated and **rejected** in favor of
the existing `VALUE_KIND_MAPPING`, because:

- it is already the established, live-proven classification for this
  exact payload shape elsewhere in the codebase (three separate
  existing call sites, not a one-off),
- it already satisfies every requirement this phase's instructions
  set for the new classification (represents "a snapshot of market
  state," never implies OHLC/derived-metric/Greeks/IV/signal),
- introducing a fifth enum member for something `MAPPING` already
  covers would be an unjustified duplicate abstraction — exactly the
  kind of premature complexity this project's own standing discipline
  (17I.9's own "reuse unless a real incompatibility is found" rule)
  argues against.

### 4. Correct semantic classification — decision

**`moc_taxonomy.VALUE_KIND_MAPPING`.** Represents "a keyed snapshot of
market facts" — matches option chain state (`ltp`, `bid`, `ask`, `oi`,
`pdoi`, identity fields) precisely, and structurally cannot be
mistaken for OHLC by any downstream consumer that branches on
`value_kind` (confirmed live: `live_market_events/engine.py:111-116`
already branches `if value_kind == OHLC` / `elif value_kind ==
MAPPING` — a future consumer built the same way will now correctly
route option records to the MAPPING branch, not silently misclassify
them as candles).

## Files changed

- **`bujji/historical_reality/capture.py`** — `build_historical_observation()`
  gained one new optional parameter, `value_kind: str =
  moc_taxonomy.VALUE_KIND_OHLC`. The default is deliberately set to
  `OHLC` (not `MAPPING`) so every pre-existing caller, which never
  passed this argument, is completely unaffected — zero code change
  required on their side, zero behavior change for their output.
  The one internal hardcoded line (`value_kind=moc_taxonomy.VALUE_KIND_OHLC`)
  was replaced with `value_kind=value_kind`, wired to the new
  parameter. Docstring updated to explain the default and point future
  non-candle callers at `VALUE_KIND_MAPPING`.
- **`scripts/capture_options_reality_session.py`** — the one call to
  `build_historical_observation()` now passes
  `value_kind=moc_taxonomy.VALUE_KIND_MAPPING` explicitly, with an
  inline comment pointing at the same precedent found in the audit
  (`market_reality.capture`, `options_observation.engine`).
- **`tests/test_options_reality_capture.py`** — 6 new tests added (see
  below); the existing `_build_obs()` test helper was updated to pass
  `value_kind=VALUE_KIND_MAPPING` so it accurately mirrors what the
  real script now does (previously it silently defaulted to OHLC,
  which would have made the storage-level tests pass for the wrong
  reason).

**Not touched, deliberately**: `ingest_india_vix_daily_historical.py`,
`ingest_india_vix_intraday_historical.py`,
`ingest_nifty_spot_daily_historical.py`,
`ingest_nifty_spot_intraday_historical.py`,
`ingest_nifty_futures_daily_historical.py`,
`ingest_nifty_futures_intraday_historical.py` — all six continue to
call `build_historical_observation()` with no `value_kind` argument,
relying entirely on the preserved default. Verified structurally by a
new test (`test_spot_futures_vix_historical_ingestion_scripts_still_default_ohlc`),
not just by inspection.

## Migration decision

**No migration of existing records.** Per this phase's own instruction
("Do not migrate old records unless required") and its own "preserve
existing historical observations unchanged" principle: the 162,150
option rows captured live on 2026-08-14 (Phase 17I.10) retain
`value_kind: "OHLC"` in the database exactly as originally written.

This is a deliberate, disclosed limitation, not an oversight:
those specific rows carry a stale classification label until/unless a
future phase explicitly decides to backfill-correct them (a pure
metadata rewrite, no payload change, well within reach if ever
needed — the `natural_key` makes every affected row addressable). All
option captures from this point forward (any run of
`capture_options_reality_session.py` after this deploy) will be
written correctly as `MAPPING` from the start.

## Compatibility analysis

- **`HistoricalObservationStore`**: unmodified, unaffected — confirmed
  by the full regression run (§ below) exercising its real schema
  with no changes needed.
- **`RawObservationStore`**: unmodified — this fix touches only
  `historical_reality.capture`, which `RawObservationStore` has no
  relationship to.
- **`CertificationGate`**: unmodified, unaffected — has no concept of
  `value_kind`.
- **Existing spot/futures/VIX historical records**: byte-for-byte
  unaffected. The default-parameter approach means their write path
  is unchanged in every respect, confirmed by a new regression test
  showing every one of their production ingestion scripts still calls
  `build_historical_observation()` without a `value_kind` kwarg.
- **Existing tests**: zero pre-existing tests referenced `value_kind`
  for a historical-reality record before this phase; nothing broke.

## Tests

6 new tests added to `tests/test_options_reality_capture.py` (29 total
in that file, up from 23):

1. `test_options_capture_writes_mapping_not_ohlc` — reads the real
   capture script's source and asserts it passes
   `value_kind=moc_taxonomy.VALUE_KIND_MAPPING` and never
   `VALUE_KIND_OHLC` — the direct proof of the fix itself.
2. `test_build_historical_observation_defaults_to_ohlc_for_backward_compatibility` —
   calls the shared function exactly as every spot/futures/VIX script
   does (no `value_kind` kwarg) and asserts the result is still
   `VALUE_KIND_OHLC`.
3. `test_build_historical_observation_honours_explicit_mapping_kind` —
   asserts a record built via the same helper the options tests use
   comes out `MAPPING`, never `OHLC`.
4. `test_mapping_value_kind_is_a_real_recognized_taxonomy_member` —
   confirms `VALUE_KIND_MAPPING` was already a first-class member of
   `ALL_VALUE_KINDS` (proving no new enum was invented, matching the
   audit's own reuse decision).
5. `test_spot_futures_vix_historical_ingestion_scripts_still_default_ohlc` —
   structural guard reading all six production ingestion scripts'
   real source, asserting none of them were touched by this phase.
6. `test_options_identity_and_idempotency_unaffected_by_value_kind_fix` —
   re-proves, under the new MAPPING classification, that identity
   uniqueness, idempotent re-write, and `ConflictingHistoricalObservationError`
   rejection all still hold (a real regression risk, since the
   observation-id hash includes `value_kind` in its seed — not a
   hypothetical check).

## Regression result

- `tests/test_options_reality_capture.py`: **29 passed** (23 existing
  + 6 new).
- Reality/historical-focused subset (`pytest -k 'reality or
  historical'`): **495 passed**.
- **Full suite: 5,678 passed, 0 failed** (up from 5,672 before this
  phase — the 6 new tests, zero regressions elsewhere).

## Summary

The Reality Integrity Debt is closed. Option chain observations are
now written with the semantically correct `MAPPING` value_kind,
matching the classification already used everywhere else in this
project for the identical payload shape (`market_reality.capture`,
`options_observation.engine`) — no new enum, no new schema, no new
storage path, and zero impact on spot/futures/VIX's OHLC candle
records, which keep their original classification via an unchanged
default. The one disclosed gap is that records captured before this
fix (2026-08-14's 162,150 rows) still carry the stale `OHLC` label
until a future, explicitly-decided backfill — a known, addressable,
non-urgent debt, not a live risk, since nothing downstream currently
branches on `value_kind` for options. Reality semantics are clean
going forward; freeze can proceed on this specific finding.
