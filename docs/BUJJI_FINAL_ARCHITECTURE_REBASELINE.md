# Bujji — Final Architecture Re-Baseline
## Autonomous Quant Desk / Broker-like Market Intelligence Operating System

**Audit only. No code written, deleted, merged, or refactored.**

---

## 0. Corrections to my own prior conclusions

Four in five turns. Stated first, because the pattern is itself a finding.

| # | I claimed | Verified reality |
|---|---|---|
| 1 | `tick/` is dead (0 importers) | Imported by `app.py` (relative import); 45 test files |
| 2 | WS symbol cap ~200 | `symbol_limit = 5000` in SDK |
| 3 | No NSE holiday calendar | `market_calendar.py` exists — unverified template, 0 production consumers |
| 4 | `capital/` is disconnected | **25 consumers** — but *all* inside `trading_brain/` (Stack A). Stack B has none. |

**Correction 4 is the most consequential.** A rich risk/capital/margin subsystem exists — `risk_governor` (adaptive governor, whole-book margin, capital check, position sizing, strategy risk pipeline), `capital/` (engine, policy, providers, broker adapter), `msi_margin_bridge`. It is **stranded in the deprecated stack**, not absent. That is a far better position than "missing" — and it makes the Stack A/B decision urgent rather than optional.

**Methodological rule going forward: absence must be *proven*, never inferred from a grep returning nothing.**

---

## 1. Current architecture (L0–L17, verified)

| L | Layer | Exists | Prod-connected | Proven by | Verdict |
|---|---|---|---|---|---|
| **L0** | Market/broker data | ✅ | ✅ Stack A (WS), ✅ Stack B (REST) | Live (REST verified 2026-07-20) | REST solid; WS full-mode unproven |
| **L1** | Raw authoritative capture | ⚠️ | ❌ | — | **UNSAFE** — `FyersTickFeed` keeps 2 of 23 fields, persists nothing |
| **L2** | Canonical storage | ⚠️ | ✅ (`market_observation`, 31 imp.) | — | Model exists; **no TickStore** |
| **L3** | Multi-TF reconstruction | ⚠️ | ❌ | Synthetic only | 5m only; 15Q window-close flaw |
| **L4** | Features | ⚠️ | ❌ | Synthetic only | 6 of ~40 |
| **L5** | Phenomena | ✅ | ✅ | Real cycles | `msi_market_phenomena`, 10 types |
| **L6** | Market state / regime | ✅ | ✅ | Real cycles | Single-timeframe only |
| **L7** | Opportunity | ✅ | ✅ | **Real: 684/708 cycles** | 15P proved all states reachable |
| **L8** | Strategy selection | ✅ | ✅ | Real cycles | Selection↔eligibility disagreement disclosed |
| **L9** | Trade intent | ✅ | ✅ | **Real: 30 intents** | Proven capable |
| **L10** | Risk / capital / margin | ✅ | ⚠️ **Stack A only** | Legacy | **Stack B has NO risk layer** |
| **L11** | Execution | ✅ | ✅ paper | Fixture | PaperBroker only; live gated (correct) |
| **L12** | Position lifecycle | ✅ | ❌ | Fixture + replay | Never executed live |
| **L13** | Portfolio intelligence | ✅ | ❌ | Fixture | 15M, 0 consumers |
| **L14** | Outcome attribution | ✅ | ❌ | Fixture | 15J |
| **L15** | Memory / learning | ✅ | ❌ | Fixture | 15N; population empty |
| **L16** | Research / replay | ⚠️ | ⚠️ | Fixture | **5 replay subsystems** |
| **L17** | Ops / health / recovery | ✅ | ⚠️ Stack A | Live incidents | `ops/`, `dashboard/`; **no kill switch (0 files)** |

**The system is strong from L5 upward and broken from L1–L4.** Intelligence is mature; the evidence substrate beneath it does not exist.

---

## 2. Target architecture

