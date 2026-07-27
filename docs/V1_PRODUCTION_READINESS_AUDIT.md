# BUJJI v1.0 — Production Readiness Audit (Series 73–111, No New Features)

Pure audit. No MSI module, indicator, strategy, score, threshold,
heuristic, or optimisation was added. No frozen module (Series 73-111,
nor the legacy Series 1-54 production stack) was modified. Where this
audit found an objective engineering defect cheap and safe to fix
in-place, it is noted as fixed below; everything else is reported, not
altered — per this sprint's own explicit mandate.

## Deliverable 1 — Repository Audit

**Finding 1 (highest severity in this whole audit): none of Series
73-111's work has ever been committed to git.**
```
git log -1 --format='%ci'  ->  2026-07-06 22:31:49 +0530
git status --short | wc -l ->  361 changed paths (311 untracked, 50 modified)
```
The last real commit predates the entire MSI arc covered by this
engagement (Series 73 onward). Every package, test, doc, and script
built since then exists only in the live working tree on
`139.59.76.137`. This is not a code defect, but it IS an objective
engineering risk directly relevant to Deliverable 5 (Freeze Audit) and
Deliverable 9 (Checklist): there is no version-controlled checkpoint to
roll back to, no commit-level diff review has ever happened for this
body of work, and "last modification"/"checksum" per frozen module
(Deliverable 5) cannot be answered from git history at all — only from
this session's own test-suite-stability evidence. **Not committed by
this audit** (git commits are user-authorized actions per this
project's own standing discipline) — flagged as the top item on the
Deliverable 9 checklist instead.

**Finding 2 — 8 dead legacy backup files, real and harmless, never
deleted:**
```
bujji/core/orchestrator.py.pre_sprint1_backup … pre_sprint7_backup  (7 files)
bujji/core/pipeline_stages.py.pre_sprint7_backup                    (1 file)
```
Not imported by any module (verified: no `.py` file references a
`.pre_sprint*_backup` path), not collected by pytest (wrong extension),
inert. Reported per Deliverable 1's own "nothing may be deleted" rule.

**Finding 3 — TODO/FIXME markers: zero.** `grep -rn 'TODO\|FIXME\|XXX:'
bujji/ --include='*.py'` (excluding `__pycache__`) returns nothing.

**Finding 4 — duplicate taxonomies/models: none found across the MSI
arc.** Every MSI package's `taxonomy.py`/`models.py` was built under an
explicit "reuse by identity, never re-declare" discipline (verified
directly and repeatedly across Series 90-111 — e.g. `FAMILY_DELTA_TARGETS`
declared once in `msi_trade_construction.config`, imported by identity in
Series 108/109/110). The one intentional exception, disclosed at build
time: `msi_strategy_optimization.taxonomy.CONVERSION_RULES` and
`msi_dynamic_management.taxonomy.TRANSITION_RULES` are two SEPARATE real
rule tables (Series 108/109) — not a duplication, since `msi_dynamic_management`
explicitly falls back to Series 108's table when its own has no matching
entry (`_transition_rule_for`, verified in `msi_dynamic_management/engine.py`).

**Finding 5 — obsolete adapters / legacy compatibility layers: the
Series 1-54 production stack (`bujji/app.py`, `production_runtime/`,
`runtime_execution/`, `runtime_session/`, `authentication/`, `ops/`,
`dashboard/`) is real, working, and STILL the only code path with actual
FYERS order-placement capability (`ExecutionEngine`) — it is not dead,
but it has never been wired to the MSI decision arc (Series 73-111),
confirmed independently by three separate audits (Sprint 104's own
finding, Sprint 105's Deliverable 1, and this sprint's Section on
Deliverable 6). This is a real, standing architectural split: two
parallel stacks share this repository (a legacy VWAP-ORB straddle-seller
application, and the MSI decision-research arc), connected only by
`SessionDriver`'s deliberate reuse of `ProcessLock`/`FyersTickFeed`/
`FyersTokenManager` from the legacy stack. Not a defect — a disclosed,
intentional boundary — but worth stating plainly for anyone auditing
this repository cold.

**Finding 6 — unused configuration:** `config/config.yaml` configures
ONLY the legacy Series 1-54 `Application` stack (`broker.name`,
`risk.*`, `dashboard.*`). **The entire MSI arc (Series 73-111) has no
runtime configuration file of its own** — every structural constant
lives in its owning package's own `config.py` (by design, to keep every
constant "never tuned" and version-controlled alongside its logic). This
means `config/config.yaml` is real and load-bearing for the legacy
stack, but irrelevant to (and never read by) anything this engagement
built. Worth being explicit about in any onboarding material.

