# Strategy Selector Architecture

**BUJJI Options OS v3 — Engineering Series 34, Sprint 1**

## Status

Deployed. This is the first component in the Trading Brain capable of
autonomous decision-making, per `TRADING_BRAIN_CONSTITUTION.md`. It
lives at `bujji/trading_brain/strategy_selector/`, depending only on
the frozen Trading Ontology (Series 31), the frozen Evidence
Interpreter (Series 32), and the frozen Market State Builder
(Series 33) — none of which this sprint modifies.

## Purpose and Philosophy

Everything before this sprint only described the market. This sprint
answers the first genuinely decision-shaped question: "given
everything we know about today's market, which single existing
options strategy should we trade — if any?"

It does not ask "which strategy would make the most money?" That
question never appears anywhere in `engine.py`, and is explicitly
forbidden by the specification. It asks a narrower, honest question,
strategy by strategy: "can this strategy honestly operate under
today's market state?"

## Input: Exactly One MarketStateAssessment

The firewall established in Series 32 and respected by Series 33
remains absolute. `engine.py::select()` accepts exactly one
`MarketStateAssessment` (or `None`) — never MIC v2, never an
`EvidenceInterpretation`, never a `PublicationRecord` or
`ConsumerRecord`, never a broker, replay, or candle.

## The Strategy Registry: Metadata Only

`registry.py::ALL_STRATEGIES` declares eleven of BUJJI's existing
strategies as `StrategyDefinition` records — pure metadata, zero
executable trading logic:

| Strategy | Family | Bias | Risk | Required States |
|---|---|---|---|---|
| Premium VWAP Straddle | Premium Selling | Neutral | Undefined | RANGE, QUIET |
| Iron Fly | Premium Selling | Neutral | Defined | RANGE, QUIET |
| Iron Condor | Premium Selling | Neutral | Defined | RANGE, QUIET |
| Calendar Spread | Volatility | Neutral | Defined | RANGE |
| Directional Call Spread | Directional | Bullish | Defined | TREND, BREAKOUT |
| Directional Put Spread | Directional | Bearish | Defined | TREND, BREAKOUT, REVERSAL |
| Long Straddle | Volatility | Volatility | Defined | BREAKOUT, VOLATILE, EVENT_DRIVEN |
| Long Strangle | Volatility | Volatility | Defined | BREAKOUT, VOLATILE, EVENT_DRIVEN |
| Short Strangle | Premium Selling | Neutral | Undefined | RANGE, QUIET |
| Covered Call | Income | Neutral | Defined | RANGE, TREND |
| Cash Secured Put | Income | Bullish | Defined | RANGE, TREND |

Each also declares `minimum_market_character` and `required_confidence`
— e.g. undefined-risk strategies (Premium VWAP Straddle, Short
Strangle) demand `CLEAR`/`VERY_HIGH`, while defined-risk, more
tolerant strategies (Iron Condor, Calendar Spread, Covered Call, Cash
Secured Put) accept `CONTESTED`/`MODERATE`.

Two additional metadata fields, `required_governance` and
`required_calibration`, are declared on every `StrategyDefinition` for
forward compatibility with a future, more granular selector — **but
are not evaluated by this sprint's `engine.py`**. `MarketStateAssessment`
(Series 33) only exposes the *fused* `market_character`/`confidence`,
not the raw Governance/Calibration classifications that produced them;
evaluating those fields honestly requires a future series with access
to more granular input, not a workaround here that reaches past the
firewall.

**Extending the registry never touches `engine.py`.** Adding a new
strategy means appending one `StrategyDefinition` to `ALL_STRATEGIES`
— the evaluation loop reads every entry generically.

## Deterministic Tie-Breaking: Registry Order, Documented Once

`ALL_STRATEGIES`'s declaration order **is** the tie-break order. When
more than one strategy is `ELIGIBLE` for today's market, the first one
appearing in the tuple is selected. This order lives in exactly one
place (`registry.py`) and is never re-derived, randomized, or
re-sorted by `engine.py`.

## Evaluation: A Plain Deterministic Loop, Nothing Else

For every registered strategy, `engine.py::evaluate_strategy()` checks,
in order:

1. Is `market_state` itself `UNKNOWN`? → `UNKNOWN` eligibility
   (nothing else can be assessed).
2. Is `market_state` in the strategy's `forbidden_market_states`? →
   `NOT_ELIGIBLE`.
3. Is `market_state` **not** in the strategy's
   `required_market_states`? → `NOT_ELIGIBLE`.
