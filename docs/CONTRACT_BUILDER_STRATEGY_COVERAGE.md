# Contract Builder Strategy Coverage

**BUJJI Options OS v3 — Engineering Series 63**

## Status

Resolved. The `COVERED_CALL → UNKNOWN_STRATEGY` gap discovered by
Historical Qualification Campaign v2 (2026-07-09) was root-caused and
resolved as **Outcome B**: the limitation was already intentional and
already documented in code and architecture docs, but the failure mode
it produced was indistinguishable from a genuine anomaly. This sprint
makes that distinction explicit and auditable — it does not add new
trading capability.

## Strategy coverage matrix

11 strategies are registered by the Strategy Selector
(`bujji/trading_brain/strategy_selector/registry.py`). All are NIFTY
index options strategies; the Trading Brain has no other asset class.

| Strategy ID | Intended execution model | Contract Builder support | Leg template | Unit tests | Campaign v2 evidence |
|---|---|---|---|---|---|
| `PREMIUM_VWAP_STRADDLE` | Delta-neutral premium capture | v1 supported | Yes | Yes (`test_nifty_contract_builder.py`) | Not selected in the 13-session real corpus |
| `IRON_FLY` | Defined-risk premium capture | v1 supported | Yes | Yes | Not selected |
| `IRON_CONDOR` | Defined-risk premium capture | v1 supported | Yes | Yes | Not selected |
| `DIRECTIONAL_CALL_SPREAD` | Directional, defined-risk | v1 supported | Yes | Yes | Not selected |
| `DIRECTIONAL_PUT_SPREAD` | Directional, defined-risk | v1 supported | Yes | Yes | Not selected |
| `CALENDAR_SPREAD` | Time-decay/volatility term structure | v1 supported | Yes | Yes | Not selected |
| `LONG_STRADDLE` | Volatility-long, undefined-loss-limited-by-premium | **v1 out of scope** | No | Yes (failure-path test) | Not selected |
| `LONG_STRANGLE` | Volatility-long | **v1 out of scope** | No | Yes (failure-path test) | Not selected |
| `SHORT_STRANGLE` | Volatility-short, undefined risk | **v1 out of scope** | No | Yes (failure-path test) | Not selected |
| `COVERED_CALL` | Covered/collateralized, requires an underlying position | **v1 out of scope** | No | Yes (failure-path test, this sprint) | **Selected 2026-07-09 — the discovered gap** |
| `CASH_SECURED_PUT` | Collateralized, requires cash collateral tracking | **v1 out of scope** | No | Yes (failure-path test) | Not selected |

6 of 11 registered strategies have v1 leg templates; 5 do not. This
was already true before this sprint and is unchanged by it — this
sprint changes only how the 5 unsupported strategies fail, not which
strategies are supported.

## Root-cause analysis

**Outcome B: the Trading Brain's registration of `COVERED_CALL` (and
four sibling strategies) was already intentionally out of v1 scope —
already documented, in two places, before this campaign ever ran:**

1. `bujji/trading_brain/nifty_contract_builder/taxonomy.py` (pre-existing
   comment, unchanged by this sprint): *"The remaining five strategies
   registered in the Strategy Selector's own registry (Series 34) —
   LONG_STRADDLE, LONG_STRANGLE, SHORT_STRANGLE, COVERED_CALL,
   CASH_SECURED_PUT — have no v1 contract template and resolve to
   UNKNOWN_STRATEGY. This is a disclosed, deliberate v1 scope limit,
   not an oversight."*
2. `docs/NIFTY_CONTRACT_BUILDER_ARCHITECTURE.md` (pre-existing,
   unchanged by this sprint): *"The remaining five registered
   strategies ... have no v1 template and resolve to UNKNOWN_STRATEGY —
   a disclosed, deliberate scope limit, not an oversight. Templating
   them is natural future work, not silently assumed here."*

