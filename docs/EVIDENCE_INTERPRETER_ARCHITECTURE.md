# Evidence Interpreter Architecture

**BUJJI Options OS v3 — Engineering Series 32, Sprint 1**

## Status

Deployed. This is the architectural firewall between MIC v2
Intelligence and the Trading Brain, per
`TRADING_BRAIN_CONSTITUTION.md`. It lives at
`bujji/trading_brain/evidence_interpreter/`, alongside — and depending
only on — the frozen Trading Ontology (Engineering Series 31, now
closed to modification absent a discovered architectural flaw).

## Purpose and Philosophy

MIC v2 speaks the language of Intelligence. The Trading Brain speaks
the language of Decisions. The Evidence Interpreter translates between
them and **never decides**. It answers exactly one question — "what
does today's published intelligence mean in Trading Ontology
vocabulary?" — and never the question "what should we trade?"

Every future Trading Brain module (Strategy Selector, Risk Brain,
Position Manager, Learning Engine, Portfolio Brain, Strategy
Evolution) must consume `EvidenceInterpretation` objects exclusively.
None of them may import MIC v2's `PublicationRecord` or
`ConsumerRecord` models, and none of them may read raw evidence,
replay objects, broker data, or a live feed. This package is the only
gateway.

## Inputs Are Already-Extracted Classification Strings

This package never imports MIC v2's own Python package and never
imports `bujji.intelligence`'s `PublicationRecord`/`ConsumerRecord`
models — it has no way to reach into MIC v2 even if it wanted to, by
construction. `engine.py::interpret()` accepts plain classification
strings (e.g. `"BULLISH"`, `"TRENDING_UP"`,
`"APPROVED_WITH_WARNINGS"`) — exactly the kind of value BUJJI's
existing `intelligence/mic_adapter` readers already expose via their
own `translate_classification`-style functions. Extracting those
strings from a published MIC v2 artifact remains that existing
adapter's job, not this package's.

## One Field, One Source, Deterministic Table Lookup

Per the specification's own recommended mapping, each Trading Ontology
field is driven by exactly one MIC v2 layer. There is no voting, no
fusion, no weighting, no reasoning — every mapping is a plain
dictionary lookup in `taxonomy.py::MAPPINGS`.

| Ontology Field | Source Layer | Mapping |
|---|---|---|
| Market State | Market Context (trend) | `TRENDING_UP/TRENDING_DOWN → TREND`, `SIDEWAYS → RANGE`, `TRANSITION → REVERSAL`, `UNKNOWN → UNKNOWN` |
| Strategy Intent | Market Opinion | `BULLISH → DIRECTIONAL_BULLISH`, `BEARISH → DIRECTIONAL_BEARISH`, `NEUTRAL → DELTA_NEUTRAL`, `MIXED/INSUFFICIENT_EVIDENCE/UNKNOWN → UNKNOWN` |
| Confidence | Context Stability | `STABLE → VERY_HIGH`, `MOSTLY_STABLE → HIGH`, `TRANSITIONING → MODERATE`, `HIGHLY_VARIABLE → LOW`, `INSUFFICIENT_HISTORY/UNKNOWN → UNKNOWN` |
| Opportunity State | Calibration | `CALIBRATED → HIGH_EDGE`, `MOSTLY_CALIBRATED → MEDIUM_EDGE`, `UNCALIBRATED → AVOID`, `INSUFFICIENT_HISTORY/UNKNOWN → UNKNOWN` |
| Risk State | Governance | `APPROVED → NORMAL`, `APPROVED_WITH_WARNINGS → HIGH`, `REVIEW_REQUIRED → HIGH`, `REJECTED → EXTREME`, `UNKNOWN → UNKNOWN` |
| Execution Intent | Lifecycle | `ACTIVE → PREPARE`, `STALE/ARCHIVED/SUPERSEDED → NO_TRADE`, `UNKNOWN → UNKNOWN` |
| Capital Intent | Intelligence Contract | `COMPLETE → NORMAL`, `PARTIAL/LEGACY → SMALL`, `EXPERIMENTAL → NO_ALLOCATION`, `UNKNOWN → UNKNOWN` |

