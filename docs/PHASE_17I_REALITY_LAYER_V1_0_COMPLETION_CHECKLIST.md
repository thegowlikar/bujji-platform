# Phase 17I — Reality Layer v1.0 Completion Checklist

**Status: PENDING live evidence.** No code changes in this document.
This is a sign-off checklist only — each row gets checked against real
output from tomorrow's Phase 17I.3 Futures Microstructure Reality
Validation Campaign (09:10–15:40 IST), not assumed complete tonight.

---

## Execution plan (tomorrow, 2026-08-14, Friday)

| Time (IST) | Step | Script |
|---|---|---|
| 09:10 | Pre-flight certification | `scripts/certify_fyers_futures_depth_access.py` |
| 09:15–15:40 | Live capture, uninterrupted | `scripts/run_futures_depth_poller.py --live` |
| After close | Validation + this checklist | (analysis only, no new script) |

Pre-flight already verified tonight (2026-08-13, outside market hours,
does not require a session): FYERS token valid, `resolve_nearest_future("NIFTY")`
resolves `NSE:NIFTY26AUGFUT` (expiry 2026-08-25), and
`data_certification/` currently holds no `direct_sdk_fyers_broker_py_depth`
artifact — exactly the expected pre-campaign state.

---

## Sign-off checklist

### 1. Certification

- [ ] `certify_fyers_futures_depth_access.py` produces
      `validation_result = CERTIFIED_AVAILABLE`
- [ ] Artifact's `access_method = "direct_sdk_fyers_broker_py_depth"`
- [ ] Confirmed independent from `direct_sdk_fyers_broker_py` (the live
      quote cert) — querying `CertificationGate.status_for()` for each
      access_method separately returns two distinct records, not one
      shared status
- [ ] Depth payload structurally contains `bids`, `ask`(s), `oi`,
      `pdoi`, `totalbuyqty`/`totalsellqty` per the certification
      script's own structural-integrity check

### 2. Did Reality arrive?

- [ ] `RawObservationStore` (`layer0_data/raw_observations.jsonl`)
      contains `kind=MARKET_DEPTH` rows
- [ ] Row count > 0 after a full session

### 3. Is identity correct?

- [ ] Every `MARKET_DEPTH` row's `instrument = "NIFTY_FUT_CONTINUOUS"`
      (never the literal contract symbol)
- [ ] Every row's `identity_fields.source_symbol = "NSE:NIFTY26AUGFUT"`
      (the real, resolved contract used for the request)
- [ ] This is the rollover-safety proof: a future contract-symbol
      change (next rollover) must not fragment the observation
      identity — confirmed by design in 17I.2's tests
      (`test_identity_survives_contract_rollover_unchanged`), to be
      reconfirmed against real captured rows tomorrow

### 4. Is OI available?

- [ ] Measure: `rows with non-null open_interest / total depth rows`
- [ ] Historical futures never carry OI (17H.1 §1.6) — this is the
      first Reality-tier source in the project where a real OI
      availability rate can be measured at all

### 5. Is the book stable? (facts only, no interpretation)

- [ ] Missing-depth rate (`get_depth()` returned `None` this cycle) / total cycles
- [ ] Duplicate rows (idempotent no-op outcome count)
- [ ] Conflicts (none expected under this store's content-hash identity
      model — see 17I.2's `test_differing_depth_content_at_same_timestamp_gets_distinct_ids_not_a_conflict_error`;
      any unexpected `REJECTED` outcome here is worth a closer look)
- [ ] Actual polling cadence achieved vs. the configured 60s
      (`POLL_INTERVAL_SECONDS`)
- [ ] Certification continuity — no `NOT_CERTIFIED`/`CERTIFICATION_MISSING`
      appends mid-session (would indicate a gate/artifact problem, not
      a market fact)

**Explicitly NOT measured, per the phase's own restriction:** bid/ask
imbalance, "smart money" pressure, liquidity scoring, sentiment,
prediction. These remain out of scope for Reality and any future
Understanding-tier phase, not this checklist.

---

## What "Reality Layer v1.0 Complete" means, if every box above checks

| Domain | Status if campaign succeeds |
|---|---|
| Price Reality — Spot | ✅ (existing, 17H.4) |
| Price Reality — Futures | ✅ (existing, 17H.6) |
| Price Reality — VIX | ✅ (existing, 17H.6) |
| Historical Memory Source — Daily | ✅ 1998/2008/2018 → today (17H.4/17H.6) |
| Historical Memory Source — Intraday | ✅ 2017/2018 → today, 5-min (17H.9) |
| Microstructure Reality — Futures depth | Pending tomorrow's evidence (17I.2/17I.3) |
| Microstructure Reality — OI | Pending tomorrow's evidence |
| Microstructure Reality — Bid/Ask ladder | Pending tomorrow's evidence |

If all checklist items pass: Reality Layer v1.0 is complete, and Phase
17J (Market Memory) becomes the next, first genuinely
Understanding-adjacent phase — retrieval and pattern recall over
already-certified Reality facts, still computing nothing, per the
Reality → Memory → Understanding → Intelligence → Strategy → Execution
sequence this whole engagement has held to since Phase 17H.0.

If any item fails: the gap gets named specifically (which check,
what was actually observed) and addressed as a narrowly-scoped fix to
Reality — not folded into Memory design, and not waved through.

**No sign-off happens tonight.** This document is the checklist itself,
not a completion record — it gets filled in and dated against real
output after tomorrow's session closes.
