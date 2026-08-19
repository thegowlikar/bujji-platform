# BUJJI ENGINEERING LEDGER

Append-only record of engineering decisions and verified events. Newest last.

## 2026-08-18 (pre-charter, session summary)
- Full-tree audit (15 agents + critic): 1,016 uncommitted paths dispositioned; 954 committed in 6 thematic commits; pushed. Zero secrets.
- Ownership sweep: 369 root-owned paths under data/layer0/logs → 0.
- Risk-reality wiring: lot size from instrument master (65, fail-closed; config 75 was stale vs 2026-07-19 audit); whole-book SPAN margin via new bujji/broker/fyers_span_margin.py against the live-certified endpoint — OPERATOR CERTIFIED (providers.margin.type: fyers_certified) on live evidence ₹220,455.74/lot, benefit ₹124,987.20, deterministic; capital: live get_funds probe showed real account ₹0.00 → OPERATOR DECLARED ₹5,00,000 simulated (explicit YAML, loss-limit ratio preserved at 5%).
- Capture reliability: concurrent capture fix (sequential subprocess loop meant the 2nd script could never capture); token pre-flight built + timer 08:45; depth poller timer 09:17; spot-from-chain wired behind its own certification gate + certifier script.
- Safety-guard baseline advanced b148e39 → 360c003 (audited backlog); guards' function preserved.
- Paper-intelligence units: 09:20 beat-collision fixed → 09:27:30; installed; install-test caught missing sandbox dir (226/NAMESPACE) and missing market-open gate (pre-open artifact produced at 05:02 — gate added before any filesystem effect; test artifact quarantined).

## 2026-08-19 (charter day)
- **Chief Engineer charter accepted.** 13-agent forensic recon executed (26-row matrix, 22 findings dispositioned, 4 vision docs validated, adversarial critic). Archived in session task output.
- 08:45 pre-flight FAILED (correct: token expired 06:00). 09:10 shadow campaign died on auth — pre-flight's prediction exact.
- 09:14 operator refreshed token; refresh-file valid but NOT promoted — promoted at 09:15 by CE; shadow campaign restarted; 09:16 fires clean. **Lesson: refresh+promote must be one ritual.**
- 09:17 spot-from-chain CERTIFIED live (5/5, mean 0.062 bps) → first live SPOT rows ever at 09:21 (store, source=fyers).
- 09:17 depth poller first scheduled run (running). 09:22:42 trading session #2: honest NO_TRADE in 12s (regime_source=market_thesis, trend UNKNOWN — single-snapshot limitation confirmed).
- 09:27:30 paper-intelligence first fire REFUSED by own guard vs daily-intelligence all-day lock — structural deadlock; OPERATOR DECISION pending.
- 09:42 live incident: capture running 26 min, zero rows. Diagnosed via strace: **EROFS — layer0_data missing from unit ReadWritePaths** (mount sandbox granted logs/data/.env only; layer0 predates data/ convention). Fixed 09:49, proven live (15 QUOTE rows in 75s). Committed. Same class as 08-18 root-owned failures: layer0 outside data/ escapes every data/-scoped fix.
- **BUJJI_CHIEF_ENGINEER_MASTER_PLAN.md committed** (this ledger's sibling). Roadmap CP-A..CP-F; Gate 3 is the target; execution begins at CP-A remainder + CP-B tick-source fix.

## 2026-08-19 evening — Phase CP-A remainder + CP-B (executed post-close)

- **EOD verdict, first credible full day:** capture 17,043 rows / zero capture
  errors / intelligence replay_equivalent=True — but final_stage FAILED on
  `KeyError: 'open'`: the FIRST-ever live SPOT rows (point samples,
  {"ltp"}) broke the snapshot builder's OHLC assumption. Fixed (degenerate
  point-sample bar, disclosed in-code; unknown shapes return None — absent
  beats invented). Re-ran completeness for the day directly: spot ✓ (first
  ever), options ✓, VIX ✗ — honest: live VIX lands in layer0 while
  completeness reads SQLite (two-store split, Master Plan item).
