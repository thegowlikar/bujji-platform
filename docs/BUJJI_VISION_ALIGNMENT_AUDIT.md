# Bujji Vision Alignment Audit — Autonomous Quant Options Trading OS

Read-only. No code written. Every claim verified against the real codebase
on the VPS (`/opt/bujji/app`, 116,049 lines across 148 packages) — either
in this audit's own checks or in the audits of the preceding phases, all
of which are preserved in `docs/`.

---

## THE ANSWER FIRST

**"If we switched Bujji on during a live NSE session tomorrow (PaperBroker
only), would it behave like an autonomous quant trading system?"**

**No — but for exactly two reasons, and neither is intelligence, learning
machinery, execution, or architecture.**

1. **The trading runner has no live eyes.** `bujji_options_os_runner.py`
   — the ONE entrypoint that reaches a real fill, real risk approval, a
   real exit, and a real `OutcomeMemoryRecord` — constructs
   `ReplayChainProvider` (a historical bhavcopy file) at line 309 and
   `HumanSuppliedRegimeProvider` (a human types the regime) at line 312.
   Both live-capable replacements ALREADY EXIST and are tested:
   `StoreChainProvider`/`LiveTickProvider` for data, and
   `MarketThesisRegimeProvider` for regime. They are simply not the ones
   the runner constructs.

2. **Nobody wakes it up.** Two systemd timers fire every trading morning
   — both for observation-only systems. The trading runner has **no
   systemd unit at all**. Switched on "tomorrow," Bujji observes the
   market beautifully and trades nothing, because nothing starts the
   trader.

Everything else in the loop — decide → construct → risk-check → execute
→ manage → exit → attribute → remember — runs end to end today and is
proven by executed tests, not by design documents. **The missing piece is
wiring and scheduling, category B, not category D.** That is an unusual
and genuinely good place to be: the expensive parts exist; the cheap
parts are what's missing.

---

## 1. MARKET DATA ACQUISITION — Score: 78/100

**Exists and production-real:** `FyersBroker` with spot/VIX/option-chain/
futures/candles (live-verified across multiple phases); a websocket
client (`bujji/broker/fyers_ws.py`); `capture_market_reality_session.py`
and `capture_options_reality_session.py` running daily via an enabled
timer — the options capture proved itself with 162,150 five-minute option
rows (ltp/bid/ask/volume/OI/OI-change) across 2,190 instruments in one
session. Greeks, IV, premium behaviour, and liquidity are computed
downstream by dedicated engines from this feed.

**Gaps:** tick-by-tick is really 5-minute cadence (the ws client exists;
the capture pipeline is candle-shaped); market **depth** beyond
top-of-book is not captured; **order flow** is not captured at all
(FYERS retail API largely cannot supply it — a data-vendor constraint,
not a Bujji defect). Loss-awareness exists (freshness/completeness
gates, tick-silence watchdog) rather than loss-proofing.

**The one that matters:** the acquisition infrastructure feeds the
*observation* systems on a timer every morning. It feeds the *trading*
runner not at all — see The Answer First.

## 2. STORAGE AND MEMORY — Score: 85/100

The strongest layer in the whole system. `HistoricalObservationStore`
(SQLite, 670k rows, typed identities, resolutions, quality metadata);
event-sourced stores for intelligence cycles, decisions, positions;
`DecisionArtifactJournal` (append-only, replayable); canonical
`PositionLifecycle` is event-sourced with a single reducer shared by
live and replay ("replay can never diverge from live behavior" — its own
docstring, and tested). Historical replay is not aspirational: LIVE vs
HISTORICAL_REPLAY equality is an executed test.

Can Bujji compare present to past? The machinery exists **twice** —
`market_memory` (Phase 19.5, weighted-dimension similarity) and
`market_understanding/memory_similarity` — and here is the honest
finding: **no decision path calls either.** Bujji remembers everything
and recalls nothing. Classified B, and doubly so: two recall engines,
zero callers.

**One disclosed operational wart:** reading one day's option chain from
the store costs ~950MB peak RSS (the store hydrates all matching rows).
Documented in `StoreChainProvider`'s own docstring; fine at one load per
session, and the store-level streaming fix is scoped future work.

