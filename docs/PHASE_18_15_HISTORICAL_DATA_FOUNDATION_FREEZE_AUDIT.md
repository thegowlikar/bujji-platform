# Phase 18.15 — Bujji Historical Data Foundation Freeze Audit

**Status: AUDIT ONLY. Zero code changes.** This is the formal boundary
document Phase 19 must treat as authoritative. Every claim is
re-verified fresh this phase — full regression re-run (5,775 passed),
key architectural claims re-confirmed by direct grep — not copied
forward from prior reports without checking.

---

## 1. Frozen Components

| Component | Status | Evidence |
|---|---|---|
| Raw observation storage (`HistoricalObservationStore`) | **FROZEN** | INSERT-only, zero UPDATE/DELETE statements (re-confirmed by grep this phase, unchanged since Phase 18.2, verified independently 4+ times across 18.2/18.4/18.9/18.13); `ConflictingHistoricalObservationError` structurally prevents silent revision |
| Reality capture (ingestion scripts, options capture) | **FROZEN** | Zero ingestion-path files touched by any phase in the 18.x series; Phase 18.14's own incident (a fabricated row written by a TEST script, not by any ingestion path) was corrected by direct, scoped SQL delete outside the capture pipeline — the pipeline itself was never implicated |
| `MarketRealitySnapshot` (reconstruction, no-look-ahead, component timestamps, `fingerprint()`, `reconstruction_version`) | **FROZEN** | Zero diff across Phases 18.10–18.14, each of which explicitly re-verified this by regression; no-look-ahead re-proven live at least 4 times (18.1, 18.2, 18.3, 18.10) |
| Dataset assembly (`DatasetVersion`, `ResearchCalendar`, `ResearchSessionReadiness`) | **FROZEN** | Query-plan performance fix (Phase 18.10) is the only change since Phase 18.7, and was itself proven to preserve identical output via a byte-for-byte historical fingerprint match; unchanged since |
| `DatasetArtifact` / `DatasetIdentity` registry | **FROZEN for its Phase 18.12 core fields; ADDITIVE ONLY since** | `artifact_id`/`dataset_id` immutability enforced at both the dataclass and store layer (`ConflictingDatasetArtifactError`); every addition since (Phase 18.14's `parent_artifact_id`) is a new field with a safe default, never a change to an existing field's meaning |
| Lifecycle governance | **FROZEN as of this phase** (newly introduced in 18.14, not yet battle-tested over multiple real phases) | A real, closed transition table exists and is enforced; classified FROZEN in the sense that its rules will not change without a new phase, but it has only existed for one phase's worth of real-world exercise — noted as a lower-confidence FROZEN than components proven across 5+ phases |
| Certification integration | **FROZEN** | Calls the real, pre-existing `CertificationGate` unmodified (Phase 17E/17I); Phase 18.14 added no new certification vocabulary, confirmed live that fabricated `certification_status` claims alone cannot pass the gate |
| Eligibility checks (`check_backtest_eligibility`) | **FROZEN as of this phase** | Same caveat as lifecycle governance — one phase of real exercise, live-proven true once and false five distinct ways, but not yet exercised against a real Phase 19 consumer |
| Reproducibility (fingerprint determinism, restart survival) | **FROZEN** | Proven live across Phases 18.3, 18.7, 18.10, 18.12, 18.14 — five independent phases, including two genuinely separate process-restart demonstrations (18.12, 18.14) — the single most-validated property in this entire series |

**Overall**: every component with 3+ phases of independent
re-verification is genuinely, defensibly FROZEN. The two newest
additions (lifecycle governance, eligibility) are internally consistent
and tested but have exactly one phase of real-world exercise behind
them — flagged, not hidden, as a real (small) confidence gap Phase 19
should be aware it is the FIRST real stress test of.

## 2. Known Deferred Risks

### Futures Identity

**Unchanged since Phase 18.0, re-confirmed this phase**: the only
historical futures series Bujji has ever captured is
`NIFTY_FUT_CONTINUOUS` — a FYERS-computed, broker-adjusted continuous
series (`cont_flag=1`), never the literal price of any single real,
traded expiry contract. `FuturesSnapshot.source` distinguishes
`SOURCE_HISTORICAL` (always this synthetic series) from `SOURCE_LIVE`
(the real resolved contract, only available going forward from
whenever live capture runs) — a real, honest signal, but it does not
provide historical access to real contract prices.

**Can current data safely support futures backtesting? No, not for
any strategy whose PnL depends on the literal price level a specific
contract traded at.** It CAN safely support a regime/direction-level
read (the continuous series is a legitimate, real, broker-computed
signal for "was the market trending up or down") — but that is a
narrower claim than "backtest a futures strategy."

**Required boundary for Phase 19**: any Phase 19 futures-strategy
backtest MUST either (a) restrict itself to `SOURCE_LIVE` futures data
only (real contracts, but only from whenever live capture began
forward — a narrow date range, mirroring options' own single-day-origin
constraint), or (b) explicitly document that PnL computed against
`NIFTY_FUT_CONTINUOUS` is a synthetic-series approximation, not a real
tradeable-contract result, and surface that caveat to any consumer of
such a backtest. Silently treating the continuous series as if it were
a real contract's price would be a real, avoidable correctness defect
in Phase 19, not a Reality-tier problem — the underlying data is
honestly labeled; a future consumer choosing to misuse it would be the
failure.

### Replay Architecture

**Re-confirmed this phase, with a corrected methodology**: an initial
grep for "bujji.replay" produced a false-positive match against
`bujji.replay_engine` (an unrelated, actively-used module supplying
`fingerprint_state()`, first wired into this project's own governance
layer in Phase 18.3 and reused throughout 18.7–18.14). A precise
pattern excluding that collision confirms: **zero real references** to
`bujji.replay`/`bujji.qualification.HistoricalQualificationRunner`
exist anywhere in `bujji/trading_brain/`, `bujji/shadow_runtime/`,
`bujji/production_runtime/`, or `bujji/market_reality_snapshot/`.

**Are multiple replay systems active? Yes, structurally, but only one
is load-bearing.** `market_reality.replay.replay()` (Phase 17F.1.2's
bitemporal reader) is real and used by `MarketRealitySnapshot`'s own
live-aggregation path. The older `bujji.replay`/`bujji.qualification`
pipeline (Series 46–64) still physically exists in the repository but
is orphaned — confirmed, again, zero real call sites from anywhere
Phase 19 would plausibly import from.

**Can Phase 19 accidentally depend on obsolete paths?** Only by a
literal, explicit `import bujji.replay` or
`import bujji.qualification` — nothing auto-wires it in, nothing in
the `market_reality_snapshot`/`dataset_artifact` chain touches it.
**Required boundary for Phase 19**: its own module(s) must never
import from `bujji.replay` or `bujji.qualification` — a one-line rule,
trivially enforceable by code review, not a structural risk requiring
new tooling.

### Options Data Boundary

Re-confirmed field-by-field, against real, live-captured data:

| Field | Available? |
|---|---|
| Option chain (complete, all strikes/expiries) | **Yes** — 2,190 real contracts, 18 expiries, live-proven |
| OHLC | **No** — options carry `ltp`/`bid`/`ask`, never open/high/low/close (Phase 17I.10's own design; `VALUE_KIND_MAPPING`, not `VALUE_KIND_OHLC`, since Phase 17I.11) |
| Bid/ask | **Yes** — real, including genuine zero-liquidity strikes honestly preserved |
| OI (open interest) | **Yes** — real, plus prior-day OI and OI-change fields |
| Depth (order-book ladder) | **No** — never integrated into options capture; futures depth exists separately (Layer 0 only, not in `MarketRealitySnapshot`) |
| Greeks | **No** — deliberately never requested (`greeks=1` never passed to FYERS, structurally guarded by a regression test since Phase 17I.10) |
| IV | **No** — same deliberate exclusion |
| Tick history | **No** — 5-minute resolution only; options have never been captured at tick or 1-minute granularity |
| **Historical depth (dates before 2026-08-14)** | **No** — options data exists for exactly one real calendar day; this is the single largest boundary of the entire dataset, re-confirmed unchanged through Phase 18.14 |

**What can Phase 19 legitimately backtest against options data?**
Strategies whose entry/exit logic depends only on ltp/bid/ask/OI, at
5-minute resolution, **on 2026-08-14 only** (until a real operator runs
options capture on additional real trading days — a manual,
already-proven, already-working process, per Phase 17I.10/18.5, simply
not yet repeated). Any strategy requiring Greeks, IV, tick-level
execution modeling, or a date before 2026-08-14 is **out of scope for
Phase 19** on the data that exists today — not a code gap, a real data
coverage fact.

### Multi-Instrument Boundary

**Re-confirmed this phase by fresh grep**: zero references to
`BANKNIFTY` (or any underlying other than NIFTY) exist anywhere in
`bujji/market_reality_snapshot/` or the capture scripts. Every
identity constant (`SPOT_SYMBOL`, `FUTURES_CONTINUOUS_IDENTITY`,
`OPTIONS_UNDERLYING`) is hardcoded to NIFTY throughout the builder,
readiness, calendar, and artifact modules. This is unchanged from
Phase 18.11's own audit finding — **classification only, not
implemented here, per this phase's own instruction**: Bujji is a
**single-underlying (NIFTY) system today**. Phase 19 must not assume
multi-instrument capability exists; a backtester built with an
implicit "which underlying" parameter that silently defaults to
anything other than an explicit, single NIFTY constant would be
building on an assumption this data foundation does not support.

## 3. Backtester Dependency Contract

Defined here for the first time as an explicit boundary, based on
what the governance layer (Phase 18.12–18.14) actually provides:

**Phase 19 MAY consume**:
- `DatasetArtifact` (identity, fingerprint, lineage, code identity)
- `DatasetManifest` (the customer-facing summary)
- `check_backtest_eligibility()`'s own output — and MUST refuse to
  proceed if `eligible: False`, surfacing the real `reasons` tuple to
  whatever called it, never silently overriding an ineligible result
- Read-only reconstruction via `MarketRealitySnapshot`/`build_market_reality_snapshot()`
  for the SPECIFIC date range an eligible artifact already covers —
  never a fresh, unchecked date range Phase 19 invents on its own
  without first passing it through artifact creation + eligibility

**Phase 19 MUST NOT consume**:
- `HistoricalObservationStore` directly, bypassing `MarketRealitySnapshot`
  reconstruction — this would re-open every no-look-ahead/point-in-time
  question Phases 18.1–18.3 spent multiple phases proving closed, for
  no benefit, since the reconstruction layer already exists and is free
  (Phase 18.10's own performance fix)
- Any observation whose own `certification_status` is not
  `CERTIFIED_AVAILABLE` — enforced today only at the `run_certification_checks()`
  layer, meaning Phase 19 must actually CALL eligibility checking
  rather than assume some invisible enforcement already blocks
  uncertified data from reaching it
- `bujji.replay`/`bujji.qualification` (§2) — the orphaned pipeline
- Live capture streams (`capture_options_reality_session.py` or any
  running/in-progress capture process) — Phase 19 is a
  HISTORICAL backtester; it has no legitimate reason to touch a live
  capture path at all

## 4. Commercial Readiness

Customer scenario: **"NIFTY Options Iron Condor, Jan 2020 – Aug 2026."**

| Requirement | Status |
|---|---|
| Exact dataset identity | **Ready** — `DatasetIdentity`/`DatasetArtifact` provide this, proven deterministic across 5 phases |
| Reproducibility | **Ready** — proven live, twice, across genuine process restarts |
| Lineage | **Ready** — `ingestion_run_references`, `certification_references`, transitively to real `HistoricalObservation` rows |
| Certification | **Ready as a MECHANISM** — calls the real gate; **NOT ready as a DATA FACT for this specific date range**, because... |
| ...the requested range itself | **NOT READY** — options data exists for exactly one day (2026-08-14) inside a `Jan 2020–Aug 2026` request; `check_backtest_eligibility()` would correctly, honestly return `eligible: False` for this literal customer request today, for the same reason Phase 18.4/18.5's own worked examples found: `required_instruments_missing`/`not_all_dates_ready` |
| Explanation of future changes | **Partially ready** — `parent_artifact_id` (Phase 18.14) provides the POINTER between versions; no automated diff/explanation of WHAT changed between two linked artifacts exists yet (explicitly out of Phase 18.14's own minimal scope) |

**Classification: PARTIALLY READY.** The GOVERNANCE MACHINERY for this
exact customer promise is fully built and proven. The DATA COVERAGE to
honor this exact literal request does not exist yet — and, critically,
**Bujji would not lie about it**: `check_backtest_eligibility()` is
specifically designed to say "no, and here is exactly why" rather than
silently proceeding on incomplete data. This is the correct, honest
state to freeze at — a system that would report `PARTIALLY READY`
truthfully is safer to build a backtester on than one that would
report `READY` falsely.

## 5. Final Freeze Decision

**B — Freeze with explicit exceptions.**

Not (A): the multi-year customer scenario in §4 cannot be honored
today, and Phase 19 needs to know that going in, not discover it after
building against an assumed-complete dataset. Not (C): every piece of
foundation work this decision would require is already built,
proven, and frozen (§1) — the exceptions are DATA COVERAGE facts
(one real day of options, one synthetic-only futures series,
single-underlying), not missing architecture. Building a backtester
today is safe PROVIDED it explicitly respects the four deferred risks
in §2 and the dependency contract in §3.

**Explicit exceptions Phase 19 must carry forward, not silently
inherit**:

1. Futures backtesting must either restrict to `SOURCE_LIVE` data or
   explicitly disclose synthetic-series usage (§2).
2. Phase 19's own code must never import `bujji.replay`/`bujji.qualification`
   (§2).
3. Options backtesting is real and usable, but is bounded to
   ltp/bid/ask/OI at 5-minute resolution, on 2026-08-14 only, until
   more real capture days accumulate (§2).
4. Phase 19 must treat NIFTY as the only real underlying — no silent
   multi-instrument assumption (§2).
5. Phase 19 must call `check_backtest_eligibility()` before consuming
   any dataset, and must refuse to proceed on `eligible: False` rather
   than falling back to a raw, unchecked reconstruction (§3).
6. Lifecycle governance and eligibility checking (§1) are real but
   have only one phase of exercise behind them — Phase 19 will be
   their first genuine external consumer; any friction discovered
   there is expected, should be reported, and is not evidence the
   underlying Reality/Snapshot layers (which have 5+ phases of
   validation) are unsound.

This freeze is a statement about what has been PROVEN, not a claim
that everything is complete. Phase 19 can proceed today, on real,
audited, repeatedly-verified ground — as long as it treats the six
items above as load-bearing constraints, not footnotes.
