# NIFTY Contract Builder Architecture

**BUJJI Options OS v3 — Engineering Series 42, Sprint 1 (v1, NIFTY only)**

## Status

Deployed. This closes the gap identified explicitly in Series 41's
production architecture review: a Trading Brain decision could not
become a broker `OrderRequest` because no module constructed a
concrete option contract. It lives at
`bujji/trading_brain/nifty_contract_builder/`, depending only on the
frozen Strategy Selector (Series 34) and Capital Brain (Series 36).

## Scope: NIFTY Only, v1

This sprint supports NIFTY exclusively — no BANKNIFTY, no FINNIFTY, no
stock options, no multi-underlying architecture. `UNDERLYING_NIFTY`
and `STRIKE_INTERVAL = 50` are hardcoded constants, not configuration.
Generalizing to other underlyings is explicitly future work.

## Purpose and Philosophy

The Trading Brain thinks in strategies. The broker thinks in
contracts. This module performs only that translation — for NIFTY
weekly options, via deterministic templates. It never optimizes, never
searches, never scores, never uses a Greek or a probability, and never
places an order, calculates a lot size, calculates margin,
authenticates, or monitors a fill.

## Inputs: Four Objects, Never Fetched Internally

`engine.py::build_contracts()` accepts exactly a `StrategyDecision`, a
`CapitalDecision`, a `NiftySpotSnapshot`, and a
`NiftyOptionChainSnapshot` — never MIC v2, the Market State Builder,
the Risk Brain, a broker SDK, or the Runtime Execution Service. The
two "live input" types (`NiftySpotSnapshot`, `NiftyOptionChainSnapshot`)
are plain, broker-neutral data carriers this module never fetches
itself — the caller supplies them, exactly like every other Trading
Brain module receives its inputs already produced. No historical
analysis and no prediction of any kind is performed on either.

## The v1 Template Registry

`engine.py::TEMPLATES` maps a strategy id to a finite, ordered tuple of
leg templates (`option_type`, `moneyness`, `side`, and — for Calendar
only — a `week_offset`). Six of the Strategy Selector's eleven
registered strategies (Series 34) have a v1 template:

| Strategy | Legs |
|---|---|
| Premium VWAP Straddle | ATM CE Sell, ATM PE Sell |
| Iron Fly | ATM CE Sell, ATM PE Sell, OTM1 CE Buy, OTM1 PE Buy |
| Iron Condor | OTM1 CE Sell, OTM2 CE Buy, OTM1 PE Sell, OTM2 PE Buy |
| Directional Call Spread | ATM CE Buy, OTM1 CE Sell |
| Directional Put Spread | ATM PE Buy, OTM1 PE Sell |
| Calendar Spread | ATM CE Sell (current week), ATM CE Buy (next week, same strike) |

Adding a strategy means adding one entry to `TEMPLATES` — nothing else
in `engine.py` changes. **The remaining five registered strategies**
(`LONG_STRADDLE`, `LONG_STRANGLE`, `SHORT_STRANGLE`, `COVERED_CALL`,
`CASH_SECURED_PUT`) have no v1 template and resolve to
`UNKNOWN_STRATEGY` — a disclosed, deliberate scope limit, not an
oversight. Templating them is natural future work, not silently
assumed here.

## Strike Selection: Deterministic, Never Scored

ATM is computed once per construction via floor-based midpoint-up
rounding to the nearest 50-point strike interval — the same rounding
convention documented in production's own `Broker.atm_strike()`
(discovered during the Series 41 audit), chosen for the identical
reason: Python's default banker's rounding is not deterministic in the
way a strike-selection policy requires. This module does not import
that production code (see the Series 40 precedent for why — the
convention is replicated, not the code, to keep this module's only
dependencies the two frozen Trading Brain modules it actually
consumes); the formula itself is reused verbatim.

Every leg's strike is then resolved via one of five finite moneyness
primitives: `ATM`, `ATM_PLUS_1`, `ATM_MINUS_1` (absolute strike offsets,
direction-agnostic) and `OTM1`, `OTM2` (moneyness-relative — direction
depends on `option_type`: for CE, further OTM means a higher strike;
for PE, further OTM means a lower strike). `ATM_PLUS_1`/`ATM_MINUS_1`
are declared in the taxonomy (the specification's own strike-selection
policy names them) but are not used by any v1 template — every
template above expresses its strikes fully via `ATM`/`OTM1`/`OTM2`.

## Expiry Selection: Weekly Only, Never Searched

