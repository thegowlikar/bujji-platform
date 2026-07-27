# Daily Research Operator v1 (DRO v1)
## BUJJI Options OS — Engineering Sprint 103

**Status:** No decision-making module was modified. This sprint operates
BUJJI; it does not redesign it. The one piece of new, real code is a
thin daily-report generator reusing every existing engine unchanged,
plus real proof that production's own `ProcessLock` (F4) prevents
duplicate runs. No broker order was placed anywhere in this sprint.

---

## 0. Architecture Freeze Review (performed before any implementation, per the user's own explicit request)

**Frozen as of this sprint** (change requires the formal change-control process in Section 0.1 below):

| Module | Series | Frozen since |
|---|---|---|
| Observation / Events / Episodes | 73B/73C, live_market_events, market_episode | Pre-77 |
| MSI (Decision Synthesis, Price Structure, Market Structure, Consensus) | 77, 78, 79, 81 | Series 82 |
| Strategy Eligibility / Trade Intent | 82, 83 | Series 84 |
| Market Direction Intelligence | 85 | Series 86 |
| Participant Positioning Intelligence | 86 | Series 87 |
| Strategy Selection Foundation | 87 | Series 88 |
| Volatility Structure Bridge | 88 | Series 89 |
| Strategy Selector | 89 | Series 93 (additive `expression_compatible_families` parameter added, then re-frozen) |
| Trade Thesis Engine | 92 | Series 93 |
| Strategy Expression Engine | 93 | Series 94 (investigated, explicitly NOT changed) |
| Trade Construction | 90 | Series 91 |
| Portfolio & Risk Construction | 91 | Series 95 |
| Position Construction Intelligence | 95 | Series 96 |
| Position Lifecycle Intelligence | 96 | Series 97 |
| Margin Bridge & Capital Fidelity | 97 | Series 98 |
| Execution Planning Engine | 98 | Series 99 |
| Decision Auditor & Learning Observatory | 99 | Series 100 |
| Shadow Trading Engine | 100 | Series 101 |
| Performance Analytics & Edge Validation | 101 | Sprint 102 |
| Evidence Collection Framework (thresholds/gates) | Sprint 102 | This sprint |

**Explicitly reusable, unfrozen (may still evolve without invalidating evidence)**: this sprint's own new daily-report generator, any future weekly-aggregation tooling, and the research archive's storage format (Section 7) -- none of these compute a trading decision; they only read and present already-frozen engines' real output.

### 0.1 Change-control process (binding from this sprint forward)

1. **No frozen module may be edited while evidence collection is active** without an explicit, separate "evidence collection paused" declaration.
2. Any change to a frozen module **starts a new evidence-collection epoch**: all shadow trades and analytics collected under the PRIOR engine version are tagged with that version and never silently merged with a new version's results (Sprint 102's own Blind Validation Protocol already establishes this discipline at the whole-engine level; this rule operationalizes it for ANY change, not only a formal validation cycle).
3. A change is only permitted following: (a) a written rationale, (b) confirmation it does not fall under any of Series 92-101's own "do not tune/optimise/redesign" constraints, (c) a fresh git commit tag marking the new frozen version.
4. This sprint's own new code (the daily report generator) is explicitly NOT covered by this freeze -- it may be extended freely, since it makes no trading decision.

## 1. Deliverable 1 — Existing Infrastructure Audit

