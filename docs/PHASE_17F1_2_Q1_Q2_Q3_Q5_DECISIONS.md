# Phase 17F.1.2 — Design Decisions: Part 8 Q1, Q2, Q3, Q5

Q4 (dual `as_of` bounds on `replay()`) was already decided and implemented
separately — see `bujji/market_reality/replay.py` and the full-regression
confirmation (5,312 passed) from that change. This document closes the
remaining four unresolved questions from
`docs/PHASE_17F1_2_FUTURES_STATS_MATERIALIZER_DESIGN.md` Part 8.

These are **decisions only** — no code is authorized to change by this
document. The Futures Statistics Materializer implementation itself
remains gated on a separate, explicit "start implementation" instruction.

---

## Q1 — Store `price_change` as a materialized scalar, or compute on demand?

**Decision: store it**, per the design doc's own recommendation (§3.2).

**Reasoning:**
- It is a single derived scalar (`close - open` off the referenced
  Candle), not a duplicated series — storing it does not recreate the
  "two places hold the same time series" problem the brief warns
  against. The Candle remains the sole source of `open`/`close`
  themselves; `FuturesStatistics.price_change` is a one-hop derivative
  of it, exactly like `oi_change` is a one-hop derivative of
  `oi_open`/`oi_close` within the same record.
- `referenced_candle_keys` makes the derivation auditable — any consumer
  or test can resolve the exact Candle `price_change` came from and
  recompute it independently as a check. Storing it is not an
  unauditable shortcut.
- Every consumer of `FuturesStatistics` (OI-price joint observations,
  future Understanding-layer reasoning) will want price movement
  alongside OI movement in the same record. Forcing every consumer to
  independently join against `CandleStore` to get one scalar is
  friction with no correctness benefit — the join is already done once,
  correctly, at materialization time.

## Q2 — Add `transformation_history` to `FuturesStatistics` now; retrofit `Candle` separately?

**Decision: yes to both, as two separate changes.** `FuturesStatistics`
gets `transformation_history: Tuple[str, ...]` in its Part 4 schema, as
originally proposed. The `Candle` retrofit (adding the same field
retroactively, to bring 17F.1 in line with the standard this phase sets)
is **authorized as a follow-up, additive change** — it may be done
either immediately before `FuturesStatistics` implementation begins, or
as its own small patch; either is acceptable since the `candles` table
holds no production rows and the field is purely additive (same pattern
already used for the 7 provenance fields added to `Candle` in 17F.1).

**Reasoning:**
- Not bundling the `Candle` retrofit into the `FuturesStatistics` build
  keeps the two changes independently reviewable and independently
  revertable — one touches a new record type, the other touches an
  already-shipped one.
- Leaving `Candle` without `transformation_history` indefinitely would
  let the gap identified in §3.3 persist as a permanent inconsistency
  between two sibling record types in the same package. Since the fix is
  free (additive field, zero migration cost, no production data), there
  is no reason to leave it open-ended — it is decided as "do it," just
  not gated on this materializer's own delivery.

## Q3 — Same `.db` file as `CandleStore`, or a new file?

**Decision: separate file** — `bujji/market_timeseries/futures_stats.db`,
via the new `FuturesStatsStore` class in
`bujji/market_timeseries/futures_stats_store.py`, distinct from
`CandleStore`'s `candles.db`.

**Reasoning:**
- Matches the existing convention across this codebase's Layer 1/Layer 2
  stores (`RawObservationStore`'s accepted/rejected JSONL files,
  `CandleStore`'s own file, `outcome_memory`, `portfolio_intelligence`,
  etc.) — each materialized record type owns its own file. A shared file
  would be the exception, not the norm, and would need its own
  justification; none of the stated benefits (one WAL, one backup) are
  load-bearing enough to break that convention for a schema that is
  otherwise unrelated to `Candle`'s.
- `FuturesStatistics.referenced_candle_keys` already makes the
  cross-reference to `CandleStore` explicit and queryable without
  requiring the two tables to live in the same file — the relationship
  is expressed in data, not in shared physical storage.
- Keeping the files separate means a future schema change to one store
  (a migration, a rebuild, a corruption-recovery wipe-and-replay) can
  never accidentally touch the other's table. Given the project's
  standing "rebuild from Layer 0 alone" discipline, isolating blast
  radius per store is worth the (currently zero, since there's no
  production data yet) cost of an extra file.

