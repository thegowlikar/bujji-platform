# Market State Builder Architecture

**BUJJI Options OS v3 — Engineering Series 33, Sprint 1**

## Status

Deployed. This is the first genuine reasoning component in the Trading
Brain, per `TRADING_BRAIN_CONSTITUTION.md`. It lives at
`bujji/trading_brain/market_state/`, depending only on the frozen
Trading Ontology (Series 31) and the frozen Evidence Interpreter
(Series 32) — neither of which this sprint modifies.

## Purpose and Philosophy

The Evidence Interpreter answered "what does each intelligence layer
mean?" — one source, one field, table lookup. The Market State Builder
answers a fundamentally different question: "taking all of today's
translated intelligence together, what kind of market am I actually
looking at, and how much do I trust that read?"

This is the first module in the pipeline permitted to fuse multiple
signals into a single conclusion. It is still not permitted to decide
anything about trading: there is no strategy field, no risk
allocation, no position size, no execution decision anywhere in
`MarketStateAssessment`. It only describes market structure and the
quality of the evidence behind that description.

## Input: Exactly One EvidenceInterpretation

The firewall established in Series 32 remains absolute.
`engine.py::assess()` accepts exactly one `EvidenceInterpretation` —
never MIC v2 directly, never a `PublicationRecord` or `ConsumerRecord`,
never a candle, never a broker call, never a replay object. Everything
this module reasons about is already present on that one object: its
`ontology_snapshot` (the translated ontology values) and its
`translation_provenance` (the raw MIC v2 classification strings and
the reason each was derived, used here purely to produce a readable
`reasoning_trace`).

## The Four Fused Signals

Of the seven fields Series 32 translates, this Builder fuses exactly
four — the four that speak to market *structure* and the *quality* of
that structural read:

| Signal | Ontology Field | Source Layer |
|---|---|---|
| Structure | `market_state` | Market Context (trend) |
| Stability | `confidence` | Context Stability |
| Opportunity quality | `opportunity_state` | Calibration |
| Governance | `risk_state` | Governance |

`strategy_intent`, `execution_intent`, and `capital_intent` are
deliberately not consulted — those describe what to *do*, not what the
market *is*, and belong to later Trading Brain modules (Strategy
Selector, Risk Brain, Capital Brain).

## An Honest Note on Unreachable Market States

Because this Builder's only structural signal is `market_state` as
translated by the Evidence Interpreter — which itself derives solely
from Market Context's Trend dimension — `BREAKOUT`, `VOLATILE`,
`QUIET`, and `EVENT_DRIVEN` remain structurally unreachable this
sprint, exactly as they were unreachable to the Evidence Interpreter.
This is not a shortcut taken by this module; it is an inherited
limitation of what the Series-32 firewall currently exposes. Reaching
those states honestly requires a future series that extends the
Evidence Interpreter to translate Market Context's Volatility,
Liquidity, and Regime dimensions as well — never a workaround here that
reads more than `EvidenceInterpretation` exposes.

## Fusion: A Finite Decision Table, Nothing Else

`engine.py::assess()` evaluates, in order, exactly one rule that
applies:

1. **Three or four of the four signals are missing** (their MIC v2
   layer was never supplied) → `market_character = INSUFFICIENT_EVIDENCE`,
   `market_state = UNKNOWN`, `confidence = UNKNOWN`. There is not
   enough evidence to reason from, and none is fabricated.
2. **`market_state` itself did not resolve** (Market Context was
   missing or itself classified `UNKNOWN`) → `market_character = MIXED`,
   `market_state = UNKNOWN`. No active contradiction exists among the
   other signals; the evidence is simply silent on structure.
3. **A defined market state exists and zero signals contradict it** →
   `market_character = CLEAR`. `market_state` and `confidence` are
   reported as translated, unchanged.
4. **A defined market state exists and two or more signals contradict
   it** → `market_character = UNCERTAIN`. `market_state` is withheld
   (`UNKNOWN`) and `confidence` becomes `UNKNOWN` — the read exists in
   the underlying data but is not trusted enough to report.
5. **A defined market state exists and exactly one signal contradicts
   it** → `market_character = CONTESTED`. `market_state` is still
   reported, but `confidence` is stepped down exactly one position on
   the finite Confidence scale (see "Confidence" below).

