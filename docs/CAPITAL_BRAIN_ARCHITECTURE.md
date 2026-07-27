# Capital Brain Architecture

**BUJJI Options OS v3 — Engineering Series 36, Sprint 1**

## Status

Deployed. This is the capital governance layer of the Trading Brain,
per `TRADING_BRAIN_CONSTITUTION.md`. It lives at
`bujji/trading_brain/capital_brain/`, depending only on the frozen
Risk Brain (Series 35) — which this sprint does not modify.

## Purpose and Philosophy

The Risk Brain answered "should this trade be allowed?" This module
answers a deliberately narrower question: "assuming it is allowed,
what capital policy should be authorized?" Capital is a scarce
resource; this module allocates a **policy**, not an execution plan —
think of an institutional risk committee approving a budget, leaving
the trading desk to decide implementation later.

## Input: Exactly One RiskAssessment

`engine.py::authorize()` accepts exactly one `RiskAssessment` (or
`None`) — never the Strategy Selector, the Market State Builder, MIC
v2, a broker, an order, a replay engine, or execution of any kind.

## The Decision Table

Evaluated top to bottom; first matching rule wins.

| # | Condition | Capital Intent | Allocation Status | Constraint | Required Control |
|---|---|---|---|---|---|
| 1 | No `RiskAssessment` supplied | `UNKNOWN` | `UNKNOWN` | `NONE` | `NONE` |
| 2 | `risk_level == EXTREME` (priority override, regardless of approval) | `NONE` | `DENIED` | `MANUAL_REVIEW` | `NONE` |
| 3 | `approval == DENY` | `NONE` | `DENIED` | `NONE` | `NONE` |
| 4 | `approval == UNKNOWN` | `UNKNOWN` | `UNKNOWN` | `NONE` | `NONE` |
| 5 | `approval == ALLOW_WITH_CONTROLS` | `REDUCED` | `LIMITED` | `REDUCE_EXPOSURE` | `FOLLOW_RISK_CONTROLS` |
| 6 | `approval == ALLOW`, `risk_level == LOW` | `STANDARD` | `APPROVED` | `NONE` | `NONE` |
| 7 | `approval == ALLOW`, `risk_level == MODERATE` | `REDUCED` | `LIMITED` | `REDUCE_EXPOSURE` | `FOLLOW_LIMITS` |
| 8 | `approval == ALLOW`, `risk_level == HIGH` | `MINIMAL` | `LIMITED` | `MAX_SINGLE_POSITION` | `FOLLOW_LIMITS` |
| 9 | `approval == ALLOW`, unrecognized `risk_level` (defensive) | `NONE` | `DENIED` | `MANUAL_REVIEW` | `NONE` |
| 10 | unrecognized `approval` (defensive) | `UNKNOWN` | `UNKNOWN` | `NONE` | `NONE` |

Rule 2's `EXTREME` override sits ahead of the approval check
deliberately: this module never trusts an `ALLOW`/`ALLOW_WITH_CONTROLS`
paired with `EXTREME` risk, a combination the Risk Brain's own policy
never actually produces (Series 35's `UNCERTAIN` branch, the only path
to `EXTREME`, always pairs it with `DENY`) — but this module never
trusts an upstream object blindly. Rule 9 is the analogous defensive
check for `ALLOW` paired with an unrecognized risk level.

## An Honest Note on Unreachable Values

- **Capital Intent `FULL`** is declared in the taxonomy for
  completeness and forward compatibility but is never produced by this
  sprint's policy — the most permissive real outcome is `STANDARD`
  (clean `ALLOW` + `LOW` risk). Authorizing `FULL` would require a
  richer signal than a single `RiskAssessment` provides; that is a
  future series's decision, not this one's.
- **Constraint `REQUIRE_HEDGE`** is likewise declared but never
  produced — this module has no signal from `RiskAssessment` alone
  that specifically calls for a hedge rather than a broader exposure
  reduction (`REDUCE_EXPOSURE`) or position cap
  (`MAX_SINGLE_POSITION`).

## Confidence: Preserve or Downgrade by Exactly One Level

`confidence` always starts from `RiskAssessment.confidence`. It is
downgraded by exactly one level whenever this module introduces an
**additional allocation constraint** beyond `NONE` (rules 2, 5, 7, 8);
it is preserved unchanged when no new constraint is introduced (rules
3, 4, 6, 10) or set to the literal `"UNKNOWN"` only when no real
upstream confidence exists to preserve (rules 1, 9). Confidence is
never invented and never increased under any path.

## Decision Trace: Always Explainable

`decision_trace` states the risk approval, the risk level, the
resulting capital intent, the constraints, and the final allocation
status — built entirely from the input `RiskAssessment`'s own fields
and this function's own computed verdict.

## Determinism

`decision_id` is derived via `hashlib.md5` over the source risk
assessment's id (or `"NONE"`), the capital intent, the allocation
status, and the timestamp — never `uuid4()`. `timestamp` is genuinely
wall-clock-derived by design (an injectable `clock` parameter
defaulting to `datetime.now`), the same pattern used throughout the
Trading Brain. Given the same `RiskAssessment` and the same fixed
clock, `authorize()` always produces a byte-identical `CapitalDecision`.

## Journaling

`bujji/journal/capital_brain_journal.py::CapitalBrainJournal` —
append-only JSONL, own schema (`schema_version` field), independent of
every other journal in the codebase.

## Query API

`capital_brain/query.py::CapitalDecisionIndex` mirrors the established
`*Index` pattern: `ingest()`, then read-only `latest()`, `history()`,
`find_by_id()`, `find_by_capital_intent()`,
`find_by_allocation_status()`, `summary()`. No mutation method beyond
`ingest`.

## What This Sprint Explicitly Does Not Build

Per the specification's own explicit exclusions, this sprint contains
zero: lot calculation, quantity calculation, margin calculation,
broker exposure computation, account balance reads, return
optimization, Kelly/Sharpe usage, expectancy estimation, position
management, order placement, upstream-decision mutation, randomness,
or machine learning.

## Isolation Guarantees

- No import of `mic_v2` (any module), the Strategy Selector, the
  Market State Builder, the Evidence Interpreter, or the Trading
  Ontology's engine internals anywhere in this package — only the
  frozen `RiskAssessment` model this module consumes.
- No import of `bujji.core`, `bujji.execution`, `bujji.trade`,
  `bujji.broker`, `bujji.market`, or `bujji.tick` anywhere in this
  package.
- No lot, quantity, margin, exposure, balance, PnL, or probability
  field anywhere on `CapitalDecision`.
- `authorize()` is a pure function apart from its injectable clock;
  every id is `hashlib.md5`-derived, never `uuid4()`, and no randomness
  of any kind appears anywhere in this package.
- Confidence is only ever passed through, stepped down by exactly one
  position, or set to `UNKNOWN` when nothing real exists to preserve —
  never invented or increased.

**The Capital Brain translates an approved risk decision into a
deterministic capital authorization policy — a budget, not an
execution plan — keeping capital governance fully independent of
broker-specific implementation details and preserving the layered
architecture established across the Trading Brain.**
