# LSQ2_PLAN.md

Planning document for LSQ-2, built entirely from LSQ-1's actual findings (`docs/LSQ1_DAY1_REPORT.md`, `STATUS.md`, `TODO.md`, `docs/PRODUCTION_READINESS_GATE_REVIEW.md`, all confirmed committed and current as of `332540d`/`fab65fe` on `v1.0-shadow`). No repository recovery, ORB-VWAP comparison, or architecture discussion is included, per instruction. No code is proposed here — this is a plan, not an implementation.

---

## 1. What remains to qualify before production?

LSQ-1 Day 1 qualified the system's **infrastructure resilience and data-integrity behavior under real market conditions while idle** — that is real, evidenced, and should not be understated. What it did *not* and *could not* qualify, because the mechanism doesn't exist yet, is the trading pipeline itself. Two categories remain, both evidence-backed from the current committed state:

**Structural safety gaps (P1, `TODO.md`), independent of whether trading has started:**
- **P1-1** — `production_runtime`'s Shadow mode has no structural guard against a real broker; `RuntimeConfig(mode="SHADOW", broker_name="fyers")` constructs successfully today, producing a live, unguarded `FyersBroker`. Confirmed by direct execution, not inferred.
- **P1-2** — a confirmed, deterministic, proven (`tests/test_concurrency_lifetime_proof_p3.py`) silent connection-state corruption path in `FyersTickFeed`: a stale socket generation's callback can silently flip `is_connected=True` on the current generation with nothing able to catch it.
- **P1-3** — no numeric risk limits (max capital, max daily loss, max simultaneous positions) exist anywhere in the Trading Brain v3 dispatch path; `risk_brain` and `runtime_safety` gate on market *condition* trustworthiness only, never on Rupee numbers.
- **P1-4** — a real, live-certified margin engine (`bujji/capital/`) exists but is completely disconnected from `production_runtime`'s dispatch path; the EQ1 sprint's own real end-to-end trace produced an order with no margin figure attached anywhere.

**The trading pipeline itself has never been exercised end-to-end under live conditions**, because `run_live_shadow.py` has no entry-order-construction path (P2-1) — LSQ-1 Day 1 recorded 27 decision cadences and zero trades, by design, not by chance. Every downstream qualification concern this system cares about (per-trade journal consistency, replay-vs-live match on a real trade, exit-engine correctness under a real position, portfolio valuation against a real non-zero P&L) is *wired and unit-tested* but has never been observed operating on a real, spontaneous, live-market entry.

Both categories must close, in some order, before a production/live-capital discussion is meaningful — P1 because it's a live safety gap regardless of trading volume, and the entry-construction gap because without it, no amount of additional idle-session qualification adds new evidence about the thing that actually matters: whether the system trades correctly.

## 2. What was the largest weakness exposed by LSQ-1?

**Not a defect — a scope limit, and it's the single most important thing LSQ-1 Day 1 revealed about itself.** Every anomaly that occurred today (the Cloudflare burst, three isolated disconnects, the pre-market silence) was handled correctly by the existing reliability mechanisms, exactly as designed. But because there is no entry-order-construction path, LSQ-1 as currently scoped can only ever qualify the **idle/monitoring path** — connectivity resilience, tick integrity, journal correctness under zero legs. It structurally cannot produce a single data point about decision quality, execution correctness, exit-engine behavior under a real position, or replay-vs-live agreement on an actual trade, no matter how many days it runs. Continuing to accumulate idle-session days does not close this gap; it can only ever repeat the same category of evidence.

