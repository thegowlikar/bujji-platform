# LSQ-1 — Live Shadow Qualification Protocol

**Mission**: determine whether BUJJI can be trusted to trade exactly as designed. Not whether the strategy is profitable. Architecture, features, and strategy logic are frozen for this period — the only acceptable code changes during LSQ-1 are bug fixes, reliability improvements, data correctness, replay correctness, and operational stability. Anything else goes into a backlog, not into the running code.

---

## Scope, stated honestly before Day 1

**What LSQ-1 can actually validate today, given the real state of the system** (per the EQ1 qualification sprint and the two most recent sprints' own disclosures):

- `run_live_shadow.py` has **no entry-order-construction path wired in** — no code there generates a real entry order from a live decision. Today's live sessions therefore run with the tick feed, decision cadence (Series 99-106 MSI stack), watchdog, portfolio valuation, and Exit Engine all live and real — but with **zero real paper positions to revalue or exit**, unless a position is seeded manually for validation purposes.
- Because of this, LSQ-1's per-trade checks (entry/exit/ledger/replay/MTM consistency) cannot be exercised by real, spontaneously-generated trades until entry-order-construction is wired into this specific script — which is out of LSQ-1's own scope (that's a feature/architecture change, explicitly frozen).
- **What LSQ-1 CAN and WILL validate every real trading day, starting immediately**: tick feed reliability, watchdog behavior, decision-cadence health, session lifecycle (startup/shutdown), journal integrity, and — whenever a position exists (seeded for validation, or once entry is wired in a future, separate sprint) — the full entry→MTM→exit→journal→replay chain, using the exact same mechanism proven in the Exit Engine v1 sprint's own real, replayed demonstration.

This is not a workaround — it is the honest boundary of what exists today, stated up front so no daily report overclaims.

---

## Daily Operating Sequence

1. Fresh FYERS token generated and installed (operator action, existing routine).
2. Pre-market checklist run (existing, real, mandatory-fail-aborts).
3. `run_live_shadow.py --live` launched — tick feed, watchdog, decision cadence, portfolio valuation, Exit Engine all active.
4. Monitored through market close exactly as Sessions #1-3 were (event-driven + heartbeat).
5. At close: `tools/lsq_consistency_check.py` run against the day's real journals.
6. `tools/lsq_daily_report.py` run — produces the LSQ Daily Report (9 sections, per the sprint's own spec).
7. `tools/lsq_chief_engineer_log.py` run — produces the Chief Engineer's Log.
8. Cumulative scorecard (`data/lsq/scorecard.json`) updated with the day's real statistics.
9. Any Critical Qualification Failure (see Stop Conditions) halts qualification until documented, reproduced, fixed, and verified — per the sprint's own 5-step discipline.

---

## Per-Trade Capture (when a real position exists)

Trade ID, Entry Time, Entry Reason, Evidence used, Strategy selected, Entry premium, Live MTM timeline, Maximum MTM, Minimum MTM, Exit reason, Exit time, Exit premium, Realized P&L, Journal status, Replay status — all already real, structured fields across `PortfolioValuationJournal` (valuation/exit records) and `TradeLifecycleTracker` (max/min MTM), proven in the Exit Engine v1 sprint.

## Per-Session Capture

Startup health, token refresh, WebSocket health, tick freshness, tick loss, reconnects, order count, position count, portfolio MTM, exit count, journal completeness, replay generation, shutdown health — sourced from the existing health dashboard (P1/P4/P5 sprints), the watchdog's own real metrics, and the portfolio/exit journals.

## Automatic Consistency Checks (per completed trade)

Entry exists / Exit exists / Ledger flat / Journal complete / Replay generated / Replay matches live / No orphan positions / MTM internally consistent. Any failure is a **Critical Qualification Failure** — stop, document, reproduce, fix, verify, resume. Implemented in `tools/lsq_consistency_check.py`.

## Stop Conditions

Replay mismatch, ledger mismatch, duplicate positions, position not flat after exit, MTM inconsistency, journal corruption, tick corruption. These are engineering bugs, not trading outcomes — qualification halts until closed with the same evidence discipline used throughout this whole engagement (root-caused, reproduced, fixed, verified — never patched blind).

## Scorecard (cumulative, `data/lsq/scorecard.json`)

Operational: crash-free sessions, runtime uptime, reconnect count, tick integrity. Trading: trades, win rate, avg win, avg loss, profit factor, max drawdown. Execution: entry correctness, exit correctness, MTM correctness, journal correctness. Replay: replay success %, replay match %.

## Deliverables Per Day

**LSQ Daily Report**: trades taken, trades skipped, market regime, every warning, every exception, unexpected behavior, engineering observations, potential bugs, potential strategy improvements (**observations only — never implemented during LSQ-1**).

**Chief Engineer's Log**: the format specified by the user — System health / Replay fidelity / Tick integrity / Position reconciliation, each PASS/FAIL/PARTIAL with evidence; Engineering concerns (real, specific); code changes recommended (if any, and only reliability-class per Rule #1); Overall confidence (X.X/10, justified, never a round number picked for optics).

## Final Deliverable

At the end of the qualification period, one Qualification Report answering exactly one question: **is the system operationally trustworthy?** Not whether it's profitable, not whether returns can be improved.
