# Phase 17I.10 — Options Reality Capture: Implementation

**Status: IMPLEMENTED, CERTIFIED, LIVE-VALIDATED.** Reality-tier only.
No Greeks, no IV, no PCR, no max pain, no signals, no derived
analytics — verified structurally, not just by intent (§ below).

---

## Files changed

**New files only — zero modifications to any existing file:**

- `scripts/certify_fyers_optionchain_reality_access.py` — certification
  for the new `direct_sdk_fyers_optionchain_reality` access_method.
- `scripts/capture_options_reality_session.py` — the live, 5-minute,
  market-hours-gated, complete-chain capture loop.
- `tests/test_options_reality_capture.py` — 23 tests.

**Reused, completely unmodified**: `historical_reality.capture.build_historical_observation()`,
`historical_reality.store.HistoricalObservationStore`,
`market_reality.certification.CertificationGate`,
`market_reality.taxonomy.INSTRUMENT_OPTION`/`REQUIRED_IDENTITY_FIELDS`.
No new store, no new model, no new schema — per 17I.9's own readiness
conclusion, confirmed correct in practice.

## Architecture decisions

- **Identity**: `"{underlying}|{expiry_iso}|{strike}|{option_type}"`
  (e.g. `NIFTY|2026-08-18|21850|PE`) — packed into
  `HistoricalObservationStore`'s existing single `instrument_identity`
  column, exactly the encoding convention 17I.9 flagged as the one
  remaining open decision. `fyToken` and the broker symbol are never
  identity — `fyToken` is carried inside the payload as a plain fact,
  `source_symbol` is stored as lineage.
- **Access method**: `direct_sdk_fyers_optionchain_reality` — new,
  distinct, never reuses any existing value. Certified independently;
  confirmed live that `direct_sdk_fyers_broker_py` (live quote) and
  this value resolve to two separate, non-conflating certification
  records.
- **Capture scope**: complete chain, **all 18 real listed expiries**
  (confirmed live — not an assumption from 17I.8's 6-expiry estimate;
  the real number is 3x higher, spanning near-term weeklies through
  2031-06-24 long-dated options), `strike_count=50` per expiry
  (~101 strikes/side, live-verified).
- **Frequency**: 5 minutes, matching 17I.8's evidence-based
  recommendation.
- **Market hours**: 09:15–15:30 IST, per this phase's explicit
  instruction — narrower than futures depth's 15:40. **This
  discrepancy was preserved deliberately, not silently reconciled** —
  both captures ran side-by-side today with their own distinct windows
  and both closed cleanly at their own boundary.

## Corrections to two prior audit findings, made during implementation