Secondary, concrete weakness, directly evidenced and worth naming on its own: **`reconnect_count` is confirmed dead for the second time** (Session #3, and again today — read `1`, real count `~9`). An operational dashboard metric that has now failed identically twice is not a one-off; it's a metric nobody should be building further trust or alerting logic on top of until it's fixed.

## 3. Single highest-impact engineering task for LSQ-2

**P1-1 — close the structural broker guard gap in Shadow mode.**

This is not the task that unblocks the most backlog items (that would be P2-1, entry-order construction), but it is the single highest-impact task specifically because it is a **live, exploitable capital-risk hole in the system exactly as it runs today**, independent of whether entries exist. A configuration mistake, a bad merge, or a future engineer changing `broker_name` without realizing the mode implication would today produce a fully-live, real-order-capable broker with zero code-level check stopping it — confirmed by directly constructing that exact object, not by inference. The fix is a low-risk extension of a pattern this codebase has already built, tested, and proven live: `bujji/broker/guard.py`'s `disable_live_execution()` — the same mechanism the paper/hybrid broker composition already uses successfully. Closing this before LSQ-2 begins means every subsequent session, including the eventual introduction of real entries under P2-1, inherits a structurally safe Shadow mode rather than one safe only by convention and operator discipline.

## 4. LSQ-2 Objectives

1. **Apply and verify the P1-1 fix before LSQ-2 Day 1 begins.** LSQ-1's own Rule #1 (architecture/feature work frozen *during* qualification) does not block this — `TODO.md` explicitly scopes "bug fixes, reliability, data correctness, replay correctness, and operational stability changes" as permitted, and this is exactly that category, applied in the gap between LSQ-1's conclusion and LSQ-2's start, not mid-session.
2. **Fix or formally retire `reconnect_count` and `cadence_duration_seconds`** before trusting either in an LSQ-2 dashboard or report — don't carry a second known-dead metric into a second qualification window.
3. **Continue multi-day accumulation** — LSQ-2 should extend the track record started by LSQ-1 Day 1, specifically to determine whether the Cloudflare 502 burst pattern (2/2 sessions so far, same ~07:47 IST window) is environmental noise or a genuine recurring characteristic worth designing around.
4. **Do not introduce P2-1 (entry-order construction) inside LSQ-2 itself** unless it is explicitly, separately authorized as its own scoped sprint with its own tests — LSQ-2, like LSQ-1, should qualify a fixed, frozen system, not a moving one. If entry-order construction is wanted, that is a decision for a dedicated sprint *before* LSQ-2 starts, with LSQ-2 then qualifying the resulting (still frozen) system — not something to build mid-qualification.
5. **Preserve every LSQ-1 discipline that worked**: the immutable provenance header, the honest disclosure of not-yet-run checks (e.g. today's undone replay-vs-live re-execution) rather than assuming pass, and the human-reviewed verdict layered on top of (not silently overriding) the automated tool's raw score.

## 5. Objective PASS Criteria for LSQ-2

Built directly on LSQ-1's own framework (`docs/LSQ1_PROTOCOL.md`'s four-verdict scale: PASS / PASS WITH OBSERVATIONS / CONDITIONAL PASS / FAIL, and the consistency checker's existing pass conditions), extended with the specific items LSQ-2 exists to close:

- **Consistency check clean**, same bar as Day 1: `orphan_positions: []`, `critical_qualification_failures: []`, `STOP_QUALIFICATION: false`.
- **P1-1 fix independently re-verified**, using the same direct-execution method that originally found the gap: constructing `RuntimeConfig(mode="SHADOW", broker_name="fyers")` (or the equivalent post-fix construction path) must now either fail to construct, or fail before any `place_order`-capable method is reachable — not merely "believed fixed" from the diff.
- **`reconnect_count` (and `cadence_duration_seconds` if addressed) verified accurate** against the real, independently-counted event log for at least one full session — not just disclosed as fixed, demonstrated.
- **Every ERROR-level log line individually traced and classified**, same discipline as Day 1's 8-for-8 — an LSQ-2 report that doesn't account for 100% of its own error lines does not meet the bar Day 1 set.
- **No Critical Qualification Failure at any point**, and any anomaly (infrastructure stress, disconnects, etc.) explicitly classified as expected/unexpected-but-harmless/unresolved, with unresolved being an automatic block on PASS.
- **No code change occurs mid-session** (LSQ-1's Rule #1, carried forward) — if a defect is found requiring a fix, the correct outcome is ending that LSQ-2 session's evidence window honestly and fixing it in the gap before the next one, exactly as this plan proposes doing for P1-1 before LSQ-2 even starts.
- **The verdict is human-reviewed and disclosed as such**, with the automated tool's raw score reported alongside it, not replaced by it — continuing Day 1's practice of stating explicitly when and why a human judgment differs from the raw automated signal.

If all of the above hold: **PASS**. If the consistency check and P1-1 re-verification hold but new, real, unresolved anomalies occur (the Cloudflare pattern recurring a third time, for instance, without a clear explanation): **PASS WITH OBSERVATIONS**, consistent with how Day 1 itself was scored. Any orphan position, any critical qualification failure, any unresolved error, or a P1-1 re-verification failure: **FAIL**, no exceptions.

---

No code was written or proposed to produce this plan.