| Component | Real finding | Classification |
|---|---|---|
| Scheduler / cron | **Confirmed: no crontab exists on the host, no scheduler infrastructure anywhere in this codebase** (repeats Series 98's own identical finding). | **MISSING.** |
| systemd unit (`deploy/bujji.service`) | Real, production-grade: `Restart=always`, crash-loop rate limiting, security hardening (`ProtectSystem=strict`, `NoNewPrivileges`), designed for a CONTINUOUSLY-RUNNING live-broker process (`asyncio.run(Application(config).run())`), not a once-daily batch job. | **PATTERN REUSABLE (security hardening, restart policy conventions), CODE OBSOLETE for direct reuse** -- a NEW systemd *timer* unit (or cron entry) pointed at a new batch entrypoint is the right shape, reusing this unit's own hardening directives, never its continuous-run design. |
| Single-instance lock (`bujji.core.process_lock.ProcessLock`, F4) | Real, generic, `flock`-based, crash-safe, carries **no trading logic at all**. | **REUSABLE DIRECTLY, verified by real execution in this sprint**: acquired against a NEW, dedicated lock file (`/tmp/bujji_dro_demo.lock`, never the live trading lock file), a second acquisition attempt was confirmed blocked (`LockAcquisitionError`), and the lock was proven to release and re-acquire cleanly for a subsequent run. |
| Market Recorder / replay runner | This whole MSI arc's own established per-day replay-script pattern (used continuously since Series 88). | **REUSABLE PATTERN, already in continuous use** -- this sprint's daily report generator follows it exactly. |
| Shadow Trading / Analytics | Series 100/101, real, complete, unmodified. | **REUSED DIRECTLY, unmodified** -- confirmed by the full 2726-test suite passing with zero code changes to either package. |
| Report generation | No pre-existing automated report generator (Series 99's dashboard views and Series 101's dashboard views are callable functions, not a scheduled job). | **MISSING** the scheduling/automation wrapper; the underlying report CONTENT functions are real and reused directly. |
| Health monitoring | `bujji/runtime_safety/`, `bujji/runtime/health.py` exist for the LIVE trading system (broker connectivity, rate limits) -- confirmed real but broker-coupled. | **ADAPTER REQUIRED** -- DRO's own health check (Section 2) is simpler (data-file presence/parseability), since no broker connection exists in this sprint's scope. |

## 2. Deliverable 2 — Daily Research Pipeline

The exact schedule the user specified, annotated with which existing (frozen) component executes each stage -- no new pipeline logic, only sequencing:

```
08:45  System health     — NEW, simple check (Section 1): data files present, prior lock released, disk space OK.
09:00  Data validation   — real Bhavcopy/observation parse succeeds (reuses options_observation, unmodified).
09:15  Market observation — Observation → Events → Episodes (unmodified, pre-Series-77).
09:20  Decision pipeline  — MSI → Thesis → Expression → Selection → Construction → Portfolio → Margin
                            → Execution Planning → Decision Auditor (unmodified, Series 77-99).
       Shadow position    — Series 100, unmodified, opens iff TRADE_APPROVED.
       Lifecycle tracking — Series 96, unmodified, reused via Series 100's own direct import.
15:30  Outcome recording  — Series 99's OutcomeRecord, unmodified.
       Analytics update   — Series 101's TradeAnalytics/EdgeValidationReport, unmodified, recomputed over the
                            growing real corpus.
       Decision journal   — Series 99's DecisionAuditorJournal, unmodified.
       Research report    — NEW (this sprint): formats the above into the permanent daily record (Section 4).
```

**Restart-safety, proven not just asserted**: the daily report generator was run twice against the same real day in this sprint and produced a byte-identical report both times (verified directly). Every underlying builder function in Series 92-101 is already a pure, deterministic function of its real inputs -- restart-safety is a direct, structural consequence of that discipline, not new work.

## 3. Deliverable 3 — Failure Recovery

| Failure | Recovery |
|---|---|
| Restart during market hours | Every stage is a pure function re-run from the same real inputs -- re-running from the last completed stage reproduces the exact same output (proven in Section 2). No in-flight state is lost because none is held outside each stage's own real, already-persisted inputs. |
| Duplicate execution | `ProcessLock` (Section 1), reused directly -- a second run against the same lock file fails immediately with `LockAcquisitionError`, verified by real execution in this sprint. |
| Missing market data | Every builder function in this arc already fails closed on missing real data (`None`, never a guess) -- e.g. Series 90's `REJECT_INCONSISTENT_CHAIN`, Series 97's `CONFIDENCE_UNKNOWN`. The daily operator's own health check (08:45) catches this BEFORE the pipeline runs, but the pipeline itself is also safe if data arrives late or incomplete. |
| Broker disconnect | Not applicable -- this sprint places no broker calls; there is no broker connection to disconnect. |
| Partial session (market data cuts off mid-day) | Whatever real intraday closes exist are used; Series 99's `build_outcome_record` already handles an empty or short `closes` sequence honestly (`None`/`SURVIVAL_UNKNOWN`), never fabricating the missing part of the session. |
| Corrupted journal | Every MSI journal (in-memory, per-run) is rebuilt fresh from replay -- there is no persistent journal file today to corrupt (Sprint 102's own Deliverable 1 finding); this is itself the strongest possible corruption-recovery story (nothing to corrupt) but also the reason Deliverable 7's archive design (below) matters -- IT is the thing that must not be lost. |
| Interrupted analytics | Series 101's `build_edge_validation_report` is a pure function over the real trade set -- an interrupted run simply has not yet been computed; re-running it from the same real, already-recorded `ShadowPosition`/`DecisionRecord` data reproduces the identical report. |

**Never lose evidence**: the binding rule from this sprint is that the REAL underlying evidence (Decision Records, Shadow Positions, Outcome Records) must be durably archived (Section 7) BEFORE any report is considered final -- a report can always be regenerated from durable evidence; evidence itself, once real, must never be regenerated or approximated.

## 4. Deliverable 4 — Daily Research Report (real, generated in this sprint)

Real output, generated by this sprint's own new code, for two real corpus days:

```
=== Daily Research Report: 2026-07-22 ===

Health summary: OK (real data parsed, real chain non-empty, no exceptions)
Data quality: real Bhavcopy day

Decision: thesis=RANGE_PERSISTENCE, family=NONE, confidence=LOW
No-trade: True

Shadow position opened today: False
Shadow positions closed today: 1
Lifecycle events today (open positions tracked): 1

Realised outcome: movement=-0.2406%, direction=DOWN, thesis_survival=UNKNOWN

Reliability status (per Sprint 102's own Gate A): 11/100 completed shadow trades -- NOT READY
```

Every field is a real value read from Series 92-101's own outputs -- nothing in this report is computed by new logic beyond simple formatting/aggregation.

## 5. Deliverable 5 — Weekly Evidence Report

Same template as Sprint 102's own Section 7, now confirmed executable against real per-day reports: aggregate the week's real daily reports' decision/no-trade/shadow/outcome fields into the same sample-growth, strategy-usage, thesis-usage, confidence-distribution, drift, and data-quality summary Sprint 102 already specified. Not separately re-specified here to avoid duplicating that design.

## 6. Deliverable 6 — Operational Metrics

- **Uptime**: not yet meaningfully trackable -- this sprint runs on demand, not on a live schedule (Section 1's confirmed missing scheduler). Becomes real once a timer/cron entry exists.
- **Successful vs. failed sessions**: real today -- 41/41 real Bhavcopy days parsed and replayed successfully across this whole arc (0 failed sessions).
- **Replay divergence**: real, PASS -- every series from 88 onward has verified byte-identical replay on two independent runs, including this sprint's own daily report (Section 2).
- **Missing observations**: real -- 2 of 43 real weekdays (Sprint 102's own finding).
- **Corrupted inputs**: real -- 0 of 41 real Bhavcopy files were undersized/corrupt (Sprint 102's own finding, re-verified).
- **Deterministic consistency**: real, PASS -- structural, not incidental (every builder function across Series 90-101 is a pure function).

## 7. Deliverable 7 — Research Archive (design)

Immutable storage design (not yet built as running infrastructure -- a design deliverable, per this sprint's own "no new intelligence" scope):

```
/research_archive/
  daily/{YYYY-MM-DD}/decision_record.json
  daily/{YYYY-MM-DD}/shadow_position.json      (if a trade was opened/tracked/closed)
  daily/{YYYY-MM-DD}/outcome_record.json
  daily/{YYYY-MM-DD}/report.txt
  weekly/{YYYY-Www}/report.txt
  analytics_snapshots/{YYYY-MM-DD}/edge_validation_report.json
```

Every file is a direct JSON serialization of an already-existing, real, frozen model (`decision_record_to_dict`, `position_to_dict`, `outcome_record_to_dict`, `edge_report_to_dict` -- all reused verbatim from Series 99-101's own `serialization.py` modules, never re-serialized with new logic). Write-once, append-only at the directory level -- a given date's files are never overwritten, only added to, mirroring this whole arc's established append-only journal discipline.

## 8. Deliverable 8 — Readiness Dashboard (real, generated in this sprint)

```
Evidence Status

Completed shadow trades
  11 / 100

Reliability
  NOT READY

Data coverage
  95.35%

Determinism
  PASS

Architecture
  FROZEN

Evidence
  IN PROGRESS
```

Every field above is a real value, generated by this sprint's own code, matching Sprint 102's own template exactly.

## 9. Evidence philosophy

Operating BUJJI daily is now itself the mechanism by which Sprint 102's evidence gates get closer to being met -- no engineering work accelerates Gate A; only real trading days do. This sprint's only job is to make sure NONE of those real days are wasted (lost to a crash, a duplicate run, or an unrecorded session).

## 10. Production transition

Once Gate A (100 real completed shadow trades, Sprint 102's own threshold) is reached, and Gates B/C/D/E hold simultaneously, the SAME daily pipeline (unmodified) becomes the natural basis for Paper Trading -- the only change at that point is swapping the market-data source from historical Bhavcopy replay to a live feed, and, far later, swapping Shadow Trading's own "no order" behavior for a real (small-capital, fail-closed) broker call -- exactly the layering this whole arc was built to support without redesign.

## 11. Deliverable 10 — Recommendation: **Continue daily evidence collection**

Evidence-based, from the measurements in this document only:

1. **Every piece of infrastructure this sprint needed already existed and was real** (`ProcessLock`, the replay-script pattern, every Series 92-101 engine) -- confirmed by direct execution, not assumption.
2. **The only real gap (a scheduler) is orthogonal to evidence quality** -- running the pipeline manually once a day accumulates the exact same real evidence as running it via a timer; the timer only removes manual labor, it does not change what evidence exists.
3. **Sprint 102's Gate A (100 trades) remains the single binding constraint**, unchanged by this sprint -- this sprint proved the OPERATIONAL machinery works (restart-safe, duplicate-safe, deterministic), which is a necessary but not sufficient condition; the trades themselves still need to accumulate day by day.

**This is Continue daily evidence collection, not Expand Historical Corpus or Enable Paper Trading** -- the operational reliability this sprint proved is exactly what makes daily evidence collection trustworthy enough to run unattended; expanding the corpus (Sprint 102's own separate recommendation) and eventually paper trading both remain downstream of accumulating enough REAL trades under this now-proven-reliable daily operation, not a substitute for it.
