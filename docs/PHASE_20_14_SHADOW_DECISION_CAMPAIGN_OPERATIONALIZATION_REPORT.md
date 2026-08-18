# Phase 20.14 — Shadow Decision Campaign Operationalization

**Bujji OS v1.0 Roadmap, Cycle-1 lineage.** Converts Phase 20.13's own historical-validation-proven runner into a persistent, unattended, calendar-aware, crash-safe operational shadow campaign — still zero execution capability.

---

## 1. Audit findings

**Naming approved, no re-audit needed** — `bujji-shadow-decision-campaign.service`/`.timer`, confirmed against `docs/SYSTEM_OWNERSHIP.md`'s existing reservations before this phase began (Phase 20.13's own closing audit).

**Reusable operational infrastructure confirmed and reused, none duplicated:**

| Component | Source | Disposition |
|---|---|---|
| Daily session lifecycle, heartbeat, graceful shutdown | `bujji.shadow_runtime.daily_session.DailySessionRuntime` (Phase 19.11) | **A) Reused directly** — already wired by Phase 20.13, unchanged this phase. |
| Single-instance locking | `bujji.core.process_lock.ProcessLock` | **A) Reused directly** — new dedicated lock path `data/shadow_decision_campaign.lock`, never `data/daily_intelligence.lock`. |
| NSE trading-day/holiday awareness | `bujji.market_calendar.MarketCalendar` | **A) Reused directly**, unmodified — `is_trading_day()` called before any broker connection. |
| Systemd oneshot + timer pattern, security hardening | `deploy/bujji-daily-intelligence.service`/`.timer` (Phase 19.12/19.14.1) | **B) Extended as a pattern** — new unit files mirror the exact same structure (`Type=oneshot`, `ProtectSystem=strict`, `NoNewPrivileges`, restricted `ReadWritePaths`, `TimeoutStopSec=30`/`SIGTERM`), never a shared unit — this phase's own decision-producing campaign is explicitly out of scope for `bujji-daily-intelligence.service`'s own documented boundary ("never a trading decision"), and the reserved `bujji-options-os.service` is reserved for real execution, which this also isn't. |
| Operator visibility (what happened per expected trading day) | `bujji.shadow_runtime.campaign_continuity` (Phase 19.17) | **C) Pattern reused, not imported** — that module hydrates Phase 19.x-specific `daily_intelligence_artifacts`/`cycle_artifacts` `EventStore`s Cycle 1 never writes to; importing it would create exactly the sideways lineage coupling the Interface Map exists to prevent. Its five-way classification vocabulary and calendar-first logic pattern is mirrored in a new module (`bujji.live_shadow_runner.continuity`) reading THIS lineage's own JSONL artifacts instead. |
| Operator visibility (what's running right now) | `bujji.shadow_runtime.status.get_operational_status()` | **A) Reused directly, zero extension needed** — already fully generic (`heartbeat_path`/`lock_path` parameters); works unmodified against this campaign's own heartbeat/lock paths. |

**Nothing was rebuilt.** The only genuinely new code this phase adds is: the lock/calendar wiring inside the existing entrypoint script, one new continuity module for Cycle-1's own artifacts, and the two new unit files.

## 2. Architecture / Interface Map alignment

Per `docs/BUJJI_OS_V1_INTERFACE_MAP.md`'s own enforcement mechanism, this phase touches exactly the **Shadow Result** row (operationalizing its own delivery cadence) and does not open any new sideways connection between lineages — the campaign_continuity pattern-reuse (not import) decision above is the concrete proof of that discipline.

```
NSE Market Session → Market Data Capture (FyersBroker, read-only, guarded)
    → Observation Schema (compose_market_state, Phase 20.1)
    → MIC / Intelligence Layers (20.5–20.10) → Decision Orchestration (20.10)
    → Shadow Decision Runtime (20.11) → Shadow Decision Campaign Storage (20.12/20.13/20.14)
    → Health + Continuity Monitoring (20.13/20.14, this phase's own continuity.py)
    → Operator Visibility (status.py, reused unmodified)
```

No execution layer is connected anywhere in this chain.

## 3. Files created

- `deploy/bujji-shadow-decision-campaign.service`
- `deploy/bujji-shadow-decision-campaign.timer`
- `bujji/live_shadow_runner/continuity.py`
- `tests/test_live_shadow_runner_continuity.py` (7 tests)
- `tests/test_phase20_14_entrypoint_wiring.py` (5 tests)

## 4. Files modified

- `scripts/run_phase20_13_live_entrypoint.py` — added `--date-today` (mutually exclusive with `--session-date`), `MarketCalendar` gating before any broker connection (clean exit 0 on a non-trading day), `ProcessLock` acquisition/release around the whole session (new dedicated lock path). Body logic (`candle_fetch_fn`/`vix_fetch_fn`/`process_cycle` loop) is byte-identical, only moved into a `_run_session()` helper for clean separation from the new gating logic.
- `bujji/live_shadow_runner/__init__.py` — exports the six new `continuity.py` symbols.
- `docs/SYSTEM_OWNERSHIP.md` — added the fourth ownership entry (Cycle-1 Shadow Decision Intelligence) with its own table, disclosed the pre-existing gap that the Phase 19.x Intelligence Foundation itself isn't tracked as its own generation in this document (out of this phase's own scope to fix).

