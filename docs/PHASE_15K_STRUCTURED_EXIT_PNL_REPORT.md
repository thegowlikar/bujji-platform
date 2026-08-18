# Phase 15K -- Structured Exit + P&L Linkage: Final Report

## 1. Forensic findings -- field classification

| Field | Classification | Source |
|---|---|---|
| Entry timestamp, entry premium (mid), option type/strike/expiry, side | `AVAILABLE_NOW` | `LegRecord`/`EntrySnapshot` (15G), already persisted. |
| Entry bid/ask | **`DERIVABLE`** (real data one layer up) | `ShadowTradeLeg.entry_bid/entry_ask` (Phase 14) -- existed but was never carried onto `LegRecord`. **Now captured additively.** |
| Quantity | `AVAILABLE_NOW` | `LegRecord.quantity` (= `ShadowTradeLeg.ratio`, always a positive, unsigned int -- direction lives exclusively in `side`). |
| Multiplier / contract size (lot size) | **`DERIVABLE`** (real data one layer up, never persisted) | `ShadowTradeCandidate.lot_size` (Phase 14) -- existed but was never carried into the lifecycle at all. **Now captured additively on `EntrySnapshot`.** |
| Exit timestamp, exit reason | `AVAILABLE_NOW` | `PositionLifecycle.closed_at`/`exit_reason` (15G), unchanged. |
| Exit price/premium, exit bid/ask | **`MISSING`, now representable** | No PaperBroker linkage exists (Phase 15B's disclosed limitation, still true) -- but the STRUCTURE to carry real values, once they exist, is now built (`ExitLegRecord`). |
| Realized P&L (gross) | **`DERIVABLE`** once exit price exists | Computed deterministically by `bujji.position_lifecycle.pnl`, never fabricated when exit price is absent. |
| Fees, slippage | **`MISSING`** for THIS phase's own construction path, but **`DERIVABLE`** from PaperBroker | `PaperBroker.place_order()`'s `ExecutionReport` already simulates `charges` and `slippage` (a real, more detailed surface than initially assumed -- see Section 6) -- but it is not currently linked to `position_id` at all. |
| Net realized P&L | Derived, `UNKNOWN` whenever fees/slippage are `UNKNOWN` | `bujji.position_lifecycle.pnl.compute_net_pnl` -- never assumes zero. |
| MTM (unrealized) | `MISSING` | No mark-to-market computation exists anywhere in the lifecycle; explicitly out of scope this phase (Step 10's own "keep MTM and realized P&L conceptually separate" -- there is currently no MTM to keep separate FROM, which is itself a disclosed gap, not fabricated). |

The legacy `trading_brain/portfolio_valuation`/`exit_engine` packages were
**re-confirmed disconnected** (not imported, not trusted merely because
they exist) -- consistent with every prior phase's own finding.

## 2. Canonical economic model (additive, under `bujji/position_lifecycle/`)

No parallel position system was created. Extended existing models:
- `LegRecord` (+`entry_bid`, `+entry_ask`, both defaulted `None`).
- `EntrySnapshot` (+`lot_size`, defaulted `None`).
- New `ExitLegRecord` (leg_id, exit_price/bid/ask, exit_quantity, gross_leg_pnl, fees, net_leg_pnl, pnl_status).
- New `StructuredExit` (exit_timestamp, exit_reason, legs, gross_realized_pnl, fees, slippage, net_realized_pnl, pnl_status) -- gross/fees/slippage/net kept **explicitly separate**, never collapsed.
- `PositionLifecycle` (+`structured_exit`, defaulted `None`; `realized_pnl` now derived as "best-known" -- net if known, else gross, else `None` -- for backward compatibility with Phase 15J's existing consumer).

## 3. P&L semantics -- one canonical calculation path

`bujji/position_lifecycle/pnl.py` -- pure, zero imports beyond its own
`models` (verified by a dedicated safety test): 

```
BUY:  P&L = (exit_price - entry_price) * quantity * multiplier
SELL: P&L = (entry_price - exit_price) * quantity * multiplier
```

**Forensic confirmation before implementing**: `ShadowTradeLeg.side` is
always `"BUY"|"SELL"`, and `ratio`/`LegRecord.quantity` is always a
positive, UNSIGNED integer (direction lives exclusively in `side`) --
confirmed by direct inspection of `shadow_trade_construction/engine.py`.
This is why `pnl.py` never multiplies by a signed quantity (that would
double-sign) and never infers direction from quantity's sign (there
isn't one). Missing `quantity`/`multiplier`/`entry_price`/`exit_price`
all degrade to `(None, PNL_UNKNOWN)` -- multiplier is NEVER assumed to
be 1. `engine.py` itself contains **zero multiplication** (verified by
an AST-based safety test checking for `ast.Mult` nodes specifically) --
all real arithmetic is delegated to `pnl.py`.

## 4. Lifecycle/event changes (Step 7's forensic decision)

**Chose to enrich the EXISTING `POSITION_CLOSED` event** rather than
add a second `POSITION_EXIT_RECORDED` event. Reasoning: in this
system's current scope (no partial exits, see Section 8), an exit and
a close are the SAME real-world moment -- splitting them would double
persistence and complicate guard logic for no real benefit. The new
`structured_exit` parameter on `build_position_closed_payload` is
purely additive and optional (`None` default) -- every pre-Phase-15K
call site produces the exact same payload shape as before, plus one
new `"structured_exit": None` key.

No second lifecycle state machine was created; `NONE -> OPEN -> CLOSED`
is unchanged.

## 5. Backward compatibility (Step 6)

Proven by a dedicated test: a legacy `POSITION_CLOSED` payload with NO
`structured_exit` key at all (the exact shape every pre-Phase-15K call
produces) hydrates successfully -- `structured_exit=None`,
`realized_pnl=None`, never a `KeyError`, never a fabricated value.

## 6. PaperBroker integration contract (Step 6, audit-only, NOT wired)

Re-audited `bujji/broker/paper.py` after building the P&L linkage.
**Finding: PaperBroker is richer than any prior phase's audit assumed.**
It already simulates, per real order, via `place_order()` ->
`ExecutionReport`:
- `client_order_id` (order identity)
- `filled_qty`/fill price (via `FillSimulator`)
- real timestamps (`entry_timestamp`, `now_ist()` at fill)
- **`charges` (fees)** and **`slippage`** (via `SlippageConfig`) -- a real, already-built economic surface this phase did not need to invent.
- per-symbol `_realized_pnl` (Phase 15B's own `restore_realized_pnl`)
- partial-fill support (`partial_fill_qty`, `PartialFillConfig`)

**What remains unavailable for integration**: PaperBroker has **zero**
concept of `position_id` -- it is keyed by `symbol`/`client_order_id`
only, with no multi-leg grouping and no link to `bujji.position_lifecycle`'s
identity at all. Wiring it would require an additive bridge mapping
`(position_id, leg_id) -> client_order_id` at entry and reading back
`ExecutionReport.charges`/`slippage`/fill price at exit -- a concrete,
buildable contract, but **NOT attempted this phase**, per the explicit
hard boundary.

## 7. Position Management integration (Step 12)

No execution was implemented. Verified the economic model CAN
represent HOLD/ADJUST/HEDGE/ROLL/EXIT consequences without a new
position identity -- `MANAGEMENT_ASSESSED` events already thread
through the same `position_id` (Phase 15I), and a future
`PositionAdjusted`/`PositionRolled` economic event (still deferred, no
proven need yet) would naturally extend the SAME `StructuredExit`-style
model (partial economic change, not a new identity). This phase adds
no new event for management actions -- only documents the natural
extension point.

## 8. Partial exits (Step 9) -- explicitly investigated, deferred

**`PARTIAL_EXIT = DEFERRED`.** Confirmed by direct inspection:
`ALL_STATUSES = (STATUS_OPEN, STATUS_CLOSED)` -- no `PARTIAL_EXIT`
status exists, and implementing one would require a NEW
quantity-tracking mechanism (per-leg remaining quantity across
multiple exit events) that no part of the current architecture needs
yet (no real position has ever been opened in any persisted session).
Building it now would be premature complexity with zero evidence of
necessity -- documented as a real, disclosed limitation, not silently
pretended to exist. `ExitLegRecord.exit_quantity` exists as a field
(future-safe) but currently always equals the leg's own full entry
quantity.

## 9. Outcome Attribution integration (Step 11, re-verified)

Phase 15J's `outcome_attribution/engine.py` consumes `PositionLifecycle`
as before -- now with real `structured_exit`/`realized_pnl` data when
present. **Re-verified, not weakened**: a dedicated safety test
confirms zero import path from `outcome_attribution` to
`position_management` or `msi_strategy_selection_foundation` still
holds. P&L outcome vs. decision quality separation (Phase 15J's own
principle) is unaffected -- `attribute_position_outcome` still reads
`realized_pnl` and thesis/management evidence completely independently.

## 10. Semantic fixture results (Step 3/16 in the mission's numbering)

**28 P&L unit tests** (`test_position_lifecycle_pnl.py`): all 6
long/short call/put sign combinations, quantity/multiplier linear
scaling, zero movement, zero/negative/invalid quantity rejection,
missing entry/exit price/multiplier/quantity, multi-leg Iron Condor
and straddle aggregation, partial-leg-information never producing a
false total, asymmetric leg quantities, net P&L with missing
fees/slippage, and explicit confirmation `pnl.py` has zero thesis/
decision coupling.

**19 lifecycle-integration tests** (`test_position_lifecycle_structured_exit.py`):
all 6 sign-correctness cases through the FULL reducer (not just the
pure function), multi-leg Iron Condor and straddle through the
lifecycle, partial-exit-deferred confirmation, missing exit price,
missing entry bid/ask (doesn't block calculation), zero-quantity/
malformed-leg rejection, idempotent duplicate close, conflicting close
rejection, backward-compatible legacy close, deterministic replay, and
explicit confirmation P&L is independent of thesis status.

**7 replay/recovery tests** (`test_position_lifecycle_pnl_replay.py`):
full `OPEN -> MANAGEMENT_ASSESSED -> CLOSED(structured) -> OUTCOME_ATTRIBUTED`
chain reconstructs identically; deterministic double replay; restart
before close then close matches uninterrupted; torn close event
degrades safely; duplicate close event handled at store layer; multi-leg
position replay; legacy lifecycle without structured exit hydrates safely.

**A real bug was found and fixed during this testing**:
`_apply_outcome_attributed`'s `PositionLifecycle` constructor call did
not carry forward the new `structured_exit` field, silently wiping it
whenever an outcome attribution was recorded after a structured close.
Caught by `test_full_chain_replay_reconstructs_identical_state`, fixed
by adding `structured_exit=current.structured_exit` to that
constructor call -- exactly the kind of defect this project's own
test-first discipline exists to catch.

**9 safety tests** (`test_position_lifecycle_pnl_safety.py`): no
forbidden imports (explicitly including `msi_strategy_selection_foundation`,
`msi_trade_intent`, `msi_decision_synthesis`), `pnl.py` confirmed to
import NOTHING beyond its own `models`/`typing`/`__future__`, no
forbidden order calls (AST-based), no broker/network references, no
wall-clock/randomness dependence, P&L never fabricates a number from
missing inputs, structured exit never mutates status/legs outside the
existing state machine, and Outcome Attribution's hard boundary
re-verified intact.

**One pre-existing safety test needed a documented, scoped correction**
(not a weakening): `test_no_p_and_l_math_is_computed_here` (Phase
15I/15J-era) asserted engine.py contained zero P&L-shaped text at
all -- correct THEN (realized_pnl was permanently `None`), but Phase
15K's entire purpose is to add real P&L computation. Renamed to
`test_no_ad_hoc_p_and_l_arithmetic_in_engine`, now checking via AST for
the actual unambiguous signal of ad-hoc math (`ast.Mult`, not a naive
string match) -- confirming engine.py still performs ZERO direct
multiplication, delegating all real arithmetic to `pnl.py`.

## 11. Real-data validation (Step 5/13)

`SHADOW-OBSERVATORY-2026-08-06` never opened OR closed a real position
(confirmed again). **Explicit result: `NO_REAL_CLOSED_POSITION_AVAILABLE`.**
What WAS genuinely validated against real data: **1/174** real cycles
have BOTH real ATM CE and PE bid/ask simultaneously (the same known
sparse-liquidity finding from Phase 14/15E/15F/15I/15J, reconfirmed
here, not new). What is validated ONLY through semantic fixtures,
never presented as real-market evidence: P&L sign correctness,
multi-leg aggregation, gross/net separation, `UNKNOWN` preservation.
What remains genuinely impossible this phase: any real realized P&L
number.

## 12. Safety

All safety tests pass (9 new + the 1 scoped correction above). No
other pre-existing safety test needed any exception. `trading_brain/`,
`risk_governor/`, `broker/guard.py`, `broker/hybrid.py` all confirmed
byte-untouched.

## 13. Full regression

**4877 passed** (1 pre-existing, unrelated deprecation warning). Zero
failures. Test breakdown this phase: 28 (pnl unit) + 19 (structured
exit integration) + 7 (replay) + 9 (safety) + 1 scoped correction to a
pre-existing test = **63 new/updated tests**.

## 14. Economic trust-gate verdict (Step 18)

**`ECONOMIC_LIFECYCLE_TRUST: TRUSTED`** -- for the scope actually built.

- P&L mathematics proven: YES (28 dedicated tests, all 6 sign combinations, multi-leg aggregation).
- Sign conventions proven: YES (explicit BUY/SELL formula, unsigned quantity, verified never double-signed).
- Multi-leg proven: YES (Iron Condor + straddle, both at the pure-function AND full-lifecycle level).
- Persistence proven: YES (reuses `EventStore` directly, zero new mechanism).
- Replay proven: YES (7 dedicated tests, including a real bug caught and fixed).
- Restart proven: YES (restart-before-close-then-close test, byte-identical to uninterrupted).
- `UNKNOWN` preserved: YES (missing quantity/multiplier/price/fees/slippage all degrade honestly, never fabricated).
- Backward compatibility proven: YES (legacy close without structured_exit hydrates safely).
- Safety boundary proven: YES (9 tests + 1 scoped correction, zero weakening).
- Outcome Attribution integration proven: YES (re-verified hard boundary intact).
- No fabricated real-data claims: YES (`NO_REAL_CLOSED_POSITION_AVAILABLE` reported honestly).

**The scope boundary, explicit**: this trust verdict covers the
STRUCTURE and MATHEMATICS of P&L linkage -- proven correct wherever
real economic evidence exists. It does NOT mean a real dollar P&L
number exists anywhere in the system yet (none does -- no position has
ever actually been opened or closed in any persisted session). PaperBroker
integration remains, as instructed, a separate future gate.

## 15. Files changed

Modified (additive only):
- `bujji/position_lifecycle/models.py` -- `PNL_*` status constants, `LegRecord`+2 fields, `EntrySnapshot`+1 field, new `ExitLegRecord`/`StructuredExit`, `PositionLifecycle`+1 field.
- `bujji/position_lifecycle/engine.py` -- entry bid/ask/lot_size capture, `build_structured_exit`, enriched `build_position_closed_payload`, `_apply_position_closed` P&L derivation, **the `_apply_outcome_attributed` bugfix**.
- `tests/test_position_lifecycle_safety.py` -- one scoped, documented test correction (Section 10).

New:
- `bujji/position_lifecycle/pnl.py`
- `tests/test_position_lifecycle_pnl.py`
- `tests/test_position_lifecycle_structured_exit.py`
- `tests/test_position_lifecycle_pnl_replay.py`
- `tests/test_position_lifecycle_pnl_safety.py`

## 16. Remaining limitations (disclosed)

- No real position has ever been opened or closed in any persisted session -- the entire economic model remains fixture-proven, not real-market-proven.
- Fees/slippage have no construction path from THIS phase's own builders (only `None`/caller-supplied) -- PaperBroker already simulates them, but isn't linked (Section 6's contract).
- Partial exits remain `DEFERRED`, undocumented complexity avoided deliberately.
- MTM/unrealized P&L does not exist anywhere yet -- only realized P&L, and only once an exit price exists.
- Per-leg fee attribution does not exist (`ExitLegRecord.fees` is always `None`; only position-level fees/slippage are representable this phase).

## 17. Architecture gap audit (Step 20), re-run from scratch

Re-inspecting the whole system, not assuming the Phase 15J ranking still holds:

- **PaperBroker lifecycle integration**: now has a CONCRETE, documented contract (Section 6) -- the highest-readiness gap, but still explicitly gated by this project's own standing discipline (no premature activation).
- **Real live position capture**: blocked purely by market hours / FYERS token availability, not by architecture -- every layer (lifecycle, management, attribution, P&L) is now ready to receive one.
- **Structured execution/fill records**: PaperBroker already has these (Section 6) -- the gap is the BRIDGE, not the underlying capability.
- **Partial exits**: correctly deferred (Section 8), no evidence of need yet.
- **Portfolio-level aggregation**: does not exist -- no code aggregates multiple `PositionLifecycle`s into a session/portfolio view (total exposure, total realized P&L across positions). Confirmed absent by source inspection.
- **Execution-quality attribution**: would need real fill data (PaperBroker has it) linked to `position_id` (the same Section 6 gap) -- not yet buildable without that bridge.
- **Adaptive action plans**: still deferred (Phase 15I's own finding, unchanged).
- **Learning/outcome memory**: still absent (Phase 15J's own finding, unchanged) -- and now has richer real P&L data to eventually learn from, once real positions exist.
- **Multi-session validation**: not yet attempted -- every real-data validation so far uses the SAME single archived session.

**Recommended Phase 15L: PaperBroker Lifecycle Bridge (the contract
from Section 6, built additively, still with NO automatic execution
gate lifted).** This is the single highest-leverage next step because
every other remaining gap (real position capture, execution-quality
attribution, a real economic trust proof beyond fixtures) is
downstream of it. The bridge itself would remain read-only/observational
from the lifecycle's perspective -- linking `position_id` <->
`client_order_id` and reading back real fill price/charges/slippage --
NOT activating any new order-placement capability. This is not started
yet, per your explicit instruction to report the verdict and target first.
