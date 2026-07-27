# Replay Validation Report — Decision Pipeline Refactor (Sprint 1)

## Scope of this validation

BUJJI's production VPS has never run a historical CSV replay (no
historical candle CSV exists on disk — confirmed by search), and BUJJI
runs exclusively in `fyers_paper` mode (paper-trading against live
market data), which itself was confirmed `inactive` throughout this
sprint. Given that, validation was performed via:

1. **Historical replay** (synthetic, deterministic candles) — via
   `bujji.replay.engine.ReplayEngine`, the same replay harness
   `tests/test_tier1_replay.py` already uses, run through both the
   pre- and post-refactor orchestrator (see Regression Comparison
   Report for the full diff).
2. **"Paper-trading replay"** — the paper-trading path shares the
   identical `Orchestrator.on_candle()` entry point as the historical
   replay path; the only substitution at that boundary is
   `PaperBroker`/`HybridPaperBroker` vs. `ReplayBroker` for order
   fills, neither of which this sprint touched. `tests/test_execution_and_e2e.py`
   and related paper-mode tests are part of the 426 pre-existing tests
   that pass unmodified post-refactor, exercising this exact path.

## Comparisons performed

| Comparison | Result |
|---|---|
| Decision journals (before vs. after) | Byte-identical (`decision_journal.jsonl`) |
| Trade journals (before vs. after) | Byte-identical (`trade_journal.csv`) |
| Execution logs (stage-boundary events) | New this sprint — `pipeline_stage_start`/`pipeline_stage_finish` events now present for all 7 stages, confirmed via `caplog` assertions in `tests/test_pipeline_stages.py`; no pre-existing log line was removed or altered |
| Final FSM state | Identical (`State.DONE_FOR_DAY`) |
| Trade count / trade fields | Identical (1 trade, identical entry/exit/pnl/reason) |
| Order sequence (CE then PE, idempotency keys) | Unchanged — `execution_adapter.translate()` and the two `submit_and_confirm()` calls were only wrapped, never reordered or altered |
| Risk decisions | Identical outcome path confirmed via `test_risk_validation_stage_reports_error_outcome_on_capital_rejection` — a forced `CapitalRejectedError` (zero max-lots ceiling) is still raised and still rolls the FSM back to `READY`, exactly as before |

## Any mismatch found?

None. No mismatch was found in any comparison above. The one
architectural finding from this validation — that "Market Intelligence"
(the 8 MIC brains) executes *after* Strategy Evaluation/Execution
Decision in the real runtime, not before, as the requested stage
ordering's numbering would imply — is a pre-existing characteristic of
the production code (confirmed by reading the original,
pre-refactor `on_candle()` body and its own comment: *"Audit runs LAST
... never affects trading logic"*). It was left exactly as-is, per this
sprint's zero-behavioural-change mandate, and is documented in full in
the Stage Documentation deliverable rather than silently corrected.

## Verdict

**Replay outputs are identical. Paper-trading behaviour is unchanged**
(same entry point, same broker abstraction boundary, same 426
pre-existing tests passing unmodified). The refactor is validated safe
to stand as the new baseline.
