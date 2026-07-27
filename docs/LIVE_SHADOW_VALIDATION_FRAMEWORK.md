# Sprint 106 — Live Shadow Validation Framework

## 0. Scope and framing

Per this sprint's own spec: "success is measured by observed behaviour,
not new architecture." This sprint adds no new MSI package and modifies
zero frozen decision modules (MSI, Strategy Selection, Portfolio,
Lifecycle — all untouched, verified by the full regression suite
remaining at parity with the pre-sprint count plus only this sprint's
own new tests). It adds one new integration/tooling module,
`bujji/live_shadow_validation.py`, that:

1. Runs the FULL decision chain (Strategy Suitability → Strategy
   Selection → Trade Construction → Position Construction → Portfolio
   Construction → Margin Bridge → Execution Planning → Shadow Trading →
   Decision Auditor) on top of `SessionDriver` (Sprint 105 + its three
   follow-ups), which itself already runs the tick → Observation →
   Event → Episode → MSI → Thesis → Expression stages.
2. Compares any two runs of that chain field-by-field and explains every
   mismatch with a concrete, categorised reason.
3. Generates a human-readable daily parity report.
4. Captures real operational metrics (tick/observation/episode/decision
   counts, reconnects, dropped ticks, duplicate observations, decision
   latency, cadence duration, peak memory).
5. Evaluates the spec's own six success criteria against real,
   caller-supplied measurements.

**Environment constraint (disclosed, unchanged since Sprint 104):** no
live, authenticated FYERS session can be opened from this environment —
no credentials, no market-hours access here. Every "live" run in this
sprint's tests, smoke checks, and corpus measurement feeds real,
recorded intraday ticks through `SessionDriver.process_tick` one at a
time, exactly as a genuine live feed would — this exercises the real
live-shaped code path (tick-by-tick event/episode construction, real
duplicate-drop counters, real per-stage call sequence) but is **not** a
substitute for observing an actual live network session. That remains
open; see Section 5.

## 1. Live session runner (Deliverable 1)

`run_full_cadence(driver, *, spot, day, portfolio, open_positions,
timestamp)` extends `SessionDriver.run_decision_cadence` (which stops at
Thesis/Expression and a partially-populated Decision record) through the
rest of the Series 87-100 chain, reusing the identical call sequence
every corpus-replay script since Series 99 already uses (`_day_signal`'s
own tail) — no decision function's signature or behaviour is touched.

Requirements verified directly:
- **Never imports execution modules, never submits broker orders**:
  `test_module_never_imports_execution_or_broker_or_calls_place_order`
  (AST-based, not raw string search — this project's own established
  false-positive-safe convention, since a raw `"execution" not in
  source` check would trip on this module's own docstrings explaining
  what is *not* imported).
- **Restart-safe via the existing `ProcessLock`**: `SessionDriver.acquire
  ()`/`.release()` are reused unmodified (Sprint 105); this sprint adds
  no new lock logic.
- **Persists every assessment**: every stage's real, frozen assessment
  object (`ssf`, `selection`, `position_construction`,
  `trade_construction`, `margin_estimate`, `portfolio_decision`,
  `execution_plan`, `shadow_position`, `decision`) is returned on
  `FullCadenceResult`, in-memory for the caller to persist — consistent
  with Sprint 102's own disclosed finding that this arc's journals are
  in-memory, per-run (no new persistence layer invented here).

## 2. Replay parity recorder (Deliverable 2)

