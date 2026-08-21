# Phase 15O -- Shadow Runtime Position Lifecycle Integration: Final Report

## 1. Forensic audit of the actual runtime path (Step 1)

Traced from source, not assumed.

**What `ShadowSessionRunner` actually calls** (`shadow_runtime/shadow_session_runner.py`, 357 lines): quote observation -> `MarketQuoteAdapter`/`QuoteObservationStore` -> `LiquidityBrain` -> `_run_market_perception_step` -> `MarketDataAdapter.build_snapshot` -> `build_intelligence_snapshot` -> `IntelligenceCycleRecorder.record_cycle` -> `record_regime_cycle` (Phase 15C). That is the ENTIRE production path. The runner's own imports confirm it: **it does not import `position_lifecycle`, `paper_bridge`, `position_intelligence`, `position_management`, `outcome_attribution`, `outcome_memory`, or `portfolio_intelligence` at all.**

**`IntelligenceCycleRecorder`** (the real decision chain) runs: market state -> MDI/MSSI/PSI/MPPI/VSB -> consensus -> decision synthesis -> opportunity -> trade thesis -> strategy eligibility -> `assess_all_families` -> `select_strategy` -> `determine_trade_intent`. It stops there. Everything Phases 15G-15N built was **completely disconnected from the runtime** before this phase.

**Answers to the 10 audit questions:**

| # | Question | Finding |
|---|---|---|
| 1 | Which components are actually called by `ShadowSessionRunner` | Only observation/perception/intelligence-cycle recording + regime persistence. |
| 2 | Which remain disconnected | `position_lifecycle`, `paper_bridge`, `position_intelligence`, `position_management`, `pnl`, `outcome_attribution`, `outcome_memory`, `portfolio_intelligence`, `replay_engine` -- all 9. |
| 3 | Can a real `TradeIntent` produce a valid candidate | **NO** -- and this is the phase's central finding, see Section 2. |
| 4 | Can trade construction produce a complete lifecycle-open payload | Yes, mechanically -- `build_shadow_trade_candidate` -> `build_position_opened_payload` is a clean fit (proven this phase). But only if a CONSTRUCTED candidate exists, which it never does on real data. |
| 5 | Can PaperBroker safely simulate the resulting orders | **YES**, unmodified -- `place_order`/`get_order`/`get_execution_report` are sufficient. No PaperBroker change was needed or made. |
| 6 | Can lifecycle identity be established without inventing identity | **YES** -- `position_id_for(session_id, candidate_id, entry_timestamp)` (15G) + `client_order_id_for` (15L) cover it completely. Nothing invented. |
| 7 | Can every subsequent cycle monitor the position | **YES** -- `position_management.assess_position_management` consumes a thesis evaluation the caller supplies; no new monitoring engine needed. |
| 8 | Where are exit decisions currently generated | **NOWHERE for the new Core.** No component decides to exit. Phase 15I produces an ADVISORY `EXIT` recommendation but deliberately never acts on it. The orchestrator therefore takes exit timing from its caller (e.g. session end), never from an advisory recommendation -- disclosed, not silently invented. |
| 9 | Can the runtime close the position deterministically | **YES** -- proven this phase via PaperBroker closing orders + `paper_bridge` reconciliation. |
| 10 | Can the closed position flow through structured exit -> P&L -> thesis -> management -> attribution -> memory | **YES** -- proven end to end this phase (Section 4). |

## 2. THE central forensic finding: why no real position has ever existed

Every prior phase (15G-15N) reported `NO_REAL_..._AVAILABLE` without identifying the cause. This phase traced it to a definitive, fully-verified chain across **all 708 real persisted intelligence cycles** (3 sessions):