```
                              MARKET (NSE)
                                   │
  ╔════════════════════════════════▼════════════════════════════════╗
  ║ L1  RAW EVENT FABRIC        raw payload preserved verbatim       ║
  ║     full-mode WS · depth · REST chain · instrument master        ║
  ╚════════════════════════════════┬════════════════════════════════╝
  ╔════════════════════════════════▼════════════════════════════════╗
  ║ L2  AUTHORITATIVE STORAGE                                        ║
  ║  TickStore │ SnapshotStore │ UniverseStore │ MarketEventStore     ║
  ╚════════════════════════════════┬════════════════════════════════╝
  ╔════════════════════════════════▼════════════════════════════════╗
  ║ L3  MULTI-TF RECONSTRUCTION  1m←ticks; 5/15/30/1H/D←1m; W/M←D    ║
  ╚════════════════════════════════┬════════════════════════════════╝
  ╔═══════════════▼════════════════╗   ╔═════════════════════════════╗
  ║ L4a TECHNICAL FEATURES         ║   ║ L4b DERIVATIVES/MICROSTRUCT ║
  ╚═══════════════┬════════════════╝   ╚══════════════┬══════════════╝
  ╔═══════════════▼═══════════════════════════════════▼══════════════╗
  ║ L5  PHENOMENA          ← msi_market_phenomena (EXISTS)            ║
  ╚════════════════════════════════┬════════════════════════════════╝
  ╔════════════════════════════════▼════════════════════════════════╗
  ║ L6  MULTI-TIMEFRAME MARKET STATE   conflict-preserving (NEW)      ║
  ╚════════════════════════════════┬════════════════════════════════╝
  ║ L7 OPPORTUNITY → L8 STRATEGY → L9 TRADE INTENT   (ALL EXIST)      ║
  ╚════════════════════════════════┬════════════════════════════════╝
  ║ L10 RISK/CAPITAL/MARGIN   ← port from Stack A, do NOT rebuild     ║
  ╚════════════════════════════════┬════════════════════════════════╝
  ║ L11 EXECUTION (paper) → L12 LIFECYCLE → L13 PORTFOLIO             ║
  ║ → L14 OUTCOME → L15 MEMORY      (ALL EXIST, 15G–15N)              ║
  ╚════════════════════════════════┬════════════════════════════════╝
  ║ L16 RESEARCH / REPLAY / COUNTERFACTUAL → VALIDATED KNOWLEDGE      ║
  ╚═════════════════════════════════════════════════════════════════╝

CROSS-CUTTING (not downstream features):
  DATA QUALITY · UNCERTAINTY · REPLAY · OBSERVABILITY
  CONFIGURATION/LINEAGE · SAFETY GOVERNANCE
```

---

## 3. Genuinely strong

- **L7 Opportunity** — 15P proved every documented state reachable; anti-fabrication structurally enforced (`synthesize()` has no state-forcing parameter)
- **L12–L15** — lifecycle, canonical P&L, portfolio, attribution, memory: replay-proven, recovery-proven, immutability-enforced, safety-tested
- **L5 Phenomena** — real, connected, 10 types
- **`market_observation`** — 31 importers, de-facto canonical
- **`live_observation`** — late-tick correct, refuses to fabricate empty candles
- **L10 in Stack A** — adaptive risk governor, whole-book margin, capital check, position sizing. Substantial and battle-tested.
- **Feed reliability knowledge** — two real production incidents diagnosed and mitigated (`TickSilenceWatchdog`, `force_reconnect`)
- **Epistemic discipline** — UNKNOWN-first is genuinely pervasive, not aspirational

---

## 4. Incomplete · 5. Disconnected · 6. Duplicated · 7. Unsafe · 8. Unknown

**Incomplete:** features (6/~40) · timeframes (1 of 8) · Greeks (ATM-only, per-cycle) · OI (snapshot, no series) · single-TF state · calendar (unverified template)

**Disconnected:** `FyersTickFeed`→intelligence · `capital`/`risk_governor`→Stack B · `portfolio_intelligence` · `outcome_memory` · `replay_engine` · `market_timeseries` · `market_calendar` (0 prod consumers)

**Duplicated:** **5 replay subsystems** (`replay`, `mic_replay`, `replay_engine`, `msi_counterfactual_replay`, `qualification`) · 6+ confidence vocabularies across 20 packages · 2 application stacks

**Unsafe:**
1. `FyersTickFeed` destroys 21/23 fields at the ingestion boundary
2. Silent backpressure loss — synchronous callback, no queue, no drop counter
3. No `as_of` discipline — look-ahead currently possible
4. No `calc_version` — 0 files; derived history permanently incomparable
5. Unverified calendar that code could trust
6. 15Q tick-driven window closure — illiquid strikes hold windows open indefinitely
7. **No kill switch** (0 files)
8. Confidence laundering — uncertainty resets at every layer boundary