4. Does `market_character` meet the strategy's
   `minimum_market_character` (on an internal-only tolerance ordering:
   `CLEAR` > `CONTESTED` > `MIXED` > `UNCERTAIN` >
   `INSUFFICIENT_EVIDENCE` > `UNKNOWN`)? If not → `NOT_ELIGIBLE`.
5. Does `confidence` meet the strategy's `required_confidence` (on the
   ontology's own six-level scale)? If not → `NOT_ELIGIBLE`.
6. Otherwise → `ELIGIBLE`.

Every rejection records exactly which check failed and why, in
`rejecting_conditions`. There is no scoring, no weighting, no
probability, no expectancy, and no machine learning anywhere in this
function.

## Selection: One Outcome From Three

| `selection_status` | When |
|---|---|
| `UNKNOWN` | No `MarketStateAssessment` was supplied at all. |
| `NO_STRATEGY` | An assessment was supplied, every strategy was evaluated, and either `market_state` was itself `UNKNOWN` (contradictory or silent evidence) or every strategy came back `NOT_ELIGIBLE`. This is always a valid, honestly-reported outcome — never forced into a trade. |
| `SELECTED` | At least one strategy was `ELIGIBLE`; the first in registry order is chosen. |

## Confidence: Preserve or Downgrade, Never Invent

`selection_confidence` is never computed from scratch — it always
starts from `MarketStateAssessment.confidence`. This sprint exercises
exactly one downgrade path: when **more than one** strategy is
`ELIGIBLE`, the tie-break itself introduces a genuine ambiguity (an
arbitrary registry-order pick among equals, not a uniquely-supported
choice), so `selection_confidence` is stepped down one position on the
finite Confidence scale. When exactly one strategy is eligible, or when
the outcome is `NO_STRATEGY`, confidence is passed through unchanged
(or collapsed to `UNKNOWN` only when `market_state` is itself
`UNKNOWN`). Confidence is never upgraded under any path.

## Decision Trace: Always Explainable

`decision_trace` names the selected strategy (or explains
`NO_STRATEGY`/`UNKNOWN`), states its supporting conditions verbatim,
notes when a tie-break was applied, and lists every rejected
strategy's own recorded rejection reason. Nothing in the trace is
derived from data not already present on `all_evaluations`.

## Determinism

`decision_id` is derived via `hashlib.md5` over the source
assessment's own id (or the literal string `"NONE"`), the selected
strategy id (or selection status), and the timestamp — never
`uuid4()`. `timestamp` is genuinely wall-clock-derived by design (an
injectable `clock` parameter defaulting to `datetime.now`), the same
pattern used throughout the Trading Brain. Given the same
`MarketStateAssessment` and the same fixed clock, `select()` always
produces a byte-identical `StrategyDecision`.

## Journaling

`bujji/journal/strategy_selector_journal.py::StrategySelectorJournal`
— append-only JSONL, own schema (`schema_version` field), independent
of every other journal in the codebase.

## Query API

`strategy_selector/query.py::StrategyDecisionIndex` mirrors the
established `*Index` pattern: `ingest()`, then read-only `latest()`,
`history()`, `find_by_id()`, `find_by_selected_strategy()`,
`find_by_status()`, `summary()`. No mutation method beyond `ingest`.

## What This Sprint Explicitly Does Not Build

Per the specification's own explicit exclusions, this sprint contains
zero:
- Position sizing or lot calculation
- Risk management or position management
- Order placement or execution logic
- PnL estimation or expectancy optimization
- Learning, adaptation, or strategy evolution
- Registry or strategy mutation
- MarketStateAssessment mutation

## Isolation Guarantees

- No import of `mic_v2` (any module) anywhere in this package.
- No import of `bujji.intelligence`'s `PublicationRecord` or
  `ConsumerRecord` models, and no import of the Evidence Interpreter's
  own models, anywhere in this package.
- No import of `bujji.core`, `bujji.execution`, `bujji.trade`,
  `bujji.broker`, `bujji.market`, or `bujji.tick` anywhere in this
  package.
- No PnL, probability, expectancy, score, or ranking field anywhere on
  `StrategyDecision` or `StrategyEvaluation`.
- `select()` and `evaluate_strategy()` are pure functions apart from
  the injectable clock; every id is `hashlib.md5`-derived, never
  `uuid4()`, and no randomness of any kind appears anywhere in this
  package.
- Confidence is only ever passed through, stepped down, or collapsed
  to `UNKNOWN` — never invented or increased.

**The Strategy Selector is the first true act of autonomous
decision-making in BUJJI Options OS. It chooses at most one existing
strategy — or honestly chooses none — based purely on whether today's
market understanding meets that strategy's own declared, immutable
requirements. Every future trade traces back to this component's
decision.**
