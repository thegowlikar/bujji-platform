# Bujji Shadow Trading Brain — Vision, Build Log, and Status

**Last updated:** 2026-08-03 (includes Phase 7B lineage audit)
**Baseline commit:** `b148e39` (branch `v1.0-shadow`)
**Status of this document:** working reference — intended to be re-read and updated at the start of each new session/phase, not written once and forgotten.

---

## 1. The Vision

> Bujji Options OS is not a screener you run or a signal generator you act on. It is a **firm** — a fully autonomous options-selling quant desk where the "analysts" are agents, the desk runs continuously, and the output is profitable options selling at institutional quality.

Key architectural properties implied by that framing:

| Property | Meaning |
|---|---|
| Independent | No human in the loop for routine decisions |
| Self-sufficient | Owns its own data, execution, risk, and reporting |
| Self-learning | Performance feeds back into strategy; the desk gets better over time |
| Full end-to-end | Market open → position close → P&L reconciliation, nothing falls out of the system |
| Options Selling specifically | A defined edge (theta decay, high-probability, defined-risk structures) — not directional speculation |
| Quant analysts (plural) | Specialization: one "analyst" per IV regime, one per strike selection, one per Greeks management, one per exit triggers, etc. |

The hardest part of this vision is explicitly **not the coding** — it's self-learning without overfitting, which requires (in order of dependency):
1. A rigorous trade logging schema from day one (every entry, every exit, every Greek at entry)
2. A feedback loop that attributes P&L to specific decision points, not just trade outcomes
3. A model that can distinguish "bad strategy" from "bad execution" from "bad market regime"

---

## 2. What Has Been Built (confirmed, this session)

### 2.1 Live foundation — actually running against real data

| Component | Path | Status |
|---|---|---|
| FYERS broker connectivity | `bujji/broker/fyers.py` | Live-verified: `get_spot`, `get_vix`, `get_option_chain`, `get_quote`, `get_futures_quote` all confirmed against real responses |
| Market Perception | `bujji/market_perception/` | Live: builds one complete `MarketSnapshot` (spot/VIX/futures/option chain) per cycle |
| Shadow Session Runner | `bujji/shadow_runtime/shadow_session_runner.py` | **Actually run live**: a real ~3h41m session, 880/880 successful quote observations, zero errors, zero reconnects |

### 2.2 Market understanding layer — built and tested, wired into a standalone pipeline (not yet into the live runner loop)

| Component | Path | Produces |
|---|---|---|
| Market State Builder | `bujji/market_state_builder/` | Real `Observation`/`MarketEvent`/`Episode` objects from live snapshots; feeds `PriceStructureAssessment` (PSI), `MarketStructureAssessment` (MSSI), `MarketParticipantPositioningAssessment` (MPPI) |
| Market Direction Bridge | `bujji/market_state/direction_bridge.py` | `MarketDirectionAssessment` (MDI) — via `msi_market_direction`, unmodified |
| Volatility Structure Bridge | `bujji/market_perception/msi_adapter.py` | `VolatilityStructureAssessment` (VSB) — via `msi_volatility_structure`, unmodified (**not yet wired into the live cycle**) |
| Market State Synthesis | `bujji/market_state/synthesizer.py`, `confidence.py` | `MarketState` — regime, direction, volatility, liquidity, positioning, evidence-based confidence, disclosed uncertainties |
| Evidence Boundary | `bujji/market_state/evidence_boundary.py` | `MarketEvidenceState` — observability-facing summary object |

### 2.3 Decision-adjacent bridge layer — built and tested, standalone, never wired into a live loop

