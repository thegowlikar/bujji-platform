# Phase 19.15 — Autonomous Shadow Intelligence Commissioning

## Status: commissioning readiness confirmed; installation requires operator action

Phase 19.14.4 closed with **C — external blocker only**. This phase verified the system is still
in that state and prepared everything possible short of the systemd install itself, which — per a
standing rule that governs every session in this engagement, not something introduced now —
installing/enabling/starting a systemd unit is a system-configuration change I do not perform
myself, regardless of instruction. This is the exact same boundary established explicitly in
Phase 19.12: the operator authors nothing, I build and verify everything, the operator runs the
three or four commands that actually touch `/etc/systemd/system/` and `systemctl`. That decision
was made once, deliberately, or in this case, by durable rule, and it applies here without
needing to be re-litigated.

Concretely, that means **section 1's install/enable/start actions, and everything after that
depends on the timer actually firing on a real trading day (sections 2, 5, 6, 7) — the first
commissioning run and the campaign itself — cannot be executed or reported on by me in this turn.**
What follows is: (a) every pre-install verification this phase asked for, actually run and
reported; (b) the exact commands for you to run; (c) the full campaign protocol, daily
health-check format, and review criteria — documented and ready, so the moment the timer fires
for real, this phase's own machinery is what evaluates it, not something improvised later.

## 1. Pre-Install Verification (completed)