## 3. OBSERVATION → UNDERSTANDING — Score: 80/100

Is Bujji seeing the market or just calculating indicators? **Genuinely
seeing, by any fair reading.** Regime, structure, volatility state,
liquidity state, positioning (MPPI), premium behaviour, expected-move —
all real engines with vocabularies, confidence, invalidation conditions,
and evidence lineage; contradiction between domains is explicitly scored
(`ContradictionScore`) rather than averaged away; degradation is honest
(UNKNOWN, never a fabricated reading). ~708 real cycles of composed
intelligence have been recorded and forensically re-analyzed in prior
phases. Institutional-behaviour inference is the thinnest member (OI/
positioning clues exist; futures-positioning work remains a design doc).

The deduction is not quality but **plurality** — see §4.

## 4. INTELLIGENCE ENGINE — Score: 72/100

All real, all tested: MDI, PSI, MSSI, VSB, MPPI, Consensus, Liquidity,
`market_thesis`, `MarketIntelligenceSnapshot`.

**Canonical (already decided in prior phases, verified still true):**
- Thesis: **`market_thesis.assess()`** — a strict superset that calls
  `msi_trade_thesis.derive_trade_thesis()` internally. One thesis.
- Evidence: **`IntelligenceCycleRecorder.record_cycle()` → `CycleEvidence`**
  — builds every MSI object in one call.
- `MarketIntelligenceSnapshot` is **perception, not decision** — built
  from a different brain lineage (zero MSI imports, proven by trace),
  attached to `DecisionArtifact` as disclosed `perception_*` fields,
  never merged. That is the correct architecture, already implemented.

**The real problem — three intelligence lineages still run in parallel:**

| Lineage | Runs when | Feeds a trade? |
|---|---|---|
| MSI (MDI/PSI/…/market_thesis) | manual scripts only | Could — via `MarketThesisRegimeProvider`, built and tested, **not wired into the runner** |
| Heartbeat (`live_intelligence_cycle`, brain-lineage) | daily timer, 09:00 | Never (unit forbids trading) |
| Cycle-1 (`mic_v0`/`strategy_intelligence`) | daily timer, 09:10 | Never (shadow-only by design) |

The two lineages that run every morning cannot trade; the one that can
inform trading doesn't run. Intelligence is not flowing into decisions —
not because the pipe is missing, but because the pipe is not attached at
either end.

## 5. DECISION ENGINE — Score: 68/100

"Should I trade today?" — **genuinely decidable and refusable.** The
governor's selector returns NO_TRADE on unfavourable regimes (executed-
test-proven), the risk pipeline vetoes independently, and one-strategy-
per-day discipline is enforced by a real lock.

**The graded-conviction vocabulary (NO_TRADE / WATCH / SMALL / NORMAL /
HIGH_CONVICTION) exists** — `msi_decision_synthesis/taxonomy.py` defines
exactly these states — **but it is computed-and-logged, never acted on.**
Actual sizing is a config constant (`desired_quantity: 1`). Conviction-
scaled sizing is category B: vocabulary real, consumer absent.

Selector ownership (decided in the Phase 7 audit, still correct):
**TradingSessionGovernor's IRON_CONDOR/IRON_FLY selector is the one
authoritative decider** — deliberately narrow, wrapped in session
discipline. Trading Brain v3's 11-strategy registry with its own proven
order path is **frozen/research** until v1 has operating history. MSI's
13-family selector + SSF suitability remain upstream evidence. "Explain
why / reject unsuitable" is genuinely strong — every decision carries
reasons, rejected alternatives, and evidence IDs in a `DecisionArtifact`.

## 6. TRADE CONSTRUCTION — Score: 88/100

The most complete stage. `msi_trade_construction`: real expiry selection
(DTE 1–45, fail-closed), delta-targeted strikes with ATM anchors,
correct 4-leg IRON_CONDOR/IRON_FLY and 2-leg straddle structures
(leg-count/role verified), real lot size from the instrument master,
margin/capital checked by the risk governor BEFORE order construction,
Greeks carried per leg. Weakest member: margin is deliberately
conservative per-leg blocking (no spread-netting credit — over-blocks,
never under-blocks; the real SPAN-shaped provider exists but is
uncertified and correctly barred from production).