## Q5 — Depth-polling cadence for futures `MARKET_DEPTH`

**Decision: 60-second polling cadence** for futures `MARKET_DEPTH`
capture during market hours, revisited once live rate-limit certification
data exists.

**Reasoning:**
- This is the first point in the project where the cadence decision
  becomes directly consequential (§1.3/Part 8 note 5) rather than
  abstract — `oi_open`/`oi_close`/`oi_observation_count` are only as
  meaningful as the sampling rate that feeds them.
- 60 seconds is a conservative starting point chosen for two reasons: it
  keeps futures depth polling well inside any plausible broker rate
  budget alongside the tick/quote/option-chain polling this system
  already runs, and OI at the exchange does not change fast enough
  intraday for sub-minute sampling to add real information — a coarser
  cadence loses negligible signal while meaningfully reducing API load
  and stored-observation volume.
- This is explicitly a **starting value, not a permanent one**. Nothing
  in the `FuturesStatistics` schema or materializer design depends on a
  specific cadence — `oi_observation_count`/`depth_observation_count`
  exist precisely so a consumer can tell how densely a given window was
  sampled, rather than assuming a fixed rate. If live certification
  (`scripts/verify_fo_access.py`-style, once run against real futures
  depth) shows headroom for a faster cadence, or reveals the broker
  imposes a slower one, this number changes without any schema or
  materializer change — it is a poller-configuration value, not a design
  constant.
- No poller is authorized to be built or changed by this document. This
  decision only unblocks calling this number "decided" rather than
  "still undecided" wherever the design doc references it; the actual
  polling loop is separate, not-yet-authorized implementation work.

---

## Status

All 5 of Part 8's unresolved questions (Q1–Q5) are now decided. The
Futures Statistics Materializer itself has since been implemented (see
`docs/PHASE_17F1_2_FUTURES_STATS_MATERIALIZER_DESIGN.md` Part 10) and
full regression is green.

## Q5 addendum — the poller is wired, but gated pending one live check

`scripts/run_futures_depth_poller.py` (new) implements the 60-second
cadence decided above: market-hours-gated exactly like every other
collector script in this project, sleeps `POLL_INTERVAL_SECONDS = 60.0`
between calls to a new `FyersBroker.get_depth()` method
(`bujji/broker/fyers.py`).

**It does not write to Layer 0 yet, by design.** `get_depth()` is a raw,
unmodified pass-through of the FYERS `depth` action's response --
deliberately, so it never fabricates structure. This codebase has
LIVE-VERIFIED exactly three fields in that response (`oi`, `pdoi`,
`ltp`, via `get_futures_quote`, 2026-08-12); it has never confirmed what
key names (if any) carry the bid/ask ladder. `bids`/`asks` is Layer 0's
OWN vocabulary (`market_reality.taxonomy.REQUIRED_PAYLOAD_FIELDS`), not
a confirmed FYERS response shape -- guessing it and writing that guess
to Layer 0 would be exactly the kind of invented field this project's
Layer 0 discipline exists to forbid.

So the poller runs today in **DISCOVERY mode only** (the default): it
polls on the 60s cadence and logs the raw, real response shape for a
human to read, writing nothing. A `--live` flag exists but is refused at
startup (`FIELD_MAPPING_VERIFIED = False` in the script) until an
operator runs DISCOVERY during market hours, confirms the real bid/ask
field names from the logged output, updates
`_normalize_depth_payload()` in the script to map those REAL names (not
invented ones), and flips the flag. This mirrors exactly how
`certify_vix_access.py` was prepared-but-not-executed pending a live
market-hours run -- the infrastructure and cadence are real and tested;
the one remaining step is a live fact this session cannot fabricate.

Tests: `tests/test_futures_depth_poller.py` (12 tests: cadence value,
market-hours gating incl. weekend/before-open/after-close, the
verification gate's default state, DISCOVERY mode never touching
Layer 0, `--live` refusing to start while unverified) and
`tests/test_fyers_transport_mapping.py` (`get_depth()`: raw pass-through,
never invents `bids`/`asks` keys, missing-row/auth-error handling).