So the *decision* was never undocumented. What was missing was a
**mechanism** for that documented decision to be visible at the point
of failure. Before this sprint, both "a strategy the Strategy Selector
has never heard of" (a genuine anomaly — e.g. a typo, a corrupted
value, a future Strategy Selector version emitting something the
Contract Builder has never seen) and "a strategy the Strategy Selector
registers on purpose but v1 deliberately doesn't template" (an
intentional, disclosed boundary) produced the **identical** failure
reason, `UNKNOWN_STRATEGY`. Historical Qualification Campaign v2's own
report reasonably flagged this as "the first real execution-coverage
defect" — because from the qualification report alone, there was no
way to tell the two cases apart. **This ambiguity is the actual
defect** this sprint resolves — not a missing `COVERED_CALL` leg
template, and not an inconsistency between code and documentation
(Outcome C does not apply — the two existing disclosures were already
consistent with each other and with the code's actual behavior).

## Resolution

`STRATEGY_OUT_OF_V1_SCOPE` — a new, distinct `FAILURE_REASON_*` value
in `bujji/trading_brain/nifty_contract_builder/taxonomy.py` — is now
returned whenever `strategy_id` is a member of
`UNSUPPORTED_REGISTERED_STRATEGIES` (the same five-strategy tuple that
already existed). `UNKNOWN_STRATEGY` is now reserved exclusively for a
strategy the Strategy Selector's own registry has never registered at
all — a genuine anomaly. `engine.py::build_contracts()` branches on
this distinction at the exact point it previously always returned
`UNKNOWN_STRATEGY`:

```python
if strategy_id not in TEMPLATES:
    if strategy_id in taxonomy.UNSUPPORTED_REGISTERED_STRATEGIES:
        return _failure(taxonomy.FAILURE_REASON_STRATEGY_OUT_OF_V1_SCOPE, ...)
    return _failure(taxonomy.FAILURE_REASON_UNKNOWN_STRATEGY, ...)
```

This *is* the "explicit governance rejection explaining why it is
unavailable" the specification's Outcome B calls for: the rejection
reason string, carried through `ContractConstructionResult.failure_reason`
into `ExecutionSession.failure_reason` and ultimately into the Runtime
Safety Gate's `DENY` decision trace, now names the real, documented
cause (`STRATEGY_OUT_OF_V1_SCOPE`, described in
`FAILURE_REASON_DESCRIPTIONS` as *"registered by the Strategy Selector
but has no v1 contract template — a disclosed, deliberate scope limit,
not an oversight"*) rather than an ambiguous generic code.

## Why no leg template was implemented (Outcome A rejected)

Implementing a real `COVERED_CALL` leg template was considered and
explicitly rejected for this sprint:

1. The pre-existing documentation already frames the five unsupported
   strategies as deliberate v1 scope, not a backlog item this
   qualification-driven sprint should silently expand.
2. `COVERED_CALL` and `CASH_SECURED_PUT` both require tracking an
   underlying **position or cash collateral** — a capability this
   project's Position Sizing/Order Construction stages do not
   currently model at all (every existing v1 template is a pure
   multi-leg options structure with no underlying-share/collateral
   leg). Adding one would be new trading capability, explicitly
   forbidden by this sprint's own Engineering Principle ("This sprint
   is not about adding new trading capability").
3. The specification itself frames Outcome A as conditional — "if
   implementation is required" — and only after determining the
   strategy is *accidentally* unsupported. It is not; it is
   intentionally unsupported, confirmed by two independent pre-existing
   disclosures.

## Files created or modified

- **Modified** (with justification — a genuine, documented integration
  defect: ambiguous failure classification, not a Trading Brain logic
  change): `bujji/trading_brain/nifty_contract_builder/taxonomy.py`
  (added `FAILURE_REASON_STRATEGY_OUT_OF_V1_SCOPE` and its entry in
  `ALL_FAILURE_REASONS`/`FAILURE_REASON_DESCRIPTIONS`; also
  `FAILURE_REASON_UNKNOWN_STRATEGY`'s own description was sharpened
  to state its now-narrower meaning), `bujji/trading_brain/nifty_contract_builder/engine.py`
  (branch on `UNSUPPORTED_REGISTERED_STRATEGIES` membership at the
  single existing `if strategy_id not in TEMPLATES` check — no other
  line changed).
- **Modified** (test correction, same justification):
  `tests/test_nifty_contract_builder.py`,
  `tests/test_replay_qualification.py` — two pre-existing tests
  asserted the old, now-intentionally-superseded `UNKNOWN_STRATEGY`
  value for `LONG_STRADDLE`; updated to assert
  `STRATEGY_OUT_OF_V1_SCOPE`, with a comment explaining why.
- **New:** `tests/test_contract_builder_strategy_coverage.py`,
  `docs/CONTRACT_BUILDER_STRATEGY_COVERAGE.md` (this file).
- No MIC v2, Runtime, Qualification Framework, Replay Framework, or
  Operational Controls file was modified. No Strategy Selector
  behavior changed — it still registers, and can still select, all 11
  strategies exactly as before.

## Regression requirement — 2026-07-09 re-run

Re-running the exact scenario from Historical Qualification Campaign
v2 (2026-07-09, real NSE Bhavcopy, real MIC v2-derived classification
`market_context=TRENDING_DOWN`, `market_opinion=BEARISH`,
`context_stability=MOSTLY_STABLE`) through the real, unmodified
`run_shadow()`:

```
strategy: COVERED_CALL SELECTED
contract construction: FAILED STRATEGY_OUT_OF_V1_SCOPE   (was: FAILED UNKNOWN_STRATEGY)
order construction: FAILED EMPTY_POSITION_PLAN
exec session failure: EMPTY_SESSION
runtime auth decision: DENY DENIED
```

The bare `UNKNOWN_STRATEGY` is gone, replaced by the explicit,
documented `STRATEGY_OUT_OF_V1_SCOPE` — the second of the two
specification-acceptable outcomes ("explicit, documented governance
rejection"). No silent fallback, no synthetic contract, no fabricated
execution.

## Additional uncovered strategy gaps

None beyond the four sibling strategies already identified alongside
`COVERED_CALL` (`LONG_STRADDLE`, `LONG_STRANGLE`, `SHORT_STRANGLE`,
`CASH_SECURED_PUT`) — all five now produce the same explicit,
documented `STRATEGY_OUT_OF_V1_SCOPE` rejection, verified individually
by this sprint's own tests. No strategy in the Strategy Selector's
11-strategy registry is unaccounted for in the coverage matrix above.

## Recommendation

**Yes, Historical Qualification Campaign v2 should be rerun across the
full corpus.** The fix changes only the *failure classification* for
an already-failing case — it does not change which sessions complete,
does not change any strategy decision, and does not change the
qualification fingerprint (verified unchanged: `2328e0f77ec312eeca318946df293d91`).
Rerunning will not produce a different `COMPLETED` count, but it will
replace the one ambiguous `UNKNOWN_STRATEGY` entry in the campaign's
own decision-distribution table with the correct, documented
`STRATEGY_OUT_OF_V1_SCOPE` — closing the loop the campaign itself
opened, and giving any future campaign an unambiguous way to tell a
genuine anomaly from a known, accepted v1 boundary.
