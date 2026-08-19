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