- **CP-B / D-3 tick source:** LiveTickProvider now binds to the
  execution-neutered live FyersBroker (same instance as the regime path);
  FAILS CLOSED without it; synthetic requires explicit `paper_synthetic`.
  The "live quotes" log lie removed.
- **CP-B / D-4 warm-up:** providers.regime.warmup {polls:16, 30s} enabled in
  production yaml. E2E harness ran tomorrow's exact _startup() tonight:
  FyersBroker bound (not PaperBroker, same-instance check True), 16 real
  polls executed, stability gate HONESTLY REFUSED the closed-market tape
  (UNSTABLE_ACROSS_SAMPLING). CP-B proven end-to-end.
- **First-sample capture (operator ask):** capture timers 09:16/09:17 →
  09:14; scripts wait to the open instant via new
  bujji/market_reality/open_wait.py (staggered +0/+2/+5s against the shared
  10/s ceiling; far-from-open starts still refuse). The 09:15:00–09:15:59
  opening minute — previously never captured — is recorded from tomorrow.
- **D-10 backup:** bujji-backup.timer 16:30 nightly; first backup taken
  (1.85GB sqlite online-backup + layer0 + journals, keep 7). ON-BOX only —
  off-box destination = open operator decision.
- **D-9 alerts:** OnFailure=bujji-alert@%n on all 7 units → data/ALERTS.jsonl
  (proven with a forced alarm). File+journal only — push channel = open
  operator decision.
- Hygiene: 4 recurring root-owned files re-swept; 0-byte decoy store deleted.
- Guards evolved with the design: timer-fire test now pins the invariant
  (post-open OR pre-open+wait); layer0 no-writes guard moved to AST
  call-analysis after substring-matching false-positived on the identifier
  `wait_until_open`.
- Regression: 7,120 passed / 0 failed (run as the service user).

## 2026-08-19 late — CP-C/D-6 (safety spine, part 1)

- Gate B wired: proposal legs priced through the certified SPAN provider;
  capital_check.assess_capital gates the pipeline (VETO on any unverified or
  unaffordable margin). Composition root exposes the provider instances.
- Emergency close added: session-loss hard limit + sustained-blindness brake
  in every management pass, reusing the EOD close sequence; replay-blind
  sessions exempt by design.
- 12 new tests; 7,132 green as service user. Remaining CP-C: D-5 evidence
  persistence, D-7 broker realism inputs, D-8 outcome durability.

## 2026-08-19 night — Continuous session (operator directive)

- Bujji now lives through the whole market day: rolling 30s evidence window,
  regime re-derived per 5-min cycle, entry whenever the gate passes (one
  strategy/day unchanged), observation continues after any position close to
  15:30. Mandatory close = position rule, not lifetime. Production yaml opts
  in; single-shot preserved for replay/tests.
- Test suite caught a would-be live crash: poll-batch vs window stride
  validation conflated; both geometries now validated upfront.
- 7,138 green. Tomorrow 09:22:30 is the first continuous day.

## 2026-08-19 night — Bujji wakes with the market (09:15:00)

- Operator finding: the capture units were fixed for first-tick capture but
  the TRADING unit was left at 09:22:30, asleep for the opening 7.5 minutes.
- `OptionsOSRunner._await_market_open()` — authoritative in-process
  market-hours gate, before the broker connects: waits near the open,
  REFUSES far from it or past the session's configured end. Upper bound
  derived from existing config; deliberately NOT a fourth close constant.
  Explicit `--skip-market-hours-check` for replay/tests, guarded by tests
  over installed units, repo unit files and the production config.
- Timer 09:22:30 → 09:14, demoted to the coarse first net. The burst offset
  it was silently carrying moved to
  `session.continuous.decision_phase_offset_seconds: 150` (cycle 1 only), and
  `market_open_offset_seconds: 8` gives this unit its own slot now that three
  units release at the open.
- Rule learned: never move an order-placing unit's schedule without checking
  what that schedule is silently guarding.
