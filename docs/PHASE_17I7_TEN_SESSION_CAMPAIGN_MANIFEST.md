# Phase 17I.7 — Ten Session Market Reality Capture Campaign Manifest

**Status: Session 1 in progress. Addendum below (real, sourced NSE
timing change) applies to Session 2 onward.**

---

## Addendum (added mid-Session-1, 2026-08-13 13:xx IST) — NSE F&O close extended 15:30 → 15:40

Confirmed via web search against a real NSE circular dated 2026-05-30,
effective **2026-08-03** (10 days before this campaign started): the
F&O (derivatives) segment's market close moved from **15:30 to 15:40
IST** — a new 10-minute extension aligning derivatives close with the
cash market's new Closing Auction Session (15:15–15:35). Open remains
09:15. This project trades NIFTY options/futures, squarely the F&O
segment this change applies to.

Every `MARKET_CLOSE` constant across the collector/discovery scripts in
`scripts/` (9 files, including `capture_market_reality_session.py`) has
been updated from `datetime.time(15, 30)` to `datetime.time(15, 40)`,
with an inline citation. Full regression run after the change.

**Effect on this campaign, stated explicitly, not silently absorbed:**
- **Session 1 is unaffected by the fix** — its process was already
  running with the old 15:30 constant loaded in memory before this fix
  landed, and will exit at 15:30 today as originally configured. This
  is a known, accepted, documented gap for session 1 only (missing the
  final 10 minutes of the now-real trading window), not a defect.
- **Sessions 2–10 correctly use 09:15–15:40 (385 minutes)**, not the
  original 375-minute estimate. Recompute §4's volume figures for those
  sessions accordingly: ~385 cycles/session, ~1,155 observations/session,
  ~1,540 REST calls/session (4/cycle) — session 1 stays at the original
  ~375/~1,125/~1,500 estimates, so the 10-session totals are not a clean
  multiple of either single-session figure this time.

**Out of scope for this fix, found but deliberately not touched**: 7
files under `bujji/` (`futures_observation/runner.py`,
`options_observation/runner.py`, `live_shadow_operator/operator.py`,
`market_perception/greeks_adapter.py`, `market_perception/msi_adapter.py`,
`intelligence/volatility_brain.py`, `replay/option_chain_ingestion.py`)
also hardcode the old 15:30 close. These belong to separate,
previously-audited subsystems (the older Trading Brain/shadow-runtime
paths) outside this campaign's active surface — editing them without
their own scoped review would be a real architecture change mid-live-
session, which this manifest's own operational rules (§8) forbid.
Flagged here as a real, dated finding for a future, separately-scoped
fix — not silently left undiscovered.

---

## 1. Objective

This campaign is **not** for strategy discovery, prediction, or signal
generation. The objective is to accumulate trustworthy Layer 0 market
reality observations from the already-proven pipeline:

- NIFTY Spot
- NIFTY Futures
- India VIX

**The output is evidence density, not intelligence.**

- *The capture mechanism is production-proven* — Phase 17I.6's live run
  (5/5 cycles, 15/15 observations accepted, certification linkage
  intact, restart rehydration and duplicate handling verified against
  real FYERS data).
- *The evidence base is now entering accumulation phase* — 15
  observations exist today; this campaign is what turns a proven
  mechanism into an actual body of evidence.

No claim is made that MIC intelligence exists yet. Nothing in this
campaign builds toward that claim either — see §8.

## 2. Campaign Window

- **Start**: 13 August 2026
- **Sessions**: 10 NSE trading sessions, weekends excluded
- **Dates**: 13, 14, 17, 18, 19, 20, **21**, 24, **25**, **26** August 2026

### Expiry boundary event — explicit, not incidental

**Current contract**: `NSE:NIFTY26AUGFUT`, expiry **25 August 2026**
(verified directly against the live FYERS NFO instrument master in
Phase 17I.5 — a real `expiry_epoch`, not inferred from the symbol
string).

- **Session 9 (25 Aug 2026)**: the contract's real expiry day. Treat as
  a **special observation day** — OI/volume/basis dynamics around
  expiry are structurally different from an ordinary session
  (settlement effects). Record descriptively; do not discard as
  anomalous, do not interpret.
- **Session 10 (26 Aug 2026)**: expected rollover environment.
  `InstrumentMaster.resolve_nearest_future()` is called fresh at each
  session's startup, so it is expected to automatically discover and
  resolve `NSE:NIFTY26SEPFUT` without any manual intervention or code
  change. **The symbol change between session 9 and session 10 must
  not be treated as a data error** — it is the correct, designed
  behavior of the already-implemented resolver.

## 3. Capture Configuration

**DECIDED (this update): full-session bounded capture.** The
reconciliation flagged in the prior manifest version is resolved —
campaign mode is the collector's own unbounded-cycles default, not a
lighter `--cycles N` launch.

- **Collector**: `scripts/capture_market_reality_session.py`
- **Mode**:
  - Manual launch only.
  - Market-hours gated.
  - Runs until market close (`--cycles` left unset; the script's own
    `while cycles is None or completed < cycles` loop, gated each
    iteration by `within_market_hours()`, exits cleanly at 15:30 IST).
  - Non-daemon, single bounded session per launch.
