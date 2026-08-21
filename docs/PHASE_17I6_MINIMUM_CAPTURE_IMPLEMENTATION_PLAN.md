# Phase 17I.6 — Minimum Market Reality Capture Loop: Implementation Plan

**Status: PLAN ONLY. No code in this document.** Written for review before
implementation, per the phase's own sequencing requirement. Builds on
[docs/PHASE_17I5_FUTURES_IDENTITY_AUDIT.md](PHASE_17I5_FUTURES_IDENTITY_AUDIT.md)
and every decision confirmed in the 17I.3/17I.4 review chain.

---

## 1. Scope of this implementation

Two changes, both additive:

1. **`bujji/broker/instrument_master.py`** — a small, additive extension
   (per 17I.5's Decision A): a new futures-row reader and a new
   `resolve_nearest_future(underlying)`-shaped method, returning
   primitives (symbol, ISO expiry date, lot size) — not a new dataclass,
   not a modification to `_rows_for()`'s existing CE/PE filter or
   `resolve_atm()`'s behavior.
2. **`scripts/capture_market_reality_session.py`** — one new consolidated
   capture script. No other existing file is modified.

## 2. `InstrumentMaster` extension — exact shape

- New private row reader, sibling to `_rows_for()`, filtering
  `option_type == "XX"` instead of `("CE", "PE")` — same CSV parse, same
  column indices (verified identical in 17I.5), separate method so the
  existing CE/PE path and its test (`test_excludes_futures_rows`) are
  untouched.
- New public method resolving the nearest upcoming expiry for an
  underlying's futures rows, reusing the same `min(expiry_epoch >=
  today_epoch - 86400)` selection `resolve_atm()` already uses — no new
  selection logic invented.
- Return shape: the three primitives the capture script actually needs
  (real FYERS symbol, expiry as an ISO date string matching the format
  `test_market_reality_store.py`'s own fixtures already use, e.g.
  `"2026-08-25"`, and lot size) — plain values, not a dataclass, per the
  "no new dataclasses" constraint.
- `_futures_symbol()` (the wall-clock-driven, self-documented-as-
  provisional construction) stays in place unmodified, demoted in
  practice to a fallback the new resolver doesn't need but nothing else
  currently depends on removing.

## 3. `capture_market_reality_session.py` — structure

One asyncio loop, mirroring `run_futures_depth_poller.py`'s existing
skeleton (`cycles`, `POLL_INTERVAL_SECONDS`, per-cycle
`within_market_hours()` check) rather than inventing a new loop shape:

1. **Startup** (once, outside the loop): market-hours pre-check,
   `AppConfig.load()`, `FyersBroker.connect()`, one `CertificationGate`,
   one `RawObservationStore`, one `CaptureLifecycleTracker` (keyed by
   `source="fyers"`, `access_method="direct_sdk_fyers_broker_py"` — the
   single shared instance per Decision 2, confirmed across two prior
   reviews). Resolve the nearest futures expiry/symbol once at startup
   via the new `InstrumentMaster` method (an expiry does not change
   mid-session; no need to re-resolve every cycle).
2. **Per cycle** (every `POLL_INTERVAL_SECONDS`, default 60, gated by
   `within_market_hours()` on every iteration, matching the poller's own
   mid-session market-close handling):
   - `get_spot("NIFTY")` → `build_spot_observation()` (reused verbatim
     from `capture_first_spot_observation.py`) → `store.append()`.
   - `get_futures_quote("NIFTY")` → a new `build_futures_observation()`
     (same shape as the existing two `build_*_observation()` functions:
     `KIND_QUOTE`/`INSTRUMENT_FUTURE`, `identity_fields={"expiry":
     <resolved at startup>}`, payload `{"ltp": ..., "volume": ...,
     "oi": ...}` when present) → `store.append()`.
   - `get_vix()` → `build_vix_observation()` (reused verbatim from
     `capture_first_vix_observation.py`) → `store.append()`.
   - **Each of the three calls is wrapped independently.** A failure on
     one (exception or `None` return) is logged and the cycle continues
     to the next instrument — per Decision 5, this is *not* routed
     through `CaptureLifecycleTracker` unless it is one of the tracker's
     actual connection-level reasons (an `AuthenticationError`, for
     instance, legitimately is — a bad/empty quote is not). No new
     `ALL_CAPTURE_REASONS` value is added.
   - A genuine connection-level condition (e.g. `AuthenticationError`
     from any of the three calls) is recorded once via the shared
     tracker's `record_condition()`/`record_recovery()`, exactly as
     designed — not duplicated per instrument.
3. **Session end**: clean exit after `cycles` iterations or when
   `within_market_hours()` first returns `False` mid-session (matching
   the poller's existing "market hours ended mid-run -- stopping
   cleanly" behavior) — never a silent hang, never a background
   respawn.

## 4. Timestamp resolution (Decision 6)

During the session's first successful cycle only, log the complete raw
broker response for one `get_spot()`/`get_vix()`/`get_futures_quote()`
call (not just the extracted `lp`) to actually answer, from real data,
whether FYERS's quote response carries any timestamp beyond the price —
resolving the open question named in every prior 17I audit rather than
carrying it forward again. This is a log statement, not a stored
observation field — `event_timestamp` stays `None` in the written
records until this question is answered and, if a real timestamp is
found, a follow-up change (out of this phase's scope) would use it.

## 5. Failure/gap visibility (Decision 5 restated precisely)

Per the prior review's finding: a bare per-instrument miss (broker
returned `None`, or raised something other than `AuthenticationError`)
produces **no persisted Layer 0 record** — only a log line. This is
correct and intentional (Layer 0 records facts, not absence-of-facts),
but the implementation must not simulate a queryable miss-log that
doesn't exist. Phase 17I.7's gap measurement will need to infer misses
by comparing expected per-instrument cadence against actual JSONL
timestamps, not by reading a "missed observation" record — this plan
does not add one.

## 6. Test plan (before live execution)

Mirroring `test_capture_first_spot_observation.py`/
`test_capture_first_vix_observation.py`'s existing posture (script
loaded by file path, no live broker call):

- Market-hours gate (reuse the same 4-case pattern already proven twice).
- Bounded-loop behavior: given a fixed `cycles` count and a fake clock/
  broker, the loop runs exactly that many iterations and exits cleanly —
  same style as `test_futures_depth_poller.py`'s own coverage of its
  loop bounds.
- **One tracker instance**: assert the session constructs exactly one
  `CaptureLifecycleTracker`, not three — a structural test on the
  script's own construction, not a behavioral one.
- Construction of each observation type: `build_spot_observation()`
  (already tested, re-run for regression only), `build_vix_observation()`
  (same), and the new `build_futures_observation()` — same shape of test
  as the other two (kind/instrument_type/payload/identity_fields
  assertions).
- Certification linkage for all three instrument types against the real
  on-disk certification artifacts (`NIFTY_SPOT`, `NIFTY_FUTURES`,
  `INDIA_VIX`, all already confirmed `CERTIFIED_AVAILABLE` in prior
  audits) — same pattern as the existing two test files.
- Append persistence: real JSONL file, correct line count, correct
  payload — same pattern.
- Duplicate/idempotency: re-appending the same fact is a no-op, survives
  a fresh `RawObservationStore` instance — same pattern proven twice
  already.
- **Partial-failure continuation**: given a broker double where one of
  the three calls raises/returns `None`, assert the other two still get
  appended and the session does not crash — this is the one genuinely
  new behavioral test class this phase introduces.
- New `InstrumentMaster` tests: the new futures-row reader returns the
  real-shaped rows (using a small synthetic CSV fixture, matching
  `test_instrument_master.py`'s existing fixture style), the nearest-
  expiry resolver picks the correct row, and — critically —
  `test_excludes_futures_rows` and every other existing
  `InstrumentMaster` test still pass unmodified.

## 7. Regression

Run the full suite after implementation. Expected: all 5,418 existing
tests continue passing unchanged, plus the new tests above. No taxonomy
change, no schema version bump, no new dataclass — none of this phase's
work requires one.

## 8. Explicitly out of scope (unchanged from every prior audit)

Option chain capture, any materializer, any Market Memory work, any
daemon/cron/unattended execution, any new `ALL_CAPTURE_REASONS` value,
any change to `_rows_for()`'s or `resolve_atm()`'s existing behavior.

---

## Final validation

**Does this move Bujji closer to an institutional MIC?**

Yes. Every property that distinguishes this from a generic market data
collector is preserved and, with this phase, exercised live for the
first time: certification-gated writes (three real instrument types,
all already `CERTIFIED_AVAILABLE`), immutable append-only lineage,
content-addressed deduplication, honest absence-of-fact rather than
fabricated completeness, and a failure model that distinguishes
connection health from instrument-level noise instead of conflating
them. Nothing in this plan invents intelligence, signals, or fake
certainty — it only makes the existing, already-proven Reality pipeline
run continuously instead of once. That is precisely the substrate the
later Memory/Understanding/Intelligence phases need to stand on.

**Proceed.**
