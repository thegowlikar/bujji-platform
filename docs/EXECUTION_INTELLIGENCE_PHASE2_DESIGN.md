# Bujji Options OS — Execution Intelligence Phase-2 Design Specification

STATUS: DESIGN ONLY — NOT IMPLEMENTED. Nothing described in this document
exists in the codebase yet except the components explicitly labeled
EXISTING below. This is a forward-looking specification, produced by a
three-round design audit (Market Microstructure / Bid-Ask Intelligence
Audit → Execution Intelligence Round 2 → this Phase-2 Specification),
not a status report.

Before acting on anything in this document, re-verify its claims against
the codebase at the time — in particular the `PaperBroker.get_quote()`
finding below, since broker capabilities may have changed since this was
written.

---

## Verified findings this design depends on (re-check before implementing)

- `bujji/intelligence/liquidity_brain.py` — real, live-verified `LiquidityBrain`
  module. Computes combined straddle spread (points + %), classifies
  TIGHT/WIDE with documented, uncalibrated thresholds
  (`SPREAD_TIGHT_THRESHOLD_PCT = 0.50`, `SPREAD_WIDE_THRESHOLD_PCT = 2.00`).
  Currently has zero callers from Options OS (`production_runtime/`,
  `trading_session_governor/`, `trading_brain/`, `msi_trade_construction/`) —
  its only real caller is the unrelated `bujji.live_shadow_operator` system.
- `Broker.get_quote(contract)` (`bujji/broker/base.py`) — real ABC method,
  returns `Optional[{"bid": float, "ask": float, "spread": float}]`.
  Implemented by `FyersBroker` (real, live) and `HybridPaperBroker`.
  **`PaperBroker` does NOT implement it** — confirmed by direct inspection
  (`paper.py` defines `get_ltp()` only). This means shadow mode has no
  in-process source of real bid/ask today.
- Entry credit/exit debit math in `msi_trade_construction/engine.py`
  (`credit_debit += sign * leg.premium * leg.ratio`) uses a single scalar
  `leg.premium` — the historical/replay chain's own observed premium,
  functioning as an LTP-equivalent — never a bid or ask. This makes every
  current shadow-session P&L figure optimistic relative to a realistic
  bid/ask-based execution, in a structurally quantifiable way (see the
  worked CE/PE credit example in the audit trail).
- No Knowledge Graph, execution-memory, or "trade memory" module exists
  anywhere in the codebase — confirmed by grep and independently
  corroborated by `AdaptiveRiskMemory`'s (D.5) own author, who documented
  having searched exhaustively for exactly this before building it.
- `ReplayChainProvider` (Phase-1's runner) has no bid/ask fields available
  at all — real NSE bhavcopy data carries none.

---

## 1. Architecture

```
Market Data Provider            (existing Phase-1 abstraction — the ONLY place a quote source is legitimate)
        │
        ▼
LiquidityBrain                  (existing, bujji/intelligence/liquidity_brain.py — EXTENDED, not replaced)
        │
        ▼
LiquidityGate                   (NEW — the sole subject of Part 1 below)
        │
        ▼
MSI Trade Construction  /  TradingSessionGovernor   (existing — receive verdicts, own no liquidity logic)
        │
        ▼
Execution Layer                 (existing F.4 TradeLifecycleExecutor — unchanged)
        │
        ▼
ExecutionQualityJournal         (NEW — Part 4)
        │
        ▼
Shadow Observatory              (existing — records verdicts + journal entries as forensic artifacts)
```

Everything downstream of `LiquidityGate` is existing, unmodified
architecture. The only two new components in this entire specification
are `LiquidityGate` and `ExecutionQualityJournal` — both consumers/bridges,
neither a new "engine."

---

## 2. Responsibilities

### Part 1 — LiquidityGate Detailed Design

**Owns:**
- Interpreting `LiquidityBrain`'s spread analysis into a yes/no/reasoning
  gate decision at three specific moments (entry, mid-position, pre-exit).
- Tracking the *change* in liquidity condition across a position's life
  (stateful — something `LiquidityBrain` itself deliberately is not).
- Producing an execution-cost *estimate* (a number), never an
  execution-cost *guarantee*.

**Does NOT own:**
- Strategy calculation (`strategy_selector.py`).
- Strike selection (`msi_trade_construction/engine.py`).
- Risk calculation (D.1–D.6).
- Exit decisions (`ExitPolicy`/D.4). `LiquidityGate` is a new *input* to
  their existing decisions, never a replacement authority — mirroring how
  `ExitPolicy` itself was designed relative to D.4.

### Public Interface Design (conceptual only)

**`can_enter_trade(legs: Sequence[LegQuote]) -> LiquidityGateDecision`**
- Input: per-leg quote data (Part 3) for every leg the strategy is about
  to construct.
- Output: `LiquidityGateDecision { allowed: bool, reasoning: str,
  per_leg_readings: ..., data_quality: ... }`.
- Decision meaning: is the combined/per-leg spread within tolerance for a
  NEW entry. Advisory to `MSI Trade Construction`/`TradingSessionGovernor`.
- Failure behavior: fails closed on missing/invalid quote data — mirrors
  `LiquidityBrain.analyze()`'s own `_unknown(...)` pattern. A gate that
  can't evaluate liquidity must never silently default to "allowed."

**`evaluate_liquidity_health(position_group_id, current_legs) -> LiquidityHealthReading`**
- Input: the position's current per-leg quotes, plus (internally) the
  gate's own memory of the entry-time reading for that same group.
- Output: `LiquidityHealthReading { deteriorated: bool, delta_from_entry: ...,
  reasoning: str }` — a *comparison*, not a fresh classification. This is
  the one genuinely new logic `LiquidityBrain` doesn't have today (it's
  stateless).
