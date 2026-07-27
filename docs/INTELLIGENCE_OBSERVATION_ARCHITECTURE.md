# Intelligence Observation Architecture

**Integration Series 2 — Sprint 2: Continuous Intelligence Observation Framework**

## Status

Deployed. Feature-flag gated (`intelligence_adapter.enabled`, default `False`).
Purely additive to the existing Intelligence Adapter (Integration Series 1,
Sprint 1). No trading behavior change.

## Monitoring Philosophy

The Intelligence Adapter reads a MIC v2-published snapshot from a durable
JSONL journal on every decision cycle. That read can fail, return a stale
snapshot, or return nothing at all — none of which BUJJI's trading logic is
allowed to react to, by design (see
`docs/INTELLIGENCE_ADAPTER_ARCHITECTURE.md`). But an integration that fails
silently forever is not one anyone can operate with confidence.

The Observation Monitor exists to answer one question, continuously and
mechanically: **is the adapter itself working?** It measures the adapter's
own operational behavior — not the market, not a position, not a trade —
and records that measurement to its own journal. It is instrumentation
around the adapter, not a participant in any decision the adapter's caller
makes.

The monitor calls the adapter **exactly once per decision cycle** (never
more), timing that single call and classifying the outcome. It never
retries, never second-guesses, and never inspects anything the adapter
didn't already return.

## Operational Metrics

Tracked by `ObservationMetricsTracker` / `ObservationMetrics`
(`bujji/intelligence/mic_adapter/monitor/metrics.py`):

| Field | Meaning |
|---|---|
| `load_attempts` | Total adapter calls made |
| `load_successes` | Calls that returned without raising |
| `load_failures` | Calls that raised |
| `missing_snapshot_count` | Successful calls that found no snapshot |
| `stale_snapshot_count` | Snapshots found older than the staleness threshold |
| `last_latency_seconds` | Wall-clock time of the most recent adapter call |
| `mean_latency_seconds` | Running mean latency across all calls |
| `provenance` | `ObservationProvenance(source, ruleset_version)` |

None of these fields carry any trading, position, PnL, order, or broker
information — enforced structurally (frozen dataclass, fixed field set) and
by an AST-based test (`test_metrics_has_no_trading_fields`) that fails the
build if a forbidden field name is ever added.

## Health States

Taxonomy version `1.0.0`, five finite states
(`bujji/intelligence/mic_adapter/monitor/health.py`):

| State | Meaning |
|---|---|
| `HEALTHY` | Adapter enabled, load succeeded, snapshot found, fresh, fast |
| `DEGRADED` | As `HEALTHY`, but adapter latency exceeded `observation_degraded_latency_seconds` |
| `STALE` | Snapshot found but older than `observation_stale_after_seconds` |
| `UNAVAILABLE` | Adapter call raised, or no snapshot was found at all |
| `UNKNOWN` | Feature flag is disabled — health is not applicable, not "bad" |

Classification is a pure function, `classify_health(...)`, evaluated in a
fixed priority order: flag-disabled → load-failed → snapshot-missing →
stale → degraded → healthy. Every call to `ObservationMonitor.observe()`
resolves to exactly one of these five states; the state is embedded directly
in the frozen `ObservationHealth` record along with a human-readable
`reason`.

## Freshness Calculation

Snapshot age is computed as `(observation_timestamp - snapshot.timestamp)`
in seconds, using the monitor's injected `clock` (defaults to
`datetime.now`, replaced with a fixed function in every test for
determinism). A snapshot is `STALE` once this age exceeds
`ObservationMonitorConfig.stale_after_seconds` (default 7 days — matched to
the adapter's own operating cadence, not to any trading timeframe).
Latency is measured independently via an injected `latency_clock`
(defaults to `time.perf_counter`), and a call is `DEGRADED` once elapsed
latency exceeds `degraded_latency_seconds` (default 0.5s).

## Isolation Guarantees

- **No influence on trading.** `_enter()` in `bujji/core/orchestrator.py`
  calls the monitor strictly *after* the existing (Sprint 1) intelligence
  reference block and strictly *before* Stage 7 (Journal Recording). Both
  calls are wrapped in `try/except`; an observation failure is logged and
  discarded, never propagated, never blocking the trading decision that has
  already been made.
- **No mutation of the Decision Journal.** The Observation Journal
  (`bujji/journal/intelligence_observation_journal.py`) is a wholly separate
  file with its own schema. It never imports, opens, or writes to
  `decision_journal.py` or its file — verified by an AST-based test
  (`test_journal_never_touches_decision_journal_file`).
- **No MIC v2 reasoning-engine coupling.** The monitor imports only the
  adapter it observes; it never imports any MIC v2 fusion, hypothesis,
  qualification, memory, or reasoning module — verified by
  `test_monitor_never_imports_mic_reasoning_engines`.
- **No broker/position/PnL access anywhere in the monitor package** —
  verified structurally via AST identifier scans
  (`test_monitor_never_reads_broker_or_position_state`,
  `test_no_forbidden_trading_identifiers_in_monitor_code`).
- **No non-`self` mutation** anywhere in the monitor's core modules —
  verified by `test_monitor_never_mutates_non_self_attributes`.
- **Append-only journal** — the journal only ever opens its file in `"a"`
  or `"r"` mode, verified by
  `test_observation_journal_only_opens_files_in_append_or_read_mode`.
- **Deterministic given a fixed clock** — `ObservationHealth` identity
  fields (`health_id`, `health_status`, `timestamp`, `snapshot_age_seconds`)
  are exactly reproducible across independent `ObservationMonitor`
  instances given the same injected clock and latency clock.
- **Feature-flag gated, default off.** With `intelligence_adapter.enabled =
  False`, the monitor and observation journal are constructed but never
  invoked beyond a single `UNKNOWN` classification path if called directly;
  in the live orchestrator, the entire block is skipped and no observation
  journal file is ever created.

**Observation metrics exist solely to qualify integration health. They
cannot influence trading.**
