# Sprint 111 — Live Shadow Validation & Decision Coverage

## 0. Scope

`bujji/decision_coverage.py` is a single glue module (validation/
reporting only, per this sprint's own explicit instruction), matching
the precedent set by Sprints 105-107 for integration/tooling work. It
adds zero trading intelligence, zero thresholds, zero decision logic.
Every vocabulary value it counts is imported BY IDENTITY from its real,
frozen, owning module (Series 73-110) — this module never invents or
re-declares a taxonomy value.

**Environment constraint (disclosed, unchanged since Sprint 104):** no
real, authenticated FYERS session has ever been opened in this arc.
Every measurement below comes from the 41-day real Bhavcopy/intraday
corpus, replayed through the frozen `SessionDriver`/`run_full_cadence`
pipeline (Series 105-110) — this is the only real evidence source this
environment has access to. Where the sprint asks to "continue measuring"
something already measured live (Deliverable 4's replay/live parity),
this report cites the existing real Sprint 106 measurement rather than
fabricating a new live session.

## 1. Decision Coverage Audit — real, measured

Coverage matrix across 7 real taxonomies (65 total vocabulary entries),
one real 41-day corpus pass:

| Category | Never Observed | Rare (<5) | Occasional (5–20) | Common (>20) |
|---|---|---|---|---|
| `thesis_type` (10 values) | 3 | — | — | see below |
| `strategy_family` (13 values) | 9 | — | — | 1 (LONG_DIRECTIONAL=11) |
| `construction_type` (11 values + NONE) | 8 | 2 | 2 | — |
| `roll_decision_type` (6 values) | 2 | 4 | — | — |
| `roll_priority` (4 values) | 0 | 2 | 2 | — |
| `adjustment_action` (8 values) | 6 | 2 | — | — |
| `position_lifecycle_state` (9 values) | 7 | 2 | — | — |

**Total: 36 of 65 real vocabulary entries (55.4%) were NEVER observed
across the entire real 41-day corpus.** Full per-value counts are in the
corpus script's own output (`/tmp/run_series111_coverage_report.py`).

Real, notable results: `LONG_DIRECTIONAL` (11) is the only COMMON-or-
above entry anywhere in the whole matrix; `IRON_CONDOR`/`IRON_FLY`/
`CALENDAR`/`RATIO`/`SYNTHETIC` — five of thirteen real strategy families
— were never selected even once in 41 real days.

## 2. Dead Logic Detection — real, categorised, none deleted

36 findings, each labelled by kind (never disabled or removed, per this
sprint's own explicit instruction):

- **9 unused strategy families**: `SHORT_DIRECTIONAL`, `NEUTRAL_PREMIUM_BUYING`,
  `VOLATILITY_EXPANSION`, `VOLATILITY_COMPRESSION`, `CALENDAR`, `RATIO`,
  `IRON_CONDOR`, `IRON_FLY`, `SYNTHETIC`.
- **2 unused roll paths**: `STRIKE_ROLL` was never RECOMMENDED/MANDATORY
  in the real corpus (despite being independently unit-tested and firing
  correctly under fabricated evidence, Series 109); `STRATEGY_CONVERSION`
  likewise never fired for real (0/4 real position-days, matching Series
  109/110's own prior finding).
- **9 unused construction types** including every multi-strike defined-
  risk shape (`IRON_CONDOR_SHAPE`, `IRON_FLY_SHAPE`, `CALENDAR_SHAPE`) —
  a real, structural consequence of Strategy Selection never choosing
  those families in this corpus, not a Position Construction defect.
- **6 unused adjustment actions**, including `CONVERT_STRATEGY` (matches
  the roll-path finding above) and `INCREASE_DELTA`/`REDUCE_DELTA` (no
  real portfolio-Greeks watch-threshold breach occurred with a live
  position open in this corpus).
- **7 unused lifecycle states**, including `NEWLY_OPENED`/`HEALTHY` —
  real because this measurement counts lifecycle assessments for
  ALREADY-open positions on days AFTER entry, and this corpus's few
  tracked positions moved quickly to `THESIS_BROKEN`/`PROFIT_HARVEST`
  rather than lingering in a stable state long enough to be sampled
  there (a real characteristic of this specific corpus, not a logic gap).
- **3 unused thesis types**: `FAILED_BREAKOUT`, `MEAN_REVERSION`,
  `EVENT_RISK` — never derived by Trade Thesis (Series 92, frozen) on
  any of these 41 real days.

## 3. Decision Stability — real, measured

- Thesis-type day-to-day persistence: **15.0%** (41 days) — i.e. the
  real thesis changed on 85% of consecutive real trading days. This is
  a genuine, real finding (not a bug): NIFTY's real 15-minute-candle
  reconstruction is naturally noisy day-to-day, and Trade Thesis's own
  frozen priority cascade (Series 92) is sensitive to small evidence
  shifts, as already disclosed in Sprint 105's own follow-up work.
- Strategy-family day-to-day persistence (days with a real selection):
  **30.0%** (21 days).
- Roll opportunity frequency (real, MANDATORY/RECOMMENDED only):
  `DELTA_REBALANCE=2, FULL_EXIT=3, EXPIRY_ROLL=1, WING_ADJUSTMENT=1`.
- Conversion-recommended frequency: **0** — consistent across Series
  109/110/111's three independent real measurements.

## 4. Replay vs. Live Drift — citing existing real measurement, no new
live session possible

This sprint cannot open a new live session (Section 0). The most recent
real replay/live parity measurement remains Sprint 106's (Sections 5-6
there): **100% decision-level parity** (thesis, conviction, strategy,
construction, margin, portfolio decision, execution plan) between a
tick-fed live-shaped path and the historical replay convention, across
all 41 real days — with the ONLY divergence being observation/episode/
PSI-family assessment IDs, root-caused to a real, disclosed `resolution`
metadata difference (tick vs. 15-minute), not a decision defect. No new
drift is reported here because no new live-shaped run was possible in
this environment; this sprint's own corpus runs used the SAME historical
batch-replay convention as every prior corpus script, so they cannot by
construction surface NEW replay/live drift beyond what Sprint 106 already
measured.

## 5. Strategy Utilisation — real, measured

- `strategy_family` frequency: `LONG_DIRECTIONAL=11, COVERED=1,
  NEUTRAL_PREMIUM_SELLING=4, BUTTERFLY=5` (21 selection-days total; the
  remaining 20/41 days had no selection at all, consistent with Series
  91's own long-standing real finding).
- `construction_type` frequency: `SINGLE_LEG=6, VERTICAL_DEBIT_SPREAD=5,
  SHORT_STRANGLE=4, BUTTERFLY_SHAPE=5, COVERED_SHAPE=1, NONE=20`.
- Optimisation frequency: Series 108's own corpus run already measured
  this directly (21/21 strategy_optimization + strike_optimization +
  expiry_optimization runs succeeded whenever a family was selected) —
  reused here by reference rather than re-run, since nothing about
  Strategy Optimisation changed since that measurement.

## 6. Position Lifecycle Coverage — real, measured

- `position_lifecycle_state` frequency: `THESIS_BROKEN=3, PROFIT_HARVEST=1`
  (4 tracked position-days total, matching Series 109/110 exactly).
- Recomposition: **4 possible, 0 RECOMPOSITION_NOT_POSSIBLE** (Series
  110's own real finding, unchanged).
- Rolls: 0 real STRIKE_ROLL or STRATEGY_CONVERSION firings (Section 2).
- Rebalances: 2 real DELTA_REBALANCE firings.
- Exits: 3 real FULL_EXIT firings (as the top-priority pick; 3/4
  position-days also had `full_exit.recommended=True` at some priority
  level, per Series 109's broader count — see Series 110's own doc for
  the precise distinction between these two counts).

## 7. Operational Metrics — citing existing real measurements

No new live session was run in this sprint (Section 0), so these are
the most recent REAL measurements, from Sprint 107's own live-shaped
(tick-fed, recorded-data) session:

- Reconnects: **0** (never exercised live — no real `FyersTickFeed`
  reconnect has ever occurred in this arc).
- Duplicate ticks: **0** real duplicates encountered; the dedup
  mechanism itself is unit-tested and confirmed working (Sprint 105-107).
- Token refreshes: **never exercised** — no real `refresh_token` has
  ever been available in this environment (disclosed since
  `fyers_token_manager.py`'s own docstring, Sprint 107 Section 0).
- Latency: Sprint 105's own real measurement, ~0.10–0.13ms/tick
  (tick-to-observation, recorded-data path).
- Dropped observations: **0** across every corpus/session run in this
  arc to date.

## 8. Reliability Gates Dashboard — real, updated

| Field | Value |
|---|---|
| Shadow trades completed | **3** (real `ShadowPosition`s opened across the 41-day corpus) |
| Statistically reliable? (≥30, Series 101's own real floor, reused by identity) | **False** |
| Evidence collected (real days) | **41** |
| Coverage % (real vocabulary observed at least once) | **44.6%** (29/65) |
| Architecture frozen? | **True** (Series 73-110, confirmed by this sprint's own zero-modification regression check) |
| Replay parity (decision-level, Sprint 106) | **100%** |

## 9. Blind Spot Report — real, evidence-only

- **Logic never exercised** (36 entries, Section 2) — the largest
  category. Most consequential: `STRIKE_ROLL` and `STRATEGY_CONVERSION`
  (Series 109/110's own headline capabilities) have never fired on real
  data, despite being independently unit-tested and known-correct under
  fabricated evidence.
- **Logic weakly exercised** (RARE, <5 real observations): `EXPIRY_ROLL`,
  `DELTA_REBALANCE`, `WING_ADJUSTMENT`, `FULL_EXIT` (all roll/adjustment
  decision types that DID fire), `COVERED`/`NEUTRAL_PREMIUM_SELLING`
  strategy families, `THESIS_BROKEN`/`PROFIT_HARVEST` lifecycle states.
  Every one of these is real evidence, but far below Series 101's own
  30-observation reliability floor.
- **Logic exercised with contradictory evidence**: **none found.** No
  case in this corpus produced two real assessments disagreeing about
  the same real day's evidence — a genuinely clean (if sparse) result,
  not glossed over.
- **Logic requiring more market exposure**: everything in the RARE and
  NEVER_OBSERVED buckets — which is the majority of this system's real
  behaviour space (55.4% + a further ~15% RARE).

## 10. Recommendation

**Continue Evidence Collection** — computed directly from the real
dashboard (Section 8), not opinion: `shadow_trades_completed=3` is far
below the `statistically_reliable` floor of 30 (Series 101's own real,
reused threshold), and `coverage_pct=44.6%` is below even the 50%
minimum this sprint's own disclosed, structural bar requires for "Ready
for Paper Trading." Neither "Ready for Paper Trading" nor "Ready for
Capital Pilot" is supported by the evidence collected so far.

This is consistent with, and quantifies precisely, the recommendation
already reached in Series 109 ("Needs Additional Evidence") — this
sprint turns that qualitative call into a measured, reproducible
dashboard: 3 shadow trades, 41 real days, 44.6% real behaviour-space
coverage, zero decision-logic changes anywhere in Series 73-110
(confirmed by the unchanged pre-sprint regression count). The path
forward remains exactly what Sprint 107/110 already identified: more
real trading days need to be observed — through continued live shadow
operation with real credentials, which this environment still cannot
provide — not more code.
