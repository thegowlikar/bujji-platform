# Phase 15P -- Opportunity State Forensic Investigation: Final Report

## 1. Methodology

Source inspection first (`msi_decision_synthesis/{engine,config,taxonomy}.py`, `msi_strategy_eligibility/engine.py`, `market_state/intelligence_cycle_recorder.py`, `strategy_taxonomy_bridge/mapping.py`), then a full replay of **every one of the 708 real persisted cycles across all 3 archived sessions** through the *current* engine, using each cycle's own real persisted domain states and confidences. No production logic was changed during the investigation. Diagnostic scripts were run on the VPS and deleted afterwards, per standing discipline.

## 2. THE root cause -- the archived corpus is stale, not the market

`_resolve_opportunity_state` produces `MONITOR` when `considered` is empty, i.e. when every domain signal maps to `LEAN_AMBIGUOUS` in `config.STATE_LEAN_MAP`. The recorded assessments show exactly that fingerprint: `supporting_domains: []`, `conflicting_domains: []`, `confidence_level: NONE` in all 708 cycles.

But the domain states those same records persisted are **not** ambiguous — they are `BALANCE`, `CORRECTING`, `TRENDING`, `NEAR_RESISTANCE`, `INSIDE_RANGE`, `NEUTRAL_POSITIONING`, `STABLE`, `COMPRESSED`, and so on. Those keys were added to `STATE_LEAN_MAP` by **Phase 14B-P1** (the config block is literally labelled *"Phase 14B-P1 additions: real MSI brain vocabulary"*). The sessions were recorded **before** that build. Under the older map every one of those states fell through to `LEAN_AMBIGUOUS`.

Direct proof, same inputs, one real cycle:

| | opportunity_state | confidence | supporting_domains |
|---|---|---|---|
| **Recorded 2026-08-06** | `MONITOR` | `NONE` | `[]` |
| **Current engine** | `NEUTRAL_OPPORTUNITY` | `HIGH` | 4 domains |

**Verdict on the bottleneck: the 708/708 `MONITOR` result is an artifact of stale persisted data, not current behaviour, and not a market fact.** Phases 14B through 15O each concluded "MONITOR dominance / no real candidate possible" from this corpus. That conclusion does not hold for the current build. This is a **validation-methodology defect, not a production-code defect.**

## 3. Real-cycle statistics (current code, real inputs)

| | Recorded (stale build) | Current code |
|---|---|---|
| `MONITOR` | **708** | 24 |
| `NEUTRAL_OPPORTUNITY` | 0 | **644** |
| `MEAN_REVERSION_OPPORTUNITY` | 0 | 30 |
| `DIRECTIONAL_OPPORTUNITY` | 0 | 9 |
| `VOLATILITY_OPPORTUNITY` | 0 | 1 |
| **Actionable total** | **0 / 708** | **684 / 708 (96.6%)** |

Downstream, under current code: eligibility confidence `LOW` in 183 cycles / `NONE` in 525; **174 cycles have a non-empty eligible family set**; and **30 cycles produce a real, non-null `TradeIntent`** (versus 0 recorded).

The 24 cycles that still resolve to `MONITOR` under current code do so honestly — every one had all-ambiguous domain signals (`UNKNOWN`/`TRANSITIONING`/`MIXED_POSITIONING`).

## 4. Cross-session analysis

| Session | Cycles | Current-code resolution |
|---|---|---|
| `SHADOW-OBSERVATORY-2026-08-06` | 174 | 134 NEUTRAL, 30 MEAN_REVERSION, 8 DIRECTIONAL, 2 MONITOR |
| `...-run1` | 56 | 53 NEUTRAL, 1 DIRECTIONAL, 2 MONITOR |
| `...-run2` | 478 | 457 NEUTRAL, 1 VOLATILITY, 20 MONITOR |

MONITOR dominance is **not** market-condition dependent, **not** evidence-starvation dependent, and **not** threshold dependent. It is uniformly explained by the stale vocabulary across all three sessions.

Per-domain reality check (real data): `PRICE_STRUCTURE` was `TRANSITIONING` in 526/708 (genuinely ambiguous, correctly so); `SUPPORT_RESISTANCE` and `MARKET_DIRECTION` were `UNKNOWN` in 536/708 each — a real, separate observation-sparsity limitation, honestly preserved rather than papered over.