## 7. EXECUTION ENGINE — Score: 87/100

The chain Decision → Intent → Order → PaperBroker → Fill → Position runs
end to end today. After PaperBroker v2: bid/ask-crossing fills (BUY
lifts ask, SELL hits bid — a round trip across the spread now loses real
money), NORMAL-profile slippage on the production path, depth-scaled
impact, insufficient-margin rejection (exposure-increasing orders only —
exits are never blocked), quantity-weighted cost basis, honest
cancellation, idempotency, token-expiry and rejection injection, crash
persistence. Disclosed non-behaviours: no EXCHANGE_PENDING/EXPIRED
states (fills resolve synchronously), no `modify_order` (nothing
modifies; exits reduce), latency recorded but not enacted.

## 8. POSITION MANAGEMENT — Score: 60/100

Monitoring is real: per-cycle revaluation, thesis re-evaluation, D.4
recommendations, hard-limit enforcement, mandatory EOD exit — and after
the tick feed, revaluation is against **real moving prices**
(130.2 → 119.05 → 160.5 proven from capture), so stops and targets can
actually fire.

**Hold ✅ · Exit ✅ · Adjust ⚠️ (only REDUCE_SIZE executes) · Hedge ⚠️
(executor path real, governor never invokes it) · Roll ❌ (assessment
exists, no execution path at all).** Verified in the Phase 7 safety
audit and unchanged. Cadence is per-management-pass, not per-tick — the
Phase-1 runner performs few passes per session; the loop must densify
for intraday management to mean anything.

## 9. RISK MANAGEMENT — Score: 82/100

The best-engineered subsystem relative to its ambition. Independent
CAPITAL → PORTFOLIO → BUDGET → ADMISSION stages that gate order
construction (not log-and-proceed); daily-loss utilization thresholds in
`capital_safety_governor` (a −600k daily P&L blocks entry — executed
test); concentration limits; per-leg fill-monotonicity reconciliation;
crash recovery of orphaned orders; the empty-book inversion (safest
state classified riskiest) found and fixed with every fail-closed path
proven intact. Missing: a formal kill switch (mandatory-exit + oneshot
units + ProcessLock approximate one; no single emergency-stop control),
and intraday margin re-checks after entry.

## 10. LEARNING ENGINE — Score: 45/100 — **the vision's weakest limb**

**Recording: nearly complete.** Every closed trade now yields an
`OutcomeMemoryRecord` with regime, thesis, strategy, entry/exit, P&L,
outcome direction — and, since the tick feed, MFE/MAE from real
valuation history (the two fields that say how a trade *behaved*, not
just how it ended). `DecisionArtifact` separately preserves why-entered,
what-was-rejected, and full narration.

**Still missing from the record:** rejected-strategy reasoning is never
JOINED into the outcome record (both halves exist, unlinked); no
mistake-classification vocabulary exists anywhere; no
expected-vs-actual comparison is stored.

**The verdict that matters:** **the loop records; it does not yet
learn.** Verified today: no decision-path module reads outcome memory
back. `shadow_lifecycle`'s own docstring enshrines it: "WRITE-ONLY …
no feedback path into any decision can exist." That was the correct
safety posture while evidence quality was unproven. It is now the last
uncrossed bridge of the original vision — and crossing it is a
**decision about evidence sufficiency, not an engineering task**: the
Constitution says learning from outcomes only after evidence, and 30
sessions of trustworthy records is precisely the evidence bar the
Deployment Blueprint already set.

---

## 11. AUTONOMY SCORES

| Dimension | Score | One-line reason |
|---|---|---|
| A. Market Awareness | 80 | Real multi-domain understanding, honest uncertainty; runs on schedule |
| B. Data Collection | 78 | Full chain+ticks captured daily; depth/order-flow absent (vendor-bound) |
| C. Data Memory | 85 | Event-sourced, replayable, proven store; recall engines uncalled |
| D. Intelligence | 72 | Excellent engines; three parallel lineages, canonical one not scheduled |
| E. Decision Making | 68 | Real refusal + explanation; conviction sizing logged, never acted on |
| F. Execution | 87 | Realistic fills, margin, failure injection; loop closed and proven |
| G. Position Management | 60 | Monitor/exit real with real prices; adjust partial, hedge unreached, roll absent |
| H. Risk Control | 82 | Multi-stage vetoes that actually gate; no formal kill switch |
| I. Learning Capability | 45 | Records almost everything; reads back nothing (by explicit design) |
| **J. Full Autonomous Operation** | **35** | Every organ works; the organism is not switched on |