`build_parity_report(day, live_result, live_cadence, replay_result,
replay_cadence)` compares, field by field: observation-supporting-ids,
episode-evidence-ids, PSI/MSSI/MDI assessment_ids, MPPI positioning
bias, VSB volatility regime, Consensus level, Thesis type + conviction,
selected strategy family, Position Construction id, Margin Estimate id,
Portfolio Decision id, Execution Plan id — exactly the list the spec
names ("Observation IDs, Episode IDs, MSI assessment IDs, Thesis,
Strategy, Position construction, Margin estimate, Portfolio decision,
Execution plan").

Every mismatch is classified into one of the four spec-named categories
(`FAILURE_CATALOGUE`: DATA, PIPELINE, DECISION, INFRASTRUCTURE) with a
concrete, field-specific explanation string — never a bare "values
differ" (Deliverable 5).

## 3. Daily parity report (Deliverable 3)

`generate_daily_parity_report(day, metrics, parity)` produces the exact
report shape the spec sketches (ticks/observations/episodes/decisions,
per-field match percentages, an overall assessment-ID match rate, and
every mismatch listed with its category and explanation), plus the real
operational metrics appended.

## 4. Operational metrics (Deliverable 4)

`record_operational_metrics(...)` — every number is either a real
counter already tracked on `SessionResult` (observations, episodes,
reconnects, dropped_ticks, duplicate_events), a real caller-supplied
timing (decision/option-chain/quote latency, cadence duration — this
module never reads the wall clock itself, mirroring this whole arc's
own ID-determinism discipline), or a real `resource.getrusage(...)
.ru_maxrss` reading (peak memory, kilobytes). Per the spec's own
framing, these are baselines only — no threshold or optimisation is
attached to any of them except the one the spec itself sets (parity
≥99%, see Section 6).

## 5. Real corpus measurement (all 41 real Bhavcopy-covered days,
2026-05-25 through 2026-07-22)

Two distinct comparisons were run, because "replay" is ambiguous without
specifying which reference:

### Comparison A — rerun determinism (live-vs-live, identical code path)

The SAME live-shaped path (`SessionDriver` fed tick-by-tick via
`process_tick`) executed twice per day, using real recorded ticks both
times.

**Result: all 41/41 days byte-identical, including every assessment_id,
observation_id, and episode_id — 100% (615/615 fields).** This is the
spec's own "100% deterministic rerun" criterion, measured literally, not
assumed.

### Comparison B — live-tick-fed vs. the pre-existing historical-replay
convention

`SessionDriver` fed tick-by-tick (`RESOLUTION_TICK`, `source=
"recorded_stream"`) vs. the batch/15-minute-candle convention every
Series-88-onward corpus-replay script in this project already uses
(`RESOLUTION_FIFTEEN_MINUTE`, `source="FYERS_REAL_INTRADAY"`) — same 41
real days, same underlying real price ticks in both cases.

**Result: 410/615 fields matched (66.7%) overall, but 287/287
decision-level fields matched (100%)** — thesis type, conviction,
selected strategy family, position construction, margin estimate,
portfolio decision, and execution plan were **identical on every single
day** between the two paths.

**Root cause of the 205 non-matching fields (all in
observation_ids/episode_ids/PSI/MSSI/MDI assessment_ids), found by
reading `market_observation/engine.py::build_observation` directly:**
`resolution` and `source` are both part of the observation_id's content-
hash identity seed (`bujji/market_observation/engine.py:94-102`). The
live tick path legitimately uses `RESOLUTION_TICK` (per-tick data,
`live_observation/engine.py::translate_event`'s own real default for
`EVENT_TICK_RECEIVED`), while the historical-replay convention
legitimately uses `RESOLUTION_FIFTEEN_MINUTE` (this project's real
intraday data is stored as 15-minute candles) — two genuinely different,
correct real-data resolutions of the *same* underlying price series.
Because `resolution` is part of the observation identity, these two
paths will **never** byte-match at the observation/episode/PSI-family
level, even given identical prices, and this is not a bug in either
path — it is disclosed here as a structural fact about how
`observation_id` is minted, verified against the real function that
mints it. This is exactly the kind of "mismatch with a concrete
explanation" this sprint's Deliverable 5 asks for, not a defect to fix
in this sprint (no MSI/observation code was touched).

Mismatch category distribution across all 41 days (Comparison B):
```
DECISION/ASSESSMENT_ID_MISMATCH  (PSI/MSSI/MDI ids, downstream of the resolution difference): 123
PIPELINE/OBSERVATION_MISMATCH:                                                                41
PIPELINE/EPISODE_MISMATCH:                                                                     41
```
(3 mismatched fields × 41 days = 123 + 41 + 41 = 205, matching the
410/615 tally above exactly.)

## 6. Success criteria (Deliverable 6), measured against the real
41-day corpus

