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

## 2026-08-20 pre-open — biggest-gap answer + defined-risk mode

**The gap, measured not asserted.** Stop-loss, daily loss limit and emergency
brake all run on ONE 300s heartbeat. Over 168,194 real 5-min NIFTY bars
(2017–2026): worst intra-bar range **611.8 pts**; ≥100pts 54 bars/yr, ≥200pts
5/yr, ≥300pts ~1/yr. Against the real 2026-08-19 ATM straddle credit (240.95
pts, 1 lot): stop fires at ₹15,662 but the worst bar is **₹39,764 inside one
unchecked interval** — 2.5× the stop, 1.6× the ₹25,000 daily limit (checked on
the same heartbeat, so it cannot save it). At 1 lot, sizing does not rescue it.

Why this beat "no trades yet" as the biggest gap: **some gaps close by
waiting; this one only reveals itself, expensively.**

**Resolution (operator decision): defined-risk only for real money.**
- `VOLATILITY_COMPRESSION → IRON_FLY`, `NEUTRAL_PREMIUM_SELLING → IRON_CONDOR`
- Twins share short strikes EXACTLY (delta targets 0.50/0.50 and 0.20/0.20) —
  same view, same strikes, wings added. Trending branches already defined-risk.
- **Derived, not configured, fail-closed:**
  `defined_risk_only = config.get("shadow_mode") is not True`. No independent
  flag — a second switch is a second thing to forget. A test asserts no such
  key exists in the production config, so its appearance signals a bypass.
- Paper unchanged: naked shapes still reachable, evidence still accumulating.

7,490 green. Load-bearing test enumerates every regime and asserts the
real-money selection set ⊆ DEFINED_RISK_FAMILIES.

**Still open for real money:** management cadence 300s → 60s for undefined-risk
positions; websocket (certified, ~2.4 ticks/s, unwired) — either would make
naked shapes defensible later. Slippage still uncalibrated.

## 2026-08-20 pre-open — management cadence by risk class

Naked positions now revalue every **60s**; defined-risk keeps **300s** (wings
cap the loss between passes, so the extra calls buy nothing). 60s is the floor
worth asking for — the tick source polls at 60s. Budget cost ~0.03 calls/s
against a host-wide 8.3/s.

**The trap avoided:** `max_cycles: 78` meant "6h15m / 5min". At 60s it would
have ended management after **78 minutes**, leaving a naked position unwatched
from ~10:45 to the mandatory exit — silently worse than before. The cap is now
**derived** from the remaining window (`ceil(window/interval)+5`) with the
configured value as a floor. Zero interval short-circuits (tests use it).

**A real flaw the suite caught, shipped in the first version:** reading
`undefined_risk_cycle_interval_seconds` outright overrode an explicit
`cycle_interval_seconds: 0` with its 60s default — regression went 288s →
600s timeout because tests began really sleeping. Same shape would let a
config make the naked loop *slower* than the winged one. Now
`min(base, naked_key)` — **tighter, never wider**, with three tests on it.

Risk classification **fails closed**: unknown/missing family → treated as
naked → tighter loop.

7,520 green (272s, back to normal). Blind-window exposure for naked shapes cut
~5×. Genuinely continuous still needs the websocket (certified, unwired).

## 2026-08-20 pre-open — vision audit: two findings, both fixed

**1. The learning loop was open at the read end.** D-8 made outcomes durable;
`governor_context_builder` asks `AdaptiveRiskMemory.all_entries()` /
`.lookup()` on every entry decision — and the runner passed
`AdaptiveRiskMemory()`, fresh and empty, every session. `append_observation`
had **zero callers anywhere**. The risk chain was interrogating a memory that
could not answer. New `risk_memory_bridge.py` hydrates it from the durable
store; refuses to invent `volatility_regime` (UNKNOWN + named note, never
back-filled from today) or mfe/mae (None, never derived from realized_pnl).
Inert today (0 records), live from the first trade.

**2. Tests were writing into the REAL durable store.**
`_outcome_memory_store()` defaulted to `data/outcome_memory_events.jsonl`
under REPO_ROOT regardless of the session's artifacts root. Found by hydrating
it and getting **78 records back from a system that has never traded** —
sessions `OUTCOME-1/2/6`, family `SHORT_STRANGLE` (not in SUPPORTED_FAMILIES),
890 KB, growing every regression. They would have been the first thing the
hydrated risk memory learned from. **Fixed structurally**: the default now
derives from the journal's directory, so production is unmoved and any
sandboxed caller is sandboxed automatically. Polluted file **quarantined, not
deleted**. Two standing guards now assert the real store holds no synthetic
session ids and no unbuildable families.

7,535 green; a full regression no longer creates the real store at all.

