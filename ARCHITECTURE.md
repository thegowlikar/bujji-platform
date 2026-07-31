# BUJJI Options OS — Architecture

**Read this first**: this repo holds **three coexisting architectural generations**. They share `bujji/core/` and some infrastructure but are otherwise independent. Know which one a file belongs to before touching it.

---

## System Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│ GENERATION 1 — Legacy ORB-VWAP ATM Seller (README.md-documented)     │
│                                                                       │
│  config/config.yaml ──► bujji/core/orchestrator.py (FSM: WAITING ──► │
│    READY ──► CONFIRMED ──► IN_POSITION ──► EXITING ──► DONE_FOR_DAY) │
│         │              │                    │                        │
│         ▼              ▼                    ▼                        │
│  bujji/signal/    bujji/trade/        bujji/execution/               │
│  (Signal Engine)  (Trade Manager)     (Execution Engine)             │
│         │              │                    │                        │
│         └──────────────┴────────► bujji/broker/ (paper|fyers)        │
│                                                                       │
│  Coupled only via bujji/core/models.py immutable dataclasses.        │
│  bujji/tick/ (continuous IN_POSITION monitoring), bujji/market/      │
│  (shared VWAP/ORB state), bujji/dashboard/ (read-only HTTP status),  │
│  bujji/ops/ (alerting), bujji/capital/ (margin-aware sizing,         │
│  LIVE-CERTIFIED against real FYERS SPAN margin API).                 │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│ GENERATION 2 — MSI / Intelligence Observatory arc (Series 73-110)    │
│                                                                       │
│  run_live_shadow.py ──► bujji/live_shadow_operator/ (SessionDriver)  │
│         │                                                             │
│         ▼                                                             │
│  bujji/broker/fyers_ws.py (FyersTickFeed + TickSilenceWatchdog)      │
│         │                                                             │
│         ▼                                                             │
│  bujji/live_observation/ ──► bujji/live_market_events/ ──►           │
│  bujji/market_episode/  (raw fact-only pipeline, no interpretation)  │
│         │                                                             │
│         ▼                                                             │
│  ~30 bujji/msi_* packages (see table below) — perception → reasoning │
│  → decision synthesis → strategy selection → trade/position          │
│  construction → execution planning → learning/evidence/governance    │
│         │                                                             │
│         ▼                                                             │
│  bujji/trading_brain/portfolio_valuation/ + exit_engine/  (NEW,      │
│  built in this engagement, wired into run_live_shadow.py's tick loop)│
│         │                                                             │
│         ▼                                                             │
│  bujji/broker/paper.py (PaperBroker) — but NO entry-order-           │
│  construction path exists here yet, so positions never spontaneously │
│  appear in a real run_live_shadow.py session today.                  │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│ GENERATION 3 — Trading Brain v3 (TRADING_BRAIN_CONSTITUTION.md)      │
│                                                                       │
│  bujji/production_runtime/runtime.py (run_shadow / run_read_only /   │
│  verify_production_ready_construction)                               │
│         │                                                             │
│         ▼                                                             │
│  Evidence Interpreter ─► Market State ─► Strategy Selector ─►        │
│  Risk Brain ─► Capital Brain ─► Execution Planner ─►                 │
│  (trading_brain) Execution Engine ─► Broker Adapter                  │
│         │                                                             │
│         ▼                                                             │
│  Nifty Contract Builder ─► Position Sizing ─► Order Construction     │
│         │                                                             │
│         ▼                                                             │
│  bujji/runtime_execution/ (dispatch) ─► bujji/integration/            │
│  execution_adapter.py (ProductionExecutionAdapter) ─►                │
│  bujji/execution/engine.py (REAL ExecutionEngine, idempotent,        │
│  partial-fill-aware, retry/timeout/cancel logic) ─► Broker            │
│  (PaperBroker today; FyersBroker exists, real, but never proven      │
│  against a real network call)                                        │
│                                                                       │
│  PROVEN end-to-end with real market data (EQ1 sprint) -- but NOT     │
│  wired into run_live_shadow.py. Two separate live entry points.      │
│  KNOWN GAP: RuntimeConfig has no structural guard preventing         │
│  mode=SHADOW + broker_name=fyers from constructing a real,           │
│  unguarded FyersBroker -- confirmed live, unresolved (BLOCKER).      │
└─────────────────────────────────────────────────────────────────────┘

           Shared infrastructure (used across generations):
  bujji/core/ (models, enums, config, clock, event_bus, state_machine)
  bujji/journal/ (per-module append-only JSONL journals)
  bujji/broker/ (base.py ABC, paper.py, fyers.py, hybrid.py, guard.py,
                 fyers_ws.py, fyers_token_manager.py, instrument_master.py)
  bujji/replay/, bujji/qualification/ (historical replay/qualification)
```

---

## Every Key File / Package — One Line Each

### Generation 1 — Legacy ORB-VWAP (`bujji/core/`, `signal/`, `trade/`, `execution/`, `tick/`, `market/`, `dashboard/`, `ops/`, `capital/`)

| Path | Purpose |
|---|---|
| `bujji/core/models.py` | Immutable domain dataclasses (`Signal`, `TradeDecision`, `OrderRequest`, `OrderResult`) — the contract between modules. |
| `bujji/core/enums.py` | Pure value-type enums (`State`, `Side`, `OptionType`, `OrderStatus`), dependency-free. |
| `bujji/core/config.py` | Central `AppConfig`, loaded from `config/config.yaml` + env-var secret overrides. |
| `bujji/core/clock.py` | Explicit Asia/Kolkata timezone handling — single source of "now." |
| `bujji/core/orchestrator.py` | Wires Signal/Trade/Execution via the FSM; owns no trading logic. (7 stale `.pre_sprintN_backup` snapshot files sit alongside it — cleanup candidate.) |
| `bujji/core/state_machine.py` | FSM enforcing legal session-state transitions, logs every one. |
| `bujji/core/event_bus.py` | Lightweight async pub/sub — orchestrator publishes, journaling/dashboard/logging subscribe. |
| `bujji/core/decision_trace.py` | Structured self-describing record of how a decision was reached. |
| `bujji/core/position_codec.py` | Serializes/deserializes a live `Position` for crash recovery. |
| `bujji/core/session_state.py` | Atomic JSON snapshot of FSM state + open position, reconciled against broker on restart. |
| `bujji/core/process_lock.py` | Single-instance lock preventing two orchestrators on the same account. |
| `bujji/signal/` | Signal Engine — ORB/VWAP breakout detection, emits `Signal`. Never places orders. |
| `bujji/trade/` | Trade Manager — owns the position, per-candle thesis reassessment, emits `TradeDecision`. Never talks to a broker. |
| `bujji/execution/engine.py` | **Real, broker-touching** `ExecutionEngine` — idempotent placement, partial-fill handling, timeout-cancel, retry/backoff, auth-fast-fail. Reused by Generation 3 too. |
| `bujji/tick/` | Tick Engine — continuous risk monitoring while IN_POSITION, separate from candle-driven Signal Engine. |
| `bujji/market/` | "Market Brain" — shared direction-neutral market-state interpreter (VWAP/ORB/tape control). |
| `bujji/dashboard/` | Read-only stdlib HTTP status dashboard (legacy system's own, distinct from the newer `portfolio_valuation` dashboard rendering). |
| `bujji/ops/` | Alert Engine — fires on state transitions/one-shot events, never trading actions. |
| `bujji/capital/` | Capital Management Engine — margin-aware position sizing; **live-certified** against a real FYERS SPAN margin API call (`docs/AUDIT_LOG.md` Pass 8). |

### Generation 2 — MSI / Intelligence Observatory (`bujji/msi_*`, `live_shadow_operator/`, observation chain)

| Package | Purpose |
|---|---|
| `live_observation/` | Series 74 — produces the raw observation contract. |
| `live_market_events/` | Series 75 — derives "what objectively changed" between observations; fact-only. |
| `market_episode/` | Series 76 — groups events into coherent Episodes over time; still fact-only. |
| `msi_price_structure` (78) | MSI Brain 1: trend/swing/compression/expansion/balance from Episode data. |
| `msi_market_structure` (79) | MSI Brain 2: support/resistance/breakout/breakdown/retest. |
| `msi_market_direction` (85) | Market Direction Intelligence brain. |
| `msi_participant_positioning` (86) | Market Participant Positioning Intelligence. |
| `msi_volatility_structure` (88) | Volatility Structure Bridge; reuses legacy Black-Scholes IV/Greeks math. |
| `msi_consensus` (81) | Multi-Domain Consensus: coherence/agreement across MSI brain outputs. |
| `msi_decision_synthesis` (77) | Fuses domain signals into one `MarketOpportunityAssessment`; never trades. |
| `msi_strategy_eligibility` (82) | Which strategy families are permissible; defines solution space only. |
| `msi_trade_intent` (83) | One eligible family → `TradeIntentAssessment` (exposure/bias/risk); no strikes/sizing. |
| `msi_trade_thesis` (92) | Builds the market thesis between MSI and Strategy Selection. |
| `msi_strategy_selection_foundation` (87) | Strategy-family suitability only, no ranking. |
| `msi_strategy_selector` (89) | Chooses best-fit strategy via market-state matching, no return-optimization. |
| `msi_strategy_expression` (93) | Validated thesis → exposure characteristics (directional/vol bias, theta/convexity). |
| `msi_trade_construction` (90) | Fully specified position (expiry/strikes/prices/risk); never places orders. |
| `msi_position_construction` (95) | Position SHAPE (single/spread/straddle/condor) + expiry/strike philosophy. |
| `msi_portfolio_construction` (91) | Portfolio-admission gate: approve/reject/defer + size against capital. |
| `msi_margin_bridge` (97) | Deterministic per-lot margin estimator, replay + production. |
| `msi_position_lifecycle` (96) | Open position's day-over-day health/adjustment/exit classification. |
| `msi_execution_planning` (98) | Deterministic execution plan (sequencing, dependency graph); never calls a broker. |
| `msi_strategy_optimization` (108) | Strike/expiry/roll optimization guidance, read-only, atop the frozen engine. |
| `msi_dynamic_management` (109) | 6 independent roll/adjustment/exit assessments per position per day. |
| `msi_position_recomposition` (110) | Composes the above read-only to produce the exact new position from a roll decision. |
| `msi_decision_auditor` (99) | Permanent flight recorder: DecisionRecord + OutcomeRecord pairs; never scores/ranks. |
| `msi_shadow_trading` (100) | Paper-trades approved decisions with real entry/settlement prices; no broker calls. |
| `msi_evidence_packet` (101) | Immutable, content-hashed fact/measurement store; isolated from Production. |
| `msi_market_learning` (100 Phase 1.0) | Converts outcome pairs into disclosed Knowledge Candidates; pure evidence, never adaptive. |
| `msi_counterfactual_replay` (102) | Replays real causal alternative decision paths through the frozen pipeline. |
| `msi_market_phenomena` (103) | Causal, declarative classification of "what objectively happened" (10/21 phenomena implemented). |
| `msi_knowledge_validation` (105) | 7-state hypothesis validation; zero cross-package imports, most isolated package. |
| `msi_opportunity_assessment` (104) | 5-state decision-quality classification; zero cross-package imports. |
| `msi_performance_analytics` (101) | Observer-only descriptive statistics; flags results below reliability threshold. |
| `msi_engineering_evidence_board` (106) | Final learning-stack layer: INSUFFICIENT_EVIDENCE / CONTINUE_OBSERVING / READY_FOR_ENGINEERING_REVIEW / SUPERSEDED / ARCHIVED. |
| `live_shadow_operator/` | Sprint 107 — production shadow-mode orchestration; reuses only frozen Series 73-106 logic, adds zero new trading logic. |
| `run_live_shadow.py` | **The actual live entry point running today** (including LSQ-1). Real FYERS auth/WebSocket, decision cadence, watchdog, portfolio valuation, exit engine. No order-construction path. |
| `run_daily_observation.py` | Standalone script wiring Series 99→106 for one real day, with a Session Manifest (git/config/replay verification). |

### Generation 3 — Trading Brain v3 (`bujji/trading_brain/`, `production_runtime/`, `integration/`, `runtime_execution/`, `runtime_safety/`, `runtime_session/`, `authentication/`, `broker_adapter/`)

| Package | Purpose |
|---|---|
| `trading_brain/evidence_interpreter/` | Translates MIC v2 classification strings into the closed ontology vocabulary. |
| `trading_brain/market_state/` | Fuses evidence into one market-state conclusion + confidence. |
| `trading_brain/strategy_selector/` | Deterministic decision table, no scoring/ML, picks one registered strategy or `NO_STRATEGY`. |
| `trading_brain/risk_brain/` | Gates on market-*condition* trustworthiness — **not** numeric risk limits (real gap). |
| `trading_brain/capital_brain/` | Capital *policy* (NONE/MINIMAL/REDUCED/STANDARD/FULL) — not a Rupee number. |
| `trading_brain/execution_planner/` | Abstract workflow description, never order fields. |
| `trading_brain/execution_engine/` | Abstract orchestration only — despite the name, never touches a broker. |
| `trading_brain/nifty_contract_builder/` | Resolves strikes against a caller-supplied option chain; carries `last_price` (added this engagement). |
| `trading_brain/position_sizing/` | Lots-per-leg from a fixed capital-intent lookup table. |
| `trading_brain/order_construction/` | Broker-neutral `OrderRequest` per leg; carries `reference_price` (added this engagement). |
| `trading_brain/portfolio_valuation/` | **Built this engagement** — pure, tick-driven MTM revaluation; no broker/network import. |
| `trading_brain/exit_engine/` | **Built this engagement** — 4 v1 rules (3 real, 1 documented placeholder), consumer-only. |
| `production_runtime/runtime.py` | The 3 modes: `run_read_only`, `run_shadow`, `verify_production_ready_construction`. |
| `production_runtime/composition_root.py` | Constructs the full object graph; `broker_name="fyers"` builds a real, **unguarded** `FyersBroker` even in non-production modes. |
| `production_runtime/config.py` | `RuntimeConfig` — **no cross-validation between `mode` and `broker_name`** (confirmed BLOCKER). |
| `broker_adapter/` | Translates abstract execution actions to broker operation *names* only — never calls anything. |
| `integration/execution_adapter.py` | `ProductionExecutionAdapter` — bridges to the real `bujji/execution/engine.py::ExecutionEngine`. |
| `runtime_execution/` | Validates & dispatches `OrderRequest`s via an injected `ExecutionEngineInterface` Protocol. |
| `runtime_safety/` | Structural/qualification-consistency gate — **not** a risk/capital-limit engine. |
| `runtime_session/`, `authentication/` | Session and broker-auth state machines. |

### Shared / Cross-Generation Infrastructure

| Path | Purpose |
|---|---|
| `bujji/broker/base.py` | Abstract `Broker` ABC — `place_order`/`get_order`/`cancel_order`/`get_open_positions` + market data methods. |
| `bujji/broker/paper.py` | `PaperBroker` — real in-memory simulator, ledger with entry timestamp + realized P&L, fills at `reference_price` (priority) → `limit_price` → synthetic default. |
| `bujji/broker/fyers.py` | Real `FyersBroker` — calls the actual SDK; `place_order`/`get_order`/`cancel_order` implemented but **never proven against a real network call**. |
| `bujji/broker/fyers_ws.py` | `FyersTickFeed` + `TickSilenceWatchdog` — real-time tick feed with fault-tolerant reconnect, live-proven (Session #3). |
| `bujji/broker/guard.py` | `disable_live_execution()` — instance-level stub-patching of execution methods; the one **structural** (not just conventional) live-order guard in the whole codebase. |
| `bujji/broker/hybrid.py` | `HybridPaperBroker` — real market data + paper execution ledger. |
| `bujji/broker/instrument_master.py` | FYERS symbol-master download/cache — **not wired into `run_live_shadow.py`**. |
| `bujji/journal/` | One append-only JSONL journal per module (house convention), including `portfolio_valuation_journal.py` (this engagement) with `TradeLifecycleTracker` for peak/trough. |
| `bujji/replay/`, `bujji/qualification/` | Historical replay/corpus infrastructure and campaign qualification runner. |
| `docs/` | 141 files — architecture write-ups, audit logs, sprint reports; see individual `docs/*.md` for detail beyond this summary. |
| `tools/` | Operational scripts — `pre_market_supplementary_checks.py`, `eq1_*.py`, `lsq_*.py` (all this engagement). |
| `data/` | Runtime state (`0700` perms) — `bujji.db`, large journals, `instrument_master/`, `bhavcopy/`, `live_shadow_journal/`. Contains many stray `*.lock` files from ad-hoc test runs — cleanup candidate. |
| `qualification/` (top-level, root) | **Distinct from `bujji/qualification/`** — standalone MIC-adapter qualification script + session outputs. Don't conflate the two. |
| `reports/` | Output artifacts only (daily trading reports, historical campaign results) — not source. |
| `pipeline_audit.py` | Standalone Sprint 8 tool verifying the Decision Pipeline Refactor (Sprints 1-7) still holds; not part of the running app. |

---

## MCP Server

**None exists in this repo** — confirmed by a repo-wide grep for `mcp.server`/`FastMCP`/`@mcp.tool` (zero matches). If you're looking for an MCP server + scanner, that's a different, separate project — not this one.

---

## How to Start `run_live_shadow.py` (the actual live entry point)

```bash
ssh root@139.59.76.137
cd /opt/bujji/app
set -a; source .env.fyers; set +a
/opt/bujji/.venv/bin/python run_live_shadow.py --live \
  --bhavcopy data/bhavcopy/BhavCopy_NSE_FO_0_0_0_<YESTERDAY>_F_0000.csv \
  --bhavcopy-day <YESTERDAY> \
  --log-file logs/<session>.log
```

- **venv Python**: `/opt/bujji/.venv/bin/python` (note: NOT `/opt/bujji/app/.venv` — a real, previously-made mistake; the venv lives one level up from the repo).
- **`--day YYYY-MM-DD`** mode replays a recorded day instead of going live.
- Real, pre-market checklist runs automatically and **aborts on any mandatory failure** — never proceeds in a degraded state.

---

## Data Flow: Broker → Decision → Output

```
FYERS WebSocket (real ticks)
  → FyersTickFeed.latest() / tick_age_seconds()
  → TickSilenceWatchdog.check() (every loop iteration, market-hours-gated)
  → op.process_tick() → SessionDriver (MSI Series 99-106 decision chain)
  → op.run_cadence() every --cadence-seconds (default 900s)
  → portfolio_valuation.engine.revalue() (on every NEW real tick)
  → exit_engine.engine.evaluate() (if any position is open — currently never, see gap above)
  → PortfolioValuationJournal.record_valuation() / record_exit()
  → EOD: render_health_dashboard() + render_portfolio_dashboard() + render_exit_dashboard()
```

---

## Key Constants / Thresholds (Generation 1, `config/config.yaml` — legacy system only, NOT read by `run_live_shadow.py`)

```
market.strike_interval: 50      market.lot_size: 75
timing.orb_start/end: 09:15-09:20   timing.trading_end/hard_exit: 15:05
risk.lots: 1 (ceiling, Capital Management Engine may reduce, never increase)
risk.margin_safety_buffer: 0.90     risk.capital_policy: CERTIFIED (real, live-verified)
risk.max_mtm_loss: 6000             risk.daily_loss_limit: 6000
risk.breakout_body_ratio: 0.60      strategy.max_trades_per_day: 1, allow_reentry: false
broker.name: fyers_paper (live data, paper execution)
```

**`run_live_shadow.py` (Generation 2) and `production_runtime` (Generation 3) do not read this file** — they have their own, separate config surfaces (`ExitRuleConfig`, `RuntimeConfig`). Don't assume one config governs the whole repo.

---

## External Dependencies

| Dependency | Expiry / Renewal behavior |
|---|---|
| **FYERS access token** | Real-world ~1 day validity, observed to require daily manual refresh (established morning routine: generate → scp `.env` → extract `FYERS_APP_ID`/`FYERS_ACCESS_TOKEN`/`FYERS_APP_SECRET`/`FYERS_REFRESH_TOKEN` into `.env.fyers`, chmod 600). Automatic refresh via `FYERS_REFRESH_TOKEN` is documented as currently non-functional per FYERS's own SEBI-driven restriction (`docs/FYERS_TOKEN_LIFECYCLE.md`). |
| **NSE Bhavcopy** | Published EOD only, no live option-chain-structure feed. Fetched daily via a real, working two-step cookie-jar workaround (`curl` against `nseindia.com` for cookies, then `nsearchives.nseindia.com` for the real archive) — NSE blocks datacenter IPs on a plain request. |
| **NSE holiday calendar** | Self-disclosed as unverified against the real, current NSE circular — every session's pre-market checklist requires a manual cross-check. |
| **`fyers-apiv3` SDK** | Real, installed, has a confirmed non-daemon-thread lifecycle limitation (leaves a zombie process after "clean" shutdown) and a confirmed `close_connection()` no-op condition — both SDK-owned, not fixable from our side (see `docs/LIFECYCLE_INTEGRITY_PROOF_P5.md`). |
