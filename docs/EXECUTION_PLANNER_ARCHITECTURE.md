# Execution Planner Architecture

**BUJJI Options OS v3 — Engineering Series 37, Sprint 1**

## Status

Deployed. This is the last broker-independent component in the
Trading Brain, per `TRADING_BRAIN_CONSTITUTION.md`. It lives at
`bujji/trading_brain/execution_planner/`, depending only on the frozen
Capital Brain (Series 36) and Strategy Selector (Series 34) — neither
of which this sprint modifies.

## Purpose and Philosophy

The Capital Brain answered "how much capital may be committed?" This
module answers a deliberately narrower question: "what
broker-independent execution PLAN should be created from that
authorization?" Planning and execution are separate responsibilities:
this module produces an abstract, portable plan any broker adapter can
later implement — it never places an order, calculates a quantity,
generates a broker payload, chooses a strike or expiry, or connects to
any broker.

## Inputs: Exactly Two Already-Produced Objects

`engine.py::plan()` accepts exactly a `CapitalDecision` and a
`StrategyDecision` (either may be `None`) — never a broker SDK, an
execution engine, an order manager, MIC v2, a live feed, a replay
engine, or market data.

## The Decision Table

Evaluated top to bottom; first matching rule wins.

| # | Condition | Status | Execution Intent | Constraint |
|---|---|---|---|---|
| 1 | No `CapitalDecision` supplied | `UNKNOWN` | `UNKNOWN` | `NONE` |
| 2 | `allocation_status == DENIED` | `NOT_PLANNED` | `NONE` | `NONE` |
| 3 | `allocation_status == UNKNOWN` | `UNKNOWN` | `UNKNOWN` | `NONE` |
| 4 | (only once capital would otherwise support planning) `StrategyDecision` missing or unresolved | `BLOCKED` | `UNKNOWN` | `MANUAL_APPROVAL` |
| 5 | `allocation_status == LIMITED` | `PLANNED` | `PREPARE` | `FOLLOW_RISK_CONTROLS`, `REDUCE_SIZE` |
| 6 | `allocation_status == APPROVED` | `PLANNED` | `PREPARE` | `NONE` |
| 7 | unrecognized `allocation_status` (defensive) | `UNKNOWN` | `UNKNOWN` | `NONE` |

Rule 4 is deliberately positioned after rules 2 and 3: if capital was
already denied or unresolved, there is no reason to check the strategy
at all — the outcome is already settled. Only once capital would
otherwise support planning (`LIMITED` or `APPROVED`) does a missing
strategy actually block anything.

`required_controls` on `ExecutionPlan` is always passed through
verbatim from `CapitalDecision.required_controls` — this module never
recomputes or reinterprets a control the Capital Brain already named.

## Execution Steps: Conceptual Workflow Only

Every `PLANNED` plan carries the same five-step ordered workflow,
built by `_standard_steps()`:

1. `VALIDATE` — Validate prerequisites (capital authorization and
   strategy selection are both present).
2. `VALIDATE` — Confirm capital authorization.
3. `VALIDATE` — Confirm strategy selection (names the selected
   strategy id).
4. `PREPARE` — Prepare execution.
5. `WAIT` — Await execution engine.

`NOT_PLANNED`, `BLOCKED`, and `UNKNOWN` plans carry zero steps — there
is nothing to prepare a workflow for. No step here is a broker
instruction, an order field, or a payload; each is a plain descriptive
sentence about a conceptual phase.

## An Honest Note on Unreachable Values

- **Execution Intent `ENTER`/`MONITOR`/`EXIT`** and the matching
  **Execution Step Types** are declared in the taxonomy for
  completeness and forward compatibility but are never produced by
  this sprint's policy — this planner only ever prepares. Actually
  entering, monitoring, or exiting a position requires live position
  awareness this module deliberately does not have; that belongs to a
  future Execution Engine.
- **Execution Constraint `WAIT_FOR_CONFIRMATION`** is likewise
  declared but never produced — this module has no signal from
  `CapitalDecision`/`StrategyDecision` alone that specifically calls
  for an external confirmation wait rather than the broader
  `MANUAL_APPROVAL` or `FOLLOW_RISK_CONTROLS` constraints it already
  expresses.

## Confidence: Preserve or Downgrade, Never Invent

`confidence` always starts from `CapitalDecision.confidence`. It is
downgraded by exactly one level whenever this module introduces an
additional planning constraint (rules 4 and 5); it is preserved
unchanged when no new constraint is introduced (rules 2, 3, 6, 7) or
set to `"UNKNOWN"` only when no real upstream confidence exists to
preserve (rule 1). Confidence is never invented and never increased
under any path.

## Planning Trace: Always Explainable

`planning_trace` states the capital intent, the selected strategy (or
`NONE`), the execution intent, the constraints, and the final status —
built entirely from data already present on the two input objects and
this function's own computed verdict.

## Determinism

`plan_id` is derived via `hashlib.md5` over the source capital
decision's id (or `"NONE"`), the source strategy decision's id (or
`"NONE"`), the status, the execution intent, and the timestamp — never
`uuid4()`. `timestamp` is genuinely wall-clock-derived by design (an
injectable `clock` parameter defaulting to `datetime.now`), the same
pattern used throughout the Trading Brain. Given the same two inputs
and the same fixed clock, `plan()` always produces a byte-identical
`ExecutionPlan`.

## Journaling

`bujji/journal/execution_planner_journal.py::ExecutionPlannerJournal`
— append-only JSONL, own schema (`schema_version` field), independent
of every other journal in the codebase.

## Query API

`execution_planner/query.py::ExecutionPlanIndex` mirrors the
established `*Index` pattern: `ingest()`, then read-only `latest()`,
`history()`, `find_by_id()`, `find_by_status()`, `find_by_strategy()`,
`summary()`. No mutation method beyond `ingest`.

## Architectural Boundary

The Execution Planner is the last broker-independent component in the
Trading Brain. Everything above it (Evidence Interpreter through
Capital Brain) is pure decision-making. Everything below it (a future
Execution Engine and Broker Adapter) is implementation. This
separation keeps the Trading Brain portable, testable, replayable, and
independent of any specific brokerage or execution technology.

## What This Sprint Explicitly Does Not Build

Per the specification's own explicit exclusions, this sprint contains
zero: order placement, quantity calculation, broker payload generation,
strike selection, expiry selection, FYERS/Zerodha connectivity, account
balance reads, fill monitoring, position management, execution
optimization, randomness, or machine learning.

## Isolation Guarantees

- No import of any broker SDK, execution engine, order manager, MIC
  v2, live feed, replay engine, or market data module anywhere in this
  package — only the frozen `CapitalDecision` and `StrategyDecision`
  models this module consumes.
- No import of `bujji.core`, `bujji.execution`, `bujji.trade`,
  `bujji.broker`, `bujji.market`, or `bujji.tick` anywhere in this
  package.
- No order id, broker payload, strike, expiry, quantity, or fill field
  anywhere on `ExecutionPlan` or `ExecutionStep`.
- `plan()` is a pure function apart from its injectable clock; every id
  is `hashlib.md5`-derived, never `uuid4()`, and no randomness of any
  kind appears anywhere in this package.
- Confidence is only ever passed through, stepped down by exactly one
  position, or set to `UNKNOWN` when nothing real exists to preserve —
  never invented or increased.

**The Execution Planner transforms an approved capital authorization
and selected strategy into a deterministic, explainable,
broker-independent execution plan — a conceptual workflow, not an
order — preserving the layered architecture established across the
Trading Brain and keeping it fully portable across any future broker
adapter.**