J is the honest number: it measures the *organism*, and today the
organism requires a human to type a regime, point at a bhavcopy, and
start the process by hand.

---

## 12. CURRENT vs TARGET

**CURRENT — three parallel nervous systems, the trading spine manual:**

```
09:00 timer → daily-intelligence (heartbeat lineage)   → artifacts. No trade. ─┐
09:10 timer → shadow-decision-campaign (Cycle-1)       → artifacts. No trade. ─┼─ nothing reads
MANUAL      → bujji_options_os_runner                                          ─┘  each other
              bhavcopy (HISTORICAL) + human-typed regime
              → governor → risk → PaperBroker → manage → exit
              → OutcomeMemoryRecord ✅ (since this engagement)
              → ...which nothing reads back.
```

**TARGET — one spine, one loop:**

```
09:15 systemd → bujji-paper-trading.service (oneshot + lock + calendar gate)
  LiveTick/StoreChain provider ──► IntelligenceCycleRecorder ──► CycleEvidence
                                                                    │
  MarketIntelligenceSnapshot (perception_* cross-ref, never merged) │
                                                                    ▼
                     market_thesis.assess() ──► MarketThesisRegimeProvider
                                                                    ▼
     TradingSessionGovernor: select+lock ─► risk pipeline ─► construct
                                                                    ▼
     PaperBroker v2 (spread/slippage/depth/margin) ─► positions ─► manage/exit
                                                                    ▼
     lifecycle_outcome_bridge ─► attribution (MFE/MAE) ─► OutcomeMemoryRecord
                                                                    ▼
                        [Phase 2, after 30 sessions] read-back into
                        selection confidence & conviction sizing
```

Every box in the target exists today. Every arrow inside the runner
exists today. The two dashed arrows — live providers into the runner,
and a timer above it — are the whole remaining build.

---

## 13. GAP CLASSIFICATION (organism-level)

**A — complete and connected:** execution chain; risk gating; trade
construction; outcome recording incl. MFE/MAE; decision artifacts;
storage/replay; observation capture; crash persistence (broker-level).

**B — exists, not connected (the decisive category):**
1. Live data providers → trading runner (both providers built + tested)
2. `MarketThesisRegimeProvider` → runner (replaces the human)
3. systemd unit for the trading runner (three proven templates exist)
4. Conviction vocabulary → position sizing
5. Outcome memory → any decision input (write-only by explicit design)
6. Market-memory similarity → any caller (two engines, zero callers)
7. Hedge execution (real path, governor never calls it)
8. `hydrate_paper_broker` runtime wiring (restart recovery, zero real callers)
9. Trading Brain v3 selector + `run_shadow` order path (frozen/research)

**C — partial:** management cadence (passes, not ticks); adjust
(REDUCE_SIZE only); margin realism (conservative per-leg); learning
record (rejected-reasons unjoined; no mistake taxonomy); EXCHANGE_PENDING/
EXPIRED absent; store memory profile (~950MB/day-chain, disclosed).

**D — genuinely missing:** roll execution; kill switch as a formal
control; order-flow + depth data (vendor-constrained); mistake
classification vocabulary; any self-improvement mechanism (correctly
absent until the read-back decision is made).

**Duplicates/competing brains (dispositions already decided in prior
phases — enforce, don't re-litigate):** 3 intelligence lineages (MSI =
canonical for trading; heartbeat = health/observation; Cycle-1 = frozen
observation, own history); 2 thesis paths (market_thesis canonical,
derive_trade_thesis its internal engine); 4 selectors (governor's =
authoritative; SSF+MSI = upstream evidence; TB-v3 = frozen); 2 position
lifecycles (`bujji.position_lifecycle` canonical for outcomes — bridged
this engagement; `PositionLifecycleRuntime` = risk bookkeeping only);
2 memory-similarity engines (pick one when wiring recall — the OTHER is
the rare candidate for actual removal); 3 ad-hoc contract resolvers
beside the real `InstrumentMaster` (converge opportunistically).