**Not modified**: every file in the "reused directly" row of §1 — confirmed by mtime, all predate this phase's own deploy timestamps.

## 5. Real historical validation results

Rerun of Phase 20.13's own `scripts/run_phase20_13_dry_validation.py` (unaffected by this phase's entrypoint changes — it doesn't invoke the live entrypoint script) confirms byte-identical results to Phase 20.13's own report: **9 real trading days (2026-08-03 to 2026-08-13), 72 cycles/144 observations per day, `HEALTHY` every session, restart-recovery verified every session** — proving the underlying campaign logic this phase now operationalizes is unchanged and still correct.

## 6. Testing & regression

12 new tests (7 continuity classification: non-trading day, missing, complete, incomplete, failed, report counts, inverted-range rejection; 5 entrypoint wiring: distinct lock path, mutually exclusive date args, calendar/lock ordering before broker connect, no forbidden broker calls, `ProcessLock`/`MarketCalendar` genuinely imported not reimplemented), all passing on first fix (two initial test-assertion bugs — not source bugs — corrected: a `render()` header naturally contains the date-range boundary date, and the "no place_order" check needed to match actual calls rather than the disclosure prose that legitimately names the method). Phase 20.13's own 8 tests still pass unmodified. Full regression: **6,331 passed, 0 failed** (6,319 baseline + 12 new). Protection-file mtimes confirm zero modification to any reused component. `systemd-analyze verify` confirms both new unit files are syntactically valid.

## 7. Installation (operator-approved)

**Two real bugs caught and fixed before install**, neither of which any prior automated check (regression, `systemd-analyze verify`) could have caught, since both are deployment-environment issues rather than code-logic issues:

1. **`PrivateTmp=true`** (copied from the daily-intelligence template) would have given the service an isolated `/tmp` namespace, making `/tmp/local_fyers.env` invisible to it — every single fire would have failed with "credentials not found," even with a valid token on disk.
2. **Credential file ownership mismatch**: `/tmp/local_fyers.env` is `root:root 600` (this phase's own manual smoke-testing was run as root); the service runs as `User=bujji`, which cannot read a `root:root 600` file.

**Fix applied**: `ExecStart` now points `--fyers-env-file` explicitly at `/opt/bujji/.env` — the same, already `bujji:bujji`-owned credential file `bujji-daily-intelligence.service` already uses — rather than inventing a second credential convention with a different, easy-to-break ownership requirement. `PrivateTmp=true` was restored (safe again, since the service no longer touches `/tmp` at all). Verified directly before install: `sudo -u bujji test -r /opt/bujji/.env` (readable), `test -w /opt/bujji/app/data` and `.../logs` (writable) — all confirmed, not assumed.

**Installed and enabled**: `systemctl daemon-reload && systemctl enable --now bujji-shadow-decision-campaign.timer`, per explicit operator confirmation. Confirmed active:

```
● bujji-shadow-decision-campaign.timer — active (waiting)
  Trigger: Mon 2026-08-17 09:10:00 IST (12h left at install time)
```

**Operational note for the operator**: this service's own credential refresh target is now `/opt/bujji/.env`, NOT `/tmp/local_fyers.env` — keep that file current (same refresh flow already required for `bujji-daily-intelligence.timer`) rather than the file used for this phase's own manual smoke-testing.

## 8. Remaining live-session gap status

**0 of the required 20–30 live NSE sessions have run as of this report** — the timer's first fire is tomorrow (2026-08-17, Monday, a real trading day). Every weekday going forward, `MarketCalendar` additionally skips listed NSE holidays, and each real trading day accumulates one more entry toward the roadmap's own 60+-session milestone (`docs/BUJJI_OS_ROADMAP_V2_CONTINUOUS_SHADOW.md` §E). This is now a genuinely running, unattended system — the gap is calendar time, not remaining engineering work.

No trading capability added. `disable_live_execution()` remains on every broker instance this campaign constructs.
