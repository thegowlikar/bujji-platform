# BUJJI Options OS — Project Status

**Last updated**: 2026-07-31, mid-session, during LSQ-1 Day 1 (live).

## How to read this file

This repo contains **three coexisting architectural generations**, not one system:

1. **Legacy ORB-VWAP ATM Seller** (`bujji/core/orchestrator.py`, `bujji/signal/`, `bujji/trade/`, `bujji/execution/`) — the original system, documented in the repo's own `README.md`. Config-driven via `config/config.yaml`. Predates this engagement's own work; not touched or modified during it.
2. **MSI / Intelligence Observatory arc** (`bujji/msi_*`, ~30 packages, `bujji/live_shadow_operator/`, `run_live_shadow.py`, `run_daily_observation.py`) — a market-learning/evidence/decision pipeline (Series 99-107 in this arc's own numbering), frozen per `docs/LEARNING_ARCHITECTURE_CODEX.md`. **This is what `run_live_shadow.py` actually runs today**, including LSQ-1.
3. **Trading Brain v3** (`bujji/trading_brain/`, `bujji/production_runtime/`, `bujji/integration/`, `bujji/runtime_execution/`, `bujji/runtime_safety/`) — the newest, most complete decision→execution pipeline, per `TRADING_BRAIN_CONSTITUTION.md`. Reaches a real `PaperBroker` fill end-to-end (proven in the EQ1 qualification sprint), but **is not wired into `run_live_shadow.py`** — the two systems have never been merged.

Anyone picking this up cold: don't assume "the pipeline" means one thing. Ask which generation a given file belongs to before changing it.

---

## What's Working (verified, with evidence)

- **Trading Brain v3 full decision→dispatch pipeline** — Evidence Interpreter → Market State → Strategy Selector → Risk Brain → Capital Brain → Execution Planner → (trading_brain) Execution Engine → Broker Adapter → Nifty Contract Builder → Position Sizing → Order Construction → Runtime Execution → real `ExecutionEngine.submit_and_confirm()` → `PaperBroker`. Ran successfully end-to-end with **real market data** (2026-07-29 Bhavcopy) for the first time in this codebase's history during the EQ1 qualification sprint — reached `order_submitted=True`, real distinct fills. See `docs/EQ1_EXECUTION_QUALIFICATION.md`.
- **Tick-silence watchdog** (`bujji/broker/fyers_ws.py::TickSilenceWatchdog`) — fault-injection tested (8 scenarios) and **live-proven**: recovered a real recurrence of the original incident in Live Shadow Session #3 in 4.0 seconds, fully automatic, exactly one reconnect. See `docs/TICK_SILENCE_INCIDENT_P1.md`, `docs/SESSION3_FORENSIC_REPORT.md`.
- **Portfolio Valuation engine** (`bujji/trading_brain/portfolio_valuation/`) — pure, tick-driven, replay-safe (no broker/network import, structurally proven). Real end-to-end demonstration across 3 real trading days' Bhavcopy data.
- **Exit Engine v1** (`bujji/trading_brain/exit_engine/`) — 3 real rules (Maximum Loss, Profit Target, Hard Time Exit) + 1 documented placeholder (Strategy Exit — no real strategy-aware exit logic exists anywhere in this codebase). Full real lifecycle proven: real entry → real 3-day MTM evolution → correct exit trigger → real closing execution → flat ledger → replayed identically (byte-for-byte match). See `docs/EXIT_ENGINE_V1_SPRINT.md`.
- **`PaperBroker`** — real ledger (entry timestamp, realized P&L, correct long/short netting including partial-close and flip-in-one-fill), fills at the real observed `reference_price` (a field kept structurally separate from `limit_price` — see Decisions).
- **LSQ-1 (Live Shadow Qualification) tooling** — consistency checker, daily report generator, Chief Engineer's Log generator, all real, all smoke-tested, all producing an immutable provenance header. See `docs/LSQ1_PROTOCOL.md`.
- **Capital margin certification** (legacy system, `bujji/capital/`) — live-certified against a real FYERS account (`docs/AUDIT_LOG.md` Pass 8): real SPAN margin request/response schema, a real hedging-benefit proof on a live NIFTY straddle, real error-code fixtures. `config.yaml`'s `capital_policy: CERTIFIED` reflects this.

## What's Broken / Missing

- **No entry-order-construction path exists in `run_live_shadow.py`.** This is the single most consequential gap right now: LSQ-1 is running live today, but has nothing to actually enter, so the Portfolio Valuation/Exit Engine wiring is real and tested but currently idle every session. Building this is explicitly **out of scope for LSQ-1** (Rule #1 freezes architecture during the qualification period) — it's the top backlog item for after.
- **`production_runtime`'s SHADOW mode has no structural guard against a real broker.** `RuntimeConfig(mode="SHADOW", broker_name="fyers")` constructs successfully; `run_shadow()` has zero code-level check that its broker is actually `PaperBroker` — confirmed by direct execution. This is a **BLOCKER** per the Production Readiness Gate Review, unresolved.
- **A confirmed, silent, currently-undetectable connection-state corruption mechanism** in `FyersTickFeed` (Sprint P3): a stale socket generation's callback can silently flip `is_connected=True` with nothing anywhere able to catch it.
- **No numeric risk limits** (max capital, max daily loss, max simultaneous positions) exist in `risk_brain`/`runtime_safety` — these modules gate on market *conditions*, never on Rupee numbers.
- **No real margin validation is wired into `production_runtime`'s dispatch path** — real margin logic exists (`bujji/capital/`) but is a separate, unconnected module from the Trading Brain v3 pipeline.
- **`reconnect_count` and `cadence_duration_seconds` are both confirmed-dead/mislabeled operational metrics** (live-confirmed in Session #3) — don't trust either without cross-checking the real log.
- **Process lifecycle**: `run_live_shadow.py` leaves a zombie OS process after "shutdown: clean" prints (SDK-owned non-daemon thread, not fixable from our side — Sprint P5) and leaks an `asyncio` event loop in `_pre_market_checklist()` (BUJJI-owned, minor, not yet fixed).
- **The NSE holiday calendar is self-disclosed as unverified** — every session's pre-market checklist requires a manual cross-check against the real current NSE holiday circular.

## Last Task / Where It Was Left Off

**LSQ-1 (Live Shadow Qualification) Day 1 is currently LIVE** as of this writing (2026-07-31, market hours). Pre-market checklist passed (14/14 items). Session launched cleanly, shadow mode confirmed, chain loaded from real Bhavcopy. Being monitored via a persistent event-driven watcher plus a ~25-minute heartbeat. At market close (15:30 IST), the plan is to run `tools/lsq_consistency_check.py`, `tools/lsq_daily_report.py`, and `tools/lsq_chief_engineer_log.py`, and produce a full Day 1 output ending in one of four verdicts (PASS / PASS WITH OBSERVATIONS / CONDITIONAL PASS / FAIL).

**System is frozen during LSQ-1's market hours** — no code changes except a genuine safety-required fix, per the qualification protocol's own Rule #1.

## Next Immediate Task

1. Finish monitoring LSQ-1 Day 1 through close; produce the Day 1 report set.
2. Continue LSQ-1 daily (this is a standing, multi-day program, not a one-off).
3. After LSQ-1 (or in parallel, once a day's qualification work is done and market is closed): the highest-priority backlog item is closing the `production_runtime` mode/broker structural gap (see TODO.md P1).