| Component | Path | Produces |
|---|---|---|
| Domain View Adapter | `bujji/market_state/domain_view_adapter.py` | `DomainAssessmentView`/`DomainSignal` from PSI/MSSI/MDI/MPPI (VSB deliberately excluded from consensus, included in synthesis) |
| → Consensus | `msi_consensus.compute_consensus()` (unmodified) | `ConsensusAssessment` |
| → Opportunity | `msi_decision_synthesis.synthesize()` (unmodified) | `MarketOpportunityAssessment` |
| Trade Thesis Bridge | `bujji/market_state/trade_thesis_bridge.py` | `TradeThesisAssessment` (e.g. "BREAKOUT, STRONG_BULLISH, HIGH conviction") — via `msi_trade_thesis`, unmodified |
| Strategy Eligibility Bridge | `bujji/market_state/strategy_eligibility_bridge.py` | `StrategyEligibilityAssessment` — a **set** of compatible strategy families, never narrowed to one — via `msi_strategy_eligibility`, unmodified |

**Architecture discipline maintained throughout**: every bridge is a thin, pure-function wrapper around an existing, unmodified MSI engine — no reimplementation, no interpretation added, no forbidden field (order/position/entry/exit/capital/risk-approval) ever introduced. ~4,277 tests pass repo-wide with zero regressions as of Phase 6B.

### 2.4 Self-learning infrastructure — schema exists, live-feed status unconfirmed

Found in earlier architecture audits this session (not built this session, but real and present):
- `msi_decision_auditor.DecisionRecord` — full per-decision snapshot (observation ids, thesis, strategy family, lifecycle state, margin assessment, execution plan, confidence)
- `msi_decision_auditor.OutcomeRecord`/`DecisionOutcomePair` — pairs each decision with realised outcome, thesis survival, execution feasibility — **separately**, which is exactly the "attribute to decision points, not just outcome" mechanism the vision requires
- `msi_knowledge_validation`, `msi_market_learning.KnowledgeCandidate` — scored on repeatability/consistency/causal validity/market diversity, explicitly designed to resist overfitting ("produces Knowledge Candidates, never missed trades or recommendations")

**Open question, not yet answered**: whether any of this is fed live data today, or exists as tested-but-dormant schema.

### 2.5 Session-level governance (built earlier in this session, before the Shadow Campaign v2 work — relationship to the above unverified)

- V1.1 Trading Session Governor — one-strategy-per-day discipline, mandatory exits, real exit pricing. Live-tested.
- Numeric Risk Governor (Gates B–E.3), Shadow Trading OS (Gates F.0–F.5)

---

## 3. Architectural Lineages (do not conflate) — updated with Phase 7B audit findings