- `git add -A` swept a day of live session output into the commit; caught
  pre-push. `layer0_data/`, `shadow_sessions/`, `paper_intelligence_sessions/`
  now gitignored (an open CP-D item, closed).
- 7,157 green.

## 2026-08-19 night — D-5: persist the reasoning, not just the verdict

- A session recorded its conclusion and discarded its reasoning: "why no
  trade on 2026-08-19?" had no answer in any artifact. Two real computations
  were dropped every cycle — `record_cycle()`'s returned understanding
  record, and the thesis's full 13-family verdict
  (preferred/rejected/insufficient_evidence).
- New `bujji/shadow_observatory/thesis_artifact.build_thesis_artifact()`
  (pure) + `recorder.record_market_thesis()` → `market_thesis.jsonl`, one
  record per derivation, linking cycle record → thesis → family verdict →
  stability verdict → the two strings the selector was actually handed.
  Absence recorded as absence: `absent_fields`, and `families_assessed=None`
  vs `0` distinguishes "never assessed" from "assessed, none fit".
- This makes the SELECTOR AUTHORITY question measurable: divergence between
  the 13-family verdict and the two-input lookup now accumulates in the
  artifacts, so that operator gate can be decided from observation.
- Deployment timer guard rewritten: it demanded a fire after 09:15 (correct
  only while the timer was the sole protection); it now asserts the pairing
  of a pre-open fire WITH the in-process gate.
- 7,172 green. NOT yet proven live — the derivation path needs a live
  broker, so the first real `market_thesis.jsonl` lands 2026-08-20.
- Next: D-7 (broker realism: set_quote/set_depth/set_capital before
  place_order), then D-8 (durable OutcomeMemoryRecord + real fees into
  close_position).

## 2026-08-19 night — D-7: the paper broker learns what the market looks like

- PaperBroker always modelled a spread (BUY lifts ask, SELL hits bid) and
  nothing in production ever called `set_quote`. Every fill fell back to the
  leg's own premium: a flat round trip cost **0.00**. With the sync, the same
  round trip costs **-0.90** — the full observed spread. Every paper result
  before tonight was optimistic by the spread on every leg, in and out.
- New `bujji/production_runtime/paper_market_sync.py`, called before any
  order. The trap avoided: the chain's symbol is FYERS-encoded while the
  broker keys on `_leg_to_core_contract`'s format — naive keying would be a
  SILENT no-op reporting full coverage. Test pins the key against the real
  bridge.
- Depth consumed only from real `bid_quantity`/`ask_quantity`; traded
  `volume` deliberately NOT used. Verified by reading the live provider that
  it populates neither today, and a test asserts that claim against its
  source so the disclosure cannot rot.
- `margin_per_lot` deliberately not set — Gate B owns real margin.
- 7,192 green.

## 2026-08-19 night — D-8: costs are real, memory is durable (CP-C COMPLETE)

- `close_position()` accepted `fees=`/`slippage=` all along; nobody connected
  the broker's own reported charges. Every outcome recorded GROSS as net.
  New `execution_costs.py` sums real charges+slippage across entry AND exit.
  Absence rule: no report → `None`, never `0.0` (zero fees claims a free
  trade). Partial coverage flagged `is_complete=False` and logged.
- `attribute_and_remember()` built a real OutcomeMemoryRecord and dropped it
  into an in-memory dict — Bujji forgot every trade it ever made. New
  `outcome_memory_writer.py` appends to the SAME EventStore
  `outcome_memory.recovery` already replays cross-session. `event_id =
  memory_id` → retries idempotent, conflicts rejected. Own path
  (`data/outcome_memory_events.jsonl`), not per-session.
- Safety guards: three new files under the protected `production_runtime/`
  prefix authorized BY NAME (`_CPC_EVIDENCE_AND_REALISM_AUTHORIZED`) across
  all six lineage guards — NOT by advancing the 360c003 baseline, which
  would blanket-approve every unreviewed diff since then.
- Tests first drafted with a thin fake record; the real reducer rejected it
  as malformed — correctly. Rewritten against a genuine OutcomeMemoryRecord,
  proving the live path's own output round-trips.
