# Phase 20.10 — Decision Orchestration Intelligence Layer

**Bujji Trading Intelligence Roadmap v1.2, Cycle 1.**
**Question answered: "Given everything Bujji knows, what is the final intelligence decision?"** Not execution — `EXECUTABLE_CANDIDATE` means *"worth further downstream review,"* never *"execute."* No trade signal, order, broker call, position creation, quantity calculation, stop-loss logic, or entry timing exists anywhere in this phase's code.

---

## 1. Naming collision check (this phase's own explicit Step 1 instruction) and repository audit

The three explicitly-named candidate paths were checked directly:

| Path | Exists? |
|---|---|
| `bujji/decision_intelligence/` | **Yes** — Phase 19.6, this engagement |
| `bujji/decision_engine/` | No |
| `bujji/intelligence_orchestrator/` | No |
| `bujji/decision_orchestration/` (the literal requested path) | No — no collision on the exact name |

**No collision on the literal package name, but a close and important one exists: `bujji.decision_intelligence`.** It is *also* a pure composer (its own docstring: *"the one entry point... composition over already-built objects from every prior layer"*), but over a completely different, older lineage: `MarketRealitySnapshot → MarketIntelligenceSnapshot (19.3) → DecisionContext (19.4) → MarketMemoryEntry matches (19.5) → DecisionIntelligenceSnapshot` — the pre-MIC-v0, options-domain-adjacent Phase 19 Intelligence Foundation, never Cycle-1's own MIC v0 / `strategy_research` / `opportunity_*` / `capital_intelligence` chain. `bujji.shadow_runtime.intelligence_pipeline_adapter` (Phase 19.10.1) is a *second* composer over that same older lineage. Disclosed here per the phase's own instruction, even though the exact requested path was free to use.

| Component | Classification | Disposition |
|---|---|---|
| `bujji.decision_intelligence`, `bujji.shadow_runtime.intelligence_pipeline_adapter` | **C) Wrong domain** | Both real composers, both over the older Phase 19 lineage. Not reused as code; both independently confirm the "pure composition, never recalculate" pattern this phase also follows. |
| `bujji.core.orchestrator` | **D) Legacy** | Already established (Phase 20.4/20.6/20.9 audits) as the disabled ORB-VWAP legacy bot's orchestrator. Not reused. |
| `bujji.trading_brain.risk_governor.*` "recommendation" classes | **Out of scope** | Already classified wrong-domain (real capital/margin) in Phase 20.8's own audit; not applicable here either. |
| `bujji.capital_intelligence.AllocationAssessment` (20.8), `bujji.opportunity_portfolio.PortfolioDecision` (20.9) | **A) Reusable directly** | The entire input surface of this phase — read transitively, never recalculated. |
| `bujji.mic_v0.models.EVENT_CONTEXT_NOT_AVAILABLE` (20.1) | **A) Reusable directly** | The one MIC input (`event_context`) no upstream Cycle-1 layer currently carries — reused verbatim as the honest default, always surfaced under "Unknown." |

## 2. Architecture

```
bujji/decision_orchestration/
    __init__.py
    models.py    -- DecisionState vocabulary, FinalDecision
    engine.py     -- compose_decision()
    explain.py    -- explain_decision()
```

Four files, ~200 lines total.

```
MIC v0 (20.1) → Strategy Intelligence (20.5) → Opportunity Qualification (20.6) → Opportunity Ranking (20.7)
    → Capital Intelligence (20.8) → Portfolio Intelligence (20.9) → Decision Orchestration (20.10, this phase)
```

`compose_decision(allocation, portfolio_decision, event_context=EVENT_CONTEXT_NOT_AVAILABLE)` — one function, one strategy at a time, reading Phase 20.8's `AllocationAssessment` (itself carrying Phase 20.5–20.7 transitively) and Phase 20.9's overall `PortfolioDecision`. No scoring, ranking, qualification, regime-detection, or risk logic is duplicated — every fact this phase cites was computed by an earlier phase and is only ever read.

## 3. Decision rules

Checked in this fixed order:

1. **Rule 4 (insufficient intelligence must be honest), first:** `allocation is None` or `portfolio_decision is None`, or the strategy isn't even present in the portfolio decision's own candidate list → `INSUFFICIENT_INTELLIGENCE`. This is reserved *strictly* for genuinely missing inputs — never for a poor answer an upstream layer actually gave.
2. **Rule 3 (capital intelligence is a gate) + hard block:** Phase 20.6's own `BLOCKED` qualification state (e.g. `EXTREME` risk) → `BLOCKED`.
3. **Rule 1 (evidence first):** `allocation_class == NONE` (which, once `BLOCKED` is excluded by step 2, can only mean Phase 20.6's `INSUFFICIENT_EVIDENCE`) → `NO_OPPORTUNITY`. A failed validation is never rescued by anything downstream.
4. **Rule 2 (portfolio conflict matters):** if Phase 20.9 excluded this strategy despite it clearing evidence sufficiency → `NO_OPPORTUNITY`.
5. Otherwise: `EXECUTABLE_CANDIDATE` only when qualification is `ELIGIBLE`, allocation is `NORMAL`/`MAXIMUM`, *and* no negative factor was raised anywhere above — else `WATCH`.

**Rule 5 (MIC cannot manufacture opportunity)** is not a separate check — it falls out of the ordering itself: MIC only ever enters via qualification state (`ELIGIBLE`/`WATCH`, already gated by evidence sufficiency in step 3) and never bypasses steps 1–4.

## 4. Scenario validation (`scripts/run_phase20_10_decision.py` — no research rerun; evidence taken verbatim from Phase 20.5, scenarios from Phase 20.6/20.9)

All four of this phase's own worked examples reproduced exactly, on real numbers:

**Strong Case** (TREND_UP, all NORMAL): `EXECUTABLE_CANDIDATE` — *"Validated historical edge: effective_score=79/100" / "Compatible regime..." / "Risk allocation available: NORMAL" / "Portfolio accepted as primary candidate."*

**Weak Case** (Mean Reversion, real Phase 20.4 evidence): `NO_OPPORTUNITY` — *"Strategy validation failed -- insufficient historical evidence."* — reproduced in **every** scenario tested, regardless of regime, confirming Rule 5 directly on real data.

**Extreme Risk Case:** `BLOCKED` — *"EXTREME_RISK: risk_state=EXTREME."*

**WATCH Case** (TRANSITION regime): `WATCH` — positive: *"Validated historical edge... Portfolio accepted as primary candidate."*; negative: *"Qualification WATCH: mic_regime='TRANSITION' not in this strategy's validated favorable set... Reduced risk allocation confidence: REDUCED."* — matching the phase's own worked WATCH example almost verbatim.

**Missing-intelligence case:** `compose_decision(None, None)` → `INSUFFICIENT_INTELLIGENCE`, both missing layers named explicitly.

**Proof line, logged directly:** `Trend Following evidence_score: 78.62` and `Mean Reversion evidence_score: 0.0` — identical across every regime/risk/execution scenario in the run.

## 5. Limitations

1. **`event_context` is always `NOT_AVAILABLE`** — no upstream Cycle-1 layer (`MarketEnvironment`, Phase 20.6) carries an event-calendar field at all; this phase surfaces the honest gap (reused from `mic_v0`'s own Phase 20.1 disclosure) rather than inventing one, but it means every single decision this phase has ever produced carries the same "Unknown: Event calendar" line — informative once, not yet differentiating.
2. **One strategy at a time** — `compose_decision()` composes a `FinalDecision` per strategy, given the whole portfolio's verdict; it does not itself return "the portfolio's final decision" as one object. A caller wanting a full-portfolio summary calls it once per candidate in `portfolio_decision.candidates`.
3. **Only two real strategies exist in Cycle 1** — as in every prior phase, `NO_OPPORTUNITY` has been genuinely earned by Mean Reversion's real evidence in every scenario tested; `WATCH` and `BLOCKED` were exercised on Trend Following's real evidence under varied conditions; `EXECUTABLE_CANDIDATE` has so far only ever been reached by one real strategy under one real regime.
4. **Stateless, as designed** — like every phase in this Cycle-1 stack, no memory across calls; a strategy oscillating between `EXECUTABLE_CANDIDATE` and `WATCH` cycle to cycle would not be visible to this layer alone.

## 6. Future execution path

Per the roadmap's own standing rule, restated at the top of this phase's own spec: **the next future stage is shadow decision validation, not live execution.** A future phase would replay `FinalDecision` output against real historical sessions to check whether `EXECUTABLE_CANDIDATE` verdicts would have been *worth* downstream review, before any execution-adjacent capability is ever built. This phase's own `FinalDecision`/`explain_decision()` output is designed to be that shadow-validation input directly — no translation layer anticipated or built here.

---

## Testing & regression

16 new tests (strong composition: 1, evidence failure: 2, capital blocking: 1, portfolio conflict handling: 2, MIC boundary: 3, missing-data honesty: 3, explainability: 2, safety boundary: 2), all passing. Full regression: **6,294 passed, 0 failed** (6,278 baseline from end of Phase 20.9 + 16 new). Protection hashes for `bujji/mic_v0/*.py`, `bujji/intelligence/regime_brain.py`, `bujji/broker/paper.py`, `bujji/execution_reality/` (unchanged, never imported), `bujji/execution_backtest/driver.py`, `bujji/strategy_intelligence/*.py`, `bujji/opportunity_intelligence/*.py`, `bujji/opportunity_ranking/*.py`, `bujji/capital_intelligence/*.py`, `bujji/opportunity_portfolio/*.py`, and `bujji/decision_intelligence/*.py` (the collision package, §1) all byte-identical to their state before this phase began — none were touched. `grep` confirms no file in `bujji/decision_orchestration/` references `place_order`, `modify_order`, `cancel_order`, `PaperBroker`, `FyersBroker`, or imports `bujji.broker`/`bujji.capital.` (the real, forbidden broker/capital systems).
