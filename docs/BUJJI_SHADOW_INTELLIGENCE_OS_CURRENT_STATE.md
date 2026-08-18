# Bujji Shadow Intelligence OS — Current State & Learning Model
**As of: Phase 19.19 commissioning, 2026-08-15**

This document is a permanent record of what Bujji actually is today — not what it is planned to become. Every claim in this document is grounded in code that has been audited, tested, and verified against real production data on the VPS as of Phase 19.19. Where something is planned but not built, it is explicitly labeled as **NOT YET BUILT**.

---

## 0. Quick Orientation

| | |
|---|---|
| **What it is** | An automated daily market-observation and intelligence-composition system for NIFTY options |
| **What it is not (yet)** | A trading system — it cannot and structurally must not place, modify, or cancel an order |
| **Current phase** | Commissioned, timer installed, waiting for its first scheduled run (Mon 2026-08-17, 09:00 IST) |
| **Current activity** | 20–30 session Shadow Intelligence Campaign — pure observation, no strategy changes |

---

## 1. The Purpose of Bujji Today

**In trader language:**

Right now, Bujji's only job is to show up to the market every trading day, watch what happens, write down what it saw in a structured way, and try to describe the market's condition — without touching a single order. Think of it as hiring a very disciplined research analyst who sits at the desk every morning, takes detailed notes on price, volatility, and options activity, and produces a daily report — but has no authority to trade, and never will, until that authority is explicitly and separately granted later.

**Why "Shadow Intelligence OS":** it runs in *shadow mode* — parallel to the real market, observing everything a live trading system would need to see, but with zero ability to act. "OS" because it is not a single script but an orchestrated system: a scheduler, a capture layer, a persistence layer, several intelligence-composition stages, and monitoring — all wired together as one coherent daily operating loop.

**Why a 20–30 session campaign, not "it's done, ship it":** software passing tests in isolation is not the same as software behaving correctly against 20–30 real, messy, unpredictable trading days — real FYERS outages, real missing data, real market gaps, real holidays, weekends, and the ordinary chaos of production. This campaign exists to find out whether the system as built survives that chaos honestly (failing visibly when something is wrong, never fabricating success) before any conversation about giving it more responsibility.

**In technical language:**

Bujji today is `DailySessionRuntime` (Phase 19.11), fronted by `run_daily_intelligence_session.py`, triggered once per weekday by `bujji-daily-intelligence.timer` → `bujji-daily-intelligence.service` (systemd, `Type=oneshot`, no auto-restart). It composes one full observation → intelligence cycle per day, persists the result, and exits. The 20–30 session campaign is the first sustained real-world exposure of this pipeline, instrumented with heartbeat/continuity monitoring (`bujji_campaign_status.py`, Phase 19.17) specifically so that failures are detected and classified rather than silently absorbed.

---

## 2. Current Bujji Architecture

**The verified flow, exactly as it runs today:**

```
FYERS Market Data
    ↓
Capture Layer
    ↓
HistoricalObservationStore
    ↓
Market Reality Construction
    ↓
Intelligence Engine
    ↓
Decision Intelligence
    ↓
Phenomena Detection
    ↓
Market State
    ↓
Environment Classification
    ↓
Daily Intelligence Artifact
    ↓
Heartbeat / Campaign Monitoring
```

**In trader language, what each stage means:**