- 7,208 green. **CP-C is complete**: D-5 (evidence), D-6 (Gate B veto +
  brake), D-7 (fill realism), D-8 (net costs + durable memory).
- NOT yet proven live: D-5/D-7 need a live broker, D-8 needs an actual
  closed position. First real proof arrives with the first trade.
- Next: CP-D — cross-process FYERS rate budget (would retire the schedule-
  separation hack), fill-model collapse, heartbeat staleness watchdog,
  VIX/spot store unification.

## 2026-08-19 night — CP-D.1: host-wide FYERS rate budget

- The ceiling is per ACCOUNT; the pacer was per interpreter. Four processes
  share one credential (three now waking together at 09:14), each pacing to
  ~8.3/s → up to **~33/s against a 10/s ceiling**. Never seen as a crash —
  retries hid it. It surfaced as the trading unit's fire-time offset, i.e.
  schedule separation standing in for a resource budget.
- New `bujji/broker/rate_budget.py`: flock'd shared slot file. Reserve under
  the lock, sleep outside it. Applied FIRST in `_wait_for_slot()`; the
  in-process pacer stays behind it, so an unreachable budget degrades to the
  old behaviour, not to none. Fails OPEN, warns once — a rate ceiling is a
  throughput protection, not a safety guard.
- **Cost, stated up front:** throughput is now shared, not multiplied. An
  82-contract sweep takes ~10s wall clock while siblings interleave. That is
  the account's real capacity; the previous speed was an overrun.
- Implausible stored slots (corrupt, pre-reboot) discarded — honouring one
  would block every process for the last uptime and look like a hang.
- Guards: fyers.py diff pin advanced in 4 lineage guards with rationale in
  place, matching this choke point's three prior approved changes.
- Tests include a REAL two-process test with a real lock file; mocking flock
  would prove nothing about the fix. 7,221 green.
- This retires the reason the 09:22:30 offset existed. The offset itself
  stays for now (it is cheap and the budget is one night old) — revisit
  `decision_phase_offset_seconds` after observing live coverage.

### CP-D remaining (not started)
- fill-model collapse (broker_boundary vs PaperBroker)
- heartbeat staleness watchdog
- 08-14 value_kind re-normalization
- VIX/spot store unification (VIX live rows land in layer0, completeness
  reads SQLite)

## 2026-08-19 night — CP-D.2: VIX/spot store unification

- Measured, not assumed: **168,157 backfilled VIX rows in the normalized
  store, ZERO live ones.** The market-reality capture wrote only to layer0
  JSONL; completeness reads SQLite. Every evening's "VIX ✗" was real data
  sitting on disk, invisible to the only reader that mattered.
- The certification gate was never the obstacle — all four instrument types
  are already CERTIFIED_AVAILABLE for `direct_sdk_fyers_broker_py` (checked).
  The write path simply did not exist.
- Additive projection: Layer 0 written FIRST and unchanged; normalized row
  alongside, behind the same certification gate. No migration, no deletion, a
  projection failure never stops capture. Spot/VIX identities match the
  backfill exactly → one series, not two. 60s samples recorded as
  ONE_MINUTE + value_kind=MAPPING (point sample, not a bar), which also
  avoids natural-key collision with the chain's 5-minute spot rows.
- **Futures deliberately excluded.** Backfill's series is
  `NIFTY_FUT_CONTINUOUS` — a ROLLED synthetic contract. A live near-month
  quote is not that series; writing it there asserts a splice we cannot
  justify, and a real-contract identity would create a third series nobody
  reads. Left Layer 0-only, with the reason written in the code.
  **OPERATOR DECISION OPEN:** how the continuous futures series should be
  defined against live near-month quotes.
- 7,233 green. Tomorrow is the first day live VIX should appear in EOD
  completeness — verify rather than assume.

### CP-D remaining
- fill-model collapse (broker_boundary vs PaperBroker)
- heartbeat staleness watchdog
- 08-14 value_kind re-normalization

