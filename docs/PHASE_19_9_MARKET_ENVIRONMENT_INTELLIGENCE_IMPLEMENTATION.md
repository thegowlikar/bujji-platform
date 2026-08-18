# Phase 19.9 — Market Environment Intelligence Foundation

## Objective

Build the bridge between market understanding (Phase 19.3–19.8) and a future strategy-selection layer:
"what type of trading environment exists right now?" Not strategy selection, not trade generation.

Audit performed first — see
[docs/PHASE_19_9_MARKET_ENVIRONMENT_INTELLIGENCE_AUDIT.md](PHASE_19_9_MARKET_ENVIRONMENT_INTELLIGENCE_AUDIT.md).
**Found a real, direct naming-collision risk**: `bujji.decision_intelligence.models.EnvironmentAssessment`
(Phase 19.6) already exists — a different, narrower object (a plain mechanical state label). This phase's
`MarketEnvironmentAssessment` is deliberately named to not collide, per the same discipline every phase
since 19.1.1 has followed. `bujji/strategy` does not exist. No environment-taxonomy concept exists anywhere
else in `msi_strategy_selection_foundation`, `market_state_graph`, `market_understanding`, or `intelligence`.

## New package: `bujji/market_environment/`

- `models.py` — `MarketEnvironmentAssessment`, `EnvironmentType`
- `classify.py` — one documented rule per environment type
- `engine.py` — `build_market_environment_assessment()`, the one entry point

## Core Model — combines four layers, recomputes nothing

```python
environment_id: str                    # content fingerprint, same pattern as every prior phase
market_state_id: str                   # MarketStateNode.state_id (Phase 19.8), verbatim
environment_type: EnvironmentType
confidence: str                        # ENVIRONMENT confidence -- separate from observation confidence, never merged
supporting_conditions, blocking_conditions: Tuple[str, ...]
historical_similarity: Dict[str, Any]  # decision_intelligence.memory_context.to_dict() -- Phase 19.5/19.6's own observation-only object, verbatim
decision_posture: str                  # DecisionIntelligenceSnapshot.recommended_posture.value (Phase 19.6)
evidence_bundle: Dict[str, Any]
```