- Decision meaning: feeds D.4's existing escalation path as one more input
  signal — never a standalone trigger.
- Failure behavior: if no entry-time baseline was recorded, return an
  explicit "no baseline" state — never fabricate a comparison against
  nothing.

**`estimate_exit_cost(position_group_id, current_legs) -> ExitCostEstimate`**
- Input: current quotes for the position's legs.
- Output: `ExitCostEstimate { expected_cost_points: float,
  spread_contribution: float, confidence: str }` — reuses the "never
  present a number without its uncertainty" discipline already
  established in `msi_performance_analytics`.
- Decision meaning: informational, attached to the exit's forensic
  record — `ExitPolicy` may read it for context, but it can never gate or
  delay the exit itself.
- Failure behavior: if quotes are unavailable at exit time, return a
  `confidence: "NONE"` estimate rather than blocking — the exit proceeds
  regardless.

---

## Part 2 — Integration Boundary

### MSI Trade Construction — (B) Supplement, not replace or wrap
The existing OI-proxy check (`_liquidity_ok()`) stays exactly as-is — real,
tested, and an honest, disclosed proxy for the case where real bid/ask
isn't available (which, given the `PaperBroker.get_quote()` finding above,
is *every* shadow-mode session today). `LiquidityGate.can_enter_trade()`
runs as an additional, parallel check when real quote data exists.

### TradingSessionGovernor — (B) Receive a liquidity verdict as input
Never (A) — the Governor calling `LiquidityGate` directly would make it a
second decision-maker beyond its documented session-discipline-only scope.
Never (C) — the Governor must be aware enough to route the verdict to
F.4/Observatory. Matches exactly how the Governor already consumes D.4's
`RiskActionRecommendation` and `ExitPolicy`'s `ExitPolicyDecision`.

### RiskGovernor D.1–D.6 — No, deliberately
D.1–D.6 explicitly, repeatedly establish that they never model Greeks, IV,
or liquidity — a consistent, load-bearing architectural choice across the
whole Risk Governor, not an oversight. `LiquidityGate`'s output belongs in
the Governor's *composition*, sitting alongside D.1–D.6's verdict, never
inside it.

### ExitPolicy — liquidity informs, never gates, exits
```
Mandatory exit conditions fire (profit target / max loss / EOD time)
                    │
                    ▼
        ExitPolicy decision is made — UNCONDITIONALLY
                    │
                    ▼
        LiquidityGate.estimate_exit_cost() is called
        (informational only — attached to the forensic record)
                    │
                    ▼
        TradeLifecycleExecutor.execute() proceeds regardless
```
The single hardest, most important rule in this specification, enforced
structurally: `estimate_exit_cost()`'s return type carries no
`allowed`/`blocked` field at all — nothing a future caller *could* wire
into a block, even by mistake. Contrast with `can_enter_trade()`, whose
`allowed: bool` exists specifically because blocking a *new* entry is safe
and blocking an *existing* exit is not.