"Contradicts" is itself finite and named, never scored: Governance
reporting `HIGH`/`EXTREME` risk, Calibration reporting `AVOID`, or
Context Stability reporting `LOW`/`VERY_LOW` confidence each count as
one contradiction against an otherwise-defined market state.
"Supports" is the mirror: strong stability, good calibration, or
normal governance each count as one point of agreement. Every
supporting or contradicting signal is recorded verbatim in
`supporting_evidence`/`contradicting_evidence` — nothing is hidden or
summarized away.

## Confidence: Stepped, Never Invented

Confidence is never a probability, a machine-learned score, or a raw
number. It is always one of the ontology's own six finite Confidence
values. This module only ever does one of three things to it:

- Passes it through unchanged (`CLEAR`, or `market_state` undefined)
- Steps it down exactly one position toward `VERY_LOW` (`CONTESTED`)
- Collapses it to `UNKNOWN` (`UNCERTAIN`, `INSUFFICIENT_EVIDENCE`)

It never steps confidence *up*, and it never invents a value beyond
what stability itself already implied. `market_conviction` is computed
identically to `confidence` in this sprint — a disclosed, deliberate
simplification; a future series may compute conviction about the
specific state claim separately from confidence in the assessment as a
whole.

## Market Phase: Purely From Stability

`market_phase` is a direct, independent function of the stability
signal (`confidence`): `HIGH`/`VERY_HIGH` → `ESTABLISHED`,
`MODERATE` → `EMERGING`, `LOW`/`VERY_LOW` → `UNSTABLE`, `UNKNOWN` →
`UNKNOWN`. It answers "how settled is the current regime?" — a
question orthogonal to `market_character`'s "how much do I trust this
read?"

## Reasoning Trace: Always Explainable

Every `MarketStateAssessment.reasoning_trace` is a human-readable
sentence built from the same provenance the Evidence Interpreter
already recorded — e.g. *"TREND because Derived from
market_context=TRENDING_UP. Context Stability (MOSTLY_STABLE) supports
a defined Market State (TRENDING_UP). No contradictions."* Nothing in
the trace is generated from data not already present on
`supporting_evidence`/`contradicting_evidence`.

## Determinism

`assessment_id` is derived via `hashlib.md5` over the source
interpretation's own id, the final market state, character, confidence,
phase, and timestamp — never `uuid4()`. `timestamp` is genuinely
wall-clock-derived by design (an injectable `clock` parameter
defaulting to `datetime.now`), the same pattern used throughout the
Trading Ontology and Evidence Interpreter. Given the same
`EvidenceInterpretation` and the same fixed clock, `assess()` always
produces a byte-identical `MarketStateAssessment`.

## Journaling

`bujji/journal/market_state_journal.py::MarketStateJournal` —
append-only JSONL, own schema (`schema_version` field), independent of
every other journal in the codebase.

## Query API

`market_state/query.py::MarketStateIndex` mirrors the established
`*Index` pattern: `ingest()`, then read-only `latest()`, `history()`,
`find_by_id()`, `find_by_market_state()`, `find_by_market_character()`,
`summary()`. No mutation method beyond `ingest`.

## What This Sprint Explicitly Does Not Build

Per the specification's own explicit exclusions, this sprint contains
zero:
- Strategy selection or recommendation logic
- Trade recommendation logic
- Capital allocation logic
- Direction prediction logic
- Probability or expectancy computation
- Position, ranking, or optimization logic
- Learning, adaptation, or evolution logic

## Isolation Guarantees

- No import of `mic_v2` (any module) anywhere in this package.
- No import of `bujji.intelligence`'s `PublicationRecord` or
  `ConsumerRecord` models anywhere in this package.
- No import of `bujji.core`, `bujji.execution`, `bujji.trade`,
  `bujji.broker`, `bujji.market`, or `bujji.tick` anywhere in this
  package.
- No strategy, recommendation, PnL, probability, expectancy, or score
  field anywhere on `MarketStateAssessment`.
- `assess()` is a pure function apart from its injectable clock; every
  id is `hashlib.md5`-derived, never `uuid4()`.
- Confidence and market state are only ever passed through, stepped
  down, or collapsed to `UNKNOWN` — never invented or increased beyond
  what the underlying evidence supports.

**The Market State Builder is the first place in the Trading Brain
that reasons rather than merely translates. It answers only "what
market am I looking at, and how sure am I?" — never "what should I do
about it?" Everything downstream (Strategy Selector, Risk Brain,
Capital Brain, Execution Brain) will trust this component's honesty
about what it does and does not know.**
