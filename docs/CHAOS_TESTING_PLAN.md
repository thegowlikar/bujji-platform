# Chaos Testing Plan & Failure Playbook

**Status:** planning document only. No production code is modified in this step.
**Scope:** operational failure modes for a system trading real capital. This is
the runbook for "what happens when X breaks," and the test plan to prove it.

**Baseline assumed:** Tier 1 (C1–C4) is implemented — atomic session snapshot,
crash recovery with reconcile, wall-clock EOD square-off, idempotent order
placement, partial-fill handling. Every scenario below states plainly whether
Tier 1 already covers it, or whether it is a **known gap** deferred to Tier 2+.
Where a gap exists, this doc still specifies the *intended* behavior and test,
so it can be implemented against a spec rather than improvised later.

---

## How to read each scenario

| Field | Meaning |
|---|---|
| **Trigger** | The concrete fault, exactly how it manifests in the code paths |
| **Detection** | What in the system currently notices — a log line, an exception, a status flag — or "NONE" if it's a gap |
| **Automatic recovery** | What the system does today without a human, or "NONE" |
| **Expected final state** | What state the system SHOULD be in when the dust settles (flat/managed/DONE_FOR_DAY/halted) |
| **Operator action** | What a human must do, and how urgently |
| **Tier status** | ✅ Covered by Tier 1 · ⚠️ Partially covered · ❌ Gap (Tier 2+) |
| **Tests needed** | Concrete unit/replay/chaos tests to validate the behavior |

---

## A. Broker / API outages

### A1. Broker API completely unreachable during order placement
- **Trigger:** `place_order` raises (connection refused / DNS fail / 5xx) on entry or exit.
- **Detection:** `ExecutionEngine._place_idempotent` catches, logs `place_order_error`, retries `retry_attempts` times with backoff; if still failing, raises `ExecutionError`.
- **Automatic recovery:** for **entry**, the orchestrator rolls back `CONFIRMED → READY` (`entry_failed_rollback`) and does not consume the day's one trade — a later candle can retry entry.
  For **exit**, `_do_exit` returns `False`, orchestrator transitions back to `IN_POSITION` (`exit_retry`), and the *next candle* retries the exit. If it's already past `hard_exit`, `end_of_day()` retries every subsequent loop boundary.
- **Expected final state:** entry — no position, `READY` (or `DONE_FOR_DAY` if window closed). Exit — either flat once the API recovers, or `status.healthy=False` with a stuck `IN_POSITION` if the outage outlasts the trading day.
- **Operator action:** if `healthy=False` persists past `hard_exit` with a live position — **page immediately**; manually verify/flatten via broker terminal. This is the one true "wake someone up" condition.
- **Tier status:** ✅ Entry rollback and exit-retry-next-candle covered. ❌ **Gap:** no alerting/paging integration exists yet — `status.healthy` is a passive flag nobody is notified of. No maximum-retry escalation (e.g., SMS/Telegram) is wired.
- **Tests needed:**
  - Unit: `PaperBroker` variant that raises on every `place_order` call for N attempts → assert entry rolls back to `READY`, no position created, `trades_taken` unchanged.
  - Unit: broker raises on exit's `place_order` indefinitely → assert orchestrator stays `IN_POSITION`, `status.healthy is False`, position preserved (not lost).
  - Replay/chaos: inject outage starting at candle N, lasting K candles, recovering at candle N+K → assert exit eventually completes on the first successful candle after recovery.
  - Integration: outage spans past `hard_exit` → assert `end_of_day()` keeps retrying every loop iteration (not just once).

### A2. Broker API returns HTTP 200 but malformed/unexpected JSON
- **Trigger:** `_call()` returns a dict missing expected keys (e.g., no `"status"`, unexpected enum value), or a completely different schema after a broker-side API version change.
- **Detection:** **NONE today.** `_map_order` does `data.get("status")` → defaults to `OrderStatus.UNKNOWN` silently; a `KeyError`/`TypeError` elsewhere would propagate as an unhandled exception up through `_with_retry`'s generic `except Exception`, which *would* catch and retry it — but retrying a malformed-response bug just repeats the same malformed response, burning all retry attempts before raising `ExecutionError`.
- **Automatic recovery:** effectively none — it degrades to "not filled" and behaves like A1.
- **Expected final state:** should be treated as **ambiguous, not absent** — must not silently assume "no order" and re-place (this is a C3 risk if extended carelessly).
- **Operator action:** the *raw* broker payload must be recoverable from logs for investigation. Currently `OrderResult.raw` stores it — verify it's actually logged, not just held in memory.
- **Tier status:** ❌ **Gap.** No schema validation on inbound broker payloads; no distinct "malformed response" classification separate from "network error."
- **Tests needed:**
  - Unit: mock broker returns a dict missing `"status"` → assert `_map_order` doesn't crash and produces `UNKNOWN`, not a false `FILLED`.
  - Unit: mock broker returns a completely different shape (e.g., a list instead of dict) → assert the exception is caught by `_with_retry`, logged with the raw payload, and surfaces as `ExecutionError` rather than crashing the candle loop.
  - Add (Tier 2 candidate): a `raw` payload log assertion — confirm `order_confirmed`/`broker_call_failed` log lines include enough of the raw response to diagnose the schema drift after the fact.