---

## Part 3 — Per-Leg Spread Intelligence

```
LegQuote:
    symbol: str
    strike: float
    option_type: str        # CE/PE
    bid: Optional[float]
    ask: Optional[float]
    mid: Optional[float]               # derived, (bid+ask)/2
    absolute_spread: Optional[float]   # derived, ask - bid
    spread_percentage: Optional[float] # derived, absolute_spread / mid
```
A straightforward generalization of `LiquidityBrain`'s existing
straddle-shaped inputs (`ce_bid, ce_ask, pe_bid, pe_ask`) into a per-leg
list.

**Combining four legs — tradeoffs, not a blind choice:**

| Approach | Captures | Misses |
|---|---|---|
| Worst-leg model | Directly answers "one bad wing hiding behind three good legs" — safest default for gating. | Can reject an otherwise-fine trade over one thin OTM wing that barely affects overall economics. |
| Weighted spread model | More economically honest — a wide spread on a small-premium wing matters less than on the body. | Requires a weighting formula this codebase has zero live data to justify yet. |
| Portfolio spread score | Simplest to log/trend. | Can hide which specific leg is the problem — the most actionable piece of information. |

**Recommendation, not a final choice**: worst-leg for the *gating* decision
(safety-first, cheap, interpretable); per-leg breakdown (not collapsed) for
the *journal* record (preserves which leg drove the outcome, for
calibration). Never collapse to a single score where data is being
recorded for future analysis.

---

## Part 4 — Execution Quality Journal

### Purpose
A forensic, append-only record of predicted vs. actual execution
outcomes — distinct in domain from `AdaptiveRiskMemory` (strategy/regime/
sizing/outcome) and from a general Knowledge Graph (neither exists).

### Data captured

**At entry:**
```
timestamp, symbol, expected_price (leg.premium LTP-equivalent), bid, ask,
spread, expected_credit, actual_fill, slippage (actual_fill - expected_price)
```

**At exit:**
```
exit_signal (which condition fired), expected_exit_price, actual_exit_price,
spread_at_exit, execution_cost (actual_exit_price - expected_exit_price,
signed consistently with the entry side's own convention)
```

### Storage philosophy
Append-only, immutable entries, plain in-memory list backed by disk — same
shape as `TradeConstructionJournal`/`AdaptiveRiskMemory`/
`MarginCalibrationStore` (no update/delete method, ever). Written by
`TradeLifecycleExecutor` at the moment a real fill occurs; read only by
`Shadow Observatory` and Part 5's offline analytics — never by the live
decision path.

---

## Part 5 — Execution Analytics