**Unknown (cannot resolve from repo):** FYERS sequence number · broker server timestamp distinct from `exch_feed_time` · duplicate-tick rate on reconnect · tick corrections/amendments · server-side symbol cap vs SDK 5000 · whether `DepthUpdate` shares the cap · auction/opening data · settlement price delivery · real full-mode tick rate

---

## 9. What must be measured (Gate 1)

Full-mode tick rate per instrument class · latency p50/p95/p99 (sets the watermark) · symbol cap in practice · reconnect/resubscribe behaviour · depth cost · queue depth and drops at 331 symbols · **raw wire frames** (sequence-number question) · actual bytes/message

---

## 10. Universe: the three-tier tradeoff

| Tier | Width | Purpose | Cost |
|---|---|---|---|
| **Minimum strategy** | ATM±5 (~90 inst.) | Straddles, strangles, condors, verticals | Trivial |
| **Research preservation** | ATM±20–25 (331–411) | Skew, smile, wing behaviour, IV surface, OI walls | Low |
| **Future-proof capture** | Full chain (~1,851) | Anything not yet imagined | **37% of SDK cap** |

**Key finding: the full chain fits.** 231 strikes × 2 × 4 expiries + 3 = **1,851 instruments — only 37% of `symbol_limit = 5000`.** The constraint is throughput and storage, both unmeasured.

**Recommendation: ±25 now (411 instruments), and let Gate 1 test whether full-chain is viable.** Un-captured history is unrecoverable; ±20 permanently forecloses ±25 research. The marginal cost is near-zero; the option value is permanent. This is irreversible and must be decided **before** the Gate 1 run.

---

## 11. Time model, watermark, and the 15Q flaw

Four timestamps, never conflated: `exch_feed_time` (authoritative — windowing, ordering, analytics) · `broker_ts` (UNKNOWN if supplied) · `receive_ts` (latency, staleness) · `persist_ts` · `process_ts`.

**The 15Q flaw, precisely:** `CandleAggregator` closes a window only when the *next tick arrives*. An illiquid far-OTM strike can hold a window open for hours, then emit a bar stamped correctly but delivered absurdly late. Worst exactly where liquidity intelligence matters most.

**Correct model — time-driven watermark:**
```
for each (instrument, timeframe):
    window seals when   now_event_time > window_end + watermark
    a clock/heartbeat drives closure — NOT tick arrival
    late-beyond-watermark ticks are STORED (never dropped), flagged OUT_OF_ORDER
    sealed candle carries late_excluded_count and revision
```
**Watermark bound remains DEFERRED** until Gate 1 supplies p99 latency. Choosing it now would be a guess with permanent consequences.

**NSE session semantics:** 09:15–15:30 IST = 6h15m. 1H bars anchor at 09:15 (not 09:00). **4H is rejected** — it cannot divide the session evenly; one full bar plus a 2h15m stub is structurally misleading for zero analytical gain.

---

## 12. Uncertainty contract

```
EpistemicState ∈ {KNOWN, UNKNOWN, NOT_AVAILABLE, NOT_APPLICABLE,
                  INSUFFICIENT_HISTORY, STALE, GAP, DEGRADED}

Uncertainty = (state, confidence, limiting_factor, provenance[])
```

| Rule | Semantics |
|---|---|
| MIN-rule | `confidence(out) ≤ min(confidence(critical inputs))` |
| UNKNOWN absorption | Any critical input UNKNOWN → output UNKNOWN |
| GAP / STALE taint | Propagates; caps confidence at LOW |
| NOT_APPLICABLE isolation | Does **not** degrade siblings |
| INSUFFICIENT_HISTORY | Produces **no value**, not a low-confidence value |
| `limiting_factor` mandatory | Whenever output < max, name the capping input |
| Critical/non-critical | **Declared per feature** — not a global default |

Propagation: `tick(quality) → candle(gap,tick_count) → feature(sufficiency) → phenomenon → state → opportunity → strategy → decision`, with `limiting_factor` preserved end-to-end. That chain is what makes *"why does Bujji believe this, and what is the weakest evidence?"* answerable.

**The existing 6+ vocabularies become adapters. None are deleted.**

---

## 13. Opportunity semantics — the fundamental distinction

