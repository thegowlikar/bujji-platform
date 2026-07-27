# Intelligence Evaluation Architecture

**Integration Series 3 — Sprint 1: Paper-Only Intelligence Evaluation Framework**

## Status

Deployed. Feature-flag gated — runs strictly inside the existing
`intelligence_adapter.enabled` block (default `False`), the same gate
Integration Series 1 and 2 already use. No trading behavior change.

## Observational Philosophy

Everything built through Integration Series 1 and 2 answers "is the
pipe working?" — is the Adapter reading a snapshot, is it fresh, is it
fast. Nothing built so far answers the actual question this whole
project exists to investigate: **if MIC v2 had an opinion, would it
have agreed with what BUJJI actually did?**

This sprint adds a strictly parallel evaluation layer to start
collecting evidence on that question, without touching anything that
decides a trade. It runs once per decision cycle, after BUJJI's
production decision is already final and before it is journaled. It
compares two already-computed values — the production decision's
direction and the intelligence snapshot the Adapter already loaded —
and records the comparison. It never feeds back into either side.

## Agreement Taxonomy

Version `1.0.0`, five finite outcomes
(`bujji/intelligence/mic_adapter/evaluation/policy.py`):

| Outcome | Meaning |
|---|---|
| `AGREED` | Published intelligence had a directional opinion, and it matched BUJJI's own decision |
| `DISAGREED` | Published intelligence had a directional opinion, and it differed from BUJJI's decision |
| `ABSTAINED` | Intelligence was available, but there was nothing to compare — no directional opinion published, or BUJJI itself had no directional decision (e.g. a market-neutral straddle) |
| `INSUFFICIENT_INTELLIGENCE` | No snapshot was available, or MIC v2's own `consumer_status` was not `AVAILABLE` |
| `UNKNOWN` | The evaluation feature is disabled — not applicable, not a verdict |

Classification is the pure function `classify_evaluation(...)`, evaluated
in a fixed priority order: flag-disabled → snapshot-missing →
consumer-status-not-available → no-production-direction →
no-published-opinion → match/mismatch. Every evaluation resolves to
exactly one of the five states.

## The Reference Policy — and an Honest Limitation

`IntelligencePolicy` is deliberately conservative: it never generates a
trade, never loads market data or broker state, never reads positions
or PnL, never calls a MIC v2 reasoning engine, and never recomputes
intelligence. It only classifies what the already-published
`IntelligenceSnapshot` (Integration Series 1, Sprint 1) is sufficient to
say.

**This is where an important, deliberate constraint from Sprint 1
resurfaces.** `IntelligenceSnapshot` carries only reference identifiers
and MIC v2's own `consumer_status` string (`AVAILABLE` /
`NOT_AVAILABLE` / `UNKNOWN`) — it was built that way on purpose, because
the Consumer API does not publish a directional trade opinion, and
Sprint 1 chose to document that gap rather than fabricate a value that
looked like one.

That same discipline applies here. The policy's opinion extraction is a
single, explicitly injectable seam:

```python
def default_opinion_source(consumer_status, snapshot_id) -> Optional[str]:
    return None
```

Because MIC v2 does not currently publish a directional field, the
shipped default always returns `None`. Given the classification priority
above, that means **in real operation today, `AGREED` and `DISAGREED`
are not reachable** — every evaluation where intelligence is genuinely
available lands on `ABSTAINED` (or `INSUFFICIENT_INTELLIGENCE` if it
isn't). The `AGREED`/`DISAGREED` paths are fully implemented and
covered by dedicated tests using an injected `opinion_source`, and
become reachable in live operation the moment MIC v2's Consumer API
begins publishing an actual directional recommendation — no other file
in this package would need to change.

A second, independently observed limitation: BUJJI's own production
strategy is a market-neutral ATM straddle, so `intention.direction` is
frequently `None` on the decisions actually observed. When that happens
the policy reports `ABSTAINED` — "no production direction to compare
against" — rather than treating a straddle as agreement or disagreement
with anything.

Both limitations are structural, not implementation gaps, and this
sprint's own validation run against a real replay observed exactly this:
one evaluation cycle, classified `ABSTAINED` / `no_production_direction`.

## Coverage

`coverage_percentage` — the fraction of evaluations landing on `AGREED`,
`DISAGREED`, or `ABSTAINED` rather than `INSUFFICIENT_INTELLIGENCE` or
`UNKNOWN` — measures how often the evaluation framework had *anything*
to say at all, independent of what it said. All comparator statistics
(`comparator.py`) are purely descriptive counts and rates over recorded
outcomes: **no profitability, no edge, no win rate, and no expectancy is
computed anywhere in this framework**, structurally enforced (no such
field exists on `EvaluationMetrics` or `IntelligenceEvaluation`) and
verified by AST-based tests.

## Isolation Guarantees

- **No influence on trading.** `_enter()` in `bujji/core/orchestrator.py`
  calls `EvaluationEngine.run_evaluation()` strictly after the existing
  Observation Monitor call and strictly before Stage 7 (Journal
  Recording), wrapped in its own `try/except`; a failure is logged and
  discarded, never propagated, never blocking the trading decision that
  has already been made.
- **No mutation of the Decision Journal or Observation Journal.** The
  Evaluation Journal (`bujji/journal/intelligence_evaluation_journal.py`)
  is a wholly separate file with its own schema, verified by AST test to
  neither import nor reference either prior journal's module.
- **No MIC v2 reasoning-engine coupling.** The evaluation package imports
  nothing from MIC v2 at all — it only reads the Adapter's already-loaded
  `IntelligenceSnapshot` (duck-typed, not even imported by type), and
  never calls the Adapter, never calls MIC v2 directly.
- **No market data, broker, position, or PnL access anywhere** in the
  evaluation package — verified structurally via AST identifier scans.
- **No non-`self` mutation** anywhere in the package's core modules.
- **Append-only journal.**
- **Deterministic** — `evaluate()` is a pure function; given the same
  decision id, production direction, snapshot, policy, and clock, it
  produces a byte-identical `IntelligenceEvaluation`, including
  `evaluation_id` (derived via `hashlib.md5`, never `uuid4()`, never
  wall-clock).
- **Feature-flag gated, default off**, sharing the same gate as Sprints
  1–2; with it off, no evaluation journal file is ever created.

**This framework measures agreement. It does not determine correctness.
It does not evaluate profitability. It does not recommend changing
production behavior.**
