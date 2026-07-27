# Risk Brain Architecture

**BUJJI Options OS v3 — Engineering Series 35, Sprint 1**

## Status

Deployed. This is the first independent risk gatekeeper in the Trading
Brain, per `TRADING_BRAIN_CONSTITUTION.md`. It lives at
`bujji/trading_brain/risk_brain/`, depending only on the frozen Market
State Builder (Series 33) and Strategy Selector (Series 34) — neither
of which this sprint modifies.

## Purpose and Philosophy

The Strategy Selector answered "what strategy fits today's market?"
This module answers a deliberately different question: "should this
strategy be allowed to trade today at all?" A correctly-selected
strategy can still be rejected here because the risk environment is
unacceptable — this module never re-evaluates whether the strategy
choice itself was appropriate, and never asks "will we make money?"

## Inputs: Exactly Two Already-Produced Objects

`engine.py::assess()` accepts exactly a `StrategyDecision` and a
`MarketStateAssessment` (either may be `None`) — never MIC v2, never
an `EvidenceInterpretation`, never a broker, replay engine, live feed,
or trading history. It reads fields already produced upstream
(`selected_strategy`, `market_character`, `market_phase`, `confidence`,
`supporting_evidence`) and never recomputes or recreates any market
analysis.

## The Decision Table

Evaluated top to bottom; first matching rule wins.

| # | Condition | Status | Approval | Risk Level | Blocking Reason |
|---|---|---|---|---|---|
| 1 | No `MarketStateAssessment` supplied | `INSUFFICIENT_EVIDENCE` | `UNKNOWN` | `UNKNOWN` | `INSUFFICIENT_DATA` |
| 2 | No `StrategyDecision` supplied | `REJECTED` | `DENY` | `UNKNOWN` | `NO_STRATEGY` |
| 3 | `selected_strategy` is `None` | `REJECTED` | `DENY` | `UNKNOWN` | `NO_STRATEGY` |
| 4 | Defensive: `selection_status == SELECTED` but no strategy named | `REJECTED` | `DENY` | `UNKNOWN` | `UNSUPPORTED_STRATEGY` |
| 5 | `market_character == INSUFFICIENT_EVIDENCE`, or `confidence == UNKNOWN` | `INSUFFICIENT_EVIDENCE` | `UNKNOWN` | `UNKNOWN` | `INSUFFICIENT_DATA` |
| 6 | `market_character == UNCERTAIN` | `REJECTED` | `DENY` | `HIGH` | `CONTRADICTORY_EVIDENCE` |
| 7 | `market_character == MIXED` | `REJECTED` | `DENY` | `UNKNOWN` | `UNKNOWN_MARKET` |
| 8 | `market_character` is `CONTESTED` or `CLEAR` | see "Base Verdict" below | | | (none — never rejected past this point) |

Rule 4 is a defensive consistency check for an upstream contract
violation Series 34 never actually produces (a `SELECTED` status with
no named strategy). It is structurally rare by construction, included
only because this module never trusts an upstream object blindly.

## Base Verdict for CONTESTED / CLEAR

Once evidence is sufficient and not contradictory, risk is graded from
`market_character` and `confidence`:

| Market Character | Confidence | Risk Level | Warning |
|---|---|---|---|
| `CONTESTED` | any | `MODERATE` | `CONTESTED_MARKET` |
| `CLEAR` | `HIGH` / `VERY_HIGH` | `LOW` | none |
| `CLEAR` | `MODERATE` | `MODERATE` | `LOW_CONFIDENCE` |
| `CLEAR` | `LOW` / `VERY_LOW` | `HIGH` | `LOW_CONFIDENCE` |

Two further modifiers apply after the base verdict, each capable of
adding a warning:

- **No supporting evidence** (`market_assessment.supporting_evidence`
  is empty), only checked under `CLEAR`: adds `WEAK_EVIDENCE`.