15P proved the engine correct. The remaining architectural gap is that **"no opportunity exists" and "we cannot determine whether one exists" are currently conflated.**

| State | Meaning | Actionable |
|---|---|---|
| `NO_EDGE` | Evidence sufficient; genuinely no edge | Correct inaction |
| `UNKNOWN` | Evidence insufficient to judge | **Cannot conclude** |
| `MONITOR` | Something forming; not yet resolved | Watch |
| `ELIGIBLE` | Opportunity exists; constraints unchecked | Candidate |
| `TRADEABLE` | Eligible **and** risk/capital/liquidity permit | Actionable |

Today `MONITOR` absorbs both `NO_EDGE` and `UNKNOWN`. Separating them is a prerequisite for learning — you cannot learn from "I didn't know" and "there was nothing" if they are the same label.

`ELIGIBLE` vs `TRADEABLE` also does not exist, because **L10 is absent from Stack B.**

---

## 14. Maturity scorecard (0–6, brutally honest)

| L | Layer | Code | Integ. | Replay | Live data | Live mkt | **Score** |
|---|---|:--:|:--:|:--:|:--:|:--:|:--:|
| L0 | Broker/market data | ✅ | ✅ | ✅ | ✅ | ✅ | **5** |
| L1 | Raw capture | ⚠️ | ❌ | ❌ | ❌ | ❌ | **1** |
| L2 | Authoritative storage | ⚠️ | ❌ | ❌ | ❌ | ❌ | **1** |
| L3 | Multi-TF | ⚠️ | ❌ | ⚠️ | ❌ | ❌ | **2** |
| L4 | Features | ⚠️ | ❌ | ⚠️ | ❌ | ❌ | **2** |
| L5 | Phenomena | ✅ | ✅ | ⚠️ | ✅ | ⚠️ | **4** |
| L6 | State / regime | ✅ | ✅ | ✅ | ✅ | ⚠️ | **4** |
| L7 | Opportunity | ✅ | ✅ | ✅ | ✅ | ⚠️ | **5** |
| L8 | Strategy selection | ✅ | ✅ | ✅ | ✅ | ⚠️ | **4** |
| L9 | Trade intent | ✅ | ✅ | ✅ | ✅ | ⚠️ | **4** |
| L10 | Risk/capital | ✅(A) | ❌(B) | ❌ | ⚠️ | ❌ | **2** |
| L11 | Execution | ✅ | ✅ | ✅ | ❌ | ❌ | **3** |
| L12 | Lifecycle | ✅ | ❌ | ✅ | ❌ | ❌ | **3** |
| L13 | Portfolio | ✅ | ❌ | ✅ | ❌ | ❌ | **3** |
| L14 | Attribution | ✅ | ❌ | ✅ | ❌ | ❌ | **3** |
| L15 | Memory | ✅ | ❌ | ✅ | ❌ | ❌ | **3** |
| L16 | Research/replay | ⚠️×5 | ⚠️ | ✅ | ❌ | ❌ | **3** |
| L17 | Ops/health | ✅ | ⚠️(A) | ❌ | ✅ | ✅ | **3** |

**Nothing scores 6. Nothing above L0 is production-trusted.** The mean is dragged down entirely by L1–L4 — the substrate.

---

## 15. Architectural traps (audited)

| Trap | Present? | Evidence |
|---|:--:|---|
| Duplicate replay engines | **YES** | 5 subsystems |
| Duplicate state machines | No | `position_lifecycle` sole owner; 15M/15Q safety-tested against it |
| Duplicate observation schemas | Partial | 7 packages — **specialisations, not duplicates** |
| Hidden feedback loops | **No** | 15N AST-enforced: memory→decision impossible |
| Derived masquerading as raw | **YES** | `shadow_sessions/*.jsonl` is derived-and-discarded; no substrate beneath |
| Silent data loss | **YES (latent)** | No backpressure accounting |
| Symbol/expiry identity errors | Mitigated | Absolute expiry mandated; bucket = query-time projection |
| Look-ahead / future leakage | **YES (latent)** | No `as_of` discipline |
| Stale data | Partial | Watchdog exists; not tick-level per-symbol |
| Confidence laundering | **YES** | No composition semantics |
| Fabricated values | **No** | Strong: `close_window` returns None on empty; indicators return None on insufficiency |
| Strategy assumptions in shared infra | **No** | 15P confirmed clean separation |
| Portfolio risk blindness | **YES** | L10 absent from Stack B |
| Broker/feed coupling | Partial | `FyersTickFeed` shape leaks; adapter fixes |
| Session/timezone errors | **No** | `core/clock` IST-explicit, drift-guarded |