Each mapping table carries its own independent version constant
(mirroring the Trading Ontology's own per-vocabulary versioning), plus
a single overall `INTERPRETER_VERSION`.

## An Honest Note on Unreachable Ontology Values

Because each ontology field has exactly one MIC v2 source, not every
value in that field's vocabulary is reachable through this sprint's
mapping alone:

- **Market State**: `BREAKOUT`, `VOLATILE`, `QUIET`, `EVENT_DRIVEN` are
  unreachable — Market Context's Volatility/Liquidity/Regime
  dimensions are not consulted this sprint.
- **Strategy Intent**: `SELL_PREMIUM`, `BUY_PREMIUM`,
  `VOLATILITY_EXPANSION`, `VOLATILITY_CONTRACTION` are unreachable —
  Market Opinion carries no volatility-premium view.
- **Opportunity State**: `WATCH` is unreachable — Calibration's three
  real states resolve only to `AVOID`/`MEDIUM_EDGE`/`HIGH_EDGE`.
- **Risk State**: `LOW` is unreachable — a clean Governance approval
  confirms only the absence of known issues, not a positively
  low-risk environment, so `APPROVED → NORMAL`, never `LOW`.
- **Execution Intent**: `ENTER`, `ADD`, `REDUCE`, `ROLL`, `HEDGE`,
  `EXIT` are unreachable — Lifecycle only tells us whether the
  underlying intelligence is fresh, never whether to act on a
  position.
- **Capital Intent**: `LARGE`, `MAXIMUM` are unreachable — contract
  completeness alone can never justify scaling capital up beyond
  `NORMAL`.

Reaching any of these unreachable values requires a later Trading
Brain module that fuses multiple sources — never this one. This is the
same disclosed-limitation discipline used throughout MIC v2 (e.g.
Governance's `REVERSAL`, Lifecycle's `SUPERSEDED`).

## Never Fabricate: Absence Becomes UNKNOWN, With a Reason

If a source layer's classification is not supplied at all (`None`),
the corresponding ontology field becomes `UNKNOWN`, and its
`TranslationProvenance.reason` records exactly why (e.g. `"No
market_context supplied."`). This is distinct from a source value that
itself maps to `UNKNOWN` (e.g. Market Opinion's own `MIXED`) — that
distinction is preserved via `source_value` being `None` only in the
former case, never collapsed into a single ambiguous state.

If a source layer's classification is supplied but is not a
recognized member of its own domain, `engine.py` raises `ValueError`
immediately. It is never silently coerced — the same closed-vocabulary
discipline established in the Trading Ontology itself.

## Provenance: Always Explainable

Every `EvidenceInterpretation.translation_provenance` entry answers,
for one ontology field: which MIC v2 layer produced it, what that
layer's raw value was (or `None`), which mapping table version was
applied, and a human-readable reason. The Trading Brain must always be
able to answer "why do I believe Risk is HIGH?" — the answer is always
present on the object itself, never requiring a lookup elsewhere.

## The Composite Output

`EvidenceInterpretation` (models.py) wraps a `TradingOntologySnapshot`
(constructed via the frozen `ontology/runner.py::build_snapshot()` —
never reimplemented) together with the seven `TranslationProvenance`
records, a `source_versions` map (mapping-table version actually
applied per source layer, or `None` where that layer was absent), a
`timestamp`, and the package's `interpreter_version`. It is a frozen
dataclass; `engine.py::interpret()` is the only way to construct one
correctly.

## Determinism

`interpretation_id` is derived via `hashlib.md5` over the underlying
snapshot's own id, the shared timestamp, and every source layer/value
pair — never `uuid4()`. `timestamp` is genuinely wall-clock-derived by
design (an injectable `clock` parameter defaulting to `datetime.now`),
exactly the Trading Ontology's own pattern; the same fixed timestamp is
threaded into the nested snapshot's construction so the two never
diverge.

## Journaling

`bujji/journal/evidence_interpreter_journal.py::EvidenceInterpreterJournal`
— append-only JSONL, own schema (`schema_version` field), independent
of the Trading Ontology's own journal and of every MIC v2 journal.

## Query API

`evidence_interpreter/query.py::EvidenceInterpretationIndex` mirrors
the established `*Index` pattern: `ingest()`, then read-only
`latest()`, `history()`, `find_by_id()`, `find_by_market_state()`,
`find_by_risk_state()`, `summary()`. No mutation method beyond
`ingest`.

## What This Sprint Explicitly Does Not Build

Per the specification's own explicit exclusions, this sprint contains
zero:
- Strategy selection logic
- Market state fusion across multiple MIC v2 dimensions
- Risk scoring or veto logic
- Execution or position logic
- Learning or evolution logic
- Capital allocation logic
- Trade or portfolio management logic

## Isolation Guarantees

- No import of `mic_v2` (any module) anywhere in this package.
- No import of `bujji.intelligence`'s `PublicationRecord` or
  `ConsumerRecord` models anywhere in this package.
- No import of `bujji.core`, `bujji.execution`, `bujji.trade`,
  `bujji.broker`, `bujji.market`, or `bujji.tick` anywhere in this
  package.
- No PnL field, no probability field, no score field anywhere on
  `EvidenceInterpretation` or `TranslationProvenance`.
- `interpret()` is a pure function apart from its injectable clock;
  every id is `hashlib.md5`-derived, never `uuid4()`.
- An unrecognized source classification raises `ValueError`
  immediately — it is never silently coerced to `UNKNOWN`.

**The Evidence Interpreter is the constitutional boundary between
knowing and deciding. It translates MIC v2 intelligence into Trading
Ontology vocabulary, with full provenance, and answers only "what does
today's intelligence mean?" — never "what should we trade?"**