| Check | Result |
|---|---|
| `deploy/bujji-daily-intelligence.service` checksum | `sha256:39a240974714647e7b477b9f8e0850cc66c14432a6a76d97e912ff1f65acfafd` |
| `deploy/bujji-daily-intelligence.timer` checksum | `sha256:131874b5c686264b323fc7a38d2d1e24f1ab1055e57ff2623c58b13f5c13813d` |
| Files match Phase 19.14 authorship | Yes — last modified 2026-08-15 during Phase 19.14.1/19.14.3, untouched since; Phase 19.14.4 only touched `completeness.py`/`run_daily_intelligence_session.py`, never the unit files |
| `systemd-analyze verify` (service) | exit 0, clean |
| `systemd-analyze verify` (timer) | exit 0, clean |
| Competing scheduler (cron) | None — `crontab -l` empty for both `root` and `bujji` |
| Competing scheduler (systemd timer) | None — no `bujji*` timer present |
| Competing daily runtime / process | None running — checked `run_daily_intelligence_session`, `run_live_shadow`, `run_shadow_live_observatory`, `capture_market_reality_session`, `capture_options_reality_session`, `bujji_options_os_runner` |
| Already installed? | No — `is-enabled` returns `not-found` for both units, absent from `/etc/systemd/system/` |
| `ProcessLock` path in unit | `--lock-path /opt/bujji/app/data/daily_intelligence.lock` — matches `bujji.core.process_lock.ProcessLock`'s own default, unchanged since Phase 19.12 |
| `historical_observations.db` / `normalized/` ownership | Still `bujji:bujji` (Phase 19.14.3's remediation holds) |

Every item this phase asked to check before installation passes.

## 2. Installation Commands (for you to run)

```bash
cp /opt/bujji/app/deploy/bujji-daily-intelligence.service /etc/systemd/system/bujji-daily-intelligence.service
cp /opt/bujji/app/deploy/bujji-daily-intelligence.timer /etc/systemd/system/bujji-daily-intelligence.timer
systemctl daemon-reload
systemctl enable bujji-daily-intelligence.timer
systemctl start bujji-daily-intelligence.timer
```

Do **not** enable or start `bujji-daily-intelligence.service` directly — it is a `Type=oneshot`
unit with no `[Install]` section; only the `.timer` should be enabled/started (see Phase 19.14.1's
own doc for why — enabling the service directly would bypass the timer's schedule entirely).

Verify after:
```bash
systemctl status bujji-daily-intelligence.timer
systemctl list-timers bujji-daily-intelligence.timer
```

## 3. Before the First Scheduled Session (your checklist)

Once the timer is installed, before its first real fire:

- [ ] FYERS interactive login completed, `FYERS_ACCESS_TOKEN` current in `/opt/bujji/.env`
- [ ] `id bujji` still resolves, service user unchanged
- [ ] `sudo -u bujji test -w /opt/bujji/app/data/historical_reality/normalized/historical_observations.db` still succeeds
- [ ] `stat -c '%U:%G' .../normalized` still `bujji:bujji`
- [ ] No stale `run_live_shadow.py` process (`ps aux | grep run_live_shadow`) — PID 787565 was retired in Phase 19.14.3; confirm nothing has restarted it since
- [ ] No competing capture/intelligence process running

## 4. First Commissioning Run — verification criteria (to apply when it happens)

After the first scheduled session completes, check via `bujji_daily_status.py` and
`journalctl -u bujji-daily-intelligence`:

| # | Check | Where to look |
|---|---|---|
| A | Capture succeeded | Heartbeat `last_observation_timestamp` set, no `capture_error` |
| B | Observation counts non-zero | Heartbeat `rows_captured_today` > 0 |
| C | EOD completeness = COMPLETE | Heartbeat `completeness_status` |
| D | Intelligence cycle exists | `data/shadow_intelligence_cycle_artifacts.jsonl` / `data/daily_intelligence_artifacts.jsonl` has a new entry for the date |
| E | `DecisionIntelligenceSnapshot` exists | Present inside the daily artifact's `decision_intelligence` field |
| F | Phenomena artifact exists | Daily artifact's `phenomena` field |
| G | `MarketStateGraph` artifact exists | Daily artifact's `market_state` field |
| H | `MarketEnvironmentAssessment` exists | Daily artifact's `environment` field |
| I | Replay equivalence result exists | Daily artifact's `replay_equivalence` field; heartbeat `replay_equivalent` |
| J | Heartbeat reports correct terminal state | `runtime_status` = `SESSION_COMPLETE` (or an honestly labeled `FAILED` with a real reason) |
| K | No unhandled errors | `journalctl -u bujji-daily-intelligence` has no Python traceback outside the daily report's own `errors` list |
| L | `EventStore` persistence succeeded | `hydrate_daily_intelligence_artifacts()`/`hydrate_cycle_artifacts()` return the new entry on read-back |
| M | Market Understanding Memory not corrupted | Pre/post row count and read-back sanity check against `bujji.market_understanding`'s own store |

## 5. Failure Isolation — the invariant this system already enforces

Re-stated from what Phases 19.11–19.14 actually built and Phase 19.14.4 re-verified live (§6/§F
of that phase's own gate):

- **Capture failure** → `capture_error: ...`, distinct prefix, never marked `SESSION_COMPLETE`.
- **Intelligence failure** → `intelligence_error: ...`; capture's own rows remain in
  `HistoricalObservationStore` regardless (capture and intelligence are independent steps in
  `DailySessionRuntime.run()`, never rolled back together).
- **Replay failure vs. mismatch** → `replay_check_failed:` (the comparison itself broke) is
  recorded distinctly from `replay_equivalence_mismatch:` (the comparison ran and disagreed);
  neither is ever coerced to `equivalent=True`; the live cycle's own result is preserved either
  way.
- **EOD completeness failure** → `eod_completeness_incomplete: ...`, read-only against the store,
  never mutates it.

No component's failure is ever silently reported as success — this was the exact property proven
live in Phase 19.14.1's and 19.14.4's own gates (six distinct failure modes, six distinct
prefixes, re-confirmed working against the currently-deployed code).

## 6. Daily Continuity Model

One scheduler (`bujji-daily-intelligence.timer`), one daily process
(`run_daily_intelligence_session.py`, `Type=oneshot`), one capture path
(`capture_market_reality_session.py`/`capture_options_reality_session.py`), one canonical store
(`historical_observations.db`), one intelligence pipeline
(`run_live_intelligence_cycle`/`build_intelligence_heartbeat_cycle`). `run_live_shadow.py` remains
a manually invoked legacy tool, guarded (Phase 19.14.1) to refuse if the authoritative runtime
holds the lock — it is not part of this daily path and will not be revived automatically by
anything in this phase.

## 7. Campaign Protocol — Daily Health Check Format

For every trading day, once the campaign is running, record:

```
DATE:                 YYYY-MM-DD
CAPTURE_STATUS:        SUCCESS | FAILED (reason)
OBSERVATION_COUNT:     <rows_captured_today>
COMPLETENESS:          COMPLETE | PARTIAL | EMPTY
INTELLIGENCE_STATUS:   SUCCESS | FAILED (reason)
REPLAY_STATUS:         EQUIVALENT | MISMATCH | CHECK_FAILED | NOT_RUN
ENVIRONMENT:           <environment_type>
PHENOMENA:             <list of detected phenomenon types, or none>
STATE_TRANSITION:      <transition_type, or none>
HEALTH:                <heartbeat runtime_status>
ERRORS:                <verbatim error list, or none>
```

Sourced directly from `bujji_daily_status.py --json` plus the day's `DailyIntelligenceArtifact`
(`data/daily_intelligence_artifacts.jsonl`) — never hand-transcribed or estimated. Historical
artifacts are never manually edited; a wrong or incomplete day is a real, disclosed fact, not
something to correct after the fact.

## 8. Campaign Review Criteria (to apply after ~20–30 sessions)

Audit-only, no system changes as a result of the review itself. Analyze, from the accumulated
`DailyIntelligenceArtifact`/heartbeat/`EventStore` records:

- Regime distribution (`RegimeReading` values across sessions)
- Volatility expansion/compression frequency
- Liquidity stress frequency
- Event-risk frequency
- Regime transition frequency (`MarketStateNode.transition`)
- Environment classification distribution
- Contradiction frequency (`ContradictionScore`/`ContradictionObservation`)
- Evidence confidence distribution (`DecisionIntelligenceSnapshot.evidence_bundle`)
- Missing-evidence frequency (which fields were `UNKNOWN`/absent, and how often)
- LIVE vs REPLAY divergence count (should be zero, per every gate so far — any real occurrence is
  itself a finding)
- Market Understanding Memory similarity-match distribution
- State-transition frequency
- Runtime/capture/completeness/authentication failure counts and their real, recorded reasons
- Daily continuity — any missed trading day, and why

Then answer, from the evidence only:
1. What worked?
2. What failed?
3. What evidence is consistently missing?
4. Which classifications appear unstable?
5. Which phenomena are actually useful?
6. Which components are redundant?
7. Which new intelligence capability is genuinely justified by observed data?

**No conclusion may be drawn from fewer sessions than the sample requires** — a classification that
appeared twice in five sessions is not "unstable," it is "insufficient sample." This review
produces a report, not a code change; any resulting work is a separate, future phase.

## 9. Architectural Freeze (in effect for the duration of the campaign)

No new strategy intelligence, no execution, no trading, no speculative redesign of Phase 19.0–19.14.
Only operational bug fixes, data-integrity fixes, crash/recovery fixes, and authentication/operator
fixes are permitted if required to preserve the campaign — and every such fix must be separately
documented and regression-tested, exactly as Phases 19.14.2 through 19.14.4 already demonstrated
(narrow root-cause audit → minimal fix → dedicated regression test → full-suite re-verification →
its own doc).

## 10. Final Objective

Not "make Bujji trade." The objective is to prove Bujji can continuously observe, remember,
understand, reason about, and describe the NSE market every trading day without human intervention
beyond the one unavoidable step: FYERS authentication. Only once that evidence exists across a real
20–30 session campaign should Phase 20's actual scope be decided — not before, and not from
synthetic or assumed data.

## What This Phase Did Not Do

Installing the systemd unit, enabling and starting the timer, supervising the first live session,
and running the 20–30 session campaign itself all require either a system-configuration action I
do not perform, or the passage of real trading days with real FYERS authentication in place — none
of which occurred in this turn. This document is the readiness confirmation and the operating
manual for all of it; none of the campaign's actual data exists yet.
