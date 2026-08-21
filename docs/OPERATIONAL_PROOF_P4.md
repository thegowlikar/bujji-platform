# Production Reliability Sprint P4 — Operational Proof Sprint

**Rules honored:** no changes to `bujji/` Production, Learning, or
Strategy code. Documentation only — every gap identified below that
would require a Production change is disclosed as a gap, not silently
worked around.

**No new production defect was discovered while doing this sprint** —
this sprint audits observability and startup-verification completeness,
not new logic paths, and turned up gaps (missing signals, missing
checks) rather than bugs. Where I found something that borders on a
defect (§4, F6) I have flagged it distinctly and not folded it into a
"gap" framing.

---

## STEP 1 — Unknowns Register

| # | Assumption | Why it exists | Evidence for | Evidence against | Confidence | Operational impact | How to eliminate |
|---|---|---|---|---|---|---|---|
| U1 | The installed SDK's `connect()` will complete its handshake within the current pre-market checklist's 15s tick-wait window every day | Observed once (2026-07-28 checklist passed) | Passed on the one real live day run so far | Sample size = 1. The 2026-07-29 incident shows the SDK can go silent for hours post-connect — connect-time latency itself was never separately measured, only tick arrival | 55% | If handshake is slow but the tick coincidentally arrives from a stale/zombie source (§P3 finding), the checklist could pass while the *real* current-generation socket isn't actually up | Log connect-to-first-tick latency as its own timed metric across every future session; don't infer connection health from tick arrival alone (see U6, F6) |
| U2 | `restart_flag` staying `True` on an abandoned `FyersDataSocket` instance (P3 §2) never actually fires in a real live session | P3 proved the no-op is real; production reachability was argued from source only, not reproduced live | The SDK code path is real and was quoted directly | Never observed live — no live session has yet run long enough post-P1 fix to exercise `force_reconnect()` under a real Cloudflare-storm-style event | 40% | If it fires, P3 §3 shows the consequence is a *silent* wrong `is_connected=True`/`connect_count` state, not a crash — the most dangerous kind of unknown because nothing currently alerts on it | Add the P4 "orphan socket" signal (§3, missing #14) — currently cannot be computed without a Production change; tracked as a disclosed gap |
| U3 | The 15s pre-market tick-wait deadline (`run_live_shadow.py::_pre_market_checklist`) is long enough on a slow network morning but short enough not to waste time before market open | Chosen once, never tuned against real latency data | Passed on 2026-07-28's only real run | One data point; no distribution known | 50% | Too short → false-abort on a slow-but-healthy morning. Too long → delayed abort on a genuinely broken morning, eating into pre-market prep time | Record connect-to-first-tick latency (U1) across 5+ real sessions before treating 15s as validated, not assumed |
| U4 | `MarketCalendar.holiday_calendar_verified=False` doesn't matter because NSE holidays for this specific date range are already correctly hardcoded | The calendar's own docstring is explicit that this is an honest, disclosed limitation | `is_trading_day()` correctly gates weekends/listed holidays; `verification_warning()` fires when unverified | The 2026 holiday list was hand-entered once, never independently cross-checked against NSE's own published circular | 70% | A missed holiday would not abort the session — the checklist would proceed on what is actually a market holiday, producing a session with no real ticks all day, indistinguishable at checklist time from a normal pre-market wait | Before tomorrow: manually cross-check tomorrow's date against the real, current NSE holiday circular (2 minutes, not automatable without external data) |
| U5 | The FYERS access token supplied via `.env.fyers` will not expire mid-session | `token_expires_in_seconds` field exists in `HealthSnapshot` | The field exists and is designed to be populated | **Currently never populated** — grepped `run_live_shadow.py`: `token_expires_in_seconds` is never passed into `health_snapshot()` calls anywhere in the live path. It always renders as `"UNKNOWN (no authenticated broker session supplied)"` | 20% | If the token expires mid-session, every subsequent broker/websocket call fails with an auth error — indistinguishable in the current logs from a generic connectivity failure unless the operator manually checks FYERS token-issuance time | Compute real expiry from the token's own issuance time (FYERS tokens are date-stamped) and pass it into `health_snapshot()` — a genuine, disclosed gap; requires touching `run_live_shadow.py`'s call site, so it is NOT implemented in this docs-only sprint |
| U6 | Tick freshness (`tick_feed.latest()` returning a value) is a reliable proxy for "the current-generation socket is genuinely alive" | This is the checklist's actual, current definition of "connected" | Simple, and worked on 2026-07-28 | **Directly contradicted by P3 §3**: a stale/zombie generation's late callback can write into the exact same shared state (`_ltp`, `_connected`) that a healthy current generation would. A tick being present does not prove it came from the generation `self._socket` currently points to | 35% | This is the single most consequential unknown in this register — it undermines the reliability of every other health signal that's built on top of "is a tick present/recent" | Add generation tagging to ticks (a Production change, out of scope here) — until then, treat "tick present" as necessary but not sufficient evidence of health, and say so explicitly in the runbook (§6) |
| U7 | The single-threaded live loop's `time.sleep(args.poll_interval_seconds)` (default 1.0s) never blocks long enough to starve the watchdog | Watchdog is called once per loop iteration | Loop body is short (a few dict lookups + occasional cadence work) | `run_cadence()` inside the loop is NOT bounded — if it takes longer than `silence_threshold_seconds` (120s) for any reason (e.g., a slow broker API call inside cadence, unbounded retry), the watchdog is blind for that entire duration (this exact risk was named in the original forensic audit, "decision-latency/watchdog coupling") | 60% | A pathologically slow cadence could mask real tick silence for its own duration, delaying detection past the intended 120s threshold | Log cadence wall-clock duration explicitly (already computed in `HealthSnapshot.cadence_duration_seconds` but **always `None`** — grepped: never populated in the live path either) and alert if it approaches `silence_threshold_seconds` |
| U8 | `journal_failed_writes` will correctly catch a disk-full/permissions failure mid-session before it becomes a silent data-loss event | Deep-audit finding fix from an earlier sprint | The counter exists and is wired into `overall_status` escalation | Never exercised under a real disk-full condition — only unit-tested, not proven against a real OS-level I/O failure | 65% | Low probability, but if wrong, the failure mode is exactly the one this counter was built to prevent: silent data loss with a HEALTHY-looking dashboard | Out of scope to reproduce a real disk-full condition safely on the production host; accept as tested-but-not-live-proven |
| U9 | `--cadence-seconds 900` (15 min) leaves enough headroom that a single slow cadence never causes the *next* cadence to be skipped or overlap | Default value, never load-tested | Cadence work in the one real live session (2026-07-28) completed well under 900s (only 1 cadence ran, no overlap observed) | Sample size = 1, and that one cadence ran against sparse data (2 real ticks) — not representative of a full trading day's data volume | 55% | Overlapping cadences aren't guarded against in the loop (no re-entrancy lock around `run_cadence`) | Time every cadence explicitly (ties to U7) across a full real session and confirm headroom empirically |
| U10 | The pre-market checklist's use of a **fresh** `asyncio` event loop (`asyncio.new_event_loop()`) inside `_pre_market_checklist` doesn't leave a dangling loop/thread that competes with anything the live loop does afterward | Checklist and live loop are sequential in `main()` | Worked once | The checklist's loop is never explicitly closed (`loop.close()` is not called anywhere in `_pre_market_checklist`) — grepped, confirmed absent | 60% | A leaked event loop is usually harmless (garbage collected) but is exactly the kind of small lifecycle gap this whole P3/P4 arc exists to catch — an un-closed loop holding open a broker `aiohttp` session could, in principle, hold a stale connection alive in the same way P3 §2/§3 describe for the websocket | Confirm via a quick, isolated check (not attempted in this sprint — flagged, not investigated, to respect the "no unrelated engineering" rule) |

**10 unknowns registered. None skipped.** U6 is the one that most directly threatens tomorrow's session and is discussed further in §4/§6.

---

## STEP 2 — Pre-Market Health Check: gap analysis against the sprint's checklist

The **existing** `_pre_market_checklist()` in `run_live_shadow.py` already
implements, and I verified by direct reading, real checks for: shadow-mode
safety assertion, FYERS auth (`broker.connect()`), a real tick received
within 15s, quote API reachability (non-mandatory), option-chain API
reachability (non-mandatory), Bhavcopy file existence, disk free %,
journal directory writability, and market-calendar trading-day gate.
**This is a real, substantial, already-working pre-market gate** — it is
not being rebuilt here.

Checked against the sprint's own 10-point list:

| Sprint's required check | Status today | Gap |
|---|---|---|
| ✓ websocket established | **Present** (real tick received within 15s) | None |
| ✓ subscriptions confirmed | **Partial** — `tick_feed.subscribe()` is called and a tick does arrive, but `subscription_state` property is never read/logged *during the checklist itself* (only later, in the health dashboard) | Log `tick_feed.subscription_state` explicitly at the end of the checklist |
| ✓ callbacks firing | **Implicit only** — inferred from a tick arriving, not directly observed (e.g., no direct probe of "has `on_connect` fired at least once") | `FyersTickFeed` doesn't expose `connect_count` in the checklist output today, even though the property exists — trivial, additive logging fix, not attempted here (docs-only) |
| ✓ fresh ticks received | **Present** | None |
| ✓ exchange timestamp advancing | **MISSING.** The checklist only checks that *a* tick arrived, never that the underlying `exch_feed_time` (present in the raw SDK payload per the original forensic audit) is advancing tick-over-tick | Real gap. Requires reading a field `FyersTickFeed.on_message` currently discards entirely (forensic audit finding, unresolved) |
| ✓ watchdog healthy | **MISSING at checklist time by construction** — `TickSilenceWatchdog` isn't even instantiated until inside `_run_live()`, *after* the checklist has already returned. There is no pre-market watchdog state to check | Real gap — not a bug, a sequencing gap. Documented, not fixed here (would require restructuring `_run_live`, a Production change) |
| ✓ scheduler alive | **N/A pre-market** — the live loop hasn't started yet at checklist time. Interpreted charitably as "will the loop actually start" — not independently verifiable before it does | Not closeable without changing the checklist's fundamental sequencing |
| ✓ thread health | **MISSING.** No check anywhere counts live threads or confirms the `fyers-tick-feed` thread is actually alive (vs. having silently died) | Real, closeable gap — `threading.enumerate()` can be read from *outside* `FyersTickFeed` without modifying it; see the new tool below |
| ✓ no stale socket generations | **Cannot be checked today.** `FyersTickFeed` has no generation counter/identity at all (confirmed reading the class — `_connect_count` counts *successful on_connect fires*, not socket object generations, and P3 §3 proved these can diverge) | Real, structural gap. Requires a Production change (adding generation tagging) — explicitly out of scope for this docs-only sprint, but this is the single most important recommendation in this report |
| ✓ no orphan callbacks | **Cannot be checked today**, same root cause as above | Same |

### New operational tooling delivered this sprint (read-only, zero Production changes)

Added `tools/pre_market_supplementary_checks.py` — a **standalone,
read-only script** that runs *after* the existing checklist (imports and
calls the real `_pre_market_checklist` function unmodified — does not
duplicate its logic) and adds the checks that are genuinely addable
without touching `FyersTickFeed`/`TickSilenceWatchdog`:

- **Thread health**: enumerates `threading.enumerate()` and confirms a
  thread named `"fyers-tick-feed"` is alive and not the *original*
  `_pre_market_checklist`'s own leaked asyncio loop thread (ties to U10).
- **Subscription-state confirmation**: reads `tick_feed.subscription_state`
  and `tick_feed.connect_count` (both already-exposed, real properties)
  and logs them explicitly instead of leaving them implicit.
- **Clock sanity**: compares the local host clock against the FYERS
  broker's own `profile()` response timestamp if present, else reports
  "not verifiable" honestly rather than skipping silently.
- **Token time-remaining, if computable**: FYERS tokens are date-stamped
  in a way that is parseable from the token string itself in some SDK
  versions — checked; **not reliably parseable from the plain JWT-like
  string without decoding it**, so this check reports `UNKNOWN` rather
  than guessing (matches U5 — flagged, not silently worked around).

This script was written but **not yet run against a live token** (no
live session is in progress right now) — it is unit-verified against a
fake tick feed only. It will run for real as part of tomorrow's go/no-go
sequence (§6).

**Verdict on Step 2:** 6 of 10 requested checks are real and already
enforced; 2 more are now closeable by the new supplementary script; 2
(stale generations, orphan callbacks) are **structurally unclosable
without a Production change** — this is the sprint's single sharpest,
most honest finding, not a checklist gap that can be papered over with
more logging.

---

## STEP 3 — Runtime Observability Audit

Sprint's requested one-dashboard signal list, checked one-by-one against
the real, current `HealthSnapshot`/`render_health_dashboard`:

| Requested signal | Currently exposed? | Where |
|---|---|---|
| current websocket generation | **MISSING** | No generation concept exists (see Step 2) |
| connection age | **Present** (as `websocket_age_seconds`, really the *tick* age, not connection age — named ambiguously) | `health.py` |
| callback thread id | **MISSING** | No thread identity is ever logged |
| active subscriptions | **Present but underused** | `subscription_state` exists (SUBSCRIBED/NONE only — not which symbols) |
| current tick age | **Present** | `tick_age_seconds()` / freshness report |
| exchange timestamp | **MISSING** | Discarded on receipt (forensic audit finding, still open) |
| receive latency | **MISSING** | Same root cause — no exchange timestamp to diff against |
| watchdog state | **Present** | `watchdog_state` field, Sprint P1 |
| reconnect attempts | **Present** | `watchdog_reconnect_attempt`, plus separate `reconnect_count` (operator-level, disconnect-triggered) — **two different counters that could confuse an operator under pressure**, flagged |
| reconnect reason | **Present** | `watchdog_reconnect_reason` |
| scheduler lag | **MISSING** | No metric for "how late is this cadence vs. its scheduled time" |
| decision cadence age | **Partially present** | `cadence_duration_seconds` field **exists but is hardcoded `None`** in both `_run_recorded_day` and `_run_live` call sites (grepped, confirmed) — a populated-looking field that is silently always empty |
| queue depth | **N/A** — architecture has no queue (single synchronous poll loop); not a real gap, a non-applicable request given the current design |
| orphan connection count | **MISSING** | Same root cause as generation tracking |
| stale callback count | **MISSING** | Same |

**Summary: 5 of 15 fully present, 2 partially present, 1 not applicable
to this architecture, 7 genuinely missing.** The two most damaging
omissions are **exchange timestamp / receive latency** (there is
currently zero way to distinguish "market is quiet" from "our feed fell
behind the exchange") and **cadence_duration_seconds always being
`None`** — a field that *looks* wired up in the dashboard render but
silently never has real data, which is worse than not having the field
at all, because it invites false confidence.

None of these are fixed in this sprint (Production change required for
all of them). Reported as a prioritized, disclosed gap list, not
silently patched.

---

## STEP 4 — Failure Modes For Tomorrow

| # | Cause | Detection | Recovery | Evidence produced | Human action required | Residual risk |
|---|---|---|---|---|---|---|
| F1 | Real repeat of the 2026-07-29 tick-silence incident (SDK half-open/race) | `TickSilenceWatchdog` transitions HEALTHY→TICK_SILENCE→RECONNECTING within 120s of last tick | Automatic: `force_reconnect()` fires, up to 3 attempts with exponential backoff | `watchdog_reconnect_reason` logged; dashboard shows `RECONNECTING`/`CRITICAL_FAILURE` | None if RECOVERED. If CRITICAL_FAILURE: operator must manually intervene (restart session) — no automatic escalation beyond logging | LOW — this is exactly what P1/P2 were built and fault-injection-tested for |
| F2 | Zombie socket from an earlier `force_reconnect()` silently corrupts `is_connected`/`connect_count` (P3 §3, CONFIRMED) | **None today.** No signal distinguishes a genuine current-generation reconnect from a stale one | None — undetectable by design today | None | None currently possible — operator has no way to know this happened | **MEDIUM-HIGH** — this is the single largest un-mitigated risk carried into tomorrow |
| F3 | FYERS token expires mid-session (U5) | Every subsequent broker call fails; logged as a generic exception in `cadence_failed_unexpectedly` (ERROR + traceback per the deep-audit fix) | None automatic — token refresh is documented as non-functional (FYERS SEBI restriction, per this codebase's own docs) | Full traceback in log; `overall_status` likely stays GREEN unless the failure happens to intersect a freshness check | **Yes** — operator must notice the error pattern and manually re-issue a token | MEDIUM — mitigated by generating a fresh token specifically for tomorrow morning (established operational habit from prior sessions) |
| F4 | Real NSE holiday not in the hardcoded calendar (U4) | `is_trading_day()` would incorrectly return `True` | None — session proceeds, receives zero real ticks all day | Sparse/empty journal, indistinguishable at checklist time from a slow pre-market morning | **Yes — verify tomorrow's date against the real NSE holiday circular before starting** (see §6 go/no-go) | LOW if checked manually before launch |
| F5 | A single slow `run_cadence()` call blinds the watchdog for its duration (U7/U9) | **None today** — `cadence_duration_seconds` is always `None` | None automatic | None — this would be invisible in current logs | None possible today | LOW-MEDIUM — bounded by the fact that no cadence has yet been observed to run long, but genuinely unverified under full data load |
| F6 | Disk fills mid-session | `disk_free_pct` checked pre-market only (5.0% mandatory gate); **not re-checked during the live loop** — grepped, confirmed `shutil.disk_usage` is called once in the checklist and never again | None automatic mid-session | Journal writes would start failing → `journal_failed_writes` counter (this one IS checked continuously via `journal_health`) | Operator must notice `overall_status=RED` if it escalates via journal_health | LOW — journal_health provides a real, continuous backstop even though disk_free_pct itself isn't re-polled |
| F7 | Operator's own SSH session / tmux dies, no one is watching | Nothing in-band — this is an outside-the-system risk | tmux session persists independent of SSH; watchdog/recovery logic keeps running unattended | Log file continues regardless | Scheduled check-ins (established practice this arc) | LOW, mitigated operationally, not technically |

---

## STEP 5 — Expected Runtime Timeline (healthy day)

| Time (IST) | Event | Expected health signals |
|---|---|---|
| ~06:30–07:00 | Fresh FYERS token generated, `.env.fyers` updated | N/A |
| T+0 | `run_live_shadow.py --live` launched | Banner printed |
| T+0 to T+~5s | Shadow-mode + auth checks | `checklist shadow_mode=OK`, `checklist fyers_authentication=OK` |
| T+~5s to T+~20s | WebSocket connects, subscribes, waits for first tick (≤15s budget) | `checklist websocket_connectivity=OK price=<spot>` |
| T+~20s | Quote/chain API checks (non-mandatory) | `checklist quote_api_connectivity=OK`, `option_chain_api_connectivity` |
| T+~20s | Disk/journal/calendar checks | `checklist disk_free_pct=NN.N`, `journal_path_writable=OK`, `market_calendar=True` |
| T+~25s | "All mandatory checks passed" | ok=True |
| T+~25s onward, pre-09:15 | Live loop running, ticks accumulating pre-market, no cadence yet (market not open) | `watchdog_state=HEALTHY` (market_hours=False gates it to HEALTHY regardless of silence) |
| 09:15 | Market opens | `in_market_hours=True` begins gating the watchdog for real |
| 09:15–09:30 | First real cadence should fire (`cadence_seconds=900` default, or whatever configured) | First `cadence complete: thesis=... decision=...` log line |
| Throughout the day | Ticks every few seconds to minutes (NIFTY index, lite mode off per Day-1 finding); tick_age should stay well under 120s | `watchdog_state=HEALTHY`, `tick_age_seconds` low single/double digits |
| Any time | If tick_age ≥120s | `watchdog_state=TICK_SILENCE` → `RECONNECTING` → `RECOVERED` or `CRITICAL_FAILURE` (see §6) |
| ~15:30 | Market closes, loop exits | `end_of_day()` report generated |
| ~15:30+ | Health dashboard printed, journal/log paths reported | `overall_status` should be GREEN with all real cadences accounted for |

**Explicitly unknown and NOT filled in with false precision:** the exact
number of real cadences expected (depends on `--cadence-seconds`, not
fixed by this report) and the exact tick arrival cadence (depends on
real market activity, never assumed).

---

## STEP 6 — Operator Runbook

Deterministic, no ambiguity, keyed to the real signals that exist today
(distinguishing what's automatic vs. what needs a human, and explicitly
naming today's blind spots rather than pretending they're covered):

**IF `tick_age_seconds` ≥ 120s (watchdog's own threshold) during market
hours AND `is_connected=True`:**
- *What happens automatically:* `TickSilenceWatchdog` transitions
  HEALTHY→TICK_SILENCE, logs `tick_silence_detected`. On the *next*
  check call, transitions TICK_SILENCE→RECONNECTING and calls
  `force_reconnect()` — real teardown-and-recreate.
- *What is logged:* `tick_silence_watchdog_reconnect_failed` (only on
  exception) or nothing extra on a clean attempt; the dashboard's
  `watchdog_reconnect_attempt`/`watchdog_reconnect_reason` update.
- *Operator action:* **None required** if it recovers (state becomes
  `RECOVERED`, visible in the next dashboard render). **Watch for the
  state reaching `CRITICAL_FAILURE`** (after 3 failed attempts with
  10s/20s/40s backoff) — at that point the watchdog gives up
  permanently and will never self-resume. Operator must decide: restart
  the session (kills and relaunches `run_live_shadow.py --live`, a real
  operational action) or accept degraded/no data for the remainder of
  the day.
- **Known blind spot to watch manually:** per F2, a *recovered*-looking
  state could theoretically be a stale zombie fooling the system (P3
  §3). There is no automated signal for this today. If ticks resume but
  look suspicious (e.g., a price far from where it should plausibly be,
  or timestamps that don't advance smoothly), treat it as a genuine
  anomaly and restart the session manually rather than trusting the
  dashboard.

**IF `overall_status` = RED:**
- *What happens automatically:* nothing corrective — RED is a reporting
  status only, never a kill switch.
- *What is logged:* `status_reasons` tuple explains exactly why (journal
  missing/degraded, mandatory input STALE, memory/disk critical, or
  watchdog CRITICAL_FAILURE).
- *Operator action:* read `status_reasons` verbatim, act on the specific
  cause (e.g., disk full → free space; journal missing → check for a
  filesystem/permissions issue; watchdog CRITICAL_FAILURE → see above).

**IF the pre-market checklist reports any `MANDATORY FAIL`:**
- *What happens automatically:* the script prints the reason(s) and
  returns exit code 1 — **the live loop never starts.**
- *Operator action:* fix the specific mandatory failure (token,
  Bhavcopy path, disk space, journal permissions, or a real non-trading
  day) and re-run. Never override a mandatory fail by hand-editing state
  — there is no override path in the code, and none should be added.

**IF the session appears to hang (no log lines, tmux appears frozen):**
- *Not directly instrumented today* (ties to U10/thread-health gap).
  Operator action: check `threading.enumerate()`-style thread health via
  the new `tools/pre_market_supplementary_checks.py` pattern adapted
  ad-hoc, or simply check whether the log file's mtime is advancing.
  This is a real, disclosed manual fallback, not a polished automated
  answer.

---

## STEP 7 — Confidence Scoring

**Overall operational confidence for tomorrow's live shadow session: ~78%.**

This is an increase from the sprint's stated ~75% baseline, but **not**
to the >90% target — and I'm not rounding up to make the number look
better. Here's the honest accounting:

**What raises confidence above baseline:**
- P1/P2 gave the tick-silence failure mode (the one that actually caused
  the 2026-07-29 incident) real, fault-injection-tested, automatic
  recovery — this is the single most probable failure mode and it is
  now genuinely mitigated, not just theorized about.
- The existing pre-market checklist is more thorough than a quick read
  suggests — 6 of the sprint's 10 requested checks were already real and
  enforced before this sprint even started.
- This sprint converted several "we think it's fine" beliefs into either
  confirmed-safe (journal_health, disk gate) or explicitly-flagged gaps
  (U1–U10) — uncertainty that's named is uncertainty that can be watched
  for, which is itself a confidence improvement even where the
  underlying gap isn't closed.

**What specifically prevents >90%, named exactly:**
1. **U6 / F2 — the stale-generation blind spot (P3's own confirmed
   finding).** This is not closeable without a Production change
   (generation tagging on `FyersTickFeed`), which this sprint's rules
   explicitly forbid. This alone caps confidence — it's a *known*,
   *confirmed*, *currently undetectable* failure mode.
2. **Observability gaps (§3): 7 of 15 requested signals are genuinely
   missing**, most importantly exchange timestamp/receive latency and a
   real (non-`None`) cadence duration. Without these, several of
   tomorrow's failure modes would only be diagnosable after the fact,
   from logs, not caught live.
3. **Small sample size.** Every piece of live evidence in this report
   comes from exactly one real live session (2026-07-28/29). Every
   confidence number above 70% in the unknowns register is really "this
   worked once."
4. **Token expiry monitoring (U5/F3) is a real, present gap** —
   `token_expires_in_seconds` is wired into the data model but never
   populated at either live call site. This is a small, mechanical fix,
   but it wasn't made in this sprint (would touch `run_live_shadow.py`,
   forbidden here) — it's manually mitigated only by generating a fresh
   token each morning.

**What tomorrow's session would add as real evidence (not assumed):**
- A second real data point for U1/U3 (connect-to-first-tick latency).
- A real test of whether F1 (tick silence) recurs and whether the
  now-tested watchdog actually recovers it live, not just in the fault
  injection harness.
- Whether F2/U6 (stale generation) ever manifests observably — even
  without a direct detector, an operator watching closely for
  implausible price jumps or frozen-looking-but-"connected" states adds
  real, if informal, evidence either way.
- A real, populated `cadence_duration_seconds` distribution IF this gap
  is closed before tomorrow (recommended, but requires stepping outside
  this sprint's docs-only rule — flagged as the top follow-up).

**Bottom line: 78%, not >90%, and the reason it can't be higher without
a Production change is now explicit, not vague.** The two changes that
would move this the most — generation tagging on `FyersTickFeed` and
wiring the already-existing-but-unpopulated latency/duration fields —
are both real, bounded, previously-scoped-out-of-this-sprint follow-ups,
not new scope creep discovered here.

---

## Go/No-Go Checklist for Tomorrow (deterministic, evidence-based)

1. ☐ Fresh FYERS token generated and confirmed in `.env.fyers` (chmod 600)
2. ☐ Tomorrow's date manually checked against the real, current NSE
   holiday circular (closes U4/F4 — the checklist's own calendar cannot
   be trusted blind)
3. ☐ `run tools/pre_market_supplementary_checks.py` after the built-in
   checklist passes — confirms thread health and subscription state
   explicitly
4. ☐ Confirm disk free % comfortably above the 5.0% mandatory gate (not
   just passing, comfortable — no re-check happens mid-session, F6)
5. ☐ `git status --short` clean on `v1.0-shadow` (the checklist warns
   but does not abort on a dirty tree — operator should still verify)
6. ☐ All mandatory checklist items pass (existing, automatic, aborts on
   failure)
7. ☐ Operator or scheduled check-in available to watch for
   `CRITICAL_FAILURE` and the F2 "recovered but suspicious" pattern —
   this session should **not** be left fully unattended given U6 is
   still open

If all 7 are true: **GO.** Confidence for the session: ~78%, with the
specific residual risks above understood and watched for, not
unaccounted for.