**Dead paths:** `ShadowLifecycleOrchestrator` (superseded by the bridge;
its chain proved the pattern — freeze); `run_shadow_live_observatory.py`
(self-declared deprecated); PaperBroker's synthetic `resolve_atm_contract`
(expiry = literal `"WEEKLY"` — replace with InstrumentMaster when next
touched).

---

## 14. THE FINAL BUJJI ARCHITECTURE (one canonical flow)

One perception layer (`MarketIntelligenceSnapshot`, cross-referenced,
never merged). One evidence layer (`CycleEvidence` via the recorder).
One thesis (`market_thesis`). One regime translation
(`MarketThesisRegimeProvider`). One decision authority
(`TradingSessionGovernor` + its selector + the risk pipeline). One
execution surface (`PaperBroker` v2 behind the broker guard). One
outcome path (canonical lifecycle → attribution → memory). One scheduler
(systemd oneshot + lock + calendar). Everything else is observation
(runs, never trades), frozen research (TB-v3), or deprecated (named
above). **No new modules are required to reach this — the Constitution's
"wire, don't rebuild" holds all the way to the finish line.**

---

## 15. MINIMUM ROADMAP

### → "Bujji Paper Trading Autonomous Mode"
1. **Wire live providers into the runner** (config-selected:
   `StoreChainProvider`/`LiveTickProvider` + `MarketThesisRegimeProvider`;
   the runner's provider pattern was built for exactly this swap). The
   only step needing a genuinely new adapter: a live FYERS chain-to-
   provider shim, patterned on what `run_shadow_live_observatory.py`
   already does.
2. **Write + enable `bujji-paper-trading.{service,timer}`** (09:15 IST,
   oneshot, own lock, calendar gate — third instance of a proven
   template; also fix task #165's 09:00→09:15 timer while touching
   systemd).
3. **Densify the management loop** (management passes every N minutes,
   market-hours-bounded — cadence change inside the runner, not new
   machinery).
4. **Wire restart recovery** (`hydrate_paper_broker` at startup — built,
   tested, zero callers).
5. **Run Phase A: 5 sessions, stability only** — uptime, artifact
   completeness, `cycles_priced_from_ticks`, zero unrecovered crashes.
   No strategy judgment. (Operational prerequisite, rediscovered
   repeatedly: a fresh FYERS token each morning — SEBI-bound, human.)

### → "Bujji Live Trading Ready Mode" (gate, not a step)
6. **Phase B: 25 sessions of evidence** — win rate, expectancy, thesis
   accuracy, MFE/MAE distributions. No parameter changes mid-flight.
7. **Close C-items that become load-bearing with real capital:** kill
   switch as a formal control; intraday margin re-checks; hedge
   invocation; then roll execution.
8. **The learning decision** — after Phase B, deliberately open the
   write-only boundary: outcome memory → selection confidence and
   conviction→sizing first (bounded, explainable, no self-modifying
   strategy code — Constitution-compatible). This is the moment Bujji
   goes from *remembering* to *learning*, and it should be its own
   reviewed phase, never a side effect.
9. **Independent re-audit** before any capital-mode flag exists — per
   the Deployment Blueprint's own success criteria, by a fresh pair of
   eyes, not the lineage that built it.

---

## 16. CLOSING JUDGMENT

The original vision names seven verbs: **observe, understand, decide,
execute, manage, remember, improve.** Today: observe ✅ (scheduled),
understand ✅ (real, honest, plural), decide ✅ (proven, including
refusal), execute ✅ (realistic and loop-closed), manage ⚠️ (real but
sparse and hold/exit-shaped), remember ✅ (complete and replayable),
**improve ❌ (by deliberate, documented choice — not by absence of
ability).**

Bujji is not a collection of trading tools that fails to be an organism.
It is an organism, built organ-by-organ with unusual discipline, whose
organs have never all been switched on in the same body on the same day.
The distance from here to "Paper Trading Autonomous Mode" is the
shortest it has ever been, and — for the first time in this project's
history — none of it is intelligence work.
