# Phase 18.6 — Historical Research Dataset Factory Audit

**Status: AUDIT ONLY. Zero code changes.** Design concepts below
(`ResearchCalendar`, `DatasetVersion`) are documented, not implemented,
per this phase's own instruction.

---

## 1. How Does Bujji Detect a Trading Session Is Complete and Research-Ready?

**Direct answer: it doesn't, today — "complete" currently means "the
wall clock passed a fixed time," never "the expected data actually
landed."**

Re-read every capture script's own stop condition:
`capture_options_reality_session.py:87-90`'s `within_market_hours()`
compares `now.time()` against a hardcoded `MARKET_OPEN`/`MARKET_CLOSE`
— when the clock crosses `15:30`, the script logs `"Market hours ended
mid-run -- stopping cleanly"` (Phase 17I.10) and exits. **This is a
time-based stop, not a completeness check.** Nothing re-queries
`HistoricalObservationStore` after stopping to confirm the expected row
count landed, that no cycle silently failed mid-session, or that every
expected expiry was actually captured that day.

A real, existing, but **completely unwired** capability was found:
**`bujji/market_calendar.py`** (Sprint 112) — a genuine, offline,
versioned trading-day calendar (`MarketCalendar.is_trading_day()`,
holiday/half-day awareness) with one deliberately honest design
choice: `holiday_calendar_verified` defaults `False` because its own
`HOLIDAY_CALENDAR` dict is an **empty, unverified template** — the
module's own docstring states plainly that no real NSE holiday list has
ever been populated into it, and warns loudly rather than silently
pretending completeness. **Confirmed by grep: zero call sites import
`market_calendar` from any capture script, the readiness contract
(Phase 18.5), or `market_reality_snapshot`.** This means Bujji today
discovers a non-trading day only empirically — by running a capture
script and getting zero rows back (exactly what happened with
2026-08-01 in Phase 18.4/18.5) — never by consulting a calendar in
advance.

**What DOES exist and could answer "research-ready," reused, not
invented**: `check_research_session_readiness()` (Phase 18.5) already
answers "is spot/futures/vix/options all present for this date" — but
it must be called explicitly, after the fact, by an operator; nothing
calls it automatically at end of day.

## 2. `ResearchCalendar` — Design Concept (not implemented)

A day-by-day index over what Phase 18.5's readiness contract already
computes per-date, extended across a range — the natural next layer,
reusing every existing primitive rather than inventing new ones:

```
ResearchCalendar
  entries: Dict[date_str, ResearchCalendarEntry]

ResearchCalendarEntry  (one per date; NOT a new store -- a VIEW,
                         same "thin view, never a new store" precedent
                         reconstruction.py/readiness.py already
                         established)
  date:                     str
  is_trading_day:           Optional[bool]   # from MarketCalendar,
                                              # IF wired -- None today,
                                              # since nothing calls it
  resolution:                str
  instrument_availability:   {spot: bool, futures: bool, vix: bool,
                              options: bool, depth: Optional[bool]}
                              # = ResearchSessionReadiness's own fields,
                              # reused verbatim, not re-derived
  certification_state:       bool             # = certified_lineage_available
  fingerprint_available:      bool             # True iff a snapshot could
                                                # be built at all (EMPTY
                                                # snapshots still fingerprint,
                                                # Phase 18.3 -- so this is
                                                # really "was this date
                                                # ever checked," not "does
                                                # real data exist")
  readiness_status:           one of:
     COMPLETE        (ResearchSessionReadiness.is_complete)
     PARTIAL         (some but not all instruments present)
     EMPTY_TRADING_DAY (a real trading day, zero data -- a genuine gap)
     NON_TRADING_DAY   (weekend/holiday -- correctly not a gap)
     UNKNOWN           (never checked -- distinct from EMPTY; Phase
                        18.4's own "no fake completeness" discipline
                        applied to the calendar itself: a date nobody
                        has run readiness against yet must not silently
                        read as EMPTY)
```