`NiftyOptionChainSnapshot.expiries` is a caller-supplied, ascending
tuple of weekly expiry date strings. `expiries[0]` is "current week";
for the one two-expiry template (Calendar Spread), `expiries[1]` is
"next week." This module never searches across expiries, never guesses
a date, and never selects a monthly expiry — "weekly NIFTY only" is
enforced by construction, not by a filter.

## Construction Is Atomic

Every leg's `(strike, option_type, expiry)` triple is resolved against
the supplied chain *before any leg is committed*. If even one leg has
no matching chain entry, the entire construction fails
(`CONSTRUCTION_STATUS_FAILED` / `NO_MATCHING_STRIKE`) and zero
contracts are returned — never a partial, fabricated multi-leg
strategy missing one leg. This is a direct financial-safety
consequence of the "never fabricate" discipline every module in this
project has followed since MIC v2's first sprint.

## The Decision Table

Evaluated top to bottom; first matching rule wins.

| # | Condition | Failure Reason |
|---|---|---|
| 1 | No `StrategyDecision`/`CapitalDecision`, or no strategy selected | `INSUFFICIENT_DATA` |
| 2 | Selected strategy has no v1 template | `UNKNOWN_STRATEGY` |
| 3 | No usable spot price | `INVALID_SPOT` |
| 4 | No option chain supplied | `MISSING_OPTION_CHAIN` |
| 5 | Chain names fewer weekly expiries than the template needs | `NO_WEEKLY_EXPIRY` |
| 6 | Any leg's computed strike has no matching chain entry | `NO_MATCHING_STRIKE` |
| — | All legs resolve | `CONSTRUCTED` |

## Construction Trace: Always Explainable

`ContractConstructionResult.construction_trace` states the strategy,
the spot, the ATM strike, the weekly expiry, and every constructed
leg's strike/type — matching the specification's own worked example
format exactly. Each individual `NiftyOptionContract.selection_reason`
additionally states its own moneyness, side, computed strike, and the
real chain symbol it matched.

## Quantity Is Deliberately Absent

This module never calculates a broker quantity or lot size —
`NiftyOptionContract.capital_intent` carries forward exactly what the
Capital Brain (Series 36) already authorized, unchanged. Actual lot
calculation is a future module's responsibility, sitting between this
builder's output and a real `OrderRequest`.

## Determinism

`contract_id` and `construction_id` are each derived via
`hashlib.md5` over the source `StrategyDecision`'s own id, the leg
index (or strategy id), strike/type/side/expiry, and the timestamp —
never `uuid4()`. `timestamp` is genuinely wall-clock-derived by design
(an injectable `clock` parameter defaulting to `datetime.now`), the
same pattern used throughout the Trading Brain. Given the same four
inputs and the same fixed clock, `build_contracts()` always produces a
byte-identical `ContractConstructionResult`.

## Journaling

`bujji/journal/nifty_contract_builder_journal.py::NiftyContractBuilderJournal`
— append-only JSONL, own schema (`schema_version` field), independent
of every other journal in the codebase.

## Query API

`nifty_contract_builder/query.py::ContractConstructionIndex` mirrors
the established `*Index` pattern: `ingest()`, then read-only
`latest()`, `history()`, `find_by_id()`, `find_by_status()`,
`find_by_strategy()`, `summary()`. No mutation method beyond `ingest`.

## What This Sprint Explicitly Does Not Build

Per the specification's own explicit exclusions, this sprint contains
zero: broker connectivity, order submission, lot-size calculation,
margin calculation, authentication, token refresh, fill monitoring,
Greeks, strike optimization, volatility prediction, contract scoring,
randomness, or machine learning.

## Isolation Guarantees

- No import of MIC v2, the Market State Builder, the Risk Brain, a
  broker SDK, or the Runtime Execution Service anywhere in this
  package — only the frozen `StrategyDecision` and `CapitalDecision`
  models this module consumes.
- No import of `bujji.core`, `bujji.execution`, `bujji.trade`,
  `bujji.broker`, `bujji.market`, or `bujji.tick` anywhere in this
  package.
- No lot size, margin, Greek, probability, or score field anywhere on
  `NiftyOptionContract` or `ContractConstructionResult`.
- `build_contracts()` is a pure function apart from its injectable
  clock; every id is `hashlib.md5`-derived, never `uuid4()`, and no
  randomness of any kind appears anywhere in this package.
- A `NiftyOptionContract` is only ever produced from a real, supplied
  chain entry — never fabricated when no match exists.

**The NIFTY Contract Builder is the only component responsible for
transforming abstract trading intent into a concrete NIFTY options
contract. It remains completely broker-neutral, so the Runtime
Execution Service (Series 41's own roadmap) can focus solely on
operational concerns — session management, order submission, retries,
and reconciliation — rather than contract construction.**
