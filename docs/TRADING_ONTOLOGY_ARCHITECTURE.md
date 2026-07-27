# Trading Ontology Architecture

**BUJJI Options OS v3 — Engineering Series 31, Sprint 1**

## Status

Deployed. This is the first Trading Brain module, per
`TRADING_BRAIN_CONSTITUTION.md`. It lives in a new package,
`bujji/trading_brain/ontology/`, entirely separate from
`bujji/intelligence/` (which speaks to MIC v2, the Intelligence
Engine) and from every existing trading/execution package
(`bujji/core`, `bujji/execution`, `bujji/trade`, `bujji/broker`).

## Purpose and Philosophy

This sprint builds **language, not logic**. Every future Trading Brain
module — Evidence Interpreter, Market State Builder, Strategy
Selector, Strategy Constructor, Risk Brain, Position Manager,
Execution Planner, Trade Manager, Learning Engine, Strategy Evolution,
Meta Brain, Portfolio Brain — must describe the world using these
seven finite vocabularies rather than each inventing its own ad-hoc
strings, magic numbers, or free-floating floats.

There is deliberately **no `engine.py`** in this package. Nothing here
selects a strategy, scores risk, sizes a position, or decides
anything. The only function this package provides,
`ontology/runner.py::build_snapshot()`, is a strict validator/packager:
it accepts one candidate value per vocabulary and either returns a
well-formed `TradingOntologySnapshot` or raises `ValueError` if any
value isn't a real member of its vocabulary. It never coerces an
unrecognized string into `UNKNOWN` — that would silently let ad-hoc
vocabulary leak in through the back door. `UNKNOWN` is a legitimate,
honestly-reached state (produced by future modules when evidence is
insufficient), never a fallback for typos or unauthorized values.

## The Seven Vocabularies

| Vocabulary | Values | Question it answers |
|---|---|---|
| Market State | `UNKNOWN, TREND, RANGE, REVERSAL, BREAKOUT, VOLATILE, QUIET, EVENT_DRIVEN` | What kind of market is this, structurally? |
| Opportunity State | `UNKNOWN, AVOID, WATCH, LOW_EDGE, MEDIUM_EDGE, HIGH_EDGE` | Is there an edge worth pursuing today? |
| Risk State | `UNKNOWN, LOW, NORMAL, HIGH, EXTREME` | How much danger is in the environment right now? |
| Execution Intent | `UNKNOWN, NO_TRADE, PREPARE, ENTER, ADD, REDUCE, ROLL, HEDGE, EXIT` | What action, if any, is intended right now? |
| Strategy Intent | `UNKNOWN, SELL_PREMIUM, BUY_PREMIUM, DELTA_NEUTRAL, DIRECTIONAL_BULLISH, DIRECTIONAL_BEARISH, VOLATILITY_EXPANSION, VOLATILITY_CONTRACTION` | What kind of options exposure is desired? |
| Capital Intent | `UNKNOWN, NO_ALLOCATION, SMALL, NORMAL, LARGE, MAXIMUM` | How much capital should be committed? |
| Confidence (shared) | `UNKNOWN, VERY_LOW, LOW, MODERATE, HIGH, VERY_HIGH` | How sure is any module about any of the above? |

Every vocabulary includes `UNKNOWN` as the honest "insufficient
evidence" default — the same discipline MIC v2 has used since its
first sprint.

## One Shared Confidence Scale

Confidence is deliberately singular. Every future module that needs to
express "how sure am I" imports `ConfidenceLevel`-equivalent constants
from `ontology/taxonomy.py` rather than defining its own scale or
emitting a raw float. A single shared scale is what makes confidence
comparable across Strategy Selector, Risk Brain, and Meta Brain
without a conversion layer.

## Independent Versioning

`taxonomy.py` gives each vocabulary its own version constant
(`MARKET_STATE_VERSION`, `RISK_STATE_VERSION`, etc.) in addition to an
overall `ONTOLOGY_PACKAGE_VERSION`. Extending one vocabulary (adding a
new `MarketState` value in a later series) bumps only that
vocabulary's version, never forcing every other vocabulary — or every
already-journaled snapshot — to change. Extension is additive-only:
an existing constant's name or meaning is never altered or removed
once published.

## The Composite Snapshot

`TradingOntologySnapshot` (models.py) is the one composite object
every future module constructs, reads, and passes along: exactly one
value from each of the six state/intent vocabularies, plus one
confidence value, plus provenance (`source`, `ruleset_version`,
`reasoning_summary`) and a deterministic `snapshot_id`. The dataclass
itself is a frozen, pure data container — it performs no validation.
All enforcement lives in `runner.py::build_snapshot()`, the single
required entry point for constructing a snapshot.

## Determinism

`snapshot_id` is derived via `hashlib.md5` over the seven vocabulary
values plus timestamp and source — never `uuid4()`. `timestamp` is
genuinely wall-clock-derived by design (via an injectable `clock`
parameter defaulting to `datetime.now`, the same pattern used
throughout MIC v2's lifecycle and observability engines) — tests that
assert determinism across repeated calls must supply a fixed `clock`
or exclude `snapshot_id`/`timestamp` from equality, exactly as MIC v2's
Atlas determinism tests do for `case_id`.

## Journaling

`bujji/journal/trading_ontology_journal.py::TradingOntologyJournal` —
append-only JSONL, own schema (`schema_version` field), independent of
every other journal in the codebase (MIC v2's or BUJJI's own).

## Query API

`ontology/query.py::OntologyIndex` mirrors the established `*Index`
pattern from MIC v2: `ingest()`, then read-only `latest()`,
`history()`, `find_by_id()`, `find_by_market_state()`,
`find_by_execution_intent()`, `summary()`. No mutation method beyond
`ingest`.

## What This Sprint Explicitly Does Not Build

Per the Trading Brain Constitution's own separation principle, this
sprint contains zero:
- Strategy selection logic
- Risk scoring or veto logic
- Position sizing or construction logic
- Execution planning or order logic
- Learning or evolution logic
- Capital allocation logic
- Trade management logic

Those are each a future, separate Engineering Series, built entirely
on top of this shared vocabulary — never inside it.

## Isolation Guarantees

- No import of `bujji.core`, `bujji.execution`, `bujji.trade`,
  `bujji.broker`, or `bujji.intelligence` anywhere in
  `bujji/trading_brain/ontology/`.
- No decision logic, no scoring, no PnL field, no probability field
  anywhere in this package.
- `build_snapshot()` is a pure function apart from its injectable
  clock; every id is `hashlib.md5`-derived, never `uuid4()`.
- Rejecting an unrecognized value raises `ValueError` immediately —
  it is never silently coerced to `UNKNOWN`.

**The Trading Ontology defines the language every future Trading
Brain module must speak. It makes no decisions, selects no strategy,
scores no risk, and constructs no position — it only gives every
future module a finite, versioned, shared vocabulary to describe the
world in.**
