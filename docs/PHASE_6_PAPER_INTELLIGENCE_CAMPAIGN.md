# Phase 6 — Paper Intelligence Campaign Preparation

## 1. Architectural finding: the literal chain could not be built honestly

The requested chain was:

```
MarketIntelligenceSnapshot -> CycleEvidence -> market_thesis -> intelligence_orchestrator -> DecisionArtifact
```

Direct trace of `bujji/shadow_runtime/intelligence_pipeline_adapter.py` (the real
build path for `MarketIntelligenceSnapshot`) shows it imports and composes
ONLY the legacy `RegimeBrain`/`StructureBrain`/`VolatilityBrain`/`GreeksBrain`/
`EventBrain` lineage. It imports **zero** of MDI/PSI/MSSI/VSB/MPPI/Consensus/
`msi_trade_thesis` — the exact objects `CycleEvidence` and `market_thesis.assess()`
require. There is no real MSI-shaped data inside a `MarketIntelligenceSnapshot`
to honestly extract into a `CycleEvidence`.

Two dishonest options were rejected:
- Fabricating a translation from the brain-lineage Reading types into
  MSI-shaped fields (inventing evidence that was never actually produced).
- Building a second thesis object from the brain lineage (explicitly
  forbidden — "do not create duplicate thesis objects").

