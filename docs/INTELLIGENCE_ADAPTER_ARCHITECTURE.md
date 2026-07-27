# Intelligence Adapter — Architecture (BUJJI Options OS, Integration Series 1, Sprint 1)

## Architectural boundary

The Intelligence Adapter is the **only** supported integration point
between BUJJI Options OS and MIC v2. **The Intelligence Adapter cannot
influence trading decisions. Its purpose is observational only.**

```
Consumer API           (mic_v2.consumer, Sprint 29, unmodified)
        │
        ▼
Intelligence Adapter (bujji/intelligence/mic_adapter/) — NEW
        │
        ▼
Decision Pipeline       (bujji/core/orchestrator.py, ONE observation point added)
```

`bujji/intelligence/mic_adapter/` is a new, clearly-separated subpackage
inside BUJJI's existing (and unrelated) `bujji/intelligence/` directory
— which already contains BUJJI's own live signal-computation "brains"
(`regime_brain.py`, `volatility_brain.py`, etc., feeding
`self._status.intelligence` into real trading decisions). The two
subsystems share a directory only by historical naming coincidence;
`mic_adapter/` is entirely self-contained and touches none of the
existing brain modules or their models.

## Read-only guarantees

The adapter:
- reads MIC v2's Consumer API only — specifically, the Consumer
  Journal (`mic_v2.journal.consumer_journal.ConsumerJournal`, Sprint
  29's own durable, append-only, already-published record).
- **never imports a MIC v2 reasoning engine** (verified by AST/text
  test scanning every `*.engine` module and every named MIC v2
  subpackage: fusion, hypothesis, candidate, qualification, memory,
  quality, graph, counterfactual, consistency, laboratory, experiment,
  simulation, certification, registry, lineage, compatibility,
  artifact, environment, archive, diff, explain, knowledge,
  publication, runtime, live, replay).
- **never reads a MIC v2 journal other than the Consumer Journal** —
  the only `mic_v2.*` import anywhere in the package is
  `mic_v2.journal.consumer_journal` (verified by AST test).
- never invokes replay, publication, certification, or runtime.
- has no direct filesystem access to any MIC v2 artifact except the one
  configured Consumer Journal path.
- never mutates a loaded record (verified by AST test: the only
  attribute assignments anywhere in the package are `self.<attr>`
  inside a class's own constructor).
- never opens a file directly in `adapter.py` (verified by AST test) —
  all file I/O happens inside `query.py`, via `ConsumerJournal.read_all()`
  (mode `'r'` only).
- never caches mutable state between calls — every `load_*` method
  re-reads the journal fresh, confirmed directly by test
  (`test_adapter_never_caches_stale_state`): a record appended to the
  journal between two calls is picked up immediately.

## Feature flag behaviour

`AppConfig.intelligence_adapter.enabled` (`IntelligenceAdapterSettings`,
`bujji/core/config.py`) defaults to **`False`**.

- **Disabled** (default): the `if self._cfg.intelligence_adapter.enabled:`
  guard in `Orchestrator._enter()` short-circuits before the adapter is
  ever constructed-and-called — zero behavioural change, confirmed
  directly by test (`test_flag_disabled_produces_no_intelligence_reference`):
  the resulting `DecisionJournal` row has no `intelligence_reference`
  key at all.
- **Enabled**: the adapter loads the latest snapshot once per entry
  decision and records exactly three reference strings
  (`snapshot_id`, `publication_id`, `replay_id`) onto the SAME
  `DecisionJournal` row — no other behaviour changes, confirmed
  directly by test (`test_flag_enabled_adds_only_reference_metadata`).

## Mapping rules

`mapper.py::map_consumer_record()` is a pure function: every field of
`IntelligenceSnapshot` is copied verbatim from the source
`ConsumerRecord` — no calculation, no inference, no enrichment, no
transformation beyond the field mapping itself. It is duck-typed on
purpose (reads attributes by name rather than importing
`mic_v2.models.consumer.ConsumerRecord`), so it has **zero** import
dependency on MIC v2 at all — confirmed directly by test
(`test_mapper_never_imports_mic_v2`) and exercised against a plain
stand-in object.

**Honesty note**: MIC v2's Consumer API (Sprint 29) exposes reference
IDs only (`certification_id`, `compatibility_id`, `lineage_id`, ...) —
it does not itself carry the underlying status strings (e.g.
`"CERTIFIED"`). `IntelligenceSnapshot` therefore carries the ids it was
actually given, plus the Consumer record's own overall `consumer_status`
(`AVAILABLE`/`NOT_AVAILABLE`/`UNKNOWN`); it does not fabricate a
certification/compatibility/lineage status value that was never present
in the source object. A future reader wanting those detailed statuses
would need to follow these ids into MIC v2's own Query APIs directly —
this adapter does not do that on their behalf, since that would mean
reaching past the Consumer API boundary.

## Isolation guarantees

- No function or method anywhere in `bujji/intelligence/mic_adapter/`
  has a name resembling order placement, execution, broker
  connectivity, strategy inspection, sizing, or risk (verified by AST
  test scanning every `FunctionDef`/`AsyncFunctionDef` name).
- `IntelligenceSnapshot`/`IntelligenceSnapshotProvenance` are frozen
  dataclasses with no mutable field, no trading-recommendation field,
  no execution field, no broker field, no PnL field — verified directly
  by test (`test_snapshot_is_frozen`, `test_provenance_is_frozen`,
  `test_snapshot_has_no_trading_fields`).
- The single integration point in `Orchestrator._enter()` is wrapped in
  its own `try`/`except`, matching the Decision Journal's own
  best-effort philosophy: any failure (missing journal, malformed line,
  MIC v2 not importable, ...) is logged and never blocks or alters the
  trade — confirmed directly by test
  (`test_missing_journal_file_never_blocks_trading`).
- The loaded snapshot is never read by `intention`, `planned`,
  `decision`, or any variable already computed earlier in `_enter()` —
  it flows only into the `intelligence_reference` dict handed to
  `DecisionJournal.record()`, which itself only ever adds it as an
  additional, optional JSON key.

## Real-world validation

A real BUJJI replay (via `ReplayEngine`, the exact live decision path)
was run twice against the same candles — once with the adapter
disabled, once enabled, both against MIC v2's real published Consumer
Journal:

- **Trade outcome**: identical (`final_state`, `trades` list byte-equal).
- **Trade journal CSV**: byte-identical.
- **Decision journal rows**: byte-identical except for the presence of
  a single additive `intelligence_reference` key
  (`{"snapshot_id": ..., "publication_id": ..., "replay_id": ...}`)
  when the flag is enabled.

This is the sprint's own success criterion, demonstrated directly
against real code and real data, not merely asserted by unit test.
