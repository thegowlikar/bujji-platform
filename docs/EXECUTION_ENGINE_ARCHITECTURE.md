# Execution Engine Architecture

**BUJJI Options OS v3 — Engineering Series 39, Sprint 1**

## Status

Deployed. This is the bridge between the Trading Brain's decisions and
a future Broker Adapter, per `TRADING_BRAIN_CONSTITUTION.md`. It lives
at `bujji/trading_brain/execution_engine/`, depending only on the
frozen Execution Planner (Series 37) — which this sprint does not
modify.

## Purpose and Philosophy

Everything above this layer has already decided what strategy, whether
it is allowed, how much capital is authorized, and what execution plan
should exist. The Execution Engine never revisits any of those
decisions. It converts an already-produced, broker-independent
`ExecutionPlan` into an ordered sequence of abstract, broker-agnostic
workflow actions — it orchestrates, it does not decide, and it does
not execute.

```
Trading Brain -> Execution Planner -> Execution Engine -> Broker Adapter -> Broker SDK
```

Execution Engine is the bridge. Broker Adapter performs translation.
Broker SDK performs execution. Those three responsibilities must never
merge — this sprint builds only the first.

## Input: Exactly One ExecutionPlan

`engine.py::orchestrate()` accepts exactly one `ExecutionPlan` (or
`None`) — never MIC v2, the Market State Builder, the Strategy
Selector, the Risk Brain, the Capital Brain, a broker SDK, a replay
engine, or a live feed.

## The Decision Table

Evaluated top to bottom; first matching rule wins.

| # | Condition | Status | Actions | Blocking Condition |
|---|---|---|---|---|
| 1 | No `ExecutionPlan` supplied | `UNKNOWN` | (none) | `MISSING_PLAN` |
| 2 | `plan.status == UNKNOWN` | `UNKNOWN` | (none) | `MISSING_PLAN` |
| 3 | `plan.status == NOT_PLANNED` | `BLOCKED` | `BLOCK` | `UNKNOWN_INTENT` |
| 4 | `plan.status == BLOCKED` | `BLOCKED` | `BLOCK` | `FAILED_VALIDATION` |
| 5 | `plan.status == PLANNED` but no `strategy_id` (defensive) | `BLOCKED` | `VALIDATE_PLAN`, `BLOCK` | `FAILED_VALIDATION` |
| 6 | `plan.status == PLANNED`, well-formed | `READY_FOR_ADAPTER` | `VALIDATE_PLAN`, `VALIDATE_CONTROLS`, `AUTHORIZE_EXECUTION`, `WAIT_FOR_ADAPTER` | (none) |

Rule 5 is a defensive consistency check for an upstream contract
violation Execution Planner's own policy never actually produces (a
`PLANNED` status with no named strategy). It is structurally rare by
construction, included only because this module never trusts an
upstream object blindly — the same discipline every prior Trading
Brain sprint has applied to its own inputs.

`required_controls` is always passed through verbatim from
`ExecutionPlan.required_controls` — this module never recomputes or
reinterprets a control an earlier stage already named.

## Abstract Actions: Workflow, Never Broker Instructions

The four-action sequence produced by rule 6 —
`VALIDATE_PLAN → VALIDATE_CONTROLS → AUTHORIZE_EXECUTION →
WAIT_FOR_ADAPTER` — is purely conceptual. None of the seven declared
actions (`VALIDATE_PLAN`, `VALIDATE_CONTROLS`, `AUTHORIZE_EXECUTION`,
`WAIT_FOR_ADAPTER`, `EXECUTE`, `COMPLETE`, `BLOCK`) is a broker
instruction, an order field, or a payload. `EXECUTE` and `COMPLETE`
are declared for taxonomy completeness and forward compatibility but
are **never produced by this sprint's engine** — this module
orchestrates only up to `WAIT_FOR_ADAPTER` and stops; actually
executing, or observing an execution's completion, both require a live
Broker Adapter this module deliberately does not have.

## Confidence: Preserve or Downgrade, Never Invent

`confidence` always starts from `ExecutionPlan.confidence`. It is
passed through unchanged in every terminal branch that merely reflects
what an earlier stage already decided (rules 1-4) — this module is not
introducing a new signal there, only relaying one. It is stepped down
by exactly one position in exactly two cases where this engine itself
discovers something new: rule 5's defensive validation failure, and
rule 6 whenever the plan carries a real (non-`NONE`) execution
constraint — reflecting the same "additional constraint introduced"
discipline used by every downstream confidence rule since the Capital
Brain (Series 36). Confidence is never invented and never increased
under any path.

## Execution Trace: Always Explainable

`execution_trace` states the source plan's own status, the ordered
action sequence (or "none"), and the final status — built entirely
from data already present on the input `ExecutionPlan` and this
function's own computed verdict.

## Determinism

`instruction_set_id` is derived via `hashlib.md5` over the source
plan's id (or `"NONE"`), the status, and the timestamp — never
`uuid4()`. `timestamp` is genuinely wall-clock-derived by design (an
injectable `clock` parameter defaulting to `datetime.now`), the same
pattern used throughout the Trading Brain. Given the same
`ExecutionPlan` and the same fixed clock, `orchestrate()` always
produces a byte-identical `ExecutionInstructionSet`.

## Journaling

`bujji/journal/execution_engine_journal.py::ExecutionEngineJournal` —
append-only JSONL, own schema (`schema_version` field), independent of
every other journal in the codebase.

## Query API

`execution_engine/query.py::ExecutionInstructionSetIndex` mirrors the
established `*Index` pattern: `ingest()`, then read-only `latest()`,
`history()`, `find_by_id()`, `find_by_status()`, `find_by_plan()`,
`summary()`. No mutation method beyond `ingest`.

## What This Sprint Explicitly Does Not Build

Per the specification's own explicit exclusions, this sprint contains
zero: broker connectivity (FYERS, Zerodha, or any SDK), REST payload
generation, order submission, fill monitoring, quantity/strike/expiry
calculation, retry logic, position management, decision modification,
or optimization.

## Isolation Guarantees

- No import of MIC v2, the Market State Builder, the Strategy
  Selector, the Risk Brain, the Capital Brain, a broker SDK, a replay
  engine, or a live feed module anywhere in this package — only the
  frozen `ExecutionPlan` model this module consumes.
- No import of `bujji.core`, `bujji.execution`, `bujji.trade`,
  `bujji.broker`, `bujji.market`, or `bujji.tick` anywhere in this
  package.
- No order id, REST payload, strike, expiry, quantity, or fill field
  anywhere on `ExecutionInstructionSet`.
- `orchestrate()` is a pure function apart from its injectable clock;
  every id is `hashlib.md5`-derived, never `uuid4()`, and no randomness
  of any kind appears anywhere in this package.
- Confidence is only ever passed through or stepped down by exactly
  one position — never invented or increased.

**The Execution Engine converts an approved, broker-independent
execution plan into an ordered, explainable, abstract instruction set
— never a broker call. When this sprint is complete, the Trading Brain
ends at a broker-neutral execution contract; the only remaining
production-facing layer is the Broker Adapter, whose sole
responsibility is translating these abstract instructions into the
specific API calls a given broker requires.**