## 5. Second finding: Selection ↔ Eligibility disagreement (NOT a defect)

Of the 174 cycles with a non-empty eligible set, **144 have no intersection** between the family Strategy Selection picked and the families Eligibility permits:

| Count | Selection picked | maps to | Eligibility permitted |
|---|---|---|---|
| 46 | `VOLATILITY_COMPRESSION` | SHORT_VOLATILITY, UNDEFINED_RISK_PREMIUM | DEFINED_RISK_NEUTRAL |
| 27 | `NEUTRAL_PREMIUM_BUYING` | LONG_VOLATILITY | DEFINED_RISK_NEUTRAL |
| 25 | `COVERED` | UNDEFINED_RISK_PREMIUM | DEFINED_RISK_NEUTRAL |
| 16 | `SHORT_DIRECTIONAL` | UNDEFINED_RISK_PREMIUM | DEFINED_RISK_NEUTRAL |
| 15 | `VOLATILITY_EXPANSION` | LONG_VOLATILITY | DEFINED_RISK_NEUTRAL |
| 14 | `LONG_DIRECTIONAL` | DEFINED_RISK_DIRECTIONAL | DEFINED_RISK_NEUTRAL |
| 1 | `SYNTHETIC` | HEDGED_DIRECTIONAL | DEFINED_RISK_NEUTRAL |

The taxonomy bridge was verified **correct and complete** — `DEFINED_RISK_NEUTRAL` is properly fed by `IRON_CONDOR`/`IRON_FLY`/`BUTTERFLY`. Selection simply never chose one of those in these cycles.

This is a **genuine, honest disagreement between two independent reasoners**: Eligibility says "a neutral opportunity admits only defined-risk-neutral structures"; Selection ranks `VOLATILITY_COMPRESSION` highest. The system correctly declines to trade when they disagree. **This is exactly the behaviour Phase 14B's consistency check was built to surface, and it must not be "fixed" to manufacture trades.** It is disclosed here as a real characteristic, not corrected.

## 6. State reachability (Step 4) -- 19/19 tests passing

`tests/test_opportunity_state_reachability.py` proves **every documented opportunity type is reachable**: `DIRECTIONAL`, `BREAKOUT`, `VOLATILITY`, `NEUTRAL`, `MEAN_REVERSION` — plus an aggregate test asserting no declared type is dead ontology. Non-opportunity states remain reachable (`WAIT` with no signals, `MONITOR` with all-ambiguous). UNKNOWN propagation proven: UNKNOWN never counts as agreement, adding UNKNOWN domains never changes a resolved state, precondition domains (`LIQUIDITY`, `TIME_STRUCTURE`) gate but never vote. Tie behaviour proven conservative (1 vs 1 resolves to the NEUTRAL lean, never the opportunity one). Determinism proven (identical inputs → identical state and `assessment_id`).

## 7. Anti-fabrication (Step 8) -- proven

- `synthesize()` exposes **no** parameter through which any downstream component could request or bias an actionable state (verified by real signature inspection against a forbidden-parameter set).
- Zero or purely-UNKNOWN evidence can **never** produce an opportunity type.
- `MONITOR` remains a first-class reachable outcome — the system can always say *"MONITOR → no opportunity → no trade"*.
- Every non-opportunity state maps to zero compatible strategy families.

## 8. Classification (Step 5)

**`CORRECT_BEHAVIOUR`** for the current production code — with a critical **validation-corpus finding**.

None of `EVIDENCE_STARVATION`, `THRESHOLD_DEFECT`, `IMPLEMENTATION_DEFECT`, or `ARCHITECTURAL_DEFECT` applies to the current build: every state is reachable, every threshold is exercisable, UNKNOWN propagates correctly, and the recorder passes all five domain signals correctly. The defect was in the *evidence being reasoned from*, not in the code.

**Therefore no production change is justified, and none was made.** Per Step 6, this is a valid successful outcome.

## 9. Before/after behaviour

**No production behaviour changed in Phase 15P.** Zero production files were modified. The only change to production code in the recent window was Phase 15O's wiring of `determine_trade_intent_from_selection`, which remains in place and unchanged here.

## 10. Tests, replay, safety, regression