`historical_similarity` is not re-derived — `DecisionIntelligenceSnapshot.memory_context` (Phase 19.6,
itself built from Phase 19.5's `find_similar_memories_as_of()`) already carries `matched_count`,
`confidence_note`, and `statistic` with exactly the observation-never-prediction discipline this phase's
own spec calls for verbatim ("Historical similarity observed," never "Expected move is upward"). This
phase never queries the memory store itself — verified structurally (property 10) — the same "each layer
consumes only the previous layer" discipline every phase since 19.3 has followed.

## Environment Taxonomy — five categories, real conditions, honest fallback

| Type | Real conditions checked |
|---|---|
| `PREMIUM_SELLING_FAVOURABLE` | `volatility_state == IV_RICH` AND `liquidity_state` in {TIGHT, NORMAL} AND `event_state == NORMAL` AND `regime` in {RANGING, COMPRESSED} AND no contradictions — all four required, never a partial match |
| `PREMIUM_SELLING_UNFAVOURABLE` | ANY of: `liquidity_state == WIDE`, `event_state == EVENT_RISK`, `VOLATILITY_EXPANSION` phenomenon present |
| `TREND_FOLLOWING_FAVOURABLE` | `regime == TRENDING` (confirmed) AND `VOLATILITY_EXPANSION` phenomenon present AND liquidity healthy — all three required |
| `MEAN_REVERSION_FAVOURABLE` | `regime == RANGING` AND no volatility expansion/compression phenomenon (stable) AND no major transition in progress — all three required |
| `STAND_ASIDE` | contradictions present, OR `DecisionPosture.INSUFFICIENT_INFORMATION`, OR an in-progress transition with `LOW` interpretation confidence — checked FIRST, and also the honest fallback when no other category's conditions are fully met |

Every condition check reads a field that already exists on `MarketStateNode` (Phase 19.8) or
`DecisionIntelligenceSnapshot` (Phase 19.6) — no new indicator, no new brain call, no free-form reasoning.
`STAND_ASIDE` is evaluated first (safety takes priority, the same discipline
`decision_intelligence.reasoning.derive_posture()` already established for `REDUCE_EXPOSURE`), and is also
the fallback when nothing else's real conditions are fully met — **never a forced FAVOURABLE label without
complete, real supporting evidence.** Verified live: a flat, zero-variance snapshot (realized_vol == 0 →
volatility richness `UNKNOWN` → `DecisionPosture.INSUFFICIENT_INFORMATION`) correctly produced
`STAND_ASIDE`, not a guessed favourable category.

## Critical Rule — verified structurally, not just asserted

No `BUY`/`SELL`/`CE`/`PE`/order-shaped token appears anywhere: an AST identifier scan across all four files
(function/class/variable names), a direct field check on `MarketEnvironmentAssessment`, and a real built
assessment's fully serialized content all confirm this. `"Environment supports premium selling"` — the
literal phrase this phase's evidence strings compose (`"volatility: IV_RICH"`, `"liquidity: TIGHT"`, etc.)
— never `"sell NIFTY 24500 CE."`

## Historical Intelligence Integration — verified live

Built a real `MarketMemoryEntry` for one day, queried Phase 19.5's own `find_similar_memories_as_of()`
against it from a later day, and threaded the real matches through `DecisionIntelligenceSnapshot.memory_context`
into `MarketEnvironmentAssessment.historical_similarity` — confirmed byte-identical to
`decision_intelligence.memory_context.to_dict()`, with `matched_count >= 1` and no "expected"/probability
language anywhere in the serialized output.

## Confidence Separation — two fields, verified never combined

`MarketEnvironmentAssessment.confidence` (environment confidence — "how sure are we this classification
applies," derived from how many of a category's real conditions matched) is structurally separate from the
OBSERVATION confidence already embedded inside `evidence_bundle` (Phase 19.6's own min-across-brains
confidence, unchanged, just carried forward). The two are never read into, or merged into, one number
anywhere in this package.

## Testing

`tests/test_market_environment.py`, 12 tests, proving all 10 required properties:

1. **Deterministic fingerprint** — same inputs → same `environment_id`, `.fingerprint()` self-consistent.
2. **Replay equality** — LIVE vs HISTORICAL_REPLAY produce the identical environment type and id.
3. **No trade vocabulary** — 3 tests: AST identifier scan, no order-shaped field, and a real built
   assessment's full serialized content contains no BUY/SELL/CE/PE token.
4. **Evidence traceability** — a real `PREMIUM_SELLING_FAVOURABLE` classification carries ≥1 real,
   non-empty supporting condition string.
5. **Memory integration** — historical similarity matches `decision_intelligence.memory_context` exactly,
   with a real match count and no prediction language, using an isolated `tmp_path` `EventStore`.
6. **Contradiction handling** — rich premiums + wide liquidity spread (a real, constructed contradiction,
   same scenario Phase 19.6 already proved) correctly routes to `STAND_ASIDE`, never forcing
   `PREMIUM_SELLING_FAVOURABLE` from the rich-IV signal alone.
7. **Insufficient-data handling** — a genuinely flat, zero-variance snapshot correctly produces
   `STAND_ASIDE` rather than a guessed favourable category.
8. **No direct strategy imports** — AST import scan: no `msi_strategy_selector`/
   `msi_strategy_selection_foundation`/`strategy_taxonomy_bridge` import anywhere.
9. **No execution imports** — AST import scan: no broker/`fyers`/execution-engine/order-construction import.
10. **No datastore bypass** — AST import scan (no `EventStore`/sqlite/Reality-tier import) + AST call scan
    (no `.analyze` call, no brain instantiation) — combined into one test for efficiency, covering both
    "no datastore" and the same "compose, don't call brains" discipline every phase since 19.6 has proven.

## Full regression

Baseline before this phase: 5,858 passed (post Phase 19.8). After Phase 19.9's additions: **5,870 passed,
0 failed** — exactly the 12 new tests. No existing file was modified, only three new self-contained modules
in `bujji/market_environment/` + one new test file.

## Explicitly NOT built this phase (per the phase's own scope)

- ❌ Strategy selection, entry rules, position sizing, options structure, orders, backtesting engine.

## Final verdict

Bujji can now say, from real, cited evidence across every layer built since Phase 19.0: *"Current market
environment is favorable for premium strategies due to stable range conditions, elevated volatility, and
healthy liquidity"* or *"unfavorable for premium selling because volatility is expanding, event risk is
elevated, and liquidity has deteriorated. Historical similarity exists but confidence is reduced"* —
verified live against exactly these real constructed scenarios. Per the user's own framing: Phase 19.7 gave
Bujji phenomenon awareness, Phase 19.8 gave it market-evolution awareness, and this phase gives it
environment awareness — the last piece before Phase 20 can finally ask "given this environment, which
strategy family deserves consideration."

## Recommendation carried forward from the user's own closing note

The user recommended freezing the entire intelligence foundation (Phase 19.0–19.9) after this phase and
running a Shadow Market Intelligence Campaign on live NSE sessions before adding strategy selection. This
implementation did not attempt that campaign — it is a live-observation exercise, not a code-authoring
task, and is the user's own next decision to schedule.