---

## 16. Dependency graph & gate sequence

```
GATE 1 feed measurement ══════════════════════════════ (LIVE MARKET)
   │
   ├─► 16C  full-fidelity adapter + TickStore + backpressure + feed health
   │      │
   │      └─► GATE 2 capture integrity, zero silent drops ═════ (LIVE)
   │             │
   │             ├─► 16D universe manager (rollover, lifecycle)
   │             ├─► 16E multi-TF engine (watermark from Gate 1)
   │             │      └─► GATE 5 calendar verification
   │             ├─► 16F historical backfill        ‖ parallel
   │             │
   │             └─► GATE 3 replay fidelity
   │                    │
   │                    ├─► 16G feature engine (versioned, uncertainty algebra)
   │                    ├─► 16H derivatives (IV surface, OI series)
   │                    │      └─► GATE 4 derivation trust
   │                    │
   │                    └─► 16I multi-TF state ─► 16J MSI integration
   │                           └─► GATE 6 end-to-end shadow ══ (LIVE)
   │                                  └─► 16K research + tick replay
   │
   └─► [PARALLEL, independent] 16L L10 port: risk/capital Stack A → Stack B
```

---

## 17. Recommended next 5 phases

1. **GATE 1** — feed measurement (blocked on token; needs one non-expiry session)
2. **16C** — full-fidelity adapter + `TickStore` + backpressure + feed health → **GATE 2**
3. **16D + 16E** — universe manager + multi-TF engine with the measured watermark
4. **16F** (parallel) — historical backfill via REST; **GATE 5** calendar verification
5. **16G** — feature engine, with the uncertainty algebra built in from line one

**Deliberately NOT next:** L10 port, portfolio integration, learning promotion, live execution. All depend on a trustworthy substrate.

---

## 18. What should NOT be built yet

More indicators · new strategies · learning feedback · live execution · Parquet/DuckDB tiers (until Gate 1 justifies) · a new observation package · a sixth replay system · 4H bars · watermark constant (until measured)

---

## 19. Long-term roadmap to autonomous desk

```
NOW ──► substrate (16C–16F) ──► intelligence on real data (16G–16J)
    ──► first real shadow lifecycle ──► outcome memory populates
    ──► L10 port: risk/capital into Stack B
    ──► research platform + promotion gates (16K)
    ──► calibration: does stated confidence match realised accuracy?
    ──► controlled learning with explicit promotion boundary
    ──► [SEPARATE AUDITED GATE] live execution
```

**Before real money is ever enabled**, all must exist: kill switch (**0 files today**) · L10 in Stack B · reconciliation for the new core · feed-health gating of decisions · calibration evidence · disaster recovery · full decision lineage.

---

## 20. Answer to the final question

> **"Are we ready to implement the market-data spine, or are there architectural decisions that must be resolved first?"**

**The design is ready. Three decisions are not, and two of them are irreversible.**

| # | Decision | Why it blocks | Recommendation |
|---|---|---|---|
| 1 | **Universe width** ⚠️ IRREVERSIBLE | Un-captured history is unrecoverable; must be set before Gate 1 | **±25 (411)**; Gate 1 also tests full-chain viability (only 37% of cap) |
| 2 | **Stack A vs B** ⚠️ HIGH IMPACT | L10 risk/capital lives entirely in A. Autonomy is impossible without porting it. | **Harvest-and-retire incrementally** |
| 3 | **Canonical replay owner** | 5 systems; parity unverifiable | **`replay_engine`** (15H); others become adapters |

Plus **Gate 1 measurement** for the watermark bound and storage tier — deliberately deferred, correctly.

**Everything else is resolved.** The layer model, storage protocols, time model, uncertainty algebra, lineage requirements, gate sequence, and integration boundaries are internally consistent and do not require further debate before 16C.

**Verdict: resolve the three decisions, run Gate 1, then implement 16C.** The architecture is sound; the substrate is missing; the intelligence above it is genuinely strong and must not be rebuilt.

---

**No production code written, deleted, merged, or refactored. Working tree uncommitted at `b148e39`.**