### Audit gaps found but NOT fixed (operator decisions)
- **Sizing is static**: `desired_quantity: 1`, no sizing module anywhere. And
  `initial_risk` for the stop is a flat `requested_risk: 5000` — **independent
  of quantity**. Raise lots without raising it and the stop becomes
  meaningless. They must move together; nothing enforces it.
- Holiday calendar still `holiday_calendar_verified=False`.
- `_wing_width` percentage-vs-fraction trap; `_nearest_grid` clamping makes
  REJECT_IMPOSSIBLE_WING_WIDTH unreachable (both shared with IRON_CONDOR).
- Websocket certified but unwired; slippage modelled, never measured.
- Futures identity in the normalized store (rolled continuous vs real contract).

## 2026-08-20 pre-open — size and stop coupled

Three rupee figures were flat per-POSITION totals beside a separately
configured size, read from config at three call sites, never related:

    desired_quantity: 1
    requested_risk: 5000.0                     -> initial_risk (the stop)
    proposed_trade_effect.additional_margin    -> capital check
    proposed_trade_effect.additional_max_loss  -> risk budget governor

`strategy_risk_adapter` takes quantity and risk side by side and never relates
them ("requested_risk stands in as both initial_risk and current_risk"), so
only the caller could — and didn't. **At 5 lots the real risk quintuples while
the stop stays Rs 5,000**: fires on noise, and the risk-budget/capital checks
are sized for a position one fifth as large.

Fixed in ONE place: `_risk_budget()` reads all three as **per lot** and
multiplies by the lots; entry sizing and the exit policy's `initial_risk` both
read it. A test asserts the raw config reads are gone and there are exactly
two callers. **At 1 lot every value is identical to before** — no behaviour
change today.

New warning: a stop wider than the daily loss limit is incoherent (the daily
limit halts the session first). Reachable at 6 lots on today's numbers; now
logged instead of discovered by its effects. Degenerate sizes floor at 1 lot —
a zero budget would disable the stop, not tighten it.

7,549 green. Guard note: new files under `production_runtime/` are invisible
to the lineage guards while untracked and surface on commit — third time this
pattern has appeared; authorize by name, never advance the baseline.

### Still open (operator decisions)
- **Sizing itself is still static** (`desired_quantity: 1`, no sizing module).
  The coupling is fixed; adaptive sizing is a separate build.
- Holiday calendar unverified · `_wing_width` unit trap · `_nearest_grid`
  clamping · websocket unwired · slippage uncalibrated · futures identity.

## 2026-08-20 — FIRST LIVE CONTINUOUS SESSION

Everything upstream of the margin gate worked, in production, for the first
time:

- capture from the first tick: **09:15:00.866**, and **live VIX in the
  normalized store for the first time ever** (CP-D.2 proven)
- stability gate passed on cycles **6, 31, 46, 66** — the ~8% gate clearing
  repeatedly on a continuous day
- thesis + full 13-family verdict recorded on every derivation (D-5 live)
- three-part selector: SIDEWAYS+CONTRACTION → **short strangle**
- **strike selection CONSTRUCTED a real proposal on a live chain** — the
  live-premium fix proven in production (was 0/30 candidates two days ago)
- **PAPER_MARKET_SYNC quotes=82/82, 100% coverage**, depth=0 as disclosed
- **IV DIVERGENCE first live data:** strikes_agree=False, 5 disagreements,
  median |ΔIV| 0.0088, max |Δdelta| 0.119
- RISK BUDGET: 1 lot, ₹5,000 (per lot ₹5,000)

Then `GATE_B_MARGIN_VETO / MARGIN_NOT_CERTIFIED`.

### The blocker: a symbol format

Gate B sent `symbol=contract.symbol` — an INTERNAL identity. Measured live:

    'NIFTY2026-08-2524500CE'  (internal)  → verified=False  total=None
    'NSE:NIFTY26AUG22700PE'   (broker)    → verified=True   total=98,915.87

**The margin API was never broken. Every entry in Bujji's history was blocked
by a symbol format** — invisibly, because the failure path is an unusable
snapshot, not an error. Same trap D-7 fixed for the quote sync; it was here
too, unnoticed, because nothing had ever reached this gate.

Fixed by looking the symbol up from the chain row that produced each leg;
fail-closed on an unresolvable leg. **Verified live: the same strangle now
gives margin_verified=True, required ₹188,165.94 vs ₹500,000 → GATE B ALLOW.**

### Two more defects the same session exposed
- `PRICE LEVELS -- context build failed`: `MarketSnapshot.spot` is a
  SpotSnapshot OBJECT, the number is `.ltp`. L-5 passed the object, so every
  level-context build failed all day — silently, being observation-only.