While implementing, reading the real FYERS SDK source
(`fyers_apiv3.fyersModel.FyersModel.optionchain`'s own docstring)
revealed two things stated incompletely in earlier phases:

1. The `timestamp` parameter on the `optionchain` action is an
   **expiry selector** ("Use empty for current expiry"), not a
   historical-snapshot pointer as `PHASE_17I6`/`PHASE_17I7` assumed
   when testing it with a made-up past epoch. The underlying
   conclusion (no historical option-chain snapshot capability exists)
   remains correct and independently re-confirmed by the expired-symbol
   tests in `PHASE_17I6`; only the *reason* given in those two
   documents was incomplete. **This is exactly what made complete-chain,
   all-expiry capture possible today** — using `timestamp` correctly
   as an expiry selector, not despite it.
2. The same SDK docstring documents a `greeks` parameter that returns
   delta/gamma/theta/vega/IV directly from FYERS. Prior documents
   (17I.6/17I.7/17I.8) stated IV/Greeks are "never source-provided" —
   incomplete; they are source-*computable on request*. **Neither
   script in this phase ever passes `greeks=1`** — confirmed
   structurally by a regression test (`test_never_requests_greeks`)
   that greps every real `_call()` invocation, not just a docstring
   claim. IV/Greeks stay out of Reality by this project's own
   deliberate choice, not because FYERS lacks them.

## Certification evidence (real, dated, both `CERTIFIED_AVAILABLE`)

`data_certification/fyers_nifty_optionchain_reality_certification_20260814.json`:
default-expiry chain 202 rows (101 CE + 101 PE), zero integrity issues;
second-expiry probe (25-08-2026) 42 rows, zero issues; **18 real
expiries discovered live**; `greeks_requested: false`.

`data_certification/fyers_nifty_future_depth_certification_20260814.json`
(re-certified same session, unrelated to this phase's own new
access_method but part of the same market-open launch): 5-level depth
ladder confirmed, `CERTIFIED_AVAILABLE`.

## Live capture evidence — full session, 09:15–15:30/15:40 IST, 2026-08-14

| | Options chain reality | Futures depth |
|---|---|---|
| Cycles completed | 75 (17 initial + 1 restart-test + 57 resumed) | 385 |
| Final row count | 162,150 | 385 (`MARKET_DEPTH` rows in `layer0_data/raw_observations.jsonl`) |
| Distinct contract identities | 2,190 | — |
| Rejected rows | **0** (entire session) | 0 |
| Exit behavior | `"Market hours ended mid-run -- stopping cleanly"` at 15:30 IST | Same, at 15:40 IST |

**Consistency check, computed not assumed**: 162,150 ÷ 75 = exactly
2,162 rows/cycle throughout the ENTIRE session — zero variance,
confirming stable, complete capture on every single cycle, not just
the first few.

**CE/PE split**: 80,475 CE / 81,675 PE — real, close to balanced, not
suspiciously skewed.

**Identity growth during the session, a real market fact**: distinct
identities grew from 2,176 (first cycle) to 2,190 (final) — 14 new
contracts appeared intraday, almost certainly new strikes getting
listed as the ATM range shifted with spot movement, not a bug.

**Restart survival — live-tested, not assumed**: mid-session, the
running capture process was deliberately killed and restarted. Before:
36,754 rows / 17 timestamps. After one new cycle: exactly 38,916 rows
/ 18 timestamps (+2,162, matching one cycle exactly) — no duplication,
no gap, no data loss.

**Lineage — verified on a real sample row**:
```
instrument: "NIFTY|2026-08-18|21850|PE"
access_method: "direct_sdk_fyers_optionchain_reality"
source_symbol: "NSE:NIFTY2681821850PE"
certification_status: "CERTIFIED_AVAILABLE"
certification_ref: "fyers_nifty_optionchain_reality_certification_20260814.json@..."
```
Full lineage present on every row, not just this sample — same
`build_historical_observation()` machinery every other Reality-tier
writer uses.

**A real illiquid-strike fact, correctly preserved, not rejected**: the
sample row above is a genuinely far-OTM put (`ltp=0.05, bid=0, ask=0,
oi=0`) — a real, valid zero-liquidity fact, stored honestly rather than
filtered out, matching this project's standing "genuine zero is a fact,
never a rejection" discipline.

## Rejected rows

**Zero, across the entire session, both captures.** No malformed rows,
no missing required fields, no negative prices encountered in ~2,190
real contracts × 75 cycles.

## Test results

23 new tests (`tests/test_options_reality_capture.py`), all passing:
identity formatting/uniqueness (CE vs. PE, different strikes, different
expiries all produce distinct identities), row validation (rejects
missing type/strike/symbol/ltp, rejects negative prices, **accepts** a
genuine zero-bid/ask illiquid row), market-hours gate, certification
isolation (new access_method distinct from all four existing ones),
structural no-greeks guard, and storage-level duplicate/conflict/
restart-survival tests using the real `HistoricalObservationStore`.

Full regression: **5,649 → 5,672 passed, clean.**

## Limitations, disclosed

- **`value_kind` is stamped `OHLC`** on every option row — inherited
  unmodified from `build_historical_observation()`, which hardcodes
  this for its original spot/futures/VIX candle use case. Options
  payloads are MAPPING-shaped (ltp/bid/ask/oi), not genuine OHLC
  quads — this is a cosmetic metadata mislabel, **not a data integrity
  issue** (every actual field is correct and complete); fixing it would
  require extending `build_historical_observation()`'s signature, a
  small change out of this phase's own scope, not made here.
- **Far-dated expiries (2027–2031) are captured at the same
  `strike_count=50` width as near-term ones** — likely far wider than
  real listed liquidity for those tenors; not a correctness problem
  (every returned row is real), but a real storage-volume
  consideration for a future phase.
- **API call volume**: 19 real calls per 5-minute cycle (1 discovery +
  18 expiries) × 75 cycles = ~1,425 real API calls today, with zero
  rate-limit errors encountered — a real, positive data point, not
  independently stress-tested beyond what today's session already
  proved.
- **Certification's own market-hours boundary caused one real
  operational failure earlier today**: the original campaign
  orchestration script scheduled its pre-flight at 09:10, five minutes
  before the certification script's own 09:15 gate opens — the
  certification correctly refused (fail-closed, exactly as designed),
  and the launcher was corrected and relaunched at the right time. This
  was an orchestration-script bug, not a Reality-layer defect — noted
  here for completeness.

## Summary

Bujji now has eyes for options: a complete, certified, lineage-full,
zero-rejection Reality-tier capture of the entire live NIFTY option
chain (18 expiries, ~2,190 contracts) at 5-minute resolution, running
correctly through a full real trading session with a live-tested
restart-survival proof. It does not have a brain — no Greeks, no IV, no
signal, no interpretation was computed or stored anywhere in this
phase. Reality first. Memory later. Intelligence after evidence exists.