**Every field except `is_trading_day` is already computable today from
existing Phase 18.5 primitives, called once per date.**
`is_trading_day` is the one field genuinely blocked — not by missing
code (`MarketCalendar` exists), but by the unverified holiday list
(§1's own finding): populating `ResearchCalendarEntry.is_trading_day`
honestly requires either (a) wiring `market_calendar` in with its
current `holiday_calendar_verified=False` (meaning it reports "trading
day" for any unlisted weekday, including real holidays — not reliable
enough to distinguish `EMPTY_TRADING_DAY` from `NON_TRADING_DAY`
without a human first populating the real NSE calendar), or (b)
continuing to infer non-trading days empirically the way this project
has so far (2026-08-01 was identified as a Saturday only because
someone ran the readiness check and then manually checked
`date.weekday()`, Phase 18.4).

## 3. `DatasetVersion` — Design Concept (not implemented)

```
DatasetVersion
  dataset_version_id:    str   # e.g. a content hash of everything below,
                                # or a hand-assigned semantic label --
                                # NOT designed further this phase (out
                                # of scope: "audit only")
  created_at:             str
  coverage_range:          {start_date, end_date}
  resolution:               str
  included_instruments:     Tuple[str, ...]   # e.g. ("spot","futures","vix","options")
  reconstruction_version:    str               # = MarketRealitySnapshot.reconstruction_version
                                                 # (Phase 18.3) -- MUST be uniform across
                                                 # every snapshot folded into one DatasetVersion,
                                                 # or the "same logic version" guarantee breaks
  source_ingestion_runs:      Tuple[str, ...]   # = IngestionRun.ingestion_run_id, one per
                                                 # real fetch that contributed -- ALREADY a
                                                 # real, queryable field
                                                 # (`HistoricalObservationStore.ingestion_runs_for()`,
                                                 # Phase 17H.2), never wired into a
                                                 # dataset-level rollup today
  fingerprint_lineage:         Tuple[str, ...]   # = MarketRealitySnapshot.fingerprint(), one
                                                 # per date/as_of covered -- Phase 18.3's own
                                                 # method, callable today, never persisted
                                                 # at this rollup level
```

**Audit finding on representation, not just field list**: every field
above already has a real, working source EXCEPT the top-level
`dataset_version_id` itself and the act of *assembling* a range of
individually-fingerprinted snapshots into one artifact. This directly
confirms Phase 18.4's own §1 finding, re-verified this phase rather
than merely re-cited: **the ingredients exist; no container exists.**
`source_ingestion_runs` in particular is a genuinely new, useful
finding this phase — `IngestionRun` (Phase 17H.2) already exists,
already stores exactly this, and was never mentioned in Phase 18.4's
own artifact-model audit; it should be a first-class input to any
future `DatasetVersion`, not overlooked in favor of only cert-refs and
fingerprints.

## 4. Automation Boundary

Evaluated against what actually exists (no crontab entries found on
the VPS — `crontab -l` returns empty; every capture run to date,
including this project's own live sessions, was launched by a
human-initiated shell command or a manually-launched background shell
script like `run_market_open_campaigns.sh`):

**Should be automatic, after market close** (mechanically safe,
already real, idempotent-by-construction primitives — the risk of
running them unattended is low because every one of them fails closed
or no-ops safely on bad input, per Phase 18.2 §9/§10's own immutability
findings):
- Running `check_research_session_readiness()` for the day that just
  closed, for every resolution already captured — a pure read, cannot
  corrupt anything.
- Recording the day's readiness result somewhere queryable (a
  `ResearchCalendar` entry, per §2) — additive, not a correction of
  raw Reality.
- Computing (not yet persisting anywhere durable) a `fingerprint()` for
  that day's DAILY and, where complete, FIVE_MINUTE snapshot.

**Should remain manual** (per this project's own standing discipline —
"never delete/merge/refactor without explicit approval," "never commit
without explicit request" — extended here to data-affecting actions):
- Actually running the options capture script (already manual-by-design
  since Phase 17I.10, requiring a fresh FYERS token refresh — an
  intentional human-in-the-loop gate, not an oversight).
- Running the 5-min spot/futures/VIX backfill for a newly-identified
  gap date (this phase's own Task 1 precedent) — a real API-cost,
  real-write action; per this project's "Actions" risk framework, an
  action that writes real historical facts should stay a deliberate,
  reviewed step, not a silent nightly job, at least until the
  `ConflictingHistoricalObservationError` safety net (Phase 18.2 §9,
  proven) has more operating history behind it in an unattended
  context.
- Assembling or publishing any future `DatasetVersion` — explicitly a
  human decision (which date range, which resolution, which
  reconstruction_version) per §3's own design, not inferrable from
  data alone.
- Populating/verifying `HOLIDAY_CALENDAR` in `market_calendar.py`
  against the real NSE calendar (§1) — the module's own docstring
  already insists on this being a deliberate human act, not automated.

**The boundary, stated as one sentence**: automate *observing and
reporting* what already happened (readiness, fingerprinting), keep
*writing new historical facts or publishing artifacts* manual — matching
this project's entire standing pattern of read-paths being freely
composable while write-paths stay deliberate.

## 5. Remaining Blockers Before a Customer-Facing Backtesting System

Ordered by dependency, not just severity — each blocks the one after it:

1. **`ResearchCalendar` does not exist** (§2) — without it, there is no
   way to answer "which date ranges are even eligible for a customer
   backtest" except by manually re-running Phase 18.5's readiness check
   per date, one at a time.
2. **`DatasetVersion` does not exist** (§3) — without it, even a
   customer-selectable range of COMPLETE dates has no single artifact
   ID to hand back as "this is what you ran your backtest against,"
   which was this whole Phase 18 series' own stated success criterion
   (Phase 18.2's final verdict, restated).
3. **Options remains a single-day island** (Phase 18.5's own carried-
   forward finding, re-confirmed unchanged this phase — no new capture
   ran between 18.5 and 18.6) — the richest possible `ResearchCalendar`
   still has exactly one `COMPLETE` entry until options capture runs
   again on a future date.
4. **No automated detection of a newly-created gap** (§1, §4) — even
   once `ResearchCalendar` exists, nothing yet watches for tomorrow's
   equivalent of the 2026-08-14 gap (Phase 18.4's finding, closed by
   hand in Phase 18.5) recurring on 2026-08-15 or any later date.
5. **`HOLIDAY_CALENDAR` remains an unverified template** (§1) — a small
   but real blocker for `ResearchCalendar.is_trading_day` specifically;
   distinguishing "real gap on a trading day" from "correctly empty,
   market closed" currently requires a human to check `date.weekday()`
   by hand each time, as this project's own Phase 18.4/18.5 reports
   both had to do.
6. **Futures' synthetic-continuous-contract identity** and **the
   orphaned `bujji.replay` pipeline** (Phase 18.0, carried unchanged
   through every subsequent 18.x phase) remain open — not re-audited
   in depth this phase (no new evidence gathered), but still real,
   still blocking a fully honest customer promise about futures PnL
   specifically.

**None of these six are architecture rewrites.** Every one of them is
additive composition of primitives that already exist and were proven
individually across Phases 18.0–18.5 (`ResearchSessionReadiness`,
`fingerprint()`, `reconstruction_version`, `IngestionRun`,
`MarketCalendar`, `ConflictingHistoricalObservationError`) — the
remaining work is assembly and, per §4, a deliberate decision about
where the manual/automatic line sits, not new invention.
