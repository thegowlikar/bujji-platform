# Phase 17I.6.2 — Live FYERS Raw Response Discovery

**Status: DISCOVERY ONLY. No Layer 0 writes. No `RawObservation` created.
No `taxonomy.py`/`models.py`/`capture.py` changes. No `event_timestamp`
implementation.**

Answers exactly one question, from two real live captures, not
assumption: *does FYERS provide a broker/exchange event timestamp that
can populate `RawObservation.event_timestamp`?*

---

## Execution

- **Script**: `scripts/discover_fyers_raw_responses.py`, using the
  Phase 17I.6.1 raw methods (`get_spot_raw()`, `get_futures_quote_raw()`,
  `get_vix_raw()`).
- **Date/time (IST)**: 2026-08-13, two live runs — 12:23:06 and 12:24:42
  (98 seconds apart, both during NSE market hours).
- **FYERS methods inspected**: `get_spot_raw("NIFTY")`,
  `get_futures_quote_raw("NIFTY")` (both `ltp_response` and
  `depth_response` legs), `get_vix_raw()`.

## A real gap in the discovery tool itself, found and fixed before any conclusion

The script's first run reported **zero** candidate timestamp keys across
all four sections — technically true of its keyword list
(`time`/`ts`/`date`/`epoch`/`feed`), but false as a finding: reading the
printed raw JSON directly showed two real fields the scanner missed
because neither contains any of those substrings: **`tt`** (on every
plain `ltp`/quote response) and **`ltt`** (on the `depth` response).
Added `"tt"` to the keyword list (catches both, since `"ltt"` contains
`"tt"` as a substring), pinned a regression test
(`test_candidate_timestamp_keys_finds_the_real_tt_and_ltt_fields`), and
re-ran live to confirm the corrected scanner surfaces both. This is
reported here in full rather than silently corrected, since the
one-shot nature of a discovery script means an unreported false-negative
here would have produced a wrong B conclusion.

## Actual raw fields observed

**Spot / VIX (`get_spot_raw()` / `get_vix_raw()`, both via the plain
`ltp` action)** — identical shape for both:

```
message, code, d[0].n, d[0].s, d[0].v.{
  ask, bid, chp, ch, description, exchange, fyToken, high_price,
  low_price, lp, open_price, original_name, prev_close_price,
  short_name, spread, symbol, tt, volume, atp
}, s
```

**Futures `ltp_response`** — identical field set to spot/VIX (same `tt`
field present).

**Futures `depth_response`** (via the `depth` action) — a much richer,
different shape:

```
d.{symbol}.{
  totalbuyqty, totalsellqty, ask[0..4].{price,volume,ord},
  bids[0..4].{price,volume,ord}, o, h, l, c, chp, tick_Size, ch,
  ltq, ltt, ltp, v, atp, lower_ckt, upper_ckt, expiry, oi, oiflag,
  pdoi, oipercent
}, message, s
```

## Timestamp findings per instrument

| Field | Where seen | Run 1 value | Run 2 value (98s later) | Verdict |
|---|---|---|---|---|
| `tt` | Spot, VIX, and futures `ltp_response` (all three, identical shape) | `"1786579200"` | `"1786579200"` — **bit-for-bit identical** | **NOT a live event timestamp.** Decodes to `2026-08-13 05:30:00 IST` — exactly midnight UTC, the trading day's own boundary marker, not a per-quote time. Identical across spot/futures/VIX in the same run, and unchanged 98 seconds later. Structurally a day-level session stamp, not usable for `event_timestamp`. |
| `ltt` | Futures `depth_response` only | `1786603984` → `2026-08-13 12:23:04 IST` | `1786604082` → `2026-08-13 12:24:42 IST` | **A real, live, per-observation timestamp.** Moved by exactly 98 seconds between the two runs — matching the real wall-clock gap between them almost exactly (script header timestamps: 12:23:06 and 12:24:42). This is FYERS's real last-traded-time, genuinely usable as an event timestamp. |
| Anything else timestamp-shaped | — | — | — | None found in any of the four sections beyond `tt`/`ltt`. |

## Does this differ by instrument?

**Yes — but the more precise finding is that it differs by *endpoint*,
not primarily by instrument.** Spot, futures, and VIX all get the
*same* unusable `tt` field when queried via the plain `ltp` action —
instrument type doesn't change that. The only source of a genuinely
live timestamp found is the `depth` action's `ltt` field, which this
project currently only calls for futures (`get_futures_quote_raw()`'s
second leg). Spot and VIX have no depth call in the current design, so
today, in practice, only futures has any path to a real live timestamp
— not because spot/VIX structurally lack one, but because this project
has never queried the endpoint that would carry it for them. Whether
`NSE:NIFTY50-INDEX`/`NSE:INDIAVIX-INDEX` have their own real `ltt` via
their own depth call is genuinely unknown — not tested this run, since
`get_spot_raw()`/`get_vix_raw()` only call the `ltp` action, mirroring
their processed counterparts' actual usage.

## Final recommendation: **C — Mixed availability**

Not a clean A ("FYERS provides usable event timestamps," unqualified)
and not a clean B ("FYERS only provides snapshot/poll time," which
would be wrong — `ltt` is real and live). The honest finding:

- **Quote endpoint (`ltp` action)** — used by all three current
  collectors — carries only `tt`, a static day-boundary marker.
  **Retain current design here: `event_timestamp=None`,
  `capture_timestamp` remains the only honest value.**
- **Depth endpoint** — carries a real, live, second-accurate `ltt`.
  Confirmed only for futures (the only instrument this project queries
  depth for today). This is a genuine future option, **not implemented
  this phase**: if a collector ever queried depth for an instrument, its
  `event_timestamp` could legitimately be populated from `ltt` — but
  that is a separate, later decision (whether to add a depth call to
  spot/VIX, whether to change the futures collector to use `ltt`,
  whether `event_timestamp` population needs any capture-pipeline
  change) and is explicitly out of scope for this discovery-only phase.

**No code changes were made based on this finding.** `capture_market_reality_session.py`
and its three `build_*_observation()` functions are unchanged — every
observation this project writes today still correctly carries
`event_timestamp=None`, which this discovery confirms is the honest
value for the endpoint actually being used.