A deliberately separate domain from trading-performance analytics — this
asks only "did we pay more to enter/exit than the decision assumed."
Belongs here: average entry/exit slippage (with confidence, per
`msi_performance_analytics`'s `MetricEstimate` discipline), spread cost per
strategy family, spread cost by expiry, spread cost by VIX/volatility
regime, worst-liquidity-event log.

**Does NOT belong here**: win rate, realized P&L, strategy edge
validation — those remain owned by `msi_performance_analytics`/
`AdaptiveRiskMemory`. An execution-quality report should be meaningful even
for a strategy that lost money for unrelated reasons.

---

## Part 6 — Shadow Mode Requirements

Given the confirmed `PaperBroker.get_quote()` gap, shadow mode cannot
collect real bid/ask through the broker layer today. `ReplayChainProvider`
has no bid/ask fields at all.

**Minimum dataset required before any calibration work begins:**
- A new, live-data-backed `MarketDataProvider` implementation capturing
  real `get_quote()` snapshots against `FyersBroker` specifically —
  genuinely new integration work, not something Phase-1's existing
  providers can retroactively supply.
- Per option leg: `quote snapshot` + `decision` + `expected_fill` +
  `actual_simulated_fill` — the four-part bundle `ExecutionQualityJournal`
  (Part 4) is designed to hold.
- Coverage across at minimum: normal days, near-expiry days, at least one
  high-VIX session, both ATM and OTM legs.

Until that data exists, Phase C (entry gating, Part 8) cannot be
responsibly turned on.

---

## Part 7 — Calibration Framework (methodology only, no thresholds)

1. **Collect first, bucket second**: every recorded entry/exit in
   `ExecutionQualityJournal` is naturally taggable by (days-to-expiry
   bucket, VIX-regime bucket, ATM/OTM distance bucket, strategy_family) —
   all knowable at record time without new instrumentation.
2. **Threshold emerges from distribution, not declaration**: "tight,"
   "acceptable," "dangerous" should be percentile bands *within* each
   bucket, never a single absolute number applied uniformly, and never
   invented before the distribution exists.
3. **Confidence gating**: no bucket's threshold should be trusted until it
   has a minimum sample size — a bucket with 3 observations reports
   `CONFIDENCE_LOW`/`NONE`, not false precision.
4. **Recalibration is scheduled and reviewed**, not automatic — matching
   D.5/D.6's "memory, not prediction" philosophy.

---

## Part 8 — Implementation Roadmap

| Phase | Components touched | Risk | Validation required |
|---|---|---|---|
| **A — No-risk observation only** | New `MarketDataProvider` capturing real `get_quote()` snapshots; `ExecutionQualityJournal` writes (entry/exit only, no gating); `LiquidityGate` exists but every call is logged, never consulted for a decision. | Minimal — purely additive, zero behavior change. | Confirm journal entries are complete and accurate against manually cross-checked real quotes. |
| **B — Liquidity visibility** | `LiquidityBrain` extended to per-leg; Shadow Observatory records `LiquidityGate` verdicts, still non-blocking. | Low — zero effect on trading outcomes, but retrospective review becomes possible. | Compare a sample of retrospective verdicts against human judgment. |
| **C — Entry gating** | `MSI Trade Construction`/`TradingSessionGovernor.attempt_entry()` consult `can_enter_trade()`'s `allowed` field. | Moderate — first phase that can change real (shadow) trading behavior. | Requires Part 6's minimum dataset AND Part 7's per-bucket confidence at least `MODERATE`. Never enable uniformly across all buckets at once. |
| **D — Live execution intelligence** | `estimate_exit_cost()` informs real operator/desk review; full `ExecutionQualityJournal` analytics feed strategy-selection review (human-reviewed, not automated feedback). | Highest — only relevant once live money is discussed at all, out of scope for everything built this session. | Full Category C requirements from every prior audit (live broker wiring, MIC, restart recovery) must independently clear first. |

Never skip a phase. Phase C's gating logic must not go live against a
bucket Phase A/B never actually observed data for; the fail-closed default
makes an unobserved bucket "unknown, no verdict," but rollout should still
be deliberate and phased, not blanket.

---

## Part 9 — Final Architecture Diagram

```
Market Data Provider                    [NEW concrete impl needed for real quotes — Phase A]
        │
        ▼
LiquidityBrain                          [EXISTING — bujji/intelligence/liquidity_brain.py, EXTENDED to per-leg]
        │
        ▼
LiquidityGate                           [NEW — Part 1]
        │
        ├──────────────► MSI Trade Construction        [EXISTING, UNCHANGED — OI proxy stays, gate supplements]
        │
        ▼
TradingSessionGovernor                  [EXISTING, UNCHANGED — receives verdict as input, decides nothing new]
        │
        ▼
Execution Layer (F.4 TradeLifecycleExecutor)   [EXISTING, UNCHANGED]
        │
        ▼
ExecutionQualityJournal                 [NEW — Part 4]
        │
        ▼
Shadow Observatory                      [EXISTING, UNCHANGED — records verdicts + journal entries]
```

**Unchanged throughout this entire specification**: `TradingSessionGovernor`'s
own logic, `RiskGovernor` D.1–D.6, `ExitPolicy`'s own decision logic,
`PaperBroker`, `EventBus`, `TradeLifecycleExecutor`'s execution mechanics,
`AdaptiveRiskMemory`, `msi_performance_analytics`. The entire footprint of
this specification is two new components (`LiquidityGate`,
`ExecutionQualityJournal`) and one extension (`LiquidityBrain` → per-leg).

---

## Non-negotiable design rule to carry into any future implementation

**Liquidity intelligence may block a NEW entry. It must NEVER block or
delay an already-approved EXIT** — enforced structurally in `LiquidityGate`'s
own interface design (`estimate_exit_cost()` has no `allowed`/`blocked`
field to wire into a block, even by mistake), not merely by convention.