**What was actually built instead:** `CycleEvidence` stays sourced from
`IntelligenceCycleRecorder.last_evidence` — the real MSI path, proven in
Phase 5. `MarketIntelligenceSnapshot` is attached to `DecisionArtifact` as a
separate, disclosed `perception_*` field group — never merged into
`market_regime`/`directional_bias`/etc. (those stay `market_thesis`'s own).

This is honest because both objects are guaranteed to describe the SAME
cycle: `ShadowSessionRunner`'s own constructor docstring states
`intelligence_pipeline_enabled` structurally requires
`intelligence_cycle_enabled` (it reuses the same already-fetched
`MarketSnapshot` + candles). So whenever a real `MarketIntelligenceSnapshot`
exists for a cycle, that cycle's real `CycleEvidence` exists too — confirmed
this phase via a real `ShadowSessionRunner.start()` run with both flags on
(`tests/test_paper_intelligence_campaign.py`).

This is the practical meaning of the standing instruction "MarketIntelligenceSnapshot
remains the perception layer, market_thesis remains the interpretation layer,
do not merge them": two real, independent reads, both disclosed, side-by-side,
never silently reconciled into one.

## 2. Runtime diagram (post-Phase-6 wiring)

```
FyersBroker (read-only, disable_live_execution)
        |
        v
ShadowSessionRunner  (market_perception_enabled=True,
                       intelligence_cycle_enabled=True,
                       intelligence_pipeline_enabled=True)
        |
        |--> MarketDataAdapter -> MarketSnapshot + candles  (one real broker fetch/cycle)
        |
        |--> IntelligenceCycleRecorder.record_cycle(snapshot, candles)
        |         builds real: MDI, PSI, MSSI, MPPI, VSB, Consensus,
        |         LiquidityReading, PremiumBehaviour, TradeThesis, SSF assessments
        |         -> stores as CycleEvidence (.last_evidence)
        |
        |--> intelligence_pipeline_adapter.build_intelligence_heartbeat_cycle(snapshot, candles)
        |         builds real: RegimeReading, StructureReading, VolatilityReading,
        |         GreeksReading, EventReading, independent MarketThesis, ContradictionScore
        |         -> MarketIntelligenceSnapshot  (._previous_intelligence_snapshot)
        |
        v
on_cycle_evidence(evidence, perception_snapshot)     <- NEW this phase, additive callback
        |
        v
paper_intelligence_mode.run_cycle(evidence, perception_snapshot=..., session_id=...)
        |
        |--> market_thesis.assess(evidence.*)                     -> MarketThesisAssessment
        |--> intelligence_orchestrator.orchestrate(context)        -> DecisionTrace (NO_TRADE honestly,
        |                                                             no MarketStateAssessment source exists)
        |--> MarketThesisRegimeProvider(thesis) -> TradingSessionGovernor's OWN
        |         strategy_selector.select_strategy(trend, vol)    -> StrategySelectionResult
        |         (IRON_CONDOR / IRON_FLY only, real function, no stateful lock touched)
        |
        v
build_decision_artifact(trace, thesis, governor_selection, perception_snapshot)
        |
        v
DecisionArtifact  (market_thesis fields | governor_* fields | perception_* fields
                    -- three separate, disclosed, never-merged groups)
        |
        v
DecisionArtifactJournal.append()   (append-only JSONL, one file per session)

STRUCTURALLY UNREACHABLE FROM THIS PATH: PaperBroker, TradingSessionGovernor's
stateful session/lock lifecycle, msi_trade_construction, execution_engine,
risk_governor. Confirmed by import-list / dir() checks, not string search.
```

## 3. Files changed this phase

New:
- `bujji/market_state/cycle_evidence.py` — `CycleEvidence` frozen dataclass
- `scripts/run_paper_intelligence_campaign.py` — live intraday runner entrypoint
- `deploy/bujji-paper-intelligence-campaign.{service,timer}` — not installed
- `tests/test_paper_intelligence_campaign.py` — full-cycle proof (4 tests)

Edited, additive only:
- `bujji/shadow_runtime/shadow_session_runner.py` — new `on_cycle_evidence` optional
  constructor param, fires at the end of `_run_market_perception_step()`, wrapped in
  its own try/except (never breaks the main loop)
- `bujji/decision_artifact/models.py`, `engine.py` — 5 new `perception_*` Optional fields
- `bujji/paper_intelligence_mode/engine.py` — new `perception_snapshot=None` param,
  passed straight through to `build_decision_artifact()`
- `bujji/market_state/intelligence_cycle_recorder.py` — `last_evidence` property (Phase 5)

Everything else (`market_thesis`, `intelligence_orchestrator`, `TradingSessionGovernor`'s
own `strategy_selector.select_strategy()`, `PaperBroker`) is unmodified this phase.

## 4. Learning readiness audit

| Component | Status | Notes |
|---|---|---|
| `DecisionArtifact` | Real, persisted | One per cycle, append-only journal |
| Outcome Memory (`mfe`/`mae` fields) | Real fields, always `None` today | `compute_mfe_mae()` is pure and ready; no caller yet supplies real per-cycle valuation history — genuine gap, honestly disclosed since Phase 5 |
| Position Lifecycle | Real state machine (`PositionLifecycleRuntime`), Phase 15G/15I | Runs independent of Paper Intelligence Mode — no positions are opened by this phase's runner, so nothing to track yet |
| Decision Artifact <-> Outcome Memory linkage | NOT built | No trade is constructed in this phase (`construction=None` always), so there is nothing for an outcome to attach to yet. This is the natural next gap once Paper TRADING Mode is authorized |
| `DecisionArtifactJournal` query interface | Real (`find_by_decision_id`, `find_by_learning_tag`) | Every artifact today reads `learning_tag="NO_TRADE"` (or `OPEN`/`WIN`/`LOSS` once real construction exists) |

**Bottom line:** the system can observe and record its own reasoning per cycle
today. It cannot yet learn from outcomes, because no trade is constructed in
Paper Intelligence Mode by design — that requires Paper TRADING Mode, not
authorized this phase.

## 5. Proof: one simulated full market cycle

`tests/test_paper_intelligence_campaign.py::TestFullPaperIntelligenceCampaignCycle::test_one_complete_cycle_with_perception_cross_reference`
constructs a real `ShadowSessionRunner` with all three flags on, a `FakeBroker`
returning production-shaped responses, and the real `on_cycle_evidence` callback
wired straight to `paper_intelligence_mode.run_cycle()`. Result (2 cycles,
`max_cycles=2`):
- `session_artifact.errors == ()`
- 2 real `DecisionArtifact`s produced, one per cycle
- Each has real `market_regime` (from `market_thesis`) AND real
  `perception_snapshot_id`/`perception_posture` (from `MarketIntelligenceSnapshot`)
  — both present, in separate fields, never merged
- Journaled and read back byte-identical via `DecisionArtifactJournal`

Also proven: a raising `on_cycle_evidence` callback never breaks the session
(`artifact.runtime_health["heartbeats"] == 2` even when the callback raises
every cycle), and omitting the callback is fully backward compatible.

## 6. Regression

Full suite: **6703 passed**, 0 failed (up from 6699 at end of Phase 5; +4 new
tests this phase). Deployed and verified on the VPS.

## 7. Protected engines

`market_thesis`, `intelligence_orchestrator`, `TradingSessionGovernor`'s own
`strategy_selector.select_strategy()`, and `PaperBroker` were not touched this
phase — confirmed against this session's own edit list, cross-checked with
`git status`/mtimes on the VPS.

## 8. What is explicitly NOT done this phase

Per the standing instruction: no PaperBroker order is placed, no position is
opened, `TradingSessionGovernor`'s stateful session/lock lifecycle is never
touched by this runner. Only observation + reasoning artifacts are produced.
`scripts/run_paper_intelligence_campaign.py` requires a manually refreshed
FYERS token (`source /tmp/local_fyers.env` or `/opt/bujji/.env` for the
systemd path) before it can run against live data — not yet run live.