- **21 new tests** — 19 reachability/UNKNOWN/anti-fabrication + 2 stale-corpus guards.
- `tests/test_opportunity_stale_corpus_guard.py` encodes the finding so it cannot silently recur: it asserts the archived corpus contains zero actionable states *and* that the current engine produces actionable states from those same inputs; the complementary test asserts the current engine has **not** become merely permissive (some archived cycles still correctly resolve to non-opportunity states). Both skip cleanly when the corpus is absent.
- **Full regression: 5052 passed, 0 failed** (from the 5031 baseline; 1 pre-existing unrelated deprecation warning).
- No pre-existing safety test conflicted; none was weakened.

## 11. Remaining limitations

- **The archived corpus can no longer be used to validate opportunity/eligibility/intent behaviour.** Fresh sessions recorded by the current build are required. Every prior phase's `NO_REAL_..._AVAILABLE` conclusion should be re-examined once such a session exists.
- `SUPPORT_RESISTANCE` and `MARKET_DIRECTION` were `UNKNOWN` in 536/708 archived cycles — a real observation-sparsity limitation, independent of the stale-vocabulary issue, and still unaddressed.
- `PRICE_STRUCTURE` was `TRANSITIONING` (correctly ambiguous) in 526/708 — worth watching, not a defect.
- The Selection ↔ Eligibility disagreement (Section 5) remains open by design; it is disclosed, not corrected.
- No real shadow session has yet been recorded under the current build, so no real end-to-end position lifecycle exists **yet** — but the previously-assumed structural impossibility has been disproven.

## 12. Verdict

**`OPPORTUNITY_INTELLIGENCE_TRUST: TRUSTED_WITH_LIMITATIONS`**

Trusted: the engine implements its documented ontology, every state is reachable, thresholds are exercisable, UNKNOWN is preserved as information and never converted to a vote, synthesis is deterministic, and the anti-fabrication boundary holds — no downstream component can manufacture an opportunity.

Limitations: the trust rests on source inspection, semantic fixtures, and replay of *stale* archived inputs through current code. It has **not** been validated against a live session recorded by the current build, because none exists. The `TRUSTED` half is real; the missing half is real too, and is not overstated here.

## 13. Fresh architecture gap audit

The recurring Bujji pattern this phase surfaced is new in kind: not *"capability exists but is not connected"* (15O's finding) but **"validation evidence exists but no longer reflects the code"** — a persistence/replay asymmetry in which archived artifacts silently encode the behaviour of an obsolete build.

- Opportunity State: `EXISTS`, `CONNECTED`, `FIXTURE-PROVEN`, `REPLAY-PROVEN`, **not** live-proven under current build.
- Eligibility / Selection / taxonomy bridge: `EXISTS`, `CONNECTED`, correct — with a disclosed, by-design disagreement.
- TradeIntent: `EXISTS`, `CONNECTED` (since 15O), now **demonstrably capable of producing real intents** (30 from archived inputs) — previously believed structurally impossible.
- Shadow lifecycle orchestrator (15O): `EXISTS`, `CONNECTED` to all downstream engines, `FIXTURE-PROVEN`, `RECOVERY-PROVEN` — but still not called by `ShadowSessionRunner`.
- Archived session corpus: **`STALE` — must not be used for behavioural validation.**

**Highest-leverage next gap: record a fresh shadow session under the current build, and wire `ShadowSessionRunner` to the Phase 15O orchestrator so that session can actually carry a position.**

Phase 15O deliberately did not wire the orchestrator into the runner, on the reasoning that the path "could never fire". **That reasoning was based on the stale corpus and is now disproven** — under current code, 684/708 cycles reach an actionable opportunity state and 30 produce a real TradeIntent. The blocker is gone.

**Recommended Phase 15Q: Live Shadow Session + Runner Integration.** Wire `ShadowSessionRunner` to call the Phase 15O orchestrator (open/monitor/close), then record a genuine unattended shadow session against live or replayed market data under the current build. This would, for the first time, produce a real `position_lifecycle` artifact and convert the long chain of `NO_REAL_..._AVAILABLE` disclosures (15G through 15O) into actual evidence. It requires no new intelligence — only the integration Phase 15O built and correctly declined to activate, plus a fresh recording. Safety boundary is unchanged: PaperBroker only.

Per standing discipline, Phase 15Q has not been started, and the working tree remains uncommitted.