1. **All 708 real cycles have `opportunity_state = MONITOR`.** (100%, zero exceptions.)
2. `msi_strategy_eligibility._BASE_ELIGIBLE_BY_STATE` maps `MONITOR -> ()` **deliberately** -- its own comment reads *"Non-opportunity states: nothing is eligible -- there is no edge to select a family for."*
3. Therefore `eligible_strategy_families = []` and `eligibility_confidence = NONE` in **all 708 cycles** -- including the 277 cycles where Strategy Selection DID pick a family (VOLATILITY_COMPRESSION 109, VOLATILITY_EXPANSION 84, NEUTRAL_PREMIUM_BUYING 27, COVERED 26, SHORT_DIRECTIONAL 16, LONG_DIRECTIONAL 14, SYNTHETIC 1).
4. Therefore **both** intent functions return `None`: `determine_trade_intent`'s placeholder needs a non-empty eligible set, and `determine_trade_intent_from_selection` explicitly returns `None` when confidence is `NONE`.
5. Therefore `trade_intent` is null in **100%** of real cycles (verified directly: 0 non-null).
6. Therefore `build_shadow_trade_candidate` returns `INVALID_INTENT` for **all 708**.
7. Therefore no position can ever be opened.

**This is not a bug and not integration debt.** It is the intelligence stack honestly reporting that it never once identified a tradeable opportunity in the recorded market conditions. Steps 2-6 were each verified against real persisted data, not inferred.