- `"Entry did not fill (reason=not constructed)"` **when construction
  succeeded**. Reason read only `governor_result.blocking_stage`, which is
  None on a Gate B veto. Extracted to `_entry_failure_reason()`;
  `blocking_reason` now wins. A report that misstates the stage sends the next
  investigation to the wrong module — it cost an hour here.

7,570 green. **Tomorrow is the first session where an entry is actually
reachable.**

### Tooling lessons (mine, not Bujji's)
- `systemctl is-active` returns non-zero for `activating` — a long-running
  oneshot looks "dead" to a naive check. Compare `ActiveState` instead.
- `journalctl --since "today 09:14"` fails to parse; `--since today` works.
- Long-lived SSH watchers get reset (255). Use short-lived connections in a
  local loop.

## 2026-08-20 evening — direction gets three more sources

Audit finding: `determine_market_direction(psi, mssi)` used **two lenses, both
reading NIFTY spot price** at 30s polls. One instrument, one field. Disagreement
→ UNKNOWN, which is what the first live session reported most of the day.

**Now four lenses:**
- price structure (spot) · market structure (spot)
- **options positioning** (MPPI, ~199k option rows/day) — the slot
  `OPTIONS_POSITIONING_DIRECTION` had been in KNOWN_LENS_NAMES **unfilled**
- **futures basis change** — `FUTURES_POSITIONING_DIRECTION`, also unfilled

**No inversion** on MPPI: bias is already normalised to PRICE direction
(verified — call writers dominant → BEARISH_POSITIONING). **Confidence capped
at MODERATE** on both new lenses: positioning is intent, basis is one thin
interval; HIGH stays reserved for MSSI's structural breakout/breakdown.
**MIXED → UNKNOWN, never NEUTRAL.**

**Basis reads CHANGE, never level.** NIFTY futures carry a premium that decays
to expiry, so a level-keyed lens reports bullish every morning and bearish every
expiry. A basis deep in premium but *falling* is bearish; in discount but
*rising* is bullish — level logic gets both backwards.

### MPPI was running on 3 of 5 lenses and nobody knew
Plumbing "previous observation" for basis revealed
`assess_participant_positioning(chain, timestamp=...)` **never received a
previous chain** — so OI migration and OI expansion/contraction returned UNKNOWN
on **every cycle Bujji has ever run**. Their docstrings blame *"no intraday OI
history exists (Bhavcopy is end-of-day only)"* — true when written, false since
the live chain capture began. **One argument fixed it.**

### DEPTH IMBALANCE: deliberately NOT built
Requested, but the data does not exist in the decision path — `LiquidityReading`
has no quantities (its docstring says depth "is NOT assumed available"),
`get_depth()` has **zero consumers**, and the depth poller writes to layer0 which
the live cycle never reads. Building it would mean inventing the input.
**OPERATOR DECISION:** which data path — per-cycle `get_depth()` (+1 API call
against the shared budget) or reading layer0 mid-session (crosses a write-only
store boundary).

### My own error, recorded
A generic regex patch aimed at authorization guards **neutered an unrelated
safety test** (`assert True or ...`, always passes) guarding `broker/guard.py` —
files this work never touched. Restored; a grep for always-true assertions across
tests/ now returns none. **Lesson: never regex-patch assertions generically.**

Also: a lens-count assertion went stale **twice in one day** (2→3→4). All such
assertions are now name-based.

7,612 green.

## 2026-08-20 night — depth: acquired, recorded, NOT a lens

Per-cycle `get_depth()` wired (operator directive). **Field names verified, not
guessed** — `get_depth()`'s docstring forbids assuming a bids/asks shape without
live confirmation, so only `totalbuyqty`/`totalsellqty` are read, both recorded
in `data_certification/fyers_depth_discovery_20260813.json` (real example
266760/318435 → imbalance −0.0883). The 5-level ladders are **not parsed**.

**The lens is deliberately HELD.** `reconcile_lenses` ignores confidence when
detecting conflict, and takes the **minimum** confidence across opinionated
lenses. A single order-book snapshot is the thinnest of the four signals, so an
honest LOW confidence would (a) cap the whole direction read at LOW whenever it
spoke and (b) manufacture MIXED whenever the book leaned against real structure.
Switching it on would most likely have made direction **worse**.

**Recorded instead**, per derivation in `market_thesis.jsonl`:
`imbalance` (None when unobserved, never 0.0), `direction_concluded` (what MDI
actually said that cycle), `thesis_confidence`, and
`consumed_by_direction: false` written into the record itself.

