# Position Sizing Engine Architecture

**BUJJI Options OS v3 — Engineering Series 43, Sprint 1 (v1)**

## Status

Deployed. This is the final business decision before runtime
execution, per `TRADING_BRAIN_CONSTITUTION.md`. It lives at
`bujji/trading_brain/position_sizing/`, depending only on the frozen
Capital Brain (Series 36) and NIFTY Contract Builder (Series 42).

## Purpose and Philosophy

The Trading Brain decides whether to trade. The NIFTY Contract Builder
decides what to trade. This module decides how much to trade — and
nothing else. It never chooses a strategy, never chooses a strike,
never places an order, and never communicates with a broker. Quantity
is always `lots × lot_size`; `lots` always comes from
`PositionSizingConfig`'s finite table keyed by `capital_intent` — never
from emotion, confidence, prediction, leverage, margin, or exposure
modeling.

## Inputs: Four Objects, Nothing Else

`engine.py::size_position()` accepts exactly a `CapitalDecision`, a
tuple of already-constructed `NiftyOptionContract`s (Series 42), a
`CapitalPolicy`, and a `LotSpecification` — no other module.

### `CapitalPolicy`: naming reused from production, code never imported

`CapitalPolicy.policy` uses the exact vocabulary — `STRICT`,
`ESTIMATED`, `SIMULATION`, `CERTIFIED` — discovered in production's own
`RiskConfig.capital_policy` during the Series 41 audit. This module
does not import that production config; it replicates only the
naming, for the same reason the Broker Adapter (Series 40) and NIFTY
Contract Builder (Series 42) replicated production naming rather than
importing production code. This sprint applies no different sizing
math per policy value — it only validates that the value is one of the
four recognized ones; a future series may differentiate behavior by
policy, but v1 does not.

### `LotSpecification`: supplied, never fetched

`LotSpecification` is a plain, immutable carrier (`underlying`,
`lot_size`, `effective_date`, `version`) — v1 restricts `underlying` to
`NIFTY` only. The lot size is supplied by the caller (or a
configuration/reference-data source outside this module's concern);
this module contains **no exchange connectivity** and never fetches a
lot size itself.

## Capital Intent → Lots (Configured, Never Hard-Coded)

`capital_intent` is reused verbatim from the frozen Capital Brain
(Series 36) — never redefined here.

| Capital Intent | Lots |
|---|---|
| `NONE` | `0` (an honest "no position," never a fabricated size) |
| `MINIMAL` | `PositionSizingConfig.minimum_lots` |
| `REDUCED` | `PositionSizingConfig.reduced_lots` |
| `STANDARD` | `PositionSizingConfig.standard_lots` |
| `FULL` | `PositionSizingConfig.full_lots` |

Every lot count is a configuration value (`config.py`), never a
hard-coded production quantity. `PositionSizingConfig` also carries
`max_lots`, validated at sizing time: configuration is rejected
(`INVALID_CONFIGURATION`) if `full_lots > max_lots`, or if any
configured lot count is non-positive.

## Quantity: One Formula, Nothing Else

```
quantity = lots × lot_size
```

No leverage, no margin optimization, no exposure modeling. The same
`lots` value is applied to **every** leg of the strategy — the
multi-leg rule is enforced structurally: `PositionPlan` carries one
`lots_per_leg` and one `quantity_per_leg`, not a per-leg breakdown, so
there is no code path that could size two legs of the same strategy
differently.

## Validation: The Decision Table

Evaluated top to bottom; first matching rule wins.

| # | Condition | Failure Reason |
|---|---|---|
| 1 | Any of the four inputs entirely missing | `INSUFFICIENT_DATA` |
| 2 | `capital_intent` not recognized | `UNKNOWN_CAPITAL_INTENT` |
| 3 | `CapitalPolicy.policy` not one of the four recognized values, or `PositionSizingConfig` itself invalid | `INVALID_CONFIGURATION` |
| 4 | `LotSpecification.underlying != "NIFTY"`, or `lot_size <= 0` | `INVALID_LOT_SPECIFICATION` |
| 5 | Zero contracts supplied | `EMPTY_CONTRACT_SET` |
| 6 | Two or more contracts share the same `(strike, option_type, expiry, side)` | `INSUFFICIENT_DATA` |
| 7 | Computed quantity is zero (e.g. `capital_intent == NONE`) | `ZERO_QUANTITY` |
| — | All checks pass | `PASSED` |

**An honest note on rule 6**: the specification's finite failure
vocabulary has no dedicated "duplicate leg" reason. Duplicate legs are
folded into `INSUFFICIENT_DATA` — the supplied contract set does not
give this module enough unambiguous information to size correctly,
which is exactly what that reason already means elsewhere in this
table. This is a disclosed judgment call, not an assumption made
silently.

## Traceability

`sizing_trace` (and `sizing_reason`, identical in v1) states the
capital intent, the configured lots, the lot size, the resulting
quantity, and every leg it was applied to — matching the
specification's own worked example format exactly:
*"Capital Intent STANDARD. Configured Lots 2. Lot Size 75. Quantity
150. Applied to 25150CE, 25150PE."*

## Determinism

`plan_id` is derived via `hashlib.md5` over the source
`CapitalDecision`'s own id, the capital intent, the lots (or the
failure reason, on a failure path), and the timestamp — never
`uuid4()`. `timestamp` is genuinely wall-clock-derived by design (an
injectable `clock` parameter defaulting to `datetime.now`), the same
pattern used throughout the Trading Brain. Given the same four inputs
and the same fixed clock, `size_position()` always produces a
byte-identical `PositionPlan`.

## Journaling

`bujji/journal/position_sizing_journal.py::PositionSizingJournal` —
append-only JSONL, own schema (`schema_version` field), independent of
every other journal in the codebase.

## Query API

`position_sizing/query.py::PositionPlanIndex` mirrors the established
`*Index` pattern: `ingest()`, then read-only `latest()`, `history()`,
`find_by_id()`, `find_by_validation()`, `find_by_capital_intent()`,
`summary()`. No mutation method beyond `ingest`.

## What This Sprint Explicitly Does Not Build

Per the specification's own explicit exclusions, this sprint contains
zero: broker connectivity, order submission, broker margin
calculation, exchange API queries, live position reads, risk
prediction, exposure optimization, or any modification to a strategy
or contract decision made upstream.

## Isolation Guarantees

- No import of MIC v2, the Market State Builder, the Risk Brain, the
  Strategy Selector, a broker SDK, or the Runtime Execution Service
  anywhere in this package — only the frozen `CapitalDecision` and
  `NiftyOptionContract` models this module consumes.
- No import of `bujji.core`, `bujji.execution`, `bujji.trade`,
  `bujji.broker`, `bujji.market`, or `bujji.tick` anywhere in this
  package.
- No margin, leverage, exposure, Greek, or probability field anywhere
  on `PositionPlan`.
- `size_position()` is a pure function apart from its injectable
  clock; every id is `hashlib.md5`-derived, never `uuid4()`, and no
  randomness of any kind appears anywhere in this package.
- A quantity is only ever `lots × lot_size` from a validated
  configuration — never fabricated, never leveraged, never modeled.

**The Position Sizing Engine converts authorized capital intent into
concrete, validated position sizes — nothing more. All runtime
concerns (authentication, order submission, retries, reconciliation,
broker interaction) remain entirely the responsibility of the Runtime
Execution layers that follow, per the Series 41 roadmap.**