## 2026-08-19 night — L-1..L-5: the price levels & zones layer

Finding that started it: `PriceStructureAssessment` is entirely CATEGORICAL —
Bujji knew a swing was CONFIRMED and never knew at what price. Positive-
controlled grep: 186 files mention "volatility", **zero** mentioned any level
or zone vocabulary. Greenfield, not a refactor.

- **L-1 levels.** Pivot detection + agreement gate across strengths (3,5,8);
  identity is the pivot BAR INDEX, so no price-tolerance fudge. Real data:
  31,708 pivots → 12,792 survived (40.3%) over nine years; 297 → 130 over ~20
  sessions. ~59% are parameter artifacts.
- **L-2 zones.** Origin bar's own high–low band; impulse measured against the
  series' MEDIAN true range so it scales with price. Agreement across
  multiples (1.5/2/3). Lifecycle FRESH/TESTED/BROKEN, broken decided on a
  CLOSE not a wick. Real data: 93 → 20 survived, **9 live** — far more
  selective than levels, already decision-grade density.
- **L-3 context.** Proximity band (not calendar — operator directive; a test
  asserts NO lookback parameter exists). Funnel published: 130 → 123 in band
  → 119 tested → 10 published. **No invented relevance score** — two real
  orderings (proximity, strength) shipped separately.
- **L-4 refresh.** Daily snapshot from finished bars; dated, never
  overwritten; "newest on or before" selection so a replay cannot read a
  future map. Reader REFUSES live point samples and counts them. Live samples
  TEST structure, never form or break it.
- **L-5 record.** LevelContext persisted into `market_thesis.jsonl` beside the
  thesis. Never fatal, never silent: NO_SNAPSHOT/FAILED recorded, age in days
  published. Observation-only asserted by test (no decision module may import
  price_levels).

**FINDING FOR THE OPERATOR.** Proximity solves density, NOT significance. At
spot 24,366 the nearest levels are 6.8 / 9.0 pts away (5-min micro-swings,
noise at strangle scale); by strength they are 100–236 pts away with 107–165
touches. Which ordering governs, and what touch count makes a level worth
respecting, are TRADING judgements — both orderings ship, neither privileged.

**OPEN OPERATOR GATES:** (1) do levels feed strike selection at all;
(2) proximity vs strength; (3) install the refresh timer and at what time.

7,338 green. Nothing consumes the layer.

## 2026-08-19 night — options analytics + the live-premium defect

- **New `bujji/options_analytics/`**: IV/Greeks/skew DERIVED from observed
  prices. Forward + discount factor recovered by put-call parity least
  squares (no assumed rate/dividend); Black-76 on the forward; bisection IV.
  Real chain: parity R² ≈ 0.99999 on all three expiries, ATM IV 9.45/9.79/
  10.03% rising with tenor, negative skew steepening with tenor — textbook.
  All values carry value_class=DERIVED + model name. NOT wired (operator
  gate). Caveat recorded: DF noisy at weekly tenor, not readable as a rate.
- **CRITICAL DEFECT found while preparing the wiring**: construction engine
  read `row.settlement` as the only premium; live provider sets
  settlement=None (price in `close`). Live chains produced ZERO strike
  candidates → every live entry died REJECT_STRIKE_UNAVAILABLE. **Bujji
  could not construct a trade on live data at all.** Invisible because the
  whole suite drives the bhavcopy path. There were TWO no-trade gates, not
  one (stability ~8% was only the first).
- Fix: `_premium_for()` — settlement first (bhavcopy bit-identical), then
  two-sided mid, then last trade; absence never zero; basis recorded on the
  evidence. 12 guards (incl. byte-identical pins) authorized the one file BY
  NAME; baseline not advanced.
- **Deferred, operator gate**: replacing the engine's spot-BS (assumed rate)
  IV with the parity-based derivation — changes which strikes get sold =
  canonical strategy authority. Record divergence first.
- Lesson: a green suite proves the paths it drives. The live path was never
  driven end to end until tonight's probe. 7,380 green.