| Lineage | Status | Key modules |
|---|---|---|
| **A — MIC v2 / legacy Trading Brain** | Dormant. MIC v2 is a separate sibling project (`/opt/bujji-mic-v2/`), not live-fed. Tied to abandoned `production_runtime/` generation. | `trading_brain/evidence_interpreter/`, `trading_brain/market_state/`, `trading_brain/strategy_selector/`, **`trading_brain/risk_brain`** (imports `..market_state.models.MarketStateAssessment` + `..strategy_selector.models.StrategyDecision` — confirmed coupled), **`trading_brain/capital_brain`** (imports `..risk_brain.models.RiskAssessment` — coupled transitively) |
| **B — Shadow Campaign v2 (this session's main work)** | Live for perception/understanding; standalone/untested-live for the decision-adjacent bridges | `market_perception/`, `market_state_builder/`, `market_state/` |
| **C — Pre-existing MSI decision pipeline** | Now largely live-fed BY lineage B's bridges (Phase 5B–6B), **and now confirmed extendable further** | `msi_consensus`, `msi_decision_synthesis`, `msi_trade_thesis`, `msi_strategy_eligibility`, `msi_strategy_selector`, **`msi_strategy_selection_foundation`** (Phase 7B: confirmed clean — imports only `msi_consensus`/`msi_market_direction`/`msi_market_structure`/`msi_volatility_structure`, zero MIC v2, zero `trading_brain`, `assess_all_families(mdi, mssi, consensus, vsb, timestamp)` is satisfiable **today** with objects this session already produces live), `msi_trade_construction` (still unexplored) |
| **D — Structurally independent, orphaned from any live caller** | Clean code, but its only real caller sits inside dormant `production_runtime/` | **`trading_brain/exit_engine`** (imports only `..portfolio_valuation.models.PortfolioValuation`, itself dependency-free — no MIC v2, no `trading_brain/market_state`; actively touched 2026-07-31, far more recently than the Jul-27 baseline everything else shares; called only from `production_runtime/trade_lifecycle_executor.py`/`position_lifecycle_runtime.py`, and from `trading_brain/risk_governor/position_lifecycle_intelligence.py` — a 4th, not-yet-investigated thread, plausibly where V1.1 Trading Session Governor actually lives) |
| **E — Live, but not the pipeline built this session** | `msi_decision_auditor` is genuinely wired into a real live-adjacent system — `live_shadow_operator/journal.py`, `live_shadow_operator/operator.py`, `live_shadow_validation.py`, `live_pipeline_bridge.py`. This is the *other* live system discovered during this session's credential-investigation saga (`run_live_shadow.py`), **not** the Shadow Campaign v2 MSI pipeline. `msi_market_learning`/`msi_knowledge_validation` are each referenced only by sibling MSI packages (`msi_evidence_packet`, `msi_engineering_evidence_board`) — not confirmed fed by any live operator. | `msi_decision_auditor` (live-adjacent), `msi_market_learning`, `msi_knowledge_validation` (not confirmed live) |

`trading_brain/`'s remaining subpackages (`position_sizing`, `order_construction`, `execution_planner`, `execution_engine`) still have **not** been checked for lineage — real, flagged unknowns.

---

## 4. What Is Pending / Genuinely Unknown

**Resolved by the Phase 7B lineage audit** (moved out of "unknown," see §3 for detail):
- ~~`msi_strategy_selection_foundation` unexplored~~ → **confirmed clean, live-satisfiable today** — no longer blocks Strategy Selection on an object-availability basis.
- ~~`trading_brain/exit_engine`, `capital_brain`, `risk_brain` lineage unverified~~ → **confirmed**: `risk_brain`/`capital_brain` are legacy-coupled (Lineage A); `exit_engine` is code-independent but caller-orphaned in dormant `production_runtime/` (Lineage D).
- ~~Whether `msi_decision_auditor`/`msi_market_learning` are fed live data~~ → **confirmed**: `msi_decision_auditor` is live-adjacent via the *other* live system (`live_shadow_operator`), not this session's pipeline; `msi_market_learning`/`msi_knowledge_validation` remain unconfirmed live.

**Still open:**

1. **`msi_strategy_expression`** (Series 93) — optional input to `select_strategy()`, still unexplored.
2. **`msi_trade_construction`** — the layer that would build actual option legs for a chosen strategy family. Unexplored.
3. **`trading_brain/position_sizing`, `order_construction`, `execution_planner`, `execution_engine`** — lineage not verified.
4. **`trading_brain/risk_governor/position_lifecycle_intelligence.py`** — a newly-surfaced 4th caller of `exit_engine`, plausibly where V1.1 Trading Session Governor actually lives. Not yet investigated.
5. **Relationship between V1.1 Trading Session Governor and the `trading_brain/`/MSI stack** — unverified (see item 4, likely the same investigation).
6. **No live orchestration point yet calls the full Lineage B/C chain** (`market_perception → market_state_builder → market_state → domain_view_adapter → consensus/opportunity → trade_thesis → strategy_eligibility → [now reachable] strategy_selection_foundation`) in one running cycle. Every bridge past `market_state_builder` is proven correct in isolation only.
7. **`VolatilityStructureAssessment` (VSB)** is built (Phase 3A) but never wired into the live `ShadowSessionRunner` cycle.
8. **The core architecture decision itself is still open**: converge on Lineage C as the canonical trading brain and retire/replace Lineage A's `risk_brain`/`capital_brain`, or find a translation layer between them. Phase 7B provides the evidence; the decision hasn't been made.

---

## 5. What Could Be Added to Advance the Vision (candidate next phases, not yet approved)

In rough dependency order, updated post-Phase 7B:

1. **Wire the existing bridges into the live loop.** Everything through Strategy Eligibility (and now Strategy Selection Foundation) is proven correct standalone — the highest-leverage, lowest-risk next step is making `ShadowSessionRunner` actually call the full chain every cycle, so "understanding" runs continuously instead of only in tests.
2. **Wire `msi_strategy_selection_foundation` + `msi_strategy_selector`** — no longer blocked; a thin bridge (same pattern as Phase 5D/6B) can produce real `StrategySuitabilityAssessment`/`StrategySelectionAssessment` today. This is the first genuine "choose one" decision point — treat its own Phase (7C?) with the same investigation-before-code discipline given its decision-shaped output, but the object-availability question is settled.
3. **Investigate `trading_brain/risk_governor/position_lifecycle_intelligence.py`** — newly surfaced, plausibly the real home of V1.1 Trading Session Governor and the actual live caller context for `exit_engine`. Higher priority now than a generic `trading_brain/` sweep, since it's the one thread that connects `exit_engine` (Lineage D, reusable) to something real.
4. **Investigate `msi_trade_construction`** — needed before any real option leg can be proposed from a selected strategy family.
5. **Decide the canonical lineage**: converge on Lineage C + reuse `exit_engine`/`portfolio_valuation` (Lineage D), versus keeping `risk_brain`/`capital_brain` and building a translation layer into `trading_brain/market_state`'s shape. Phase 7B's evidence supports the former (less translation, less legacy debt) but this is a decision, not a technical finding — flag for explicit sign-off before building either way.
6. **Bridge `msi_market_learning`/`msi_knowledge_validation` to real outcomes** — still fully open regardless of which lineage wins; `msi_decision_auditor`'s existing live connection is to the *other* system, not usable as-is here.

---

## 6. Day-to-Day Status Validation Checklist

Run these on the VPS (`root@139.59.76.137:/opt/bujji/app`) to re-confirm current state before resuming work in a new session:

```bash
# 1. Confirm baseline / working tree state
git log -1 --oneline
git status --short

# 2. Confirm full regression is still green
/opt/bujji/.venv/bin/python -m pytest -q 2>&1 | tail -5

# 3. Confirm no protected/legacy lineage has been touched
git diff --name-only b148e39 -- bujji/trading_brain/ bujji/msi_shadow_trading/ \
  bujji/mic_replay/ bujji/production_runtime/ bujji/broker/ \
  bujji/msi_consensus/ bujji/msi_decision_synthesis/ bujji/msi_trade_thesis/ \
  bujji/msi_strategy_eligibility/

# 4. Confirm which Shadow Campaign v2 packages exist and their test coverage
ls bujji/market_perception/ bujji/market_state_builder/ bujji/market_state/
/opt/bujji/.venv/bin/python -m pytest tests/ -k "phase" -q 2>&1 | tail -5

# 5. Check whether a live Shadow Runtime session is currently running
ps aux | grep run_session.py | grep -v grep
```

**Interpretation guide:**
- Regression failing → something regressed; do not build further until fixed.
- Any diff under the protected-lineage paths above → investigate immediately, this should never happen without an explicit, approved phase.
- No live session running → expected outside market hours / between sessions; not itself a problem.

---

## 7. Document Maintenance

Update this file whenever:
- A new phase completes (append to §2, move resolved items out of §4)
- A new architectural lineage or module is investigated (update §3/§4)
- The live wiring status changes (e.g., bridges get wired into `ShadowSessionRunner` — update §4 item 7)

Keep it short enough to re-read in one sitting — if a section grows past a screen, split into a linked doc rather than letting this one sprawl.