- **Polling interval**: 60 seconds.
- **Cycles**: ~375/session (see §4 — derived from the market-hours
  window, not a fixed `--cycles` value).
- **Source**: FYERS, `direct_sdk_fyers_broker_py`.
- **Instruments**:
  - Spot: `NSE:NIFTY50-INDEX`
  - Futures: resolved dynamically via `InstrumentMaster`
  - VIX: `NSE:INDIAVIX-INDEX`

## 4. Expected Data Volume

**Cycles**: NSE market hours are 09:15–15:30 IST = 375 minutes; at a
60-second polling interval, an unbounded (full-session) launch is
expected to complete **~375 cycles/session**.

**Observations**: 375 cycles × 3 instruments (Spot + Futures + VIX) =
**~1,125 observations/session**, **~11,250 observations across the
10-session campaign**.

**REST call load** — 4 REST calls per cycle, confirmed directly against
the real broker methods the collector calls, not estimated:
1. Spot quote (`get_spot()` → one `ltp` call)
2. Futures LTP (`get_futures_quote()`'s primary `ltp` call)
3. Futures depth/OI (`get_futures_quote()`'s secondary, best-effort
   `depth` call)
4. India VIX quote (`get_vix()` → one `ltp` call)

At 375 cycles/session × 4 calls/cycle: **~1,500 REST calls/session**,
**~15,000 REST calls across the 10-session campaign**.

**The first campaign session is also a rate-limit and cadence stability
validation run.** Only ~20 REST calls (5 cycles) have been exercised
live so far (Phase 17I.6). Full-session load at ~1,500 calls has never
been tested end-to-end. Session 1's own reliability metrics (§5) should
be read with this in mind — a degraded completion rate or cadence drift
in session 1 specifically may reflect this being the first real test of
sustained load, not a defect to fix before continuing; whether it
warrants an interval change is a decision for after session 1's actual
numbers exist, not before.

Storage growth is not a concern at this scale: append-only JSONL
handling ~11,250 lines across the full campaign is trivial (confirmed
in the 17I.7 readiness review).

## 5. Measurements (Observation Only)

No interpretation in any of the below.

**Reliability**
- Attempted cycles, completed cycles.
- Per-instrument success count, failure count.

**Cadence** — calculated from persisted observations, not tracked live:
- Actual write spacing vs. the expected 60s interval.
- Unexplained gaps. **Do not invent missing-observation records** —
  absence is reconstructed from cadence vs. the JSONL, never stored as
  a synthetic "missed" fact (per the standing Layer 0 discipline: Layer
  0 records facts, not absence).

**Futures**
- Resolved symbol each session.
- Expiry identity.
- LTP.
- OI availability rate (the best-effort depth-leg OI call's null rate).
- Volume.
- Special observation: expiry-day (session 9) behavior, rollover
  (session 10) transition.

**Spot/Futures relationship** — descriptive only:
- Basis values, basis range, basis changes across the campaign. No
  signal creation.

**VIX**
- Range, observations captured.
- Relationship with session volatility noted only as future research
  material — not analyzed now.

**Data integrity**
- Duplicate IDs (expected: none).
- Rejected observations (expected: none).
- Certification references (expected: stable, pointing at the same
  three already-existing artifacts throughout).
- Restart readability (a fresh `RawObservationStore` against the
  campaign's directory correctly rehydrates all prior records).

## 6. Success Criteria

**PASS**
- ≥95% cycle completion.
- Zero certification failures.
- Zero unexpected schema failures.
- Zero duplicate observation IDs.
- All three instruments captured across normal (non-expiry-boundary)
  sessions.

**Investigate** (not automatic fail, but requires explanation before
being accepted into the record)
- OI missing periods.
- API failures.
- Expiry transition behavior (sessions 9–10).
- Cadence deviations.

**Explicitly not defined here**: profitability, prediction accuracy, or
trading usefulness — none of these are in scope for a Reality-layer
campaign.

## 7. Known Limitations (carried forward, not re-litigated)

- **Timestamp**: `event_timestamp` remains `None`; `capture_timestamp`
  is the authoritative value. Phase 17I.6.2's live raw-response
  discovery proved the `ltp` endpoint (used by all three collectors)
  carries only a static day-boundary field (`tt`), not a usable event
  time — confirmed against two real, dated live captures, not assumed.
- **Source**: FYERS is currently the only live source; Bhavcopy remains
  deferred (17I.7 readiness review §7 — no dependency, lower urgency
  than stabilizing this campaign).
- **Option chain**: explicitly deferred — the identity-model question
  from Phase 17I.3 (per-contract vs. whole-snapshot representation)
  remains unresolved and is not addressed by this campaign.
- **Depth**: not included beyond the existing best-effort futures-OI
  cross-check — general depth capture's identity/certification
  granularity gap remains unresolved.

## 8. Operational Rules

**Do not, during this campaign:**
- Add strategies.
- Add a Memory layer.
- Add an Intelligence layer.
- Build analytics engines.
- Modify taxonomy.

**During the campaign**: collect. Observe. Measure. Nothing more.