**No threshold is baked in** — choosing a cutoff before measuring is exactly
what this record exists to inform. A test asserts no lean/threshold in the
payload. Three more tests pin that `msi_market_direction` and
`direction_bridge` contain no reference to depth and that `LIQUIDITY_DIRECTION`
stays unfilled — so the held boundary fails loudly rather than drifting.

7,644 green.

### Direction now: 4 lenses live, 1 measured
price structure · market structure · options positioning (5 OI lenses) ·
futures basis change — plus depth observed on the side.

### OPERATOR GATE (from data, after N sessions)
Does the book agree with structure or fight it? If it agrees, promoting it
needs one of: accept the LOW ceiling · weight reconciliation by confidence (a
philosophy change for ALL lenses) · a high abstention bar (threshold would need
calibrating from this very trail).

## 2026-08-21 — Layer 1/2 forensic audit, and six P0 repairs

Two read-only forensic audits scored acquisition **54/100** and storage/memory
**41/100**. Their shared conclusion is the important one, and it is not about
any single defect:

> The architecture is sound. The components are built, tested, and **not
> connected to the organism that trades.** A reviewer reading this repository
> would conclude Bujji is production-ready. A reviewer reading its runtime
> would not.

The worst finding: on 2026-08-20 the live session cited **346 supporting
observation ids and none of them resolved anywhere on disk**, while the
campaign report printed "Explanation completeness: 100%" over the same
session — because that metric counts populated fields, not whether what they
point at exists.

### Closed (b8bec1a, 1b47af6, 4a5872c, 0db15fa, 4306187 — 7,748 tests)

| # | Defect | Repair |
|---|---|---|
| P0-1 | 346/346 evidence ids dangling | persisted every cycle; proven 34/34 through the real pipeline |
| P0-2 | `completeness=1.0` hardcoded | measured; now takes 0.0 / 0.6 / 1.0 on real payloads |
| P0-3 | VIX writer wrote ONE_MINUTE, reader queried FIVE_MINUTE | reader reads both honest series; 10 historical snapshots byte-identical |
| P0-6 | dropped polls left no trace | `REASON_OBSERVATION_MISS` point event |
| P0-7 | provenance never enforced | non-LIVE origin refused; adapter told the real broker |
| P0-8 | no data-quality gate | hard, fail-closed boundary before entry |

Landed with it: Bujji's **first real latency measurement**. FYERS publishes
`last_traded_time` on the depth payload and it was being captured and never
compared. Exchange 09:15:12 against receipt 09:15:13.560706 = 1.560706s.

### Deliberately NOT closed

**P0-4, futures in the canonical store.** The capture session's own comment
already argued this correctly: writing a live near-month quote under
`NIFTY_FUT_CONTINUOUS` asserts a splice the code cannot justify. It is an
operator data-modelling decision, not a bug. `futures_status: ABSENT` is
reported honestly rather than filled in.

**P0-5, the four integrity checks** still have zero production callers.

**Evidence-trail enforcement.** A broken trail DEGRADES the verdict but does
not yet block. It does not make a decision wrong, it makes it unauditable —
and promoting it to a hard stop before one live session has measured the real
resolution rate risks a fail-closed gate that silently prevents all trading.
Promotion criterion: one full live session at a measured 100% rate.

### Two lessons worth keeping

The lineage guards run `git diff` and therefore **only see tracked files**. A
new file in a protected package passed a clean 7,737-test run, then failed
seven guards the moment it was committed. `git add` before trusting green.

The `CaptureLifecycleTracker` rejected my first gap implementation with a
precise error: a miss is a POINT event, not an opening condition. It was
right — modelling it as a condition would have suppressed the second and
third miss as "the same condition", when three consecutive dropped polls are
three distinct holes in the series.

## 2026-08-21 — Websocket wired into the trading runner (c7cb659)

`WebsocketTickProvider` at the existing `IntradayPriceProvider` seam: feed
primary, per-symbol REST fallback via the execution-neutered live data
broker, `TickSilenceWatchdog` driven off the management loop's own cadence.
Stale (>90s) / zero / unreadable ticks are not prices; both sources dry
yields None and `revalue()` refuses the group. Production config switched
`tick_source: broker → websocket`; identical `market_thesis_live` fail-closed
guard. Feed stopped in `_shutdown`.

Runtime-proven after hours with the day's real token: connect, subscribe,
`is_connected=True`, clean stop. **First tick-priced management pass still
needs tomorrow's live session** — watch for "websocket priced N/N legs" vs
REST-fallback lines, and the watchdog staying HEALTHY.

Cadence note: the naked-position management interval stays at 60s. With the
websocket live, lowering `undefined_risk_cycle_interval_seconds` is now
meaningful (the tick source is no longer the floor) — an operator trading
call, deliberately not made here.