**A separate, genuine integration debt WAS found and fixed**: Phase 14B built `determine_trade_intent_from_selection` (so a real ranked Strategy Selection drives intent, rather than `determine_trade_intent`'s own documented *placeholder* family picker) -- but `grep` confirmed it was **called from nowhere in production**. It was dead code for two phases. Now wired into `IntelligenceCycleRecorder`, strictly additively: the selection-driven path is used only when a real selection with a real family exists; every other cycle falls back to the exact pre-existing call. On today's real data this changes no outcome (both paths return `None` for the reason above), but the architecturally-correct call is now live for the day opportunity state is not `MONITOR`.

## 3. Minimum missing integration (Step 2)

Exactly one new module: `bujji/shadow_lifecycle/orchestrator.py`. It builds **no** new intelligence, **no** new state machine, **no** new identity scheme, **no** new persistence format, and **no** second P&L path. Every step delegates to the engine that already owns it (lifecycle 15G, bridge 15L, P&L 15K, thesis 15F, management 15I, attribution 15J, memory 15N, persistence 15B). Public surface: `open_position_from_candidate`, `monitor_position`, `close_position`, `record_outcome_memory`.

## 4. The vertical slice (Step 4) -- PROVEN

`tests/test_shadow_lifecycle_orchestrator.py`, 10/10 passing. The complete chain runs end to end:

`ShadowTradeCandidate -> POSITION_OPENED (+ real PaperBroker entry fills) -> THESIS_EVALUATED -> MANAGEMENT_ASSESSED -> (real PaperBroker exit fills) -> reconciled via paper_bridge -> POSITION_CLOSED (structured exit, real P&L, real ChargesCalculator fees) -> OUTCOME_ATTRIBUTED -> OUTCOME_MEMORY_RECORDED`

...and the entire chain survives full rehydration from disk. Also proven: multi-leg positions carry through; P&L traces to actual PaperBroker fills (real charges, and `exit_bid`/`exit_ask` correctly staying `None` because a fill genuinely has no spread); thesis can remain intact / weaken / invalidate across cycles with real management recommendations recorded for each; an unconstructed candidate (the real-data case) is declined explicitly; a second concurrent open is declined; and **monitoring places exactly zero orders even when the thesis is INVALIDATED** -- management stays advisory.

## 5. Evidence classification (Step 5) -- kept strictly separate

- **REAL MARKET RUNTIME EVIDENCE**: the 708-cycle root-cause chain in Section 2 (every link verified against real persisted data), and the orchestrator's decline path verified against that exact real data.
- **SYNTHETIC/SEMANTIC FIXTURE EVIDENCE**: the `ShadowTradeCandidate` driving the vertical slice. Explicitly labelled in the test module's own header. Chosen per Step 5's option 3 because option 1 (a real qualifying candidate) provably does not exist in any archived session, and option 2 (run forward until one appears) cannot help while `opportunity_state` is structurally incapable of leaving `MONITOR`. **Everything downstream of the candidate is real, not mocked**: real PaperBroker fills, real `ChargesCalculator`, real `FillSimulator`, real reducers, real attribution, real memory.
- **REPLAY EVIDENCE**: all recovery/replay behaviour in Section 7 -- real `EventStore`, real hydration, real determinism.

## 6. Position opening / monitoring / closing (Steps 6, 7, 8)

Opening uses canonical `position_id_for` identity; broker order IDs derive **only** through `client_order_id_for` (proven structurally by a safety test that every `client_order_id=` keyword argument is a call to that exact function); orders go to PaperBroker only; actual execution evidence is reconciled back through the Phase 15L bridge; entry timestamp/quantity/price/bid-ask/lot size are all preserved via the existing Phase 15K fields. **PaperBroker was not modified** -- the audit proved no missing observation field. Monitoring reuses `position_intelligence`/`position_management` with no second engine. Closing produces real PaperBroker execution evidence and flows it through `paper_bridge -> structured_exit -> realized_pnl -> PositionClosed -> OutcomeAttribution -> OutcomeMemory`; attribution is computed strictly after the close and never influences it.

## 7. Crash/restart proof (Step 9) -- MANDATORY, all 7 scenarios + 3 extra

`tests/test_shadow_lifecycle_orchestrator_recovery.py`, 10/10 passing: restart after candidate formation (honest empty state); after OPEN (byte-identical rehydration); during monitoring (and monitoring CONTINUES correctly from recovered state); after MANAGEMENT_ASSESSED; immediately before CLOSE (and the close then succeeds from recovered state); after CLOSE but before OUTCOME_ATTRIBUTED (attribution still derivable from the recovered closed position); after OUTCOME_ATTRIBUTED but before memory (memory still recordable). Plus: full-chain replay determinism, torn-record mid-lifecycle degrading to `RECOVERY_PARTIAL` without a false CLOSED, and duplicate lifecycle events never double-applied.

## 8. Real runtime evidence (Step 10)

**`NO_REAL_SHADOW_POSITION_AVAILABLE`** -- with, for the first time, a complete and verified explanation of why (Section 2). Not manufactured.

## 9. Safety (Step 11) -- 12/12 passing

`tests/test_shadow_lifecycle_orchestrator_safety.py`: no live-broker or forbidden imports (AST); no FYERS/hybrid/guard reference **in code** (checked via AST identifier extraction, not raw text -- the module's own docstring legitimately names the forbidden modules to document the boundary, and a naive text scan false-positived on its own safety documentation; fixed by checking real identifiers); broker is always injected, never constructed (so the runtime alone decides what broker exists); **the orchestrator never READS outcome memory** (write-side imports only, enforced by an explicit allow-list) -- the definitive no-feedback-loop proof; no strategy-selection feedback; `monitor_position` structurally contains no `place_order` call; every `client_order_id` derives from `client_order_id_for`; no P&L arithmetic; no second state machine or identity scheme; `guard.py`/`hybrid.py`/`trading_brain/`/`risk_governor/`/`execution_engine/` all byte-untouched; cross-session identity collision impossible.

## 10. Full regression (Step 12)

**5031 passed, 0 failed** (from the 4999 baseline; **32 new tests**: 10 vertical slice + 10 recovery + 12 safety; 1 pre-existing unrelated deprecation warning).

**Bugs discovered**: one test-authoring false positive (the safety docstring scan above), fixed. No production defect -- every functional test passed on its first run.

**Files changed**: New: `bujji/shadow_lifecycle/__init__.py`, `bujji/shadow_lifecycle/orchestrator.py`, 3 test files. Modified (additive, backward-compatible): `bujji/market_state/intelligence_cycle_recorder.py` (wires the dead `determine_trade_intent_from_selection`).

**Remaining disconnected components**: `ShadowSessionRunner` still does not CALL the orchestrator. This phase built and proved the integration layer; wiring it into the live runner loop is deliberately not done, because doing so today would add a code path that provably can never fire (Section 2) -- integration for its own sake, unexercisable and unverifiable against real data.

## 11. Verdict

**`SHADOW_LIFECYCLE_INTEGRATION_TRUST: TRUSTED`** (fixture + replay proven), **`REAL_RUNTIME_LIFECYCLE: NOT_YET_POSSIBLE`** (blocked by Section 2, with root cause now fully identified for the first time).

The architecture **can** carry a real position through its complete lifecycle -- proven end to end with real PaperBroker fills, real economics, real attribution, real memory, real recovery at all 7 interruption points. What it cannot yet do is *originate* one, and the reason is now precisely known rather than merely observed.

## 12. Fresh architecture gap audit (Step 13)

| Component | Status |
|---|---|
| Market observation / perception / MSSI / PSI / MDI / VSB / regime / consensus | `EXISTS`, `CONNECTED`, `REAL-DATA PROVEN` |
| Opportunity state (`msi_decision_synthesis`) | `EXISTS`, `CONNECTED`, `REAL-DATA PROVEN` -- **but has emitted only `MONITOR` in 708/708 real cycles** |
| Strategy eligibility | `EXISTS`, `CONNECTED`, `REAL-DATA PROVEN` -- correctly empty under `MONITOR` |
| Strategy selection | `EXISTS`, `CONNECTED`, `REAL-DATA PROVEN` -- selects a family in 277/708 cycles |
| TradeIntent | `EXISTS`, `CONNECTED` (selection-driven path wired this phase), `FIXTURE-ONLY` -- never produced a real intent |
| Trade construction | `EXISTS`, `CONNECTED`, `REAL-DATA PROVEN` (as a decline: 708/708 `INVALID_INTENT`) |
| Position lifecycle / paper bridge / P&L / position intelligence / management / attribution / portfolio / outcome memory | `EXISTS`, **`CONNECTED` (via the 15O orchestrator, new)**, `FIXTURE-ONLY`, `REPLAY-PROVEN`, `RECOVERY-PROVEN` |
| `ShadowSessionRunner` -> orchestrator wiring | `MISSING` (deliberate -- see Section 10) |
| Execution quality intelligence | `MISSING` (raw material exists in 15L) |
| Learning / strategy feedback / calibration | `MISSING` (deliberate, gated) |

**The single highest-leverage next bottleneck is the Opportunity State Engine.** The evidence is now unambiguous: `opportunity_state` has been `MONITOR` in **100% of 708 real cycles across 3 sessions**, and `MONITOR` is a hard structural gate that zeroes out eligibility -> intent -> candidate -> position -> P&L -> attribution -> memory -> learning. Every downstream capability built across Phases 15G-15O is architecturally complete, replay-proven, recovery-proven -- and permanently unexercisable until this one upstream component can emit something other than `MONITOR`.

This is NOT a recommendation to loosen the gate so trades happen. The gate may well be correct -- if the market genuinely offered no edge, `MONITOR` is the honest answer, and forcing candidates through would be exactly the fabrication this project has refused at every step. The needed work is **diagnostic first**: determine whether `MONITOR` is (a) correct and the recorded sessions genuinely contained no opportunity, (b) the result of an input domain being persistently `UNKNOWN` so the synthesis can never reach a confident state, or (c) a threshold/logic defect that makes non-`MONITOR` states effectively unreachable. Only (c) would justify a behavioural change, and only with evidence.

**Recommended Phase 15P: Opportunity State Forensic Investigation + Decision Synthesis Audit.** Trace, against all 708 real cycles, exactly which inputs `msi_decision_synthesis` consumes, what values they actually held, which specific condition blocked every non-`MONITOR` state, and whether any real cycle came close to a threshold. Classify the outcome as (a)/(b)/(c) above with evidence. Only then decide whether the correct response is richer market observation (if inputs were `UNKNOWN`), a threshold correction (if provably defective), or the honest conclusion that these particular sessions contained no tradeable edge and longer/more varied market data is required.

Per the standing discipline, Phase 15P has not been started, and the working tree remains uncommitted.