1. **FYERS Market Data** — the broker feed: spot price, India VIX, NIFTY futures, and the options chain. Bujji only ever *reads* from this feed; it never sends orders through it.
2. **Capture Layer** — the part that actually calls FYERS and pulls the numbers down, on a schedule, during market hours.
3. **HistoricalObservationStore** — the permanent record. Every observation ever captured (670,000+ rows as of this writing) lives here, in a SQLite database, so nothing is ever re-derived from memory — every later stage reads from this ledger.
4. **Market Reality Construction** — takes the raw captured numbers for one moment in time and assembles them into one coherent snapshot: "here is exactly what the market looked like at 15:40 today," including whether the picture is complete or has gaps.
5. **Intelligence Engine** — turns that raw snapshot into an interpreted view: volatility read, Greeks, event context — the first layer of "what does this data actually mean."
6. **Decision Intelligence** — reasons over the intelligence snapshot to produce a structured decision-context (not a trade decision — a *market* decision context: what's compatible, what's changed).
7. **Phenomena Detection** — names specific market behaviors it can recognize in the data (e.g., a correcting day).
8. **Market State** — classifies where the market currently sits in a state graph (a structured vocabulary of market conditions, with defined transitions).
9. **Environment Classification** — a broader read of the trading environment the day represents.
10. **Daily Intelligence Artifact** — the final, permanent record of everything above for that day: every stage's output, bundled and stored with an id and a completeness flag.
11. **Heartbeat / Campaign Monitoring** — the honest status layer: did today's session actually complete, fail, get skipped for a holiday, or never run at all — visible without reading logs.

**In technical language:**

| Stage | Module | Notes |
|---|---|---|
| Capture | `bujji.historical_reality.capture`, invoked via subprocess scripts from `_make_real_capture_fn` | Writes `RESOLUTION_FIVE_MINUTE` observations |
| Store | `bujji.historical_reality.store.HistoricalObservationStore` | SQLite, WAL journal mode, natural-key idempotent writes |
| Reality | `bujji.market_reality_snapshot.builder.build_market_reality_snapshot()` (Phase 18.1) | `resolution=RESOLUTION_FIVE_MINUTE`, completeness = spot+futures+vix present |
| Intelligence | `MarketIntelligenceSnapshot` composition (Phase 19.3) | Volatility, Greeks, event brains — each with clock injection + evidence lineage (Phase 19.2.2) |
| Decision Intelligence | `bujji.decision_intelligence` (Phase 19.6) | Reasoning + evidence, no execution vocabulary |
| Phenomena | `bujji.market_phenomena` (Phase 19.7) | Named, evidenced market behaviors |
| Market State | `bujji.market_state_graph` (Phase 19.8) | Defined state vocabulary + transitions |
| Environment | `bujji.market_environment` (Phase 19.9) | Broader environment classification |
| Composition orchestration | `bujji.shadow_runtime.live_intelligence_cycle.run_live_intelligence_cycle()` (Phase 19.13) | One call composes stages 5–9 together |
| Artifact | `bujji.shadow_runtime.daily_intelligence_artifact.DailyIntelligenceArtifact` (Phase 19.13) | Full-payload, `to_dict()`-based, `completeness_gate_passed` flag, optional `replay_equivalence` |
| Orchestrator | `bujji.shadow_runtime.daily_session.DailySessionRuntime` (Phase 19.11) | Lifecycle: `PRE_MARKET → MARKET_OPEN → CAPTURING → INTELLIGENCE_RUNNING → SESSION_COMPLETE/FAILED` |
| Monitoring | `bujji.shadow_runtime.status.py` + `campaign_continuity.py` (Phase 19.12/19.17) | Heartbeat staleness, 5-way session classification |

---

## 3. What Bujji Observes From The Market

| Data | Status |
|---|---|
| Spot price (NIFTY 50) | **Implemented** — captured, stored, used in Reality construction |
| India VIX | **Implemented** — same pipeline |
| NIFTY futures (continuous) | **Implemented** — required for `COMPLETE` classification |
| Options chain (full chain, NIFTY) | **Implemented** — captured and certified (e.g. 2,190 contracts on a real verified day) |
| Premium behaviour | **Implemented** at the observation level (raw premium data captured); a dedicated *premium behaviour memory/interpretation* layer is **NOT YET BUILT** (see Section 9) |
| Volatility | **Implemented** — a `volatility_brain` component exists and contributes to the Intelligence stage |
| Greeks | **Implemented** — a `greeks_brain` component exists and contributes to the Intelligence stage |
| Market structure (state/regime) | **Implemented** at a foundational level via `market_state_graph`; this is a defined vocabulary and transition model, not a trained/adaptive regime detector |
| OI (open interest) | Captured as part of the raw options chain payload; a dedicated **OI interpretation layer is NOT YET BUILT** (see Section 9) |
| Order flow | **NOT captured** — Bujji has no order-flow/tape data source today |
| Time-based observations | **Implemented** — every observation carries a real timestamp; the daily cycle runs `as_of_time` end-of-day (15:40 IST) by convention, intraday multi-cycle observation exists as a separate, distinct capability (`ShadowSessionRunner`) not the daily entrypoint's own responsibility |