### A3. Broker accepts order but exit position query is delayed/eventually-consistent
- **Trigger:** immediately after a fill, `get_open_positions` or `get_order` doesn't yet reflect it (common with brokers that batch position updates).
- **Detection:** NONE explicit — `_await_fill` polls `get_order` (order-level, not position-level) so this mostly affects **reconciliation on restart** (`reconcile()` → `_find_live_short`), not the fill-confirmation path.
- **Automatic recovery:** none — a restart occurring in this narrow window could see broker positions not yet reflecting a fill that already happened, and misclassify a real position as "flat" (C1's already-flat branch) when it is actually open.
- **Expected final state:** should treat a restart-time reconcile as authoritative only after confirming eventual consistency (e.g., re-query after a short delay, or cross-check against the last known order's fill status before trusting "flat").
- **Operator action:** if a "ghost" position appears later (broker shows a position the bot believes is closed) — manual reconciliation via broker terminal; this is exactly the scenario C1's "orphan" handling is *designed* to catch on the *next* restart, but not within a single running session.
- **Tier status:** ⚠️ Partially covered — the next restart's orphan-detection would catch a stale "flat" belief eventually, but there's a window of blindness during the *current* running session.
- **Tests needed:**
  - Unit: broker's `get_open_positions()` returns stale (pre-fill) data for the first N calls, then correct data → assert reconcile logic doesn't misfire on the stale read (requires a `PaperBroker` hook to delay position visibility).
  - Chaos: restart exactly at the moment of a fill, before the broker's position feed updates → document expected behavior explicitly, since Tier 1 does not fully cover this window today.

---

## B. Network failures (host-level, not broker-specific)

### B1. Full internet/network outage for T minutes during market hours
- **Trigger:** every broker call raises a network error (indistinguishable from A1 at the code level, but affects *all* calls simultaneously, including `get_recent_candles`).
- **Detection:** `_candle_loop`'s outer `except Exception` catches candle-fetch/processing failures, logs `candle_processing_error`, sets `healthy=False`, and **the loop continues** to the next boundary — it does not crash the process.
- **Automatic recovery:** self-healing once network returns — the very next successful candle fetch resumes normal processing. No state is lost because nothing was ever transitioned incorrectly (candle just never arrived).
- **Expected final state:** if flat when the outage started — unaffected, resumes normally. If **in a position** — the position is neither monitored nor exited for the outage's duration; this is a real risk window (VWAP-based exits can't fire; only the wall-clock EOD square-off is network-independent in *intent*, but obviously still requires network to actually place the exit order).
- **Operator action:** if outage spans a chunk of the trading day while in a position, monitor the broker's own terminal/app on a separate connection (mobile data) as a fallback; this system has no non-network fallback for exiting a position — acknowledge this as a fundamental limitation of any API-driven bot.
- **Tier status:** ✅ Loop survivability covered. ❌ **Gap:** no secondary/independent kill-switch (e.g., a broker-side GTT stop-loss order placed at entry time as a dead-man's-switch) — this is the single biggest structural risk for a fully API-dependent system and worth a Tier 2 discussion on its own.
- **Tests needed:**
  - Integration: simulate `get_recent_candles` raising for K consecutive loop iterations, then succeeding → assert loop never exits, `healthy` flips back appropriately (needs a "recovered" transition — currently `healthy` is never reset to `True` automatically; verify/add this).
  - Chaos: outage begins 1 candle after entry, lasts through the rest of the day, recovers only after `hard_exit` → assert `end_of_day()` fires and successfully exits once network returns, however late.

### B2. Partial network degradation (high latency, not full outage)
- **Trigger:** broker calls succeed but take 10-20s instead of <1s.
- **Detection:** NONE distinct from success — `order_timeout_seconds` (default 15s) may trip mid-fill-confirmation even though the order actually filled, treating a slow success as a timeout.
- **Automatic recovery:** `_await_fill` times out, calls `_safe_cancel` — cancelling an order that may have *just* filled is a real hazard (cancel-after-fill is usually a no-op at the broker, but not guaranteed).
- **Expected final state:** should reconcile via `get_order`/`get_open_positions` one more time *after* the cancel attempt, before assuming "not filled."
- **Operator action:** none if the reconciliation-after-cancel is added; otherwise, verify no orphaned fill exists after any timeout event.
- **Tier status:** ⚠️ Partially covered — timeout handling exists but doesn't re-verify post-cancel.
- **Tests needed:**
  - Unit: `PaperBroker` hook that delays `get_order` responses beyond `order_timeout_seconds` but the order *did* fill → assert current behavior (documents the gap) and, once fixed, assert a post-cancel re-check surfaces the true fill.

### B3. DNS resolution failure specifically (broker host unreachable, rest of internet fine)
- **Trigger:** same code path as A1/B1, but worth calling out because retry/backoff timing assumptions (exponential backoff) may not be tuned for a fast-fail DNS error vs. a slow-timeout TCP error.
- **Detection/Recovery/Tier status:** identical to A1.
- **Tests needed:** none beyond A1 — noted here only so it isn't missed conceptually as "a different kind of network failure" requiring separate handling. It doesn't.

---

## C. Data quality failures

### C_D1. Stale market data (candle feed stops updating but doesn't error) — ✅ IMPLEMENTED (detection only)
- **Trigger:** `get_recent_candles` keeps returning `OK` with no exception, but the returned candle's timestamp doesn't advance (broker feed frozen), or a candle arrives out of order (a replayed/duplicated earlier timestamp).
- **Detection:** `Orchestrator.on_candle()` now checks every incoming candle's timestamp against the last one processed (`self._last_candle_ts`) *before* any signal/trade logic runs. A candle at or before that timestamp is a duplicate/stale read and is ignored outright (`duplicate_or_stale_candle_ignored` logged, `RuntimeStatus.duplicate_candles_ignored` incremented). A gap larger than 1.5× the configured candle interval is logged (`candle_gap_detected`) and surfaced via `RuntimeStatus.last_candle_gap_seconds` — pure observability, no backfill attempted.
- **Automatic recovery:** N/A by design — this is detection, not remediation. A duplicate candle can no longer double-count `candles_held`/MFE/MAE or trigger a spurious re-evaluation, because it is never handed to the Signal Engine or Trade Manager at all. A frozen feed manifests as the same candle being ignored repeatedly (harmless) rather than reprocessed; the wall-clock EOD square-off (C2) remains completely independent of candle arrival and still fires.
- **Expected final state:** unaffected trading logic during a frozen/duplicated feed (no double-counting); a gap is visible to the operator but does not itself halt or alter trading — a genuinely stale feed still leaves the position monitored at the last real reading until a new candle arrives, exactly as before, just without the double-processing bug.
- **Operator action:** watch `RuntimeStatus.last_candle_gap_seconds` / `duplicate_candles_ignored` (dashboard) — a nonzero/growing count while in a position warrants checking the broker connection even though capital isn't at risk from the double-counting bug anymore.
- **Tier status:** ✅ **Implemented** (this pass), detection-only as scoped — no backfill, no trading halt on a gap, no remediation.
- **Tests:** `tests/test_cd1_candle_dedup.py` — exact-duplicate and stale-out-of-order candles ignored; the capital-relevant proof that a duplicate while `IN_POSITION` does not double-increment `candles_held`; normal 5-minute cadence does not falsely flag a gap; a missed candle produces the expected gap measurement; and a replay-path test confirming the same protection applies through `ReplayEngine`.

### C_D2. Corrupted session snapshot file — ✅ IMPLEMENTED
- **Trigger:** `session_state.json` is truncated, contains invalid JSON, or has a schema mismatch **inside the nested `position` dict specifically** (hand-edited, a future field rename, or partial corruption that still parses as valid JSON) — the sub-case the original write-up flagged as needing a code change, not just documentation.
- **Detection:** two independent layers now. (1) `SessionStore.load()` still catches `JSONDecodeError, TypeError, ValueError, OSError` at the whole-file level. (2) **New:** `core/position_codec.py` normalizes every schema failure while reconstructing the nested `Position` (missing key, wrong nesting, invalid enum, non-numeric garbage) into a single `PositionSchemaError`, which `Orchestrator._safe_parse_position()` catches explicitly, logs (`recovery_snapshot_position_corrupt`), and flags `status.healthy = False` / `health_detail` — it does not just silently discard the corruption.
- **Automatic recovery:** a corrupt `position` is treated exactly like "no saved position" for matching purposes, and broker reconciliation (`_find_live_short`) remains the ground truth: a real live short is still discovered and flattened via the **orphan** path; an already-flat broker position finalizes cleanly. Numeric-as-string values (e.g. `"75"` for quantity — a common JSON round-trip artifact) are tolerated via explicit `int()`/`float()` coercion rather than rejected as corruption; only genuinely non-numeric or missing/mistyped data raises.
- **Expected final state:** any real open position is still discovered via broker reconciliation and safely flattened, even with the position snapshot corrupted or totally lost. The operator-visible health detail distinguishes "snapshot was corrupt" from a plain "orphan found" so both facts survive into one alert rather than the second silently overwriting the first.
- **Operator action:** if `health_detail` shows `snapshot_position_corrupt`, investigate why (disk issue, hand-edit, a version mismatch after a deploy) — this should not happen given atomic writes, so its occurrence is itself worth investigating even though capital was protected.
- **Tier status:** ✅ **Implemented** (this pass), including the specific hardening item flagged in the original C_D2 write-up (wrapping `position_from_dict` so a nested schema mismatch can't crash startup uncontrolled).
- **Tests:** `tests/test_cd2_snapshot_schema.py` — schema-mismatch unit tests at the codec level (missing key, wrong nesting, invalid enum, garbage numeric value all raise `PositionSchemaError`; numeric-as-string is tolerated; a valid dict still round-trips); `SessionStore.load()` regression guards for truncated/wrong-shape JSON; and the end-to-end scenario — **the single most important test in this pass** — corrupting the on-disk `position` payload while a real position is held at the (paper) broker, then verifying a fresh `Orchestrator.startup()` does not crash and still discovers and flattens the real position via reconciliation. A parallel test covers corrupt-snapshot-but-already-flat. A replay-path test (`test_replay_startup_survives_corrupt_snapshot`) confirms the Replay Engine's identical Orchestrator/SessionStore stack isn't exempt from this protection.

### C_D3. Snapshot and broker disagree on quantity (partial-fill drift, or manual intervention)
- **Trigger:** snapshot says qty=150, broker actually holds qty=75 (e.g., operator manually squared off half the position outside the bot).
- **Detection:** `_resume_position` compares `broker_qty != pos.quantity`, logs `recovery_qty_adjusted`.
- **Automatic recovery:** **trusts the broker's actual size** and overwrites `pos.quantity` — correct behavior (the broker is ground truth for what's actually at risk).
- **Expected final state:** resumes management at the corrected (broker-true) quantity.
- **Operator action:** none required; but if an operator manually intervenes in the broker terminal, they should expect the bot to "notice" and adapt on next restart only — **not mid-session** (a currently-running process does not re-poll broker qty except when it places its own orders). Worth documenting as an operational rule: *manual intervention requires a bot restart to be recognized.*
- **Tier status:** ✅ Covered on restart. ⚠️ Not covered mid-session (by design — Tier 1 scope was restart recovery, not continuous reconciliation).
- **Tests needed:**
  - Already covered by `test_c1_resume_open_position`'s pattern — add an explicit variant: seed broker qty different from snapshot qty → assert `pos.quantity` is corrected to the broker's value and logged.

---

## D. Timing / clock failures

### D1. VPS clock drift (few seconds to minutes) — ✅ IMPLEMENTED (in-session detection)
- **Trigger:** system clock jumps during a running session (NTP correction, suspend/resume, manual change).
- **Detection:** `core/clock.py`'s `ClockGuard` compares wall-clock elapsed time against the monotonic clock between successive checks, called once per loop boundary from `Application._candle_loop`. A divergence beyond the threshold (5s, not currently config-exposed) is flagged as drift for that interval.
- **Automatic recovery:** `Orchestrator.set_clock_trust(False, detail)` blocks **new entries only** (`_handle_pre_position` refuses to open a position while untrusted) — it deliberately never affects an already-open position's management/exit, and never blocks the wall-clock EOD square-off (capital preservation must not be held hostage by clock distrust). The signal is point-in-time: the very next normal tick restores trust automatically (mirrors the E1/E2 auth-flag self-clear pattern), since a single transient jump shouldn't permanently forfeit the day.
- **Expected final state:** if the drift coincides with a qualifying breakout, entry is skipped for that candle (logged `entry_blocked_clock_untrusted`) — the day's one-trade opportunity may be missed, same trade-off character as the H1 finding, not a capital-risk outcome. An existing position is completely unaffected.
- **Operator action:** `RuntimeStatus.clock_trusted`/`clock_drift_detail` are visible on the dashboard; investigate NTP configuration if this fires repeatedly.
- **Tier status:** ✅ **Implemented** for in-session drift detection, as scoped. **Explicitly NOT implemented:** validating the clock was already correct *before* the process started against an external trusted source (broker server time / NTP query) — that would require a new Broker-interface method or a network dependency and remains a further improvement, out of scope for this pass. The pre-flight operational check (verify NTP is running before market open) still applies.
- **Tests:** `tests/test_d1_d2_clock.py` — `ClockGuard` baseline/no-drift/forward-jump/backward-jump/self-clear behavior (deterministic via monkeypatched `time.time`/`time.monotonic`); Orchestrator entry-blocked-when-untrusted; existing position unaffected by distrust; EOD square-off unaffected by distrust; trust restoration resumes entries.

### D2. Timezone misconfiguration (host in UTC instead of IST) — ✅ IMPLEMENTED
- **Trigger:** identical root cause to D1 conceptually but a static misconfiguration rather than drift — a host running in UTC (or any non-IST timezone) must not silently reinterpret `09:15`/`15:15` config times.
- **Detection/fix:** `core/clock.py`'s `now_ist()`/`epoch_to_ist()` are now the single source of "current time" and "convert a broker epoch" for every market-time-relevant call site: `Application._candle_loop`/`_within_session`/`_sleep_to_next_boundary`, `Orchestrator`'s internal timestamps (`_flatten_orphan`'s client-order-id, `square_off`'s exit time), `FyersBroker`'s candle timestamp conversion (previously the naive `datetime.fromtimestamp(row[0])`, which used the **host's local TZ** — silently wrong on a non-IST host), `PaperBroker`'s synthetic candle generation, and `SessionSnapshot`'s trading-day stamp (previously `date.today()`, which could roll over up to 5.5h early on a UTC host relative to the actual IST trading day).
- **Expected final state:** ORB/trading-window/hard-exit comparisons produce the same decision regardless of host TZ configuration, since they're now always computed from an explicit IST instant rather than whatever the host's naive local clock happens to represent.
- **Operator action:** none required going forward for this specific failure mode; setting VPS `TZ=Asia/Kolkata` is no longer load-bearing for correctness (though still good hygiene for reading raw log timestamps).
- **Tier status:** ✅ **Implemented.** Deliberately scoped to **timezone correctness**, not clock-drift validation against an external source (see D1's explicit exclusion, which applies here too).
- **Tests:** `tests/test_d1_d2_clock.py`'s `now_ist`/`epoch_to_ist` unit tests (tz-aware, correct UTC offset, matches manual UTC conversion for a known epoch). A byproduct worth noting: fixing this surfaced a real latent bug during implementation — `square_off()` previously passed a **naive** `datetime.now()` as the exit timestamp into `_journal_trade`, which would raise `TypeError: can't subtract offset-naive and offset-aware datetimes` once `Position.entry_time` became tz-aware from the candle-timestamp fixes above; this is now fixed by using `now_ist()` consistently. The full existing test suite (75 tests) was re-run and required updating `tests/conftest.py`'s candle-builder helper to produce tz-aware IST timestamps, matching what real/paper brokers now actually produce — this is a test-fixture realism fix, not a behavior change.

### D3. Candle timestamp semantics mismatch (bar-open vs bar-close)
- **Trigger:** already identified in the original audit (C2) — FYERS candle timestamps are bar-open time, so the 15:15 candle actually represents 15:15–15:20 and isn't available until ~15:20.
- **Detection:** N/A — this is a systematic misunderstanding risk, not a runtime fault.
- **Automatic recovery:** Tier 1's wall-clock EOD square-off (`end_of_day()` triggered by `now >= hard_exit`, independent of candle arrival) **structurally sidesteps this** — it no longer depends on a specific candle being processed to trigger the exit.
- **Expected final state:** flat by/soon after `hard_exit`, regardless of candle timestamp semantics.
- **Operator action:** none — this is the scenario C2 was specifically built to eliminate.
- **Tier status:** ✅ Covered by Tier 1 (C2).
- **Tests needed:** already covered by `test_c2_end_of_day_squares_off` and `test_replay_eod_square_off_when_position_left_open`. No new tests required; listed here to close the loop with the original audit finding explicitly.

---

## E. Authentication / session failures

### E1. Broker access token expires mid-session — ✅ IMPLEMENTED
- **Trigger:** FYERS tokens are typically valid ~24h and expire once daily; a session spanning that boundary (or a token invalidated server-side) causes every subsequent broker call to fail auth.
- **Detection:** `bujji/broker/errors.py`'s `AuthenticationError` is a distinct, broker-agnostic exception. `FyersBroker._raise_if_auth_error()` classifies every response (HTTP 401/403, known FYERS auth-related error codes — labeled best-effort, verify against live docs before go-live — and a keyword fallback on the message field) and is threaded through every call site (`connect`, `get_spot`, `get_recent_candles`, `resolve_atm_contract`, `get_ltp`, `place_order`, `get_order`, `cancel_order`, `get_open_positions`). `PaperBroker`/`ReplayBroker` gained a `simulate_auth_expiry()` test hook so this is exercisable without the (still-unwired) real FYERS transport.
- **Automatic recovery:** `ExecutionEngine._with_retry` and `_place_idempotent` catch `AuthenticationError` **first**, log `auth_error_detected`, and re-raise immediately — zero retries, zero backoff sleep burned (proven by `broker.auth_error_calls == 1` / `broker.place_calls` staying at 0 in tests, vs. a generic error consuming the full `retry_attempts` budget). The Orchestrator catches it distinctly at every broker-touching call site (entry, in-position reassessment, exit, square-off, orphan-flatten, startup reconciliation) and sets `status.auth_expired = True` / a specific `health_detail`, **without** losing the position — entry rolls back to `READY` with no position created; in-position/exit failures leave the position exactly as it was, to be retried next cycle. The flag self-clears (`_clear_auth_flag()`) the next time any broker call succeeds, so a resolved issue doesn't linger as a false alarm.
- **Expected final state:** if flat when it hits — no position, safe, `READY`/`DONE_FOR_DAY`. If in a position — position **preserved**, `IN_POSITION`, `auth_expired=True` until either the token is refreshed (self-clears) or end-of-day square-off is attempted (also fails identically until refreshed, `end_of_day()` returns `False`, loop keeps retrying every boundary per C2 rather than crashing).
- **Operator action:** unchanged from the original plan — refresh/regenerate the FYERS access token and restart the process. On restart, `connect()` re-validates and, per C1, correctly resumes any open position (verified end-to-end: entry → simulated expiry mid-session → simulated refresh → fresh-process recovery → clean square-off, with zero position loss at any step). Automated token refresh remains explicitly out of scope — this is detection + fail-fast + alerting only, not remediation.
- **Tier status:** ✅ **Implemented** (this pass).
- **Tests:** `tests/test_e1_e2_auth_expiry.py` (16 tests) — FYERS classifier unit tests (401/403, known code, keyword fallback, no false positives on unrelated errors, pass-through on success); `ExecutionEngine` no-retry-on-auth vs. full-retry-on-generic-error (both for a plain call and for `submit_and_confirm`); Orchestrator entry/in-position/exit/square-off paths under simulated expiry (position never lost, distinct status set); self-clearing once the broker recovers; a startup-time auth failure (fail-fast, no silent "clean start"); and a replay-path test proving a token "expiring" mid-replay doesn't crash the Replay Engine and is flagged distinctly through the identical live decision path.

### E2. Broker session invalidated by a concurrent login (another device/session) — ✅ IMPLEMENTED (unified with E1)
- **Trigger:** FYERS (like most brokers) may invalidate an existing API session if the user logs in elsewhere.
- **Detection/Recovery/Operator action:** identical to E1 — same `AuthenticationError` mechanism, since the broker reports this the same way it reports an expired token (401/403 or an auth-class error code/message). Not a separate code path by design.
- **Tier status:** ✅ Covered by E1's implementation (unified fix, as originally planned).
- **Tests:** covered by E1's tests — no separate test needed since the classifier and every downstream handler treat the two causes identically.

---

## F. Process / infrastructure failures

### F1. Process crash (unhandled exception, OOM-killed, `kill -9`)
- **Trigger:** the Python process dies at an arbitrary point — mid-candle-processing, mid-order-placement, between placing an order and persisting its client-order-id.
- **Detection:** on next start, `Orchestrator.startup()` → `_recover()` runs automatically.
- **Automatic recovery:** this is precisely what Tier 1 (C1) was built for. The critical sub-case: crash **between order acceptance and snapshot persistence** — mitigated because `_entry_cid`/`_exit_cid` are persisted **before** placement (`orchestrator.py`, entry path: `self._persist()` immediately after setting `_entry_cid`, before `submit_and_confirm`). On restart, even if the position dict itself wasn't yet saved (crash before the *post-fill* persist), the broker reconciliation path (`_find_live_short`) discovers the live position independent of whether our own snapshot captured it — falling into either the "resume" (if a position dict happened to save) or the "orphan" (if it didn't) branch. **Either way, the position is not abandoned.**
- **Expected final state:** position resumed or (if unresumable) flattened as an orphan. Never silently forgotten.
- **Operator action:** none required for capital safety; investigate the crash cause (logs, OOM killer dmesg, systemd journal) to prevent recurrence.
- **Tier status:** ✅ Core scenario Tier 1 was built for.
- **Tests needed:**
  - Already covered by `test_c1_resume_open_position`, `test_c1_orphan_position_flattened`.
  - **New:** crash simulated *between* `self._entry_cid = cid; self._persist()` and `submit_and_confirm` returning — i.e., the order was never actually placed. Assert restart correctly finds **no** broker position and **no** stale in-flight assumption (should fall to the clean-slate branch, not misclassify).
  - **New:** crash simulated *between* a successful fill and the position-dict persist (`_enter`'s final `self._persist()` inside `_resume_position`/normal flow — check exact line) — assert broker reconciliation still discovers and resumes/flattens correctly via the orphan path.

### F2. VPS full restart (reboot), not just process restart
- **Trigger:** OS-level reboot — process restart plus loss of anything not on disk (no in-memory state survives regardless).
- **Detection/Recovery:** identical to F1 from the application's perspective; the only additional concern is **process supervision** — does the bot actually restart automatically after a VPS reboot?
- **Expected final state:** same as F1, **provided** the process is configured to auto-start (systemd/supervisor unit with restart policy). If it is *not* configured to auto-start, the bot simply doesn't come back — an open position rides unmanaged until a human notices.
- **Operator action:** install [deploy/bujji.service](../deploy/bujji.service) (`sudo systemctl enable --now bujji.service`) — see [deploy/README.md](../deploy/README.md) for install/operate instructions.
- **Tier status:** ✅ **Implemented** (this pass) — `deploy/bujji.service`: `Restart=always`, `RestartSec=5`, `StartLimitIntervalSec`/`StartLimitBurst` to avoid hammering the broker on a persistent failure, `WantedBy=multi-user.target` for auto-start after reboot, `TimeoutStopSec`/`KillSignal=SIGTERM` so graceful shutdown (releasing the F4 lock, stopping the dashboard) gets a chance to run before an escalated kill. Documented interaction with E1/E2: a restart-loop caused by an expired token will keep restarting and immediately fail auth again — `journalctl -u bujji` shows `startup_blocked_auth_failure` distinctly, so this is diagnosable, not silent. Documented interaction with F4: a `Restart=always` cycle landing on a still-shutting-down prior instance correctly fails the new instance's lock acquisition and is retried on the next `RestartSec` interval, not run concurrently.
- **Tests:** this remains **not unit-testable** — a systemd unit's behavior is validated operationally, not in the Python test suite. `deploy/README.md` specifies the manual chaos drill (reboot the VPS with a known open paper-trading position; verify the service comes back and `journalctl` shows `recovery_resumed`/`recovery_orphan_position`/`position_already_closed`) that should be run once before relying on this in live trading.

### F3. Disk full (journal/log/snapshot writes fail)
- **Trigger:** `logs/` or `data/` partition fills up (JSONL logs grow unbounded — no rotation exists today).
- **Detection:** `SessionStore.save()`'s `os.fsync`/`os.replace` would raise `OSError` on a full disk — this is **not caught** anywhere in `_persist()`, so it would propagate. In `_enter`/`_do_exit`, `_persist()` calls are not wrapped in try/except.
- **Automatic recovery:** **NONE** — an unhandled `OSError` from `_persist()` during `_enter()` would propagate up through `_handle_pre_position`'s `try/except ExecutionError` (which wouldn't catch a raw `OSError`), crashing `on_candle`, which in `app.py`'s loop **is** caught generically (`except Exception: ... healthy=False`) — so the *loop* survives, but that specific candle's action (e.g., persisting the entry) silently failed, potentially **after the order was already placed at the broker** but before it was durably recorded locally.
- **Expected final state:** this is a genuinely dangerous interaction — an order could be placed with the broker while the local snapshot write fails, leaving local state and broker state diverged until the next restart's reconciliation (which would then correctly catch it via C1's orphan/qty-mismatch paths, but with an unhealthy status and no journal entry for that trade in the interim).
- **Operator action:** disk space must be monitored proactively (this is infrastructure hygiene); log rotation should be configured (`logging_setup.py` currently creates one file per day with no size cap or rotation policy at all — confirmed by reading the file, it's a plain `FileHandler`, not a `RotatingFileHandler`).
- **Tier status:** ❌ **Gap** — both the missing log rotation and the unguarded persist-write path.
- **Tests needed:**
  - Unit: monkeypatch `Path.write_text`/`os.fsync` to raise `OSError` inside `SessionStore.save()` → assert the caller's behavior today (documents that it propagates uncaught) as a baseline for the Tier 2 fix.
  - Once fixed: assert `_persist()` failures are caught, logged, and set `healthy=False` without crashing candle processing, and that reconciliation on next restart correctly recovers from a "broker has it, local snapshot doesn't" divergence (this reuses the orphan-detection test pattern from C_D2).
  - Add a log-rotation test/config check (`RotatingFileHandler` or size-based rotation) as a straightforward Tier 2 item.

### F4. Two instances of the bot accidentally running simultaneously (double-start) — ✅ IMPLEMENTED
- **Trigger:** operator error — a previous instance didn't fully stop before a new one was launched (e.g., systemd restart race, or manual double-launch).
- **Detection:** `core/process_lock.py`'s `ProcessLock` acquires an OS-level `flock(LOCK_EX | LOCK_NB)` on `paths.lock_file` at the very start of `Application.__init__`, before the broker or any shared state is touched.
- **Automatic recovery:** a duplicate instance's `Application()` construction raises `LockAcquisitionError` immediately (logged as `CRITICAL`) — no order is ever at risk of being placed by two live processes. A **crashed** prior instance does NOT block a real restart: `flock` is scoped to the open file description, so the OS releases it the instant the holding process dies for any reason (clean exit, crash, `kill -9`, OOM) — no stale PID file to clean up.
- **Expected final state:** the second instance never starts; the first continues unaffected.
- **Operator action:** if a restart is ever refused unexpectedly, it means a live process genuinely still holds the lock — check for it (`ps`) before assuming it's stale; do not delete the lock file to force through without confirming.
- **Tier status:** ✅ **Implemented** (this pass). `enforced` reports `False` on platforms without `flock` (e.g. Windows) — the guard is a no-op there rather than a false promise; a warning is logged at startup.
- **Tests:** `tests/test_f4_process_lock.py` — refusal on a live second lock, re-acquisition after release (normal restart), idempotent re-acquire within the same instance, context-manager release, release-without-acquire safety, and an integration test constructing two `Application` instances over the same lock file (second refused before touching the broker) plus a clean-restart variant.

---

## G. Config / operator-error failures

### G1. Config file has an invalid/contradictory value (e.g., `orb_start > orb_end`, `lots <= 0`)
- **Trigger:** typo or copy-paste error in `config.yaml`.
- **Detection:** **NONE beyond Pydantic's basic type coercion.** No cross-field validators exist (previously flagged as audit item I9). A negative `lots` would silently compute a negative/zero quantity; `orb_start > orb_end` would make `OpeningRangeBuilder` never complete, leaving the bot in `WAITING` forever (a *safe* failure — it just never trades) — but a subtler contradiction (e.g., `trading_end < hard_exit`) could produce unexpected window logic.
- **Automatic recovery:** none.
- **Expected final state (intended):** config load should fail fast at startup with a clear error, before any broker connection is attempted.
- **Operator action (today):** manually review config against a checklist before deploying; this is real but currently unenforced by code.
- **Tier status:** ❌ **Gap** (audit item I9, unaddressed).
- **Tests needed:**
  - Unit: construct `AppConfig` with `orb_start >= orb_end` → assert (today) it's accepted silently — baseline test proving the gap.
  - Once fixed: assert a `model_validator` rejects it at construction with a clear message; same for `lots <= 0`, `max_mtm_loss <= 0`, `strike_interval <= 0`.

### G2. `broker.name: fyers` configured but credentials missing/blank
- **Trigger:** operator flips from `paper` to `fyers` without setting `FYERS_APP_ID`/`FYERS_ACCESS_TOKEN` env vars.
- **Detection:** `FyersBroker.connect()`'s `profile` call would fail (auth error) — falls into the same generic-exception bucket as E1/A1, but at **startup**, before any trading logic runs.
- **Automatic recovery:** `ExecutionEngine.connect()` retries `connect` per the retry policy, then raises `ExecutionError`, which propagates out of `Application.run()` uncaught — the process exits. This is actually **acceptable** (fail-fast is correct here), but the *error message* a human sees should clearly say "missing credentials" not just "connect failed after 3 attempts."
- **Expected final state:** process refuses to start; no trading occurs (safe).
- **Operator action:** set the env vars, restart.
- **Tier status:** ⚠️ Fails safely today, but the diagnostic clarity is poor (generic retry-exhaustion message rather than "credentials missing"). Minor, not a capital-safety gap.
- **Tests needed:**
  - Unit: `AppConfig.load()` with `broker.name=fyers` and no env vars set → assert `app_id`/`access_token` are `None`; a separate startup-validation test (once added) should assert a clear, distinct error rather than a generic retry-exhaustion message.

---

## H. Margin / broker-side rejection

### H1. Insufficient margin at entry time
- **Trigger:** account doesn't have enough margin to sell the ATM option (previously flagged as audit item C9 — no pre-trade margin check exists).
- **Detection:** the broker itself should reject the order — `place_order` would return `OrderStatus.REJECTED` with a message, which `submit_and_confirm` already raises as `ExecutionError` for.
- **Automatic recovery:** falls into the same path as A1's entry-failure handling — rollback `CONFIRMED → READY`, no position created. **Verified against `signal/engine.py:106`:** `SignalEngine._signalled = True` is set unconditionally at the moment a qualifying candle is found — *before* the resulting order's outcome is known. Because `_signalled` gates all future signal generation for the day, a margin-rejected entry is **not** retried on a later candle. There is no log storm; the opposite risk applies.
- **Expected final state:** no position (safe), but the day's **one allowed trade opportunity is permanently forfeited** the moment the first qualifying breakout is rejected by the broker — even though `_trades_taken` was never incremented and the FSM sits in `READY` looking retryable. This is a silent, easy-to-miss "we should have traded today but didn't" condition, not a capital-risk one.
- **Operator action:** if `entry_failed` appears in logs, expect no further trade attempts that day even though the state machine looks like it's still waiting — this is a genuine mismatch between apparent and actual bot capability that should be visible on the dashboard (currently it is not distinguished from "no breakout occurred yet").
- **Tier status:** ⚠️ **Confirmed behavior, not previously documented anywhere** — the code is doing something defensible (never double-enter on a stale thesis) but the operator-visible consequence (day forfeited after one rejected order) is undocumented and not surfaced distinctly on the dashboard.
- **Tests needed:**
  - Unit: force a `REJECTED` entry (mock broker returns `OrderStatus.REJECTED`), then feed a subsequent qualifying candle in the same session → assert **no** second `ENTER` signal is produced and the FSM remains in `READY` for the rest of the day (confirms and pins down the verified behavior above as a regression-guarded contract, not just an observation).
  - Dashboard/status: assert `RuntimeStatus` (or a new field) can distinguish "no breakout seen yet" from "breakout occurred but entry failed, day forfeited" — a nice-to-have observability fix, not urgent for capital safety.
  - Once margin pre-check (C9) is implemented: unit test that a simulated low-margin condition blocks entry *before* even attempting `place_order`, and confirm it interacts with `_signalled` the same way (day forfeited, not retried).

---

## Cross-cutting: what this plan deliberately does NOT re-litigate

- Timezone/clock (D1/D2), daily-loss-limit enforcement, replay path isolation, CONFIRMED/EXITING wedge hardening beyond what F1 already tests, candle de-dup (C_D1), and margin checks (H1) are **known Tier 2 items** from the original audit. This document specifies their *chaos-test acceptance criteria* so Tier 2 implementation has a concrete target, but does not implement them.
- **Newly surfaced in this pass** (not in the original audit): auth/token-expiry as a distinct error class (E1/E2), process supervision / auto-restart (F2), disk-full / log rotation (F3), and double-instance locking (F4). These should be folded into Tier 2 planning alongside the original list.

---

## Suggested test file organization (for when Tier 2 implementation begins)

```
tests/
  test_chaos_broker_outage.py       # A1–A3, B1–B3
  test_chaos_data_quality.py        # C_D1–C_D3
  test_chaos_clock_timezone.py      # D1–D3
  test_chaos_auth.py                # E1–E2
  test_chaos_process.py             # F1, F3, F4 (F2 is a manual drill, documented not automated)
  test_chaos_config.py              # G1–G2
  test_chaos_margin.py              # H1
```

Each fault should be injectable via small, explicit hooks on `PaperBroker` (following the pattern already established by `partial_fill_qty`, `raise_on_place_after_record`, `seed_position`) rather than monkeypatching internals — keeps chaos tests readable and prevents them from becoming brittle against refactors.

## Priority order for Tier 2 (informed by this plan)

1. ~~**F4 (double-instance lock)** and **C_D2's uncaught schema-mismatch in `position_from_dict`**~~ — **✅ implemented and tested.** `core/process_lock.py`, `core/position_codec.py`'s `PositionSchemaError`, 18 tests across `tests/test_f4_process_lock.py` and `tests/test_cd2_snapshot_schema.py`, plus one replay test.
2. ~~**E1/E2 (auth-class error detection)**~~ — **✅ implemented and tested.** `broker/errors.py`'s `AuthenticationError`, classifier in `FyersBroker`, no-retry short-circuit in `ExecutionEngine`, distinct handling + self-clearing at every Orchestrator broker-touching call site, 16 tests in `tests/test_e1_e2_auth_expiry.py`. **Remaining, explicitly deferred:** automated token refresh (requires a human — this was detection/alerting only, by design).
3. ~~**F2 (process supervision)**, **D1/D2 (clock/timezone)**, **C_D1 (candle de-dup)**~~ — **✅ implemented and tested.** `deploy/bujji.service` + `deploy/README.md` (F2, operationally validated via a manual reboot drill, not unit-testable); `core/clock.py`'s `now_ist()`/`epoch_to_ist()`/`ClockGuard` threaded through every market-time call site (D1/D2); duplicate/stale-candle rejection and gap detection in `Orchestrator.on_candle()` (C_D1). 34 new tests across `tests/test_d1_d2_clock.py` and `tests/test_cd1_candle_dedup.py`. **Explicitly NOT implemented** (documented, out of scope): validating the clock against an external trusted source (broker time/NTP) before trading starts — only in-session drift is detected; automated token refresh remains a human action (unchanged from E1/E2).
4. **F3 (log rotation + persist error handling)**, **G1 (config validation)** — the platform is now frozen per the approved plan for an extended paper-trading campaign; these (and H1) will be addressed based on observed evidence from that campaign rather than further speculative hardening.
5. **H1 (margin pre-check)** — lower urgency since broker-side rejection already fails safely; the gap is efficiency/noise, not capital risk.

**Platform status:** frozen as of this pass for an extended paper-trading campaign. No further hardening will be implemented speculatively — remaining items (F3, G1, G2, H1, A2, A3/C_D3, B2) are deferred until real paper-trading evidence indicates which of them actually matter in practice.
