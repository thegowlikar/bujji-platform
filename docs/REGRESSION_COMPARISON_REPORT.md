# Regression Comparison Report — Decision Pipeline Refactor (Sprint 1)

## Method

1. Backed up the pre-refactor `orchestrator.py` to `orchestrator.py.pre_sprint1_backup`.
2. Applied the refactor (stage-boundary observability only — see Stage Documentation).
3. Built a standalone probe (`replay_diff_probe.py`) that runs a fixed,
   deterministic candle sequence (`candles.csv`: entry candle at
   09:20, two follow-on candles, then a 15:05 EOD candle) through
   `ReplayEngine` and dumps final state, all trades, and every Decision
   Journal record to JSON.
4. Ran the probe with the **refactored** code → `output_after.json`.
5. Swapped in the **pre-refactor** `orchestrator.py`, ran the identical
   probe against the identical candles → `output_before.json`.
6. Restored the refactored `orchestrator.py`.
7. Diffed the two outputs, the raw `trade_journal.csv`, and the raw
   `decision_journal.jsonl`.

## Result

```
$ diff output_before.json output_after.json
41c41
<   "tag": "before",
---
>   "tag": "after",
```

The **only** difference is the `tag` field the probe itself writes to
label which run produced which file — an artifact of the comparison
tooling, not of the orchestrator. Every trading-relevant field is
identical:

- `final_state`: `State.DONE_FOR_DAY` in both runs.
- `trade_count`: `1` in both runs.
- The single trade's `entry_time`, `entry_premium`, `entry_spot`,
  `exit_time`, `exit_premium`, `exit_spot`, `exit_reason`,
  `daily_result`, `decision_id`, `trade_id`, `capital_status`,
  `approved_lots`, `capital_utilization`, `holding_time_min` — all
  byte-identical.
- `decision_count` and every Decision Journal record (strategy_version,
  market_observations, planned_contracts, planned_structure,
  broker_session) — byte-identical.

```
$ diff j.csv (before) j.csv (after)      -> no differences (exit code 0)
$ diff decision_journal.jsonl (before/after) -> no differences (exit code 0)
```

## Production test suite

| | Before refactor | After refactor |
|---|---|---|
| `pytest tests/` | 426 passed | 426 passed (unmodified) + 7 new pipeline-stage tests = 433 passed |

All 426 pre-existing tests — including `test_tier1_replay.py`,
`test_replay_capital.py`, and `test_replay_path_isolation.py`, which
exercise the exact refactored code paths (`on_candle`,
`_handle_pre_position`, `_enter`, `_journal_trade`) — pass unmodified,
with no test assertions changed.

## Verdict

**No behavioural regression detected.** Decisions, orders, risk actions,
and journal entries are identical before and after the refactor, across
both the standalone replay-diff probe and the full pre-existing test
suite.