---

## 4. What Happens During One Trading Day

Walking through a real 09:00 IST session:

1. **Timer fires** (`bujji-daily-intelligence.timer`, `Mon..Fri 09:00 Asia/Kolkata`) → starts `bujji-daily-intelligence.service` (oneshot).
2. **Calendar gate** (Phase 19.16): `MarketCalendar.is_trading_day()` checks weekend/holiday first. If it's not a trading day, the session writes an honest `NON_TRADING_DAY` heartbeat and exits — **no lock is acquired, nothing else runs.**
3. **ProcessLock acquired** (`data/daily_intelligence.lock`, Phase 19.12) — prevents any overlapping run (a second timer fire, or a manual invocation) from corrupting state.
4. **Broker constructed** — `FyersBroker` wrapped immediately in `disable_live_execution()` before it is used for anything.
5. **Capture runs** — pulls spot, VIX, futures, and the options chain from FYERS, writes to `HistoricalObservationStore`.
6. **Intelligence cycle runs** (`run_live_intelligence_cycle`) — composes Reality → Intelligence → Decision Intelligence → Phenomena → Market State → Environment in one call, and (per Phase 19.14.1) also runs the same composition a second time in `HISTORICAL_REPLAY` mode against the identical data, to prove LIVE and REPLAY produce the same result.
7. **EOD completeness check** — `validate_end_of_day_completeness()` confirms spot+futures+vix+options are all present for the session date.
8. **Artifact persisted** — a `DailyIntelligenceArtifact` is written to `daily_intelligence_artifacts.jsonl`, including the replay-equivalence result and the completeness-gate flag.
9. **Heartbeat written** — `daily_session_heartbeat.json` updated with the final `runtime_status` (`SESSION_COMPLETE`, `FAILED`, etc.), row count, and any error.
10. **Lock released, process exits** — cleanly, whether the session succeeded or failed.
11. **Verified separately** — `bujji_campaign_status.py` can be run any time afterward to see the honest, human-readable status of that day and the campaign so far.

Nothing in this flow places, modifies, or cancels an order at any point — that capability does not exist in the code path this daily entrypoint uses.

---

## 5. What Bujji Learns Today

Honest answer: **very little, in the "learning" sense a trader would mean.**

| Question | Answer |
|---|---|
| Does Bujji learn automatically? | **No.** No model is trained, updated, or fitted anywhere in this pipeline. |
| Does it update models? | **No.** There are no ML models in this daily pipeline — the "brains" (volatility, Greeks, event) are deterministic, rule/formula-based interpreters, not trained models. |
| Does it change strategies? | **No.** There is no strategy selection or execution logic in this pipeline at all — that layer exists elsewhere in the codebase (from earlier phases) but is explicitly disconnected from and untouched by the daily commissioning path. |
| Does it remember previous sessions? | **Partially.** Every session's full artifact is permanently persisted (`daily_intelligence_artifacts.jsonl`, keyed by date) — so the *data* to look back on exists. But nothing today automatically reads yesterday's artifact to change today's behavior. |
| Does it build market memory? | **A foundational piece exists** — `MarketMemoryEntry` + a similarity engine + store (Phase 19.5) were built and tested, providing the *mechanism* for storing and comparing market conditions across days. It is not yet wired into the daily commissioning pipeline as an active, consulted memory during a live session. |