- **`market_phase == UNSTABLE`**: adds `HIGH_VARIABILITY` and bumps
  `risk_level` up exactly one step on the finite ordering
  `LOW < MODERATE < HIGH < EXTREME` (capped at `EXTREME`).

If any warning was added, `status = APPROVED_WITH_WARNINGS`,
`approval = ALLOW_WITH_CONTROLS`. Otherwise `status = APPROVED`,
`approval = ALLOW`.

## Required Controls

Controls are added alongside their triggering condition, deduplicated:
`MONITOR_MORE_FREQUENTLY` for `CONTESTED`, `MODERATE`-confidence
`CLEAR`, weak evidence, or instability; `REDUCE_SIZE` additionally for
`LOW`/`VERY_LOW`-confidence `CLEAR`. `required_controls` is always
empty when `status == APPROVED`. These are recommendations only — no
control here is ever executed, sized, or enforced by this module or
any module in this sprint.

## Confidence: Preserve or Downgrade, Never Invent

`confidence` always starts from `MarketStateAssessment.confidence` (or
is set to the literal string `"UNKNOWN"` in the early-exit rules where
no real market read exists to preserve). Whenever at least one warning
was added, confidence is stepped down **exactly one position** on the
finite scale, regardless of how many distinct warnings apply — this
sprint never applies more than one downgrade step per assessment, and
never upgrades confidence under any path.

## Decision Trace: Always Explainable

`decision_trace` states the selected strategy id, the market character,
the confidence, the resulting risk level, any warnings (by name), and
the final approval — built entirely from data already present on the
two input objects and this function's own computed verdict. Nothing is
generated from data the trace doesn't also expose.

## Determinism

`assessment_id` is derived via `hashlib.md5` over the source strategy
decision's id (or `"NONE"`), the source market assessment's id (or
`"NONE"`), the status, the approval, and the timestamp — never
`uuid4()`. `timestamp` is genuinely wall-clock-derived by design (an
injectable `clock` parameter defaulting to `datetime.now`), the same
pattern used throughout the Trading Brain. Given the same two inputs
and the same fixed clock, `assess()` always produces a byte-identical
`RiskAssessment`.

## Journaling

`bujji/journal/risk_brain_journal.py::RiskBrainJournal` — append-only
JSONL, own schema (`schema_version` field), independent of every other
journal in the codebase.

## Query API

`risk_brain/query.py::RiskAssessmentIndex` mirrors the established
`*Index` pattern: `ingest()`, then read-only `latest()`, `history()`,
`find_by_id()`, `find_by_status()`, `find_by_approval()`, `summary()`.
No mutation method beyond `ingest`.

## What This Sprint Explicitly Does Not Build

Per the specification's own explicit exclusions, this sprint contains
zero: PnL, probability, expected value, win rate, Kelly/Sharpe/Sortino,
Monte Carlo, optimization, learning, adaptation, execution, broker
calls, order placement, capital allocation, position sizing, or trade
management logic.

## Isolation Guarantees

- No import of `mic_v2` (any module) anywhere in this package.
- No import of `bujji.intelligence`'s `PublicationRecord`/
  `ConsumerRecord`, and no import of the Evidence Interpreter's own
  models, anywhere in this package.
- No import of `bujji.core`, `bujji.execution`, `bujji.trade`,
  `bujji.broker`, `bujji.market`, or `bujji.tick` anywhere in this
  package.
- No PnL, probability, expectancy, score, or position-size field
  anywhere on `RiskAssessment`.
- `assess()` is a pure function apart from its injectable clock; every
  id is `hashlib.md5`-derived, never `uuid4()`, and no randomness of
  any kind appears anywhere in this package.
- Confidence is only ever passed through, stepped down by exactly one
  position, or collapsed to `UNKNOWN` — never invented or increased.

**The Risk Brain is the mandatory gatekeeper between strategy selection
and every future component that will determine position size,
execution, and trade management. It is the first layer that can
independently veto a trade while remaining completely deterministic
and fully explainable.**
