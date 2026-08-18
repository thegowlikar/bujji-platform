# Phase 20.26 — Live Option Market Data Acquisition & Option Intelligence Feed

**Bujji OS v1.0 Roadmap, Cycle-1 lineage.** Builds the missing sensory layer: `FyersBroker` → real option-chain/quote data → `OptionMarketDataForCycle` (Phase 20.25's own input contract). No new intelligence — the intelligence already exists (Phase 20.25).

---

## 1. Audit findings

### 1.1 Existing broker capabilities — read directly, not assumed

| Method (on `bujji.broker.base.Broker`) | Real data available | Live-verified? |
|---|---|---|
| `resolve_atm_contract(underlying, spot, direction, strike_interval, lot_size)` | Real ATM `OptionContract` (strike/expiry/symbol/lot_size), backed by `InstrumentMaster`'s real NFO CSV | ✅ Confirmed working (used elsewhere in `core/orchestrator.py`) |
| `get_ltp(contract)` | Real last-traded premium | ✅ Same `quotes` endpoint as `get_quote` |
| `get_quote(contract)` | Real `bid`/`ask`/`spread` — `spread == ask - bid` confirmed exactly | ✅ **Live-verified 2026-07-20** (docstring, `MARKET_INTELLIGENCE_CORE.md`) |
| `get_option_chain(underlying, spot, strike_count)` | Real per-strike `(strike, ce_oi, pe_oi)` — **already returns `list[tuple[float,float,float]]`, the exact shape `OptionMarketDataForCycle.strikes` needs** | ✅ **Live-verified 2026-07-20**, `oich == oi - prev_oi` confirmed exactly |
| `get_spot(underlying)` | Real spot price | Already used elsewhere |

Confirmed via `scripts/run_phase20_13_live_entrypoint.py`: a real, guarded `FyersBroker` (`disable_live_execution()`-wrapped) is already constructed and connected there — only `get_recent_candles()`/`get_vix()` were ever called from it. All 4 methods this phase needed were already real, tested (via their own docstrings' live-verification claims), and simply unused.

### 1.2 Existing option data models

`OptionMarketDataForCycle` (Phase 20.25) already defines exactly the fields needed. `get_option_chain()`'s return shape requires **zero translation** — it is already `list[tuple[float, float, float]]`, assigned directly to `.strikes`. No schema extension, no new parser, no duplicate quote model.

### 1.3 Intelligence input mapping (confirmed against real brain signatures, Phase 20.25's own audit)

| Required input | Available source | Missing |
|---|---|---|
| `VolatilityBrain`: spot, strike, t_years, ce/pe premium | `get_spot`, `resolve_atm_contract`, `get_ltp` × 2 | — |
| `LiquidityBrain`: ce/pe bid/ask | `get_quote` × 2 | — |
| `StructureBrain`: spot, `(strike, ce_oi, pe_oi)` list | `get_spot`, `get_option_chain` | — |
| `GreeksBrain`: spot, strike, t_years, iv_ce, iv_pe | Same as Volatility + `VolatilityBrain`'s own solved output | — (solved downstream in `brain_assembly.py`, unchanged) |

Nothing is missing. `t_years` is pure calendar arithmetic from `contract.expiry` (a real ISO string on `OptionContract`) — computed locally, not duplicated from any existing (private, different-lineage) helper.

### 1.4 Runtime integration point

`bujji.live_shadow_runner.runner.process_cycle()` already has the `option_market_data` parameter (Phase 20.25) — no change needed there. The smallest safe insertion point is `scripts/run_phase20_13_live_entrypoint.py`'s own per-cycle loop, the ONE place a real, guarded broker is already connected for shadow runtime.

### 1.5 Lineage collision check — a real, structural finding

Attempting to place the new adapter inside `bujji/live_shadow_runner/` (the natural-seeming home) was tried and reverted: that package's own `tests/test_live_shadow_runner.py::test_no_broker_module_imports` structurally forbids `bujji.broker` imports (and even the literal string `"FyersBroker"`) anywhere in its directory — an explicit, intentional invariant established in Phase 20.13. Respecting it, the adapter was placed in `bujji/broker/` instead (the broker layer's own domain — no analogous "no intelligence imports" restriction exists there, confirmed by search).

## 2. Architecture decision

**Option A** (thin adapter) — smallest correct implementation:

```
bujji/broker/option_market_data.py   -- NEW: fetch_option_market_data_for_cycle(broker, ...)
        |
        v (calls 4 real, already-tested, read-only Broker methods)
OptionMarketDataForCycle (Phase 20.25, unchanged)
        |
        v
scripts/run_phase20_13_live_entrypoint.py   -- +1 import, +9 lines in the existing per-cycle loop
        |
        v
process_cycle(..., option_market_data=...)   -- Phase 20.25's own parameter, unchanged
```

Typed against the `Broker` ABC (not `FyersBroker` specifically) — any broker implementing the same interface can supply this adapter safely.

## 3. Files changed

**Created**: `bujji/broker/option_market_data.py`, `tests/test_option_market_data.py` (8 tests).

**Modified**: `scripts/run_phase20_13_live_entrypoint.py` — 1 new import, and inside the existing per-cycle loop: a best-effort `get_spot()` + `fetch_option_market_data_for_cycle()` call wrapped in its own try/except (never fatal to the cycle), result passed to `process_cycle(..., option_market_data=...)`.

**Unchanged**: `bujji/mic_runtime_context/*`, `bujji/live_shadow_runner/*`, all 6 intelligence brains, `bujji.broker.fyers`/`bujji.broker.base`/`bujji.broker.guard` — confirmed by mtime.

## 4. Fabrication discipline

`fetch_option_market_data_for_cycle()` returns `None` — never a partially-populated object — on: a resolution exception, mismatched CE/PE strikes (an internal consistency check), an unparseable/expired expiry, a missing/non-positive premium, a missing quote, or a missing option chain. Every returned field is a real value from a real broker call; there is no default/zero/UNKNOWN fallback value anywhere in the dataclass construction path.

## 5. Real-data validation (via a stub `Broker` — no live credentials in this environment)

**Scenario 1 — complete data**: stub broker returns real-shaped bid/ask/premium/OI data → `OptionMarketDataForCycle` populated correctly, `t_years > 0`, `.strikes` passed through unmodified.

**Scenario 2 — partial data missing** (quote, LTP, and option-chain each tested independently as `None`): adapter returns `None` in every case — confirmed no fabricated default anywhere.

**Scenario 3 — broker data unavailable** (`resolve_atm_contract` raises): adapter returns `None`; `process_cycle()` called with `option_market_data=None` completes with zero errors and one real observation — the shadow cycle continues safely, exactly matching Phase 20.24's own preserved default behavior.

## 6. Tests (8, all passing) + additional required proof

1. Complete data → correct `OptionMarketDataForCycle`
2–4. Missing quote / missing LTP / missing option chain → `None`, never fabricated
5. Broker resolution exception → `None`, no crash
6. `process_cycle()` continues safely with `option_market_data=None` after a broker failure
7. No `place_order`/`modify_order`/`cancel_order`/`connect` calls anywhere in the adapter
8. AST-verified: the adapter calls only `resolve_atm_contract`/`get_ltp`/`get_quote`/`get_option_chain` on `broker` — no other method

Plus: the full pre-existing `live_shadow_runner`/`mic_runtime_context`/`entrypoint_wiring` test suites (39 tests) re-run unmodified — all still passing, confirming Phase 20.24's behavior is unaffected when option data is absent, and that `evidence_score`/`decision_state` remain governed by the same real chain regardless of this phase.

## 7. Regression

Full suite: **6,498 passed, 0 failed** (6,490 baseline from Phase 20.25 + 8 new; clean run, no environmental flakes). New Phase 20.26 tests: 8/8 passing, both standalone and inside the full suite.

## 8. Safety verification

`grep`/`ast`-based tests confirm zero order-placement or `connect()` calls in `bujji/broker/option_market_data.py`, and confirm only the 4 documented read-only `Broker` methods are ever called on the `broker` object. The real `FyersBroker` instance this adapter is given in `run_phase20_13_live_entrypoint.py` is already wrapped in `disable_live_execution()` by that script's own existing code (Phase 20.13) — `place_order`/`modify_order`/`cancel_order`/`get_open_positions`/`get_order` are structurally unreachable on it regardless of what this phase adds. No capital exposure, no live order capability, shadow mode only.

## 9. Remaining gaps

- **Live end-to-end proof still pending real FYERS credentials.** This environment has no live, authenticated FYERS session — validation used a stub `Broker` implementing the real interface with real-shaped responses. A live run on the VPS with a freshly refreshed token (the operator's own standing step) would be the final confirmation, not performed in this session.
- **`spot` is fetched via a fresh `get_spot()` call each cycle**, separate from the candle-derived close `candle_fetch_fn` already uses — a real, minor duplication of a live network call (one extra `get_spot()` per cycle) rather than reusing the last candle's close, disclosed as a deliberate accuracy-over-efficiency choice (a fresh spot quote is more current than a 5-minute candle close) — not optimized further in this phase.
- Per the user's own stated roadmap: with real live sensory data now wireable, the next priorities remain deepening the live shadow campaign and continuing toward real-money readiness — not strategy expansion.
