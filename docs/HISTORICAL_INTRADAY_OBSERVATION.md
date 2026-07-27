# Historical Intraday Observation Expansion

**BUJJI Options OS v3 — Engineering Series 67**

## Investigation (step 1)

`HistoricalSessionRecord` (Series 59/64) had no `open`/`high`/`low`/`close`
fields — only a single settlement `spot`. Not supported; extended.

## What changed

- **`bujji/replay/historical_session.py`**: added `open`/`high`/`low`/`close`
  (all `Optional[float] = None`, appended after Series 64's fields —
  fully additive, backward compatible).
- **`bujji/replay/schema_version.py`**: added `SCHEMA_VERSION_V3`.
- **`bujji/replay/option_chain_ingestion.py`**: `build_session_record()`/
  `build_session_records_from_bhavcopy()` gained optional
  `open_price`/`high_price`/`low_price`/`close_price` pass-through
  parameters — Bhavcopy has no underlying-index OHLC column, so these
  must come from a separate real source (FYERS `fyers_historical`,
  proven in Data Acquisition Sprint A/Series 60); never fetched or
  fabricated by this module.
- **`bujji/mic_replay/observation_adapter.py`**: `build_candle_payload()`
  uses the real four values when *all* are present; falls back to the
  original `open=high=low=close=spot` degenerate representation when
  any one is missing — never a partial mix of real and fabricated OHLC.

## Verification

104 pre-existing tests across all directly related suites pass
unchanged; 13 new tests added. Full regression: 2021 passed, 0 failed.
Fingerprint unchanged.

## Measurement

Real NIFTY 50 index daily OHLC (FYERS `fyers_historical`) for 8 real
trading days (2026-07-13 to 2026-07-22) — the only real OHLC on hand
this sprint; the FYERS session token expired mid-investigation
(`fyers_profile` returned "token expired") and could not be
re-authenticated by this agent, so a larger sample was not possible
this round. Disclosed, not worked around.

| Metric | Degenerate (baseline) | Real OHLC |
|---|---|---|
| Sessions transporting genuine (non-degenerate) OHLC | 0/8 | **8/8** |
| Reasoning cycles | 11 | 10 |
| `NOT_QUALIFIED` cycles | 11/11 (100%) | 10/10 (100%) |
| `market_context` distribution | UNKNOWN:2, SIDEWAYS:1, TRENDING_DOWN:2, TRANSITION:3 | UNKNOWN:3, SIDEWAYS:1, TRENDING_DOWN:2, TRANSITION:2 |
| Sessions with a changed `market_context` | — | 1/8 (2026-07-20: TRANSITION → UNKNOWN) |

## Result: richer OHLC transport alone did not materially change intelligence

`NOT_QUALIFIED` stayed at 100% in both cases. `TRANSITION` remained
present in both (3/8 → 2/8) — a marginal, not dramatic, reduction. This
is consistent with the mechanism found investigating "why TRANSITION
dominates": `TRANSITION` is driven by `mic_v2/memory/engine.py`'s
`REGIME_TRANSITION` rule, which fires when the `candidate_type` (from
the Hypothesis/Candidate pipeline) fails to repeat for 2 consecutive
cycles — a persistence problem one richer OHLC bar per day does not by
itself resolve, since the pipeline still receives only one data point
per day either way (a real range within that single day, not a real
multi-point time series).

Per this sprint's own success criteria: **"If nothing changes, that is
also a successful result."** No reasoning was modified to obtain a
better outcome.