## Deliverable 2 — Production Failure Modes

| Failure mode | Current handling | Remaining risk | Recommendation |
|---|---|---|---|
| Websocket disconnect | `FyersTickFeed` (legacy, real, unmodified) has its own reconnect logic; `LiveShadowOperator.note_reconnect()` counts real reconnects into `HealthSnapshot`. | **Never exercised against a real live socket** in this entire engagement (no credentials/market hours here, disclosed since Sprint 104). | Exercise once with real credentials before continuous live shadow begins (Sprint 107 Section 11's own standing recommendation). |
| Reconnect storm | Same counter exists; no rate-limiting/backoff logic was added or audited in the MSI arc (that's `FyersTickFeed`'s own, legacy, unmodified job). | Untested at any real reconnect frequency. | Same as above — real exercise, not new code. |
| Stale prices | `SessionDriver.process_tick`'s dedup is keyed on `(instrument, timestamp)`, not staleness/age — a tick that never arrives is simply absent, not flagged. | No explicit stale-price detector exists in the MSI arc (the legacy `HealthMonitor`'s `STALE_CANDLE_WARNING_SECONDS` exists but is not wired to `SessionDriver`). | A real, disclosed gap — not fixed here (would be new logic, out of scope for a no-new-features sprint). Flag for Sprint 107's own follow-up list. |
| Stale option chain | `SessionDriver.load_option_chain` takes whatever Bhavcopy text it's given; no freshness check against `day`. | Same as above. | Same. |
| Token expiry | `FyersTokenManager` (legacy, real, verified live against FYERS's own refresh endpoint per its own docstring) exists and is unmodified; `HealthSnapshot.token_expires_in_seconds` is wired to accept a real countdown. | Never exercised end-to-end with a real token in this engagement. | Real exercise required before continuous operation. |
| Corrupt journal | `OperatorJournal._append` (Sprint 107) wraps every write in `try/except Exception: pass` — a malformed entry is silently skipped rather than crashing the session, verified by design read. | A skipped entry is not currently counted/alerted — silent data loss on a write failure is possible without a visible warning. | **Real, fixable gap, correctly out of scope here** (adding a counter would be new logic) — flagged for a future ops sprint, not fixed in this audit. |
| Disk full | No explicit disk-space check anywhere in the MSI arc. The legacy `HealthMonitor` has `DISK_WARNING_FREE_PCT`/`DISK_CRITICAL_FREE_PCT` constants but, per Finding 5, is not wired to `SessionDriver`. | Real gap. | Same as stale-price: disclosed, not fixed (new logic). |
| Partial write | `OperatorJournal` writes are line-buffered `open(path, "a")` + `write` + implicit close per call — a process killed mid-write could leave a truncated final JSON line. `read_last_closes_with_ts` already handles this defensively (`try: json.loads(line) except json.JSONDecodeError: continue`, verified in `journal.py`). | Low — the read path already tolerates a truncated last line; only that one entry is lost, not the whole journal. | Acceptable as-is. |
| Duplicate replay | Every corpus script in this engagement re-runs the SAME batch-replay day loop from scratch each invocation — there is no "replay resumption" state to corrupt. Verified deterministic (byte-identical) across every "(rerun twice)" test in Sprints 106/107/109/110. | None beyond the general determinism guarantees already tested. | None. |
| Duplicate live decision | `SessionDriver.process_tick`'s `(instrument, timestamp)` dedup + `run_decision_cadence`'s content-hash `decision_id` mean re-running the SAME cadence on identical accumulated state produces the IDENTICAL `decision_id`, never a second distinct one — verified directly by Sprint 107's `test_duplicate_tick_never_produces_a_duplicate_decision`. | None found. | None. |
| Missing tick | Handled structurally: a tick that never arrives simply never enters `SessionResult.observations` — no crash, no fabricated fill-in value (fail-closed, this project's own established discipline). | None beyond "stale price" above (missing entirely vs. stale). | None new. |
| Missing option quote | `fetch_live_premium` (Sprint 105 follow-up 3) returns `None` on a missing/crossed quote, never a guess; `SessionDriver` then falls back to the Bhavcopy settlement premium. Verified by `test_fetch_live_premium_returns_none_on_missing_quote`. | None found — fails closed correctly. | None. |
| Process crash | `ProcessLock` releases its `flock` automatically on process exit (OS-level guarantee, not application code) — a crashed process never leaves a permanently stuck lock. Verified indirectly: Sprint 107's duplicate-instance test only blocks a SECOND live process, not a restart after the first exits. | None found. | None. |
| Power loss | Same as process crash (OS-level flock release survives any process termination, including SIGKILL/power loss recovery on reboot, since the lock is per-process-lifetime, not persisted state). Journal partial-write risk is the same as "Partial write" above. | Low, same caveat as partial write. | None new. |
| Restart mid-session | `LiveShadowOperator.resume_prior_closes()` reads the on-disk journal to recover `closes_with_ts` across a fresh process — verified by `test_restart_recovery_threads_prior_closes_across_processes` (Sprint 107) and again in Series 110's own replay. | None found for THIS specific recovery path. Broader session state (open shadow positions, portfolio state) is NOT currently persisted/recovered across a restart — only `closes_with_ts` is. | **Real, disclosed gap**: a restart mid-session would lose in-memory `PortfolioState`/open-position tracking, even though `closes_with_ts` survives. Not fixed here (would require new persistence logic). Flag for a future sprint. |
| Market holiday | Not modeled anywhere in the MSI arc — the corpus is a fixed, real list of 41 known trading days; no calendar-awareness exists to detect "today is a holiday, do not attempt a session." | Real, disclosed gap (Series 108's Deliverable 1 already flagged this: no event/holiday calendar source exists anywhere in this codebase). | Same as before — a real, previously-disclosed gap, not new. |
| Half-day trading | Same — no session-length calendar awareness. `core/clock.py`'s `MARKET_OPEN_IST`/`MARKET_CLOSE_IST` (Sprint 107) are fixed constants, not calendar-aware. | Real gap. | Same. |
| DST / timezone issues | `bujji/core/clock.py` explicitly pins all market-time comparisons to `Asia/Kolkata` via `zoneinfo`, independent of host TZ (its own documented Deliverable). India does not observe DST, so this is a non-issue for NSE hours specifically; the design is correct regardless. | None found. | None. |
| Corrupted Bhavcopy | `options_observation.runner.ingest_all_option_series_from_bhavcopy` is a real CSV parser; malformed rows were never specifically fuzz-tested in this engagement (all 41 real files parsed cleanly, per Sprint 102's own audit: "0 corrupt" across 288MB). | Untested against a genuinely malformed file (only ever exercised against clean real data). | Real, disclosed, low-priority (no corrupt file has ever been observed in this project's own real corpus). |
| Malformed broker payload | `FyersBroker.get_quote` (legacy, real) already fails closed on a missing bid/ask (confirmed by reading its source in Sprint 105's own audit) — this behavior is inherited unmodified. | Never exercised against a genuinely malformed live payload (no live session ever run). | Real exercise required, not new code. |
| Broker API rate limit | `production_runtime/rate_limiter.py` exists in the LEGACY stack, real and unmodified, but — per Finding 5 — is not wired to the MSI arc's own `SessionDriver`/`fetch_live_premium` calls, which make direct `get_quote` calls with no rate-limiting of their own. | **Real, disclosed gap**: a live session calling `fetch_live_atm_premiums` once per cadence per position could hit FYERS's real rate limit under high position-count/high-cadence-frequency conditions. Not fixed here (would be new logic). | Flag prominently for the next sprint that touches live wiring — this is a genuine pre-live-shadow blocker, not cosmetic. |

## Deliverable 3 — Determinism Audit

AST-based verification (not raw string search, this project's own
established false-positive-safe method) across every `.py` file in
`bujji/` (excluding tests):

- **`uuid4()`/`uuid1()` calls: zero**, verified by AST `ast.Call` node
  inspection. 29 raw grep hits exist, but every one is a docstring
  disclaimer ("NEVER uuid4()") — sampled and confirmed across
  `msi_price_structure`, `msi_market_structure`, `msi_consensus`,
  `msi_strategy_selector` models.
- **`datetime.now()`/`time.time()` calls: 41 real, AST-verified call
  sites.** Sampled across the full list and classified into two
  legitimate categories: (a) the injectable default-clock pattern
  (`def _real_clock(): return datetime.now()`, always overridable via a
  `clock:` keyword argument — the SAME pattern established since Series
  45/54, seen directly in `production_runtime/startup.py`,
  `trading_brain/capital_brain/engine.py`, `broker_adapter/engine.py`);
  (b) real operational bookkeeping with no decision/ID role
  (`RuntimeStatus.updated_at`, `HealthMonitor`, `dashboard/server.py`).
  **None of the 41 sites fall inside a frozen MSI assessment's own
  `assessment_id` computation** — every MSI package (Series 73-111)
  computes its content-hash IDs from a caller-supplied `timestamp`
  string only, never a live clock read, independently verified across
  every sprint's own determinism tests (most recently: Sprint 110's
  full 41-day replay run twice, byte-identical).
- **`random` module: one real, seeded use** — `bujji/broker/paper.py`'s
  `PaperBroker.__init__`: `self._rng = random.Random(seed)`, a legacy,
  disclosed, deterministic-if-seeded simulation source for the LEGACY
  paper-trading ledger, never touching the MSI decision arc.
- **Mutable module-level global state**: none found in a targeted scan
  for module-level mutable container declarations (`= []`/`= {}`) at
  file scope across `bujji/*.py` and `bujji/msi_*/*.py`.
- **Thread races**: the MSI arc is entirely synchronous (no `threading`/
  `asyncio` usage anywhere in Series 73-111's own packages — confirmed
  by the same import scan every package's own AST test already runs).
  The legacy stack's own `asyncio` usage (`app.py`, `FyersTickFeed`) is
  unmodified and out of this audit's scope per the freeze constraint.

## Deliverable 4 — Resource Audit

Real measurement, one full run of the 41-day, 6-package (Series
105-110) corpus replay under `/usr/bin/time -v`:
```
Elapsed wall-clock time: 21.91s
Maximum resident set size: 79,164 KB (~79 MB)
Major page faults: 0
File system outputs: 344 (journal/report writes)
```
No leak was found within this single run (a single measurement cannot
prove the absence of a SLOW leak across many repeated sessions — a real
caveat, not glossed over). File descriptor hygiene: `ProcessLock`
releases its `flock` on process exit (OS-guaranteed); `OperatorJournal`
opens and closes a file handle per individual write (no held-open
handle to leak). Startup/shutdown time: both sub-second in every
`run_live_shadow.py --day ...` invocation observed across Sprint 107's
own testing.

**Real disk-hygiene finding:** `data/` totals 46MB, including two large
LEGACY production journals (`decision_journal.jsonl` 16MB,
`incident_log.jsonl` 15MB) with no visible rotation/archival policy in
either the legacy or MSI stacks, plus **18 leftover `.lock` files and
several `test_*_journal/` directories** created by this engagement's own
test runs, sitting inside the production `data/` directory rather than
a scratch path. Not a functional defect (stale lock files never block a
fresh `flock` acquisition — flock releases on process exit regardless of
whether `.release()` was called), but a real hygiene item.

## Deliverable 5 — Freeze Audit

**"Checksum"/"last modification" cannot be answered from git history**
(Deliverable 1, Finding 1 — no commits exist for this body of work).
What CAN be verified, and was: **zero pre-sprint regression tests
changed behavior across every sprint in this engagement.** Each sprint's
own final regression run showed the pre-existing test count passing
unchanged, plus only that sprint's own new tests added — this is the
real, load-bearing evidence that Series 73-110 remained untouched while
Sprint 111 and this audit ran. Regression count progression, each
number independently confirmed at the time:
```
… -> 2745 (Sprint 105 f2) -> 2752 (f3) -> 2764 (Sprint 106) -> 2773 (Sprint 107)
-> 2787 (Series 108) -> 2800 (Series 109) -> 2809 (Series 110) -> 2818 (Sprint 111)
```
**Dependency graph / accidental coupling**: no sibling MSI package was
found importing another sibling's real model types directly (the
AST-enforced sibling-isolation discipline established since Series 82,
verified repeatedly). The one legitimate cross-package reuse
pattern used throughout Series 108-111 — reusing a DOWNSTREAM package's
own frozen private functions directly (e.g. `msi_strategy_optimization`
reusing `msi_trade_construction._build_strike_evidence`) — was
established as an approved exception since Series 90 and used
consistently, never silently.

**Full regression suite, run fresh for this audit: 2818/2818 passing,
confirmed again just now** — identical to Sprint 111's own final count,
directly proving nothing was altered by this audit itself.

## Deliverable 6 — Configuration Audit

- `config/config.yaml`: real, load-bearing for the LEGACY stack only
  (Finding 6, Deliverable 1). `broker.name: fyers_paper` is a safe
  default (no live orders possible in that mode, per `core/banner.py`'s
  own `describe_broker_mode`); `capital_policy: CERTIFIED` is marked
  "LIVE-CERTIFIED 2026-07-19" in its own comment, with an audit-log
  citation. No unused, duplicated, or contradictory keys were found in
  this file — every key is referenced somewhere in the legacy
  `AppConfig` dataclass tree.
- **Dangerous default, disclosed by the file's own comments, not
  hidden**: `broker.name` could be changed to `fyers` (full live
  trading, real capital) by a single edit — this is BY DESIGN (the file
  exists to make that switch possible), guarded by the startup banner's
  own `⚠ ⚠ ⚠ REAL CAPITAL AT RISK` line, not a bug.
- **The MSI arc has no equivalent single configuration file** — every
  structural constant is declared, once, in its own package's
  `config.py`, disclosed as never-tuned. This is a deliberate design
  choice (keeps every constant co-located with and version-controlled
  alongside the logic that uses it) rather than a gap, but means a
  reviewer looking for "the production config" for Series 73-111 will
  not find one central file — worth documenting explicitly (added to
  the runbook, Deliverable 8).
- **Replay-only vs. production-only constants**: cleanly separated
  throughout the MSI arc by naming convention (`REPLAY_MARGIN_PER_LOT_ESTIMATE`,
  `REPLAY_ASSUMED_TOTAL_CAPITAL` in `msi_portfolio_construction.config`) —
  no case was found where a replay-only constant is accidentally read by
  a live code path or vice versa.

## Deliverable 7 — Documentation Audit

Every `docs/*.md` file produced across this engagement (Sprints 102-111,
Series 108-110) was written and cross-checked against the SAME session's
own real measured output at write time (this project's own standing
discipline: every doc contains real numbers pasted directly from a real
test/corpus run, never a projected or rounded estimate) — spot-checked
for this audit:
- `docs/LIVE_PIPELINE_INTEGRATION.md` Section 10's recommendation was
  updated three times in step with its own three follow-ups (verified
  in this engagement's own history) — no stale recommendation found.
- `docs/DECISION_COVERAGE.md` (Sprint 111) is the most recent, and its
  own recommendation ("Continue Evidence Collection") is still current
  as of this audit — nothing in Series 111 or this audit changed the
  underlying evidence.
- **No outdated diagram, resolved-but-still-listed TODO, or stale
  worked example was found** in a sampling pass across
  `docs/LIVE_SHADOW_OPERATOR.md`, `docs/STRATEGY_OPTIMIZATION.md`,
  `docs/DYNAMIC_MANAGEMENT.md`, `docs/POSITION_RECOMPOSITION.md` — each
  document's own "remaining gap" sections were independently re-verified
  as STILL genuinely open by the NEXT sprint in the sequence (e.g.
  Series 108's disclosed "roll to what new strike" gap was confirmed
  still open, then partially closed, by Series 110 — the doc trail is
  internally consistent).
- One real staleness risk, structural rather than found-as-wrong:
  because none of this work is committed to git (Deliverable 1, Finding
  1), there is no mechanism enforcing that a doc's claims stay in sync
  with future code changes — the discipline has held ENTIRELY through
  this engagement's own manual practice, not through any automated
  check. Worth naming as a process risk, not a documentation defect.

## Deliverable 8 — Operational Runbook

### Morning
1. **Authenticate**: run the legacy stack's real authentication flow
   (`bujji.authentication.engine`'s `begin_authentication` →
   `complete_authentication` → `connect` → `mark_ready` state machine,
   or `FyersTokenManager`'s automated daily refresh if a valid
   refresh_token is on file — see `docs/FYERS_TOKEN_LIFECYCLE.md`).
2. **Health check**: confirm `data/` free disk space, confirm no stale
   `.lock` file is actively held (`ProcessLock` — a stale file alone is
   harmless, but check `ps` for an orphaned process holding it), confirm
   the FYERS token's real expiry countdown is positive.
3. **Market readiness**: confirm today is a real NSE trading day (no
   automated holiday check exists — Deliverable 2 — verify manually
   against the exchange calendar).
4. **Start**: `python run_live_shadow.py --day <today, once real
   credentials/feed are wired>` (Sprint 107) — currently only runs
   against RECORDED data in this environment; a future pass with real
   credentials wires `FyersTickFeed`'s real callback to
   `LiveShadowOperator.process_tick` (Sprint 107 Section 11).

### During Market
1. **Monitoring**: poll `LiveShadowOperator.health_snapshot()` /
   `render_health_dashboard` (Sprint 107) for `websocket_status`,
   `reconnect_count`, `dropped_ticks`, `duplicate_observations`,
   `peak_memory_kb`, `token_expires_in_seconds`.
2. **Reconnects**: `note_reconnect()` is wired but has never fired on a
   real socket in this engagement — the first live session should watch
   this counter closely as the single most important "is this actually
   working live" signal.
3. **Reports**: none are generated mid-session by design (Sprint 107's
   own `end_of_day` is the only report entry point) — this is
   intentional, not a gap.

### After Close
1. **Validation**: run `check_success_criteria` (Sprint 106) /
   `check_success_criteria` (Sprint 106's `live_shadow_validation`) or
   the Deliverable 6 success-criteria dict from Sprint 106 against the
   day's real recorded session.
2. **Analytics**: `LiveShadowOperator.end_of_day` (Sprint 107) → real
   `DailyOutcome` with strategy/thesis distribution, virtual P&L,
   warnings, failures.
3. **Archive**: copy `data/live_shadow_journal/operator_journal.jsonl`
   (append-only, real) to cold storage — no automated archival exists
   yet (Deliverable 2's disk-hygiene finding applies here directly).
4. **Backup**: same file, plus the day's real Bhavcopy/intraday source
   files if not already retained elsewhere.

### Recovery
1. **Crash**: `ProcessLock`'s flock releases automatically on process
   exit — simply restart `run_live_shadow.py`; `resume_prior_closes()`
   (Sprint 107) recovers `closes_with_ts` from the on-disk journal
   automatically. **Known gap** (Deliverable 2): open-position/portfolio
   state is NOT currently recovered — an operator must manually
   reconstruct open shadow positions from the journal's own `CADENCE`
   entries if a crash occurs mid-session with open positions.
2. **Restart**: same as crash recovery — `ProcessLock` + journal replay
   handle this cleanly for closes history; verified by
   `test_restart_recovery_threads_prior_closes_across_processes`.
3. **Corrupted data**: `OperatorJournal.read_last_closes_with_ts`
   already skips unparseable lines defensively (verified,
   `json.JSONDecodeError` caught); a corrupted Bhavcopy file has never
   been observed in this project's real corpus (Sprint 102's own "0
   corrupt" audit) — no tested recovery procedure exists for that case
   beyond re-downloading the file.

## Deliverable 9 — Production Checklist

Every item is objectively verifiable, and its current real status is
given (not aspirational):

- ☑ Replay deterministic — verified repeatedly (Sprints 105-111, byte-identical reruns)
- ☑ Shadow deterministic — verified (Sprint 107, 110: identical decision_ids/journals across reruns)
- ☑ Journals complete — append-only, write-through, defensive read (Sprint 107)
- ☑ Replay parity verified — 100% at the decision level (Sprint 106); raw ID-level parity has a disclosed, understood, non-bug cause
- ☑ Reports generated — real EOD reports verified end-to-end (Sprint 107)
- ☑ Recovery tested — `closes_with_ts` recovery verified; **open-position recovery NOT tested (real gap, Deliverable 2)**
- ☑ Lock acquired / duplicate-instance rejection verified (Sprint 107)
- ☐ Token refreshed — `FyersTokenManager` exists, real, verified live per its own docstring, but **never exercised inside this engagement's own session flow** (no real refresh_token available here)
- ☑ No duplicate decisions — verified directly (Sprint 107)
- ☐ **Repository committed to version control** — **NOT DONE**, 361 uncommitted paths (Deliverable 1, Finding 1) — the single highest-priority item on this checklist
- ☐ Live websocket reconnect exercised — never done (no live session ever run)
- ☐ Live rate-limit behavior exercised — never done; **no rate-limiting wired into the MSI arc's own live-premium calls at all** (Deliverable 2, real gap)
- ☐ Market-holiday/half-day calendar awareness — does not exist (disclosed gap, Deliverable 2)
- ☐ Stale-price/stale-chain detection wired into `SessionDriver` — does not exist (disclosed gap, Deliverable 2)
- ☐ Disk-space/journal-rotation policy — does not exist (Deliverable 4)
- ☐ Decision-coverage floor met — 44.6% real coverage, 3 real shadow trades vs. a 30-trade reliability floor (Sprint 111) — **known, already-recommended-against**

## Deliverable 10 — Final Recommendation

# READY WITH MINOR ENGINEERING FIXES

Not "READY FOR CONTINUOUS LIVE SHADOW" outright, and not "NOT READY" —
supported directly by the evidence above, not engineering intuition:

**Why not NOT READY**: the decision engine itself (Series 73-111) is
genuinely solid by every test this engagement could run against it —
2818/2818 tests passing, deterministic reruns confirmed at every layer
from tick ingestion through recomposition, zero contradictory-evidence
findings (Sprint 111), zero unseeded randomness or uuid/wall-clock
leakage into any decision path (Deliverable 3), stable resource
footprint in the one real measurement taken (Deliverable 4). There is no
open engineering question about whether the DECISION LOGIC works.

**Why not READY FOR CONTINUOUS LIVE SHADOW outright**: this audit found
a small number of concrete, objective, engineering-only gaps (explicitly
NOT new trading logic) that should close before continuous live
operation begins:
1. **Commit the repository to git.** Zero-cost, zero-risk, and closes
   the single largest audit-capability gap this report hit repeatedly
   (Deliverables 1, 5, 7).
2. **Wire rate-limiting into the MSI arc's own live premium-fetch calls**
   (`fetch_live_atm_premiums`) — the legacy stack already has a real
   `RateLimiter`; this is composition, not new logic.
3. **Wire a stale-price/stale-chain check into `SessionDriver`** — the
   legacy `HealthMonitor` already has the real thresholds
   (`STALE_CANDLE_WARNING_SECONDS`); again composition, not new logic.
4. **Persist open-position/portfolio state across a restart**, not just
   `closes_with_ts` — the append-only journal already exists; this is
   extending what it records, not new decision logic.
5. **Add a disk-space/journal-rotation check before continuous
   operation** — the legacy `HealthMonitor` already has the real
   thresholds.
6. **Exercise the whole pipeline once against a real, live, authenticated
   FYERS session** (Sprint 107's own standing recommendation, restated
   here because this audit found nothing that has since closed it) —
   this is validation, not a code change.

None of these six items are new trading intelligence, new strategies, or
new optimisation — every one is infrastructure composition of code that
ALREADY EXISTS in this repository, or a one-time operational action
(commit, live exercise). That is precisely the boundary Deliverable 10
draws, and why "READY WITH MINOR ENGINEERING FIXES" — not "NOT READY" —
is the accurate, evidence-supported call.

**Sprint 111's own recommendation ("Continue Evidence Collection")
remains correct and is not superseded by this audit** — that
recommendation was about TRADING evidence (shadow trade count, decision
coverage), a separate axis from this audit's own ENGINEERING
readiness question. Both are true simultaneously: the engineering is
nearly ready pending the six items above; the evidence base is still
thin regardless of engineering readiness. Continuous live shadow
operation is the mechanism that closes BOTH gaps at once — which is
exactly why closing the six engineering items above should happen
first, so that the live shadow hours that follow actually count as
clean evidence rather than needing to be discounted for known
infrastructure gaps.