## 2026-08-19 night — Part 2 decided: measure, do not swap

Operator delegated the call. Built the divergence recorder; deliberately did
NOT swap the IV derivation driving strike selection.

**Why not swap tonight:** tomorrow is the FIRST day strike selection can run
live at all (premium fix) — swapping IV underneath it makes any odd first
trade unattributable; we had zero measurements; and tomorrow already carries
the largest new live surface Bujji has taken.

**Measured over 40 real captured snapshots (08-14 → 08-19), 27 comparisons:**
- disagree on some target: **20 (74%)**; agree on all: 7 (26%)
- median |IV diff| **0.492 vol pts**; median max |delta diff| **0.0685**
- agreement at the premium-selling target (0.20): CE 70.4%, PE 74.1%
- **when they differ: +50 points, 66 times out of 66** — systematic and
  one-directional, not noise. Parity's forward sits above spot, shifting the
  whole delta curve; a parity strangle sits one strike higher on both legs.
- rate: engine assumes flat 6.500%; parity implies 6.91% near, 5.99% monthly
  (weekly 12.38% — confirms the short-tenor DF noise caveat).

`divergence.py` calls the REAL `_build_strike_evidence` (import only, never a
copy — a copy would drift and measure my idea of the engine). Wired into the
entry path, recorded to the session summary, **consumed by nothing**.

**OPERATOR GATE, now decidable from a record:** does parity replace spot-BS
as the delta authority for strike selection? Evidence accumulates per entry
attempt. 7,397 green.

## 2026-08-19 night — charges rate card + three-part regime selection

**Charges verified against FYERS' published card** (corroborated vs Zerodha).
Three defaults were wrong: STT 0.10%→**0.15%**, NSE txn 0.053%→**0.0355299%**,
clearing charges **absent → 0.009%** (no field existed); GST base widened to
brokerage+txn+clearing+SEBI+IPFT. Correct already: brokerage ₹20, SEBI
₹10/cr, stamp 0.003% buy. Net: short strangle round trip ₹124.89 → **₹130.36**
(+4.4% understated). Errors partly cancelled — luck, not design; all pointed
at understating cost. Two existing tests failed and were RIGHT to (6 of 8
components summed; 6 of 8 rates zeroed) — completed, plus a reflection-based
test so a future rate can't be forgotten.

**Three-part strategy selection (operator directive; both approvals given).**
Old table: 2 tradeable outcomes, both neutral → TRENDING always no-trade,
because no directional credit spread existed anywhere. Now:
- SIDEWAYS + HIGH_VOL → **short straddle**; + LOW/CONTRACTION → **short strangle**
- TRENDING_UP → **BULL_PUT_SPREAD**; TRENDING_DOWN → **BEAR_CALL_SPREAD** (both new)
- vol EXPANSION checked **before** direction; every no-trade path preserved
- straddle-vs-strangle is config (`SIDEWAYS_SHAPE_BY_VOLATILITY`), invertible

**RISK POSTURE CHANGED:** naked short legs enter the book for the first time
(straddle/strangle are UNDEFINED risk). Gate B SPAN, capital check, limits,
brake and mandatory exit still apply; the SHAPE no longer bounds loss.

Real-chain proof — all four construct: straddle CE/PE 24050 (credit 240.95);
strangle CE24350/PE23850 (69.65); bull put 23850/23650 (19.80, max loss
180.20); bear call 24350/24550 (25.35, max loss 174.65). Spread shorts match
the strangle's strikes — one 0.20 delta policy.

**Two PRE-EXISTING defects found and pinned, not fixed** (shared with
IRON_CONDOR): (1) `expected_move_pct` is a percentage number, not a fraction —
0.006 silently falls back to the 200pt constant while still reporting
"expected_move"; (2) `_nearest_grid` clamps, so REJECT_IMPOSSIBLE_WING_WIDTH is
unreachable and an impossible wing becomes the furthest strike. Both are
OPERATOR DECISIONS.

14 guards authorized by name; baseline not advanced. 7,439 green.