**Current learning capability = persistence + a memory-storage mechanism that exists but is not actively consulted by the daily pipeline yet.**
**Future learning capability (NOT YET BUILT)** = using that persisted history to inform market-state classification, contextualize a new day against similar past days, or feed back into decision intelligence. This is explicitly deferred — see Section 8.

---

## 6. Current Safety Boundaries

**In trader language:** Bujji is built so that even if every other part of it went wrong — a bug, bad data, a bad decision — it still physically cannot send an order to the exchange. This isn't a policy or a promise; it's enforced in the code itself.

**In technical language:**

- `bujji.broker.guard.disable_live_execution()` takes a live broker instance and replaces its `place_order`, `modify_order`, `cancel_order`, `get_open_positions`, and `get_order` methods — on that specific instance — with stubs that immediately raise `LiveExecutionDisabledError`. This happens in `_build_real_broker()` **before** the broker is ever handed to any other code.
- This is instance-level patching, not a config flag or an `if` check that could be bypassed — the methods themselves are gone, replaced with raisers, before any network call is possible.
- Verified directly in Phase 19.18: even after `broker.connect()` succeeds, `place_order` still raises immediately if called.
- **Why this campaign is observation-only:** because nothing downstream of capture in this pipeline (Reality, Intelligence, Decision Intelligence, Phenomena, State, Environment) has any concept of "place a trade" — those modules are structurally forbidden from importing broker/order/position/strategy code (enforced via AST-level checks in this project's test suite), so there is no code path from "intelligence was generated" to "an order was placed," even in principle, in what's commissioned today.

---

## 7. What We Are Testing During 20–30 Sessions

This is a real-world reliability and correctness experiment, not a performance/profitability test. Specifically:

- **Data reliability** — does FYERS capture actually succeed, day after day, without silent gaps?
- **Market observation quality** — does the captured data actually reach `COMPLETE` classification on real trading days, consistently?
- **Intelligence consistency** — does the Intelligence/Decision Intelligence/Phenomena/State/Environment composition run without errors across many different real market conditions, not just the handful of days used in development testing?
- **Market state classification** — does the state graph produce sensible, stable classifications across a real range of days (trending, choppy, volatile, quiet)?
- **Replay equivalence** — does re-running the exact same day's data in `HISTORICAL_REPLAY` mode always reproduce the same result as the original `LIVE` run? (This is checked automatically, every single session, not just spot-checked.)
- **Memory continuity** — does the system correctly track, across restarts and days, what has and hasn't run (via the campaign continuity classifier), with zero silent gaps mistaken for successes?
- **Failure recovery** — when something does fail (FYERS down, VIX missing, a crash mid-session), does the system fail *honestly and visibly*, and recover cleanly the next day, rather than corrupting state or silently limping on?
- **Whether observations become useful trading intelligence** — this is the open, unanswered question the campaign exists to inform: once we have 20–30 real days of Reality → Intelligence → Phenomena → State → Environment output, does that output actually look like something a trader could use, or does it reveal gaps that need to be closed before it's trustworthy?

**What success looks like:** 20–30 consecutive scheduled sessions where the honest failure/success signal (heartbeat + campaign status) shows a high completion rate, zero silent/fabricated successes, zero safety-boundary violations, and a body of real daily artifacts that can be reviewed to judge whether the intelligence layer's output is actually market-meaningful. Success is not "no failures ever" — a FYERS outage or a genuinely missing data day is an honest `FAILED`, not a defect. Success is that every outcome, good or bad, is correctly and honestly classified.

---

## 8. How Bujji Will Improve After This Campaign

The intended future learning loop:

```
Market Session
    ↓
Observation
    ↓
Pattern Discovery
    ↓
Memory Formation
    ↓
Better Context Understanding
    ↓
Better Strategy Selection
    ↓
Better Risk Management
```

| Stage | Status |
|---|---|
| Market Session | **Built and running** — the daily commissioning pipeline (Sections 2–4) |
| Observation | **Built and running** — Capture + HistoricalObservationStore + Reality construction |
| Pattern Discovery | **NOT YET BUILT** — no component today analyzes the accumulated daily artifacts to discover recurring patterns |
| Memory Formation | **Partially built** — `MarketMemoryEntry`/similarity engine/store (Phase 19.5) exist as a mechanism, but are not actively fed by or consulted during the daily commissioning pipeline |
| Better Context Understanding | **NOT YET BUILT** — nothing today uses past sessions to contextualize a new day |
| Better Strategy Selection | **NOT YET BUILT in this pipeline** — a strategy-selection layer exists elsewhere in the codebase from earlier phases, but it is explicitly disconnected from this commissioned daily pipeline |
| Better Risk Management | **NOT YET BUILT in this pipeline** — same as above |

The explicit, deliberate design decision behind this campaign is: **build and prove the Observation stage exhaustively first, in real production conditions, before building anything above it.** The remaining stages of this loop are real future work, not a hidden capability already present.

---

## 9. Current Limitations

Being direct about what a professional trading system would still need before this could be trusted with real capital:

- **Market regime intelligence** — `market_state_graph` provides a defined vocabulary and transition model, but it is not a validated, battle-tested regime classifier. It has not yet been checked against 20–30+ real sessions for classification stability/accuracy — that is a large part of what this campaign is for.
- **Premium behaviour memory** — raw premium data is captured, but there is no component that remembers "how did premium behave in similar past setups" and uses that to inform anything.
- **OI interpretation** — OI is present in the captured options chain data, but there is no dedicated engine that interprets OI changes (buildup, unwinding, positioning shifts) into an actionable signal.
- **Volatility intelligence** — a `volatility_brain` exists and contributes a volatility read to the Intelligence stage, but there is no term-structure memory or historical-volatility-regime comparison feeding it (earlier investigation reports on this exist from Phase 11 but were not carried into the current commissioned pipeline).
- **Liquidity understanding** — a `Liquidity Intelligence Bridge` was built earlier (pre-Phase 19) but is not part of the currently commissioned daily pipeline's flow.
- **Trade management** — does not exist in this pipeline at all; earlier phases (15E–15K) built position lifecycle/exit/adaptive-management logic, but that entire layer is disconnected from and untouched by the daily commissioning path being campaigned today.
- **Adaptive risk management** — no risk-sizing, exposure, or capital logic runs in this pipeline; it is purely observation and intelligence composition.

In short: Bujji today is a rigorously verified **market observation and intelligence-composition engine**. It is not yet, and does not currently claim to be, a trading system. The gap between "produces a daily intelligence artifact" and "can be trusted to inform real trading decisions with real capital" is real, currently unmeasured, and is exactly what this 20–30 session campaign is designed to start measuring honestly.

---

## 10. Final Summary

Bujji, as it exists today, is a commissioned, safety-guarded, daily-scheduled market intelligence engine for NIFTY options: every trading day at 09:00 IST it wakes up, checks the calendar, captures real spot/VIX/futures/options-chain data from FYERS into a permanent observation store, builds a verified snapshot of market reality, composes that into layered intelligence (volatility, Greeks, decision context, named market phenomena, a market-state classification, and an environment read), cross-checks that the same result would be produced on replay, persists the whole thing as a permanent artifact, and reports its own honest status — all while being structurally incapable of placing, modifying, or cancelling a single order, because the code path to do so simply does not exist in what runs today. It does not yet learn from its own history, does not yet select strategies, and does not yet manage risk or trades — those are real, acknowledged, deferred future stages. What it does today, it does honestly and verifiably, and the 20–30 session campaign now underway exists to find out, with real production data rather than assumptions, whether that foundation is solid enough to build the next layer on.