| Criterion | Result |
|---|---|
| 100% deterministic rerun | **True** — Comparison A, 41/41 days, 615/615 fields |
| No crashes | **True** — all 41 days ran to completion in both comparisons |
| No duplicate decisions | **True** — `test_duplicate_tick_never_produces_a_duplicate_decision` confirms a re-fed tick is dropped, never double-counted |
| No dropped sessions | **True** — every `ProcessLock.acquire()`/`.release()` pair completed cleanly across all runs |
| Replay/live assessment parity ≥99% | **False at the raw field level (66.7%, Comparison B) — but 100% at the decision level.** The spec's own 99% bar is not met literally if "assessment IDs" includes observation/episode/PSI-family ids compared against a *different-resolution* reference. It IS met (100%) for every field that actually reaches a trading decision. |
| Every mismatch explained | **True** — all 205 real mismatches in Comparison B trace to one root cause (resolution/source identity-field difference), verified by reading the real `build_observation` source, not guessed |

**Honest overall read:** the framework and its measurements work
exactly as specified. The literal ≥99% criterion is not met when
"replay" means "the pre-existing 15-minute-candle corpus convention" —
but that comparison was never an apples-to-apples test of the live
path's correctness; it mixes a genuine resolution difference into the
parity number. The comparison that actually answers this sprint's
motivating question — "does BUJJI make the same decision live that it
makes in replay, and if not, why" — is the decision-level one, and it
is unambiguous: **100% across all 41 real days, zero exceptions.**

## 7. Failure catalogue (Deliverable 5)

```
DATA            MISSING_QUOTE, LATE_QUOTE, CHAIN_UNAVAILABLE
PIPELINE        OBSERVATION_MISMATCH, EPISODE_MISMATCH
DECISION        DIFFERENT_CONVICTION, DIFFERENT_THESIS, DIFFERENT_STRATEGY,
                DIFFERENT_CONSTRUCTION, DIFFERENT_MARGIN, DIFFERENT_PORTFOLIO_DECISION
INFRASTRUCTURE  RECONNECT, CLOCK_DRIFT, DUPLICATE_CADENCE, DROPPED_TICK
```
`DATA`/`INFRASTRUCTURE` subcategories are defined but not yet observed in
this sprint's measurements — no live session has ever been run (Section
0), so reconnects/late-quotes/clock-drift have no real occurrences to
report yet, honestly disclosed rather than fabricated.

## 8. Out of scope (Deliverable 7) — confirmed honoured

No strategy was optimised, no threshold was tuned, and no line was
changed in MSI, Strategy Selection, Portfolio Construction, or Position
Lifecycle engines. The one real parity gap found (Section 5/6) was
recorded and explained, not "fixed" — per the spec's own explicit
instruction ("If parity fails, record it — don't 'fix' it during the
same sprint").

## 9. Testing and regression

12 new tests in `tests/test_live_shadow_validation.py`: failure-catalogue
shape, full-cadence integration (real shadow position on an admitted
day, never a broker fill), rerun-determinism (byte-identical), mismatch-
explanation coverage, decision-level-parity assertion, report
formatting, success-criteria pass/fail logic (threshold, unexplained
mismatch, crash/duplicate/dropped-session gating), real operational-
metric reflection, AST-based execution/broker/place_order import ban,
and duplicate-tick-never-duplicates-a-decision. All 12 pass. **Full
regression suite: 2764/2764 passing** (2752 pre-sprint + 12 new).

## 10. Recommendation

**Continue toward supervised live shadow operation, with one item still
open.** This sprint closes the code-level question this whole arc has
been building toward: given real data, does the live-shaped path reach
the same trading decision as the historical replay convention? Answer,
measured across all 41 real days: **yes, always, at every
decision-relevant field.** The literal ≥99% raw-field parity bar is not
met, but only because the two paths' `resolution` metadata differs by
design, a structural fact now disclosed and understood, not a live-vs-
replay defect.

What is still genuinely open, unchanged since Sprint 104: **no live,
authenticated FYERS session has ever been exercised in this arc.**
`FyersTickFeed`'s real reconnect behavior, real `get_quote` latency
under live network conditions, and genuine duplicate/late-tick handling
from an actual exchange feed remain unmeasured — everything in this
sprint (and the three prior follow-ups) used real recorded data or
duck-typed test doubles, never a live socket. That live exercise is the
one remaining gate before Sprint 104's own "begin supervised live shadow
operation" recommendation can be acted on for real.
