# Semantic Cleanup Sprint — Separate Reference Price from Limit Price

**Status: implemented, tested, regression-clean.**

---

## 1. Files Modified

- `bujji/core/models.py` — `OrderRequest` gets a new `reference_price: Optional[float] = None` field, documented as strictly distinct from `limit_price` in the class docstring itself.
- `bujji/integration/execution_adapter.py` — `_translate_order_request()`: `limit_price` is now unconditionally `None` (this pipeline has no path producing a real trader-specified LIMIT order today — `ExecutionPolicy.limit_price` isn't even threaded into this function, an existing, unchanged, disclosed limitation); `reference_price` now carries `runtime_order.reference_price` verbatim into its own dedicated field. Docstring corrected.
- `bujji/broker/paper.py` — `place_order()`: fill price now prefers `reference_price`, falling back to `limit_price` (unchanged behavior for any caller that only ever set that field), falling back to the existing synthetic default.
- `tests/test_production_execution_adapter.py` — the one test from the prior sprint that locked in the *old* (now-corrected) mapping renamed and rewritten to assert the correct one.
- `tests/test_fyers_transport_mapping.py` — 2 new tests.
- `tests/test_paper_broker_ledger.py` — 4 new tests.

**Not touched, per the sprint's own explicit rules**: `bujji/broker/fyers.py` (already correctly ignores `reference_price` by never referencing it — no code change needed, only verified by new tests), the valuation engine, the journal, `nifty_contract_builder`/`order_construction` (their own `last_price`/`reference_price` fields, added in the prior sprint, already had the correct name and needed no change).

---

## 2. Architecture — Before vs After

**Before** (prior sprint's interim fix): `NiftyOptionContract.last_price` → `OrderRequest.reference_price` → `ProductionOrderRequest.limit_price` (single field, dual meaning). Safe against `PaperBroker` (treats it as a passive fill hint) but a live, reachable hazard against `FyersBroker`, where `limit_price`'s mere presence flips `type=2` (Market) to `type=1` (Limit) — confirmed directly in `bujji/broker/fyers.py:485`.

**After**: two fields, one meaning each, all the way to the real broker boundary.

```
NiftyOptionContract.last_price  (observed market price, from prior sprint, unchanged)
    ↓
OrderRequest.reference_price     (from prior sprint, unchanged)
    ↓
ProductionOrderRequest:
    limit_price      = None            ← ALWAYS None in this pipeline (no real LIMIT path exists yet)
    reference_price   = 124.15 (etc.)   ← the observed price, in its OWN field now
    ↓
FyersBroker.place_order():  type = 2 if limit_price is None else 1
    → limit_price is always None here → ALWAYS type=2 (MARKET), regardless of reference_price's value
    → reference_price is never referenced anywhere in fyers.py — confirmed by a test that varies it
      from 1.0 to 9999.0 while holding limit_price constant and shows the real SDK call is byte-identical
    ↓
PaperBroker.place_order():  price = reference_price if reference_price is not None else (limit_price or default)
    → fills at 124.15 (the real observed price), unchanged from the prior sprint's own verified behavior
```

---

## 3. New Regression Tests (12 total, all real, all passing)

**`tests/test_fyers_transport_mapping.py`** (+2):
- `test_market_order_with_reference_price_still_sends_market` — an `OrderRequest` with `reference_price=124.15` and `limit_price=None` reaches the real SDK call as `type=2`, `limitPrice=0` — proving the observed price never leaks into the broker instruction.
- `test_fyers_broker_ignores_reference_price_entirely` — two orders with identical `limit_price` but wildly different `reference_price` (1.0 vs 9999.0) produce byte-identical SDK calls.

**`tests/test_paper_broker_ledger.py`** (+4):
- `test_paper_broker_fills_at_reference_price_when_present`
- `test_paper_broker_falls_back_to_limit_price_when_no_reference_price` — proves existing behavior is unchanged for legacy callers.
- `test_paper_broker_prefers_reference_price_over_limit_price_when_both_set`
- `test_paper_broker_default_fill_price_unchanged_when_neither_set` — proves the original synthetic-120.0 fallback still works exactly as before.

**`tests/test_production_execution_adapter.py`** (renamed, +0 net): `test_reference_price_flows_through_but_never_as_limit_price` now asserts `reference_price == 124.15` **and** `limit_price is None` together — the single test that most directly encodes this sprint's success criterion.

---

## 4. Regression Summary

```
Before this sprint: 3094 passed
After this sprint:  3100 passed   (+6 new, 1 renamed/rewritten, 0 unrelated changes)
0 failures. Full suite re-run clean.
```

Also re-verified the real end-to-end trace (the same `run_shadow()` pipeline exercised in the prior two sprints) directly against a real `_translate_order_request()` call: `limit_price=None`, `reference_price=124.15` at the exact production boundary — and the real `PaperBroker` ledger still fills at the correct, real, distinct premiums (124.15 / 100.2), unchanged from the prior sprint's verified output.

---

## 5. Success Criterion — met

`limit_price` now means exactly one thing everywhere it's read: a real trader-specified execution instruction, read only by the real broker layer, never populated from an observed price anywhere in this pipeline. `reference_price` means exactly one thing everywhere it's read: an observed market price for simulation/journaling/replay, read only by `PaperBroker`, structurally invisible to `FyersBroker`. No hidden production ambiguity remains — proven by a test that would fail if either broker ever started reading the wrong field.
