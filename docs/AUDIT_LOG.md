# BUJJI — Audit Log (Permanent Record)

Chronological record of every production bug found and fixed during the
2026-07-19 audit sequence (four passes: strategy/VWAP correctness, full
production readiness, ATM/premium computation, trade identity/immutability,
plus this final completion pass). Every entry below has a corresponding
regression test — no fix in this log was applied without a test proving it.

Current test count: see `tests/` — 187 tests, all passing, as of this entry.

## Pass 1 — Strategy implementation (VWAP Premium Straddle Seller build-out)

1. **Tick Engine watched only the CE leg.** `tick/engine.py` subscribed to
   and computed MTM from a single symbol (a leftover from the pre-straddle
   single-contract strategy), mismatched against the combined-premium
   baseline, and never watched the PE leg's risk at all.
   Fix: subscribes to both legs, requires both to tick before computing.
   Test: `tests/test_tick_engine.py::test_subscribes_to_both_straddle_legs`,
   `test_only_one_leg_ticking_is_a_safe_noop`.

2. **Health Engine had the identical single-leg bug** for staleness
   reporting. Fix: reports the staler of the two legs. Test:
   `tests/test_health_engine.py`.

3. **`tests/conftest.py`'s shared config fixture never set `hard_exit`**,
   silently defaulting to 15:15 instead of the intended 15:05 — every
   "hard stop" test was passing against the wrong boundary.

4. **Startup recovery's orphan-flatten path only closed one leg** of a
   corrupted/unrecognized straddle position. Fix: `_find_all_live_shorts()`
   flattens every leg found, with unique per-leg client-order-ids (a
   same-second collision would otherwise let the execution engine's own
   idempotent-submit logic silently treat the second leg's buy-back as a
   duplicate of the first and skip it). Test:
   `tests/test_cd2_snapshot_schema.py::test_recovery_survives_corrupt_position_and_flattens_real_position`.

5. **`tests/test_instrument_master.py`'s fixture hardcoded a "nearest
   expiry" timestamp** that went stale relative to the actual test-run
   date — not a production bug, a time-bombed test. Fixed by computing
   expiry epochs relative to the real run time.

## Pass 2 — Full production readiness audit

6. **[CRITICAL] Partial straddle entry left a naked, unmonitored leg.**
   `_enter()` placed CE then PE sequentially; if PE's order failed after CE
   filled, the exception propagated before `Position`/`open_position()` ever
   ran — the FSM rolled back to `READY` believing nothing opened, while a
   real CE short sat live with zero monitoring (Tick Engine never started,
   candle reassessment never ran) until the next restart's reconciliation.
   Fix: on PE failure, immediately buys back the filled CE leg same-cycle.
   Test: `tests/test_tier1_capital_protection.py::test_partial_entry_failure_auto_flattens_the_filled_leg`.

7. **FYERS fill-response field mapping is explicitly unverified** in the
   broker's own source comments (`filledQty`, `tradedPrice`, status codes,
   `orderTag` echo). **LIVE CERTIFICATION REQUIRED** — cannot be closed by
   any amount of code review or paper trading.

8. Dashboard's Market Data Health section, replay path-isolation risk,
   journal CSV/SQLite non-atomicity — identified in Pass 2, **fixed in this
   completion pass** (items 12-14 below).

## Pass 3 — ATM contract resolution & combined premium audit

9. **[CRITICAL] ATM strike rounding was inconsistent at exact grid
   midpoints**, live in the currently-deployed production path
   (`InstrumentMaster.resolve_atm`, which independently reimplemented the
   same buggy formula as `Broker.atm_strike`). Proven by direct execution:
   spot=25225 → 25200, spot=25275 → 25300 — an asymmetry from Python's
   banker's-rounding `round()`. Fix: deterministic round-half-up, single
   canonical formula (deduplicated). Test: `tests/test_atm_strike.py` (14
   cases including both midpoints), plus a midpoint case through the real
   `InstrumentMaster.resolve_atm` path.

10. **Contract-resolution failures (`LookupError`) were not caught** by
    `_handle_pre_position`'s entry error handling — left the FSM wedged at
    `CONFIRMED` for the rest of the day (no other code path ever revisits
    that state). Fix: added a catch-all rollback to `READY`. Test:
    `test_contract_resolution_failure_rolls_back_to_ready_not_stuck`.

## Pass 4 — Trade identity & position lifecycle audit

11. **[CRITICAL] A snapshot with exactly one leg present silently resumed
    as valid.** `_load_opt_contract` independently returned `None` for a
    missing `ce_contract`/`pe_contract` key with no cross-check that both
    are present together. Proven exploitable by direct execution (not
    speculation) — a corrupted snapshot with only `pe_contract` missing
    parsed successfully, would have resumed live management believing a
    single-leg LTP was the combined premium, and bought back only that one
    leg on exit, permanently orphaning the other. Fix: rejected as
    `PositionSchemaError` unless both legs are present or both absent. Test:
    5 new tests in `tests/test_cd2_snapshot_schema.py`, including an
    end-to-end proof that recovery correctly flattens both real legs.

## Pass 5 — Final completion (this session)

12. **Dashboard's "Market Data Health" section always showed a false
    "TRADING DISABLED" alarm** — wired to a permanently-empty dummy
    spot-VWAP tracker (`SignalEngine.vwap_quality()`, never updated), not
    the strategy's actual live indicator. Fix: renamed to "Premium VWAP
    Health", now sourced from `TradeManager.premium_vwap_quality()` — the
    real, live, currently-tracking equal-weight combined-premium VWAP. New
    `PremiumVwapQuality` dataclass. Test: `tests/test_vwap_audit.py`
    (rewritten, 7 tests).

13. **`python -m bujji.replay` defaulted to the SAME persistence paths as
    the live process** — could read a stale live session snapshot (breaking
    replay determinism) and would write synthetic replay trades into the
    real production journal/database. Fix: replay now isolates under
    `--workdir` (default `data/replay/`) unless `--use-live-paths` is
    explicitly passed. Test: `tests/test_replay_path_isolation.py` (3
    tests, including a full CLI invocation proving the live-default paths
    are never touched).

14. **Journal write (CSV + SQLite) was not atomic**, and any failure was
    silent. Fix: each half now wrapped, failures logged at CRITICAL with
    which half failed; schema migration (`ALTER TABLE ADD COLUMN`) added so
    an existing database gains new columns without a destructive rebuild.
    Also added `trade_id`/`ce_symbol`/`pe_symbol`/`expiry` fields — the
    permanent record previously lost the actual traded contract identity,
    keeping only a derived strike number. Test: `tests/test_journal.py` (4
    tests).

15. Cosmetic: renamed "Bujji ORB-VWAP ATM Seller" → "Bujji VWAP Premium
    Straddle Seller" across `app.py`, `__init__.py`, `banner.py`,
    `dashboard/server.py`, `deploy/bujji.service` — the running system's own
    logs/dashboard/systemd description no longer name the wrong strategy.
    **Operational note:** the deploy template was updated; the
    *currently-installed* `/etc/systemd/system/bujji.service` on the VPS
    still needs `Description=` refreshed via your normal deploy process (a
    cosmetic-only field, no functional impact either way).

## Documented, not fixed — requires your decision

- Instrument-master's 24h expiry-selection grace window could theoretically
  select an already-expired contract if the symbol-master cache is stale by
  more than a few hours past a Thursday 15:30 IST expiry. Low probability
  given the daily fresh-download pattern; needs a decision on hard-fail vs.
  warn-and-proceed.
- `Position` is a plain mutable `@dataclass`, not frozen — trade-identity
  immutability is proven true today by exhaustive search (zero reassignment
  sites found) but is convention-enforced, not type-enforced.
- No exchange-side (GTT/bracket) backstop independent of the bot process —
  an outage longer than systemd's 5s auto-restart leaves the straddle
  unmonitored until the process returns.

## Pass 6 — Capital Management Engine (new platform subsystem)

**Finding (prior to this pass): BUJJI was NOT capital-aware and NOT
broker-margin-aware at all.** Position size was a single hardcoded config
integer (`risk.lots`) multiplied by a lot_size value that was itself a
static config passthrough, not read from the exchange. No code path
anywhere read account funds, margin, or buying power — confirmed by
exhaustive repository search (a `"funds": "funds"` FYERS dispatch-table
entry existed but was never called from anywhere).

**Built:** `bujji/capital/` — a new, strategy-independent platform
subsystem (`engine.py`, `models.py`, `broker_adapter.py`, `health.py`,
`exceptions.py`). `Orchestrator._enter()` now calls
`CapitalManagementEngine.approve_trade(ce_contract, pe_contract)` and uses
`decision.quantity` — the direct `risk.lots * lot_size` multiplication is
gone. See `docs/CAPITAL_MANAGEMENT_ENGINE.md` for full architecture,
sequence diagram, decision flow, and failure-mode reference.

**Test coverage added:** `tests/test_capital_engine.py` (18 unit tests —
worked examples from the audit, safety buffer, increasing/decreasing
capital, zero capital, every fail-safe path), `tests/test_capital_integration.py`
(6 end-to-end Orchestrator tests — capital-blocked entry cleanly rolls back
to READY with zero orders placed, dynamic lot-size flow-through, journal
publication, recovery survival), `tests/test_replay_capital.py` (3 tests —
deterministic replay given identical capital/margin/lot-size schedules).

**IMPORTANT OPERATIONAL CONSEQUENCE, not a bug:** `FyersBroker.get_order_margin()`
returns `None` unconditionally — confirmed by directly introspecting the
installed `fyers-apiv3` SDK (`dir(FyersModel)`, `dir(Config)`): there is NO
margin-calculator or SPAN-margin method/endpoint anywhere in the installed
package. Per the mandate's own explicit instruction ("do not estimate... if
unavailable, clearly classify as LIVE CERTIFICATION REQUIRED"), this means
**the Capital Management Engine will now BLOCK every entry attempt in
`fyers`/`fyers_paper` mode** — including the currently-deployed production
`fyers_paper` configuration — until a real, broker-verified margin
calculator is wired in and certified against a live account. This is
intentional, correct, fail-safe behavior exactly as specified, not a
regression — but it is a material change to what the currently-running
system will do on its next trading day. Only `paper` mode (fully synthetic)
and `replay` mode (schedule-driven synthetic) can currently produce an
approved trade.

## Pass 7 — span_margin live certification attempt (BLOCKED)

Attempted to live-certify the researched `span_margin` endpoint against the
real deployed account, using the EXISTING `FyersBroker.connect()` path (no
new/untested auth code) with the credentials already configured in the
VPS's `/opt/bujji/.env` (never printed or exposed — only pass/fail results
were observed).

**Result: AUTH_FAILED.** The stored `FYERS_ACCESS_TOKEN` has expired
(`code=-8: "Your token has expired. Please generate a token"`). The
automatic fallback — `FyersTokenManager.refresh()`, using the stored
`FYERS_REFRESH_TOKEN` — ALSO failed, with a NEW finding:

    code=-16, message="Refresh token API is currently disabled to comply
    with SEBI regulations."

This directly contradicts `docs/FYERS_TOKEN_LIFECYCLE.md`'s prior
"verified live" claim that this refresh flow works — it worked when
verified, and has since been disabled by FYERS for regulatory reasons
(SEBI). **Automatic daily token renewal is currently non-functional for
this account.** `docs/FYERS_TOKEN_LIFECYCLE.md` updated with this finding.

**Consequence for span_margin certification: LIVE CERTIFICATION NOT
COMPLETE.** None of the six certification objectives (authentication,
request schema, response schema, multi-leg behavior, error behavior,
repeatability) could be attempted — every one requires a valid,
authenticated session, and none currently exists. `capital_policy:
CERTIFIED` and `margin_provider_certified: true` remain **disabled**, per
the explicit instruction not to guess or infer certification.

**Unblocking this:** the interactive login flow (browser + TOTP/PIN) must
be run manually to obtain a fresh `FYERS_ACCESS_TOKEN` — this cannot be
done by an agent; it requires the account owner's own 2FA. Once a valid
token is in place, the span_margin certification attempt can be re-run
immediately (the test script is ready — see `docs/CAPITAL_MANAGEMENT_ENGINE.md`
for what it will exercise).

## Pass 8 — span_margin LIVE CERTIFICATION COMPLETE

After the token was refreshed (interactive login, run by the account
owner), re-attempted certification against the real account. All six
objectives completed with live evidence:

**1. Authentication** — `Authorization: "{app_id}:{access_token}"` CONFIRMED
correct (HTTP 200 with a valid token; HTTP 401 `{"s":"error","code":-17,
"message":"Could not authenticate the user"}` with an invalid one).

**2. Request schema** — confirmed required fields: `symbol`, `qty`, `side`
(-1=sell), `type` (2=market), `productType` ("INTRADAY"), `limitPrice`,
`stopLoss`, all inside a `data` array (supports multiple legs in one call).
Omitting required fields -> `{"s":"error","code":-50,"message":"Invalid
input"}`.

**3. Response schema — REAL SHAPE FOUND, DIFFERENT FROM WHAT COMMUNITY
POSTS SUGGESTED.** The margin figures are nested under a `"data"` key, not
top-level:

    {"code": 200, "message": "", "s": "ok", "latency": "",
     "data": {"span": <float>, "expo": <float>, "total": <float>, "benefit": <float>},
     "individual_info": {"<internal_id>": {"ltp_info", "span", "expo", "total"}, ...}}

This was a REAL BUG in the pre-certification implementation (it read a
top-level `data["total"]` that does not exist — would have returned
`None`/`fyers_span_margin_unexpected_shape` on every real call, never
actually working). **Fixed**: `broker/fyers.py`'s `get_order_margin()` now
reads `response["data"]["total"]`.

**4. Multi-leg behavior — CONFIRMED, with genuine hedging benefit.** A real
NIFTY 24350 CE+PE short straddle (65 qty, the live-verified lot size):
single CE leg alone = ₹142,048.25 margin; CE+PE combined = ₹143,177 total,
with `benefit: 142048.25` — the combined margin is barely above a single
leg's margin, not additive (which would have been ~₹275,000+) — proving
the exchange's SPAN hedging benefit is genuinely applied to the combined
position, not two legs priced independently.

**5. Error behavior — all captured and now used as regression fixtures**
(`tests/test_fyers_span_margin_certified.py`):
  - invalid symbol -> HTTP 400, `{"s":"error","code":-310,"message":
    "Please provide valid symbols"}`
  - malformed payload -> HTTP 400, `{"s":"error","code":-50,"message":
    "Invalid input"}`
  - invalid/expired token -> HTTP 401, `{"s":"error","code":-17,"message":
    "Could not authenticate the user"}`

**6. Repeatability — CONFIRMED.** Two identical back-to-back calls returned
byte-identical response bodies.

**Also fixed during this same certification (funds mapping):** the real
`fund_limit` row titles were captured live and are DIFFERENT from the
pre-certification guess — the actual title is `"Clear Balance"`, not
`"Clear Cash"`. A genuine `"Collaterals"` row exists and is now mapped to
`collateral` (previously always `None`). `get_funds()` updated accordingly.

**Result:** `config.yaml` updated to `capital_policy: CERTIFIED`,
`margin_provider_certified: true`. `FyersBroker.get_order_margin()` and
`get_funds()` are now genuinely live-verified, not merely researched.
`docs/CAPITAL_MANAGEMENT_ENGINE.md` updated with the full certified
request/response schema. 8 new regression tests
(`tests/test_fyers_span_margin_certified.py`) lock in every captured
response shape and error code as a fixture — a future SDK/endpoint
regression will be caught immediately instead of silently degrading to
"None" in production.

**Separately discovered during this pass (unrelated to margin, but
important):** the automatic token-refresh flow this codebase previously
verified working (`FyersTokenManager.refresh()`) is now REJECTED by FYERS:
`code=-16, message="Refresh token API is currently disabled to comply with
SEBI regulations."` This is a new regulatory restriction, not a code bug —
`docs/FYERS_TOKEN_LIFECYCLE.md` updated. Automatic daily token renewal is
currently non-functional; the interactive login flow must be run manually
every trading day.

## Pass 9 — Strategy parameter change: exit rule (explicit operator request)

**Not a bug fix — a deliberate, requested strategy parameter change.**
Changed the Premium VWAP exit trigger from "2 consecutive candle closes
above VWAP" to "1 candle close above VWAP" — i.e., exit fires immediately
on the first close above the running VWAP, no confirming second candle.

**Changed:** `bujji/trade/manager.py::TradeManager._check_vwap_breach` —
threshold `self._consecutive_above < 2` -> `< 1`. `_consecutive_above` is
still tracked (for the DecisionTrace/dashboard's observability) but no
longer gates the decision beyond the first breach.

**Tests updated:** `tests/test_trade_manager.py` —
`test_exit_on_two_consecutive_above_vwap` replaced with
`test_exit_on_first_candle_close_above_vwap` (single breach -> immediate
EXIT) and `test_streak_resets_on_below_candle` replaced with
`test_hold_persists_while_premium_stays_at_or_below_vwap` (multiple
at/below candles still HOLD; the first strictly-above candle exits
immediately).

**Documentation updated:** `docs/ARCHITECTURE.md`'s strategy summary,
`bujji/signal/indicators.py`'s `PremiumVwapTracker` docstring.

**Consequence, empirically observed:** re-running the real-data backtests
from this session with the new rule would very likely change every
day's exit point and PnL (several of those backtests exited exactly on
the second consecutive candle, which now would have exited one candle
earlier) -- if a fresh backtest comparison is wanted, it should be re-run
against this new rule rather than compared to the Pass 8-era backtest
results recorded in the chat history.

## Pass 10 — Premium VWAP: equal-weight -> genuine volume-weighted

**Not a bug fix — a deliberate, requested design change**, prompted by
re-examining the codebase's own long-standing justification for
equal-weighting ("options volume from FYERS is too noisy to use as a
weight"). Verified live during this pass: real FYERS ATM option 5-minute
candles carry genuine, substantial, non-zero volume throughout the session
(checked a full day's worth for both CE and PE — zero zero-volume candles).
The original justification did not hold for ATM strikes specifically, so
the operator asked for a real volume-weighted VWAP.

**New capability required and built:** `Broker.get_option_candles(contract,
minutes, count)` — distinct from `get_recent_candles` (which is the
underlying INDEX's candles) — returns real OHLCV (with volume) for a
SPECIFIC option contract. `get_ltp()` alone cannot support this: it only
ever returns a bare last-traded-price snapshot with no volume attached.
Implemented in all four broker adapters:
  - `FyersBroker`: real historical-candle fetch for the option's own
    symbol (same call pattern as the index-candle fetch, verified live).
  - `PaperBroker`/`ReplayBroker`: synthetic single candle with a
    configurable volume (`set_option_volume()` / schedule-driven).
  - `HybridPaperBroker`: delegates to the live leg.
  - `ExecutionEngine.get_option_candles()`: new retry-wrapped pass-through,
    matching `get_ltp`/`get_spot`'s existing pattern.

**`PremiumVwapTracker`** (`signal/indicators.py`) now computes
`sum(premium_i * volume_i) / sum(volume_i)` instead of a plain running
average. `update()` takes an optional `volume` (default 1.0) so the
entry-time seed (a single fill, not a "candle") and any caller that
genuinely cannot obtain volume both degrade to an equal-weight
contribution for that one data point — never a crash, never a
fabricated volume. A reported volume of 0 or negative is also treated as
1.0 (equal-weight) rather than being allowed to silently zero out that
candle's contribution entirely (which `price * 0 = 0` would otherwise do
to both the numerator AND denominator, correctly excluding it, but this
codebase prefers "still counted, unweighted" over "silently vanished").

**`Orchestrator._handle_in_position`** now fetches each leg's own most
recent completed candle (for its close AND volume) instead of a bare LTP
snapshot, falling back to plain `get_ltp` (equal weight) if
`get_option_candles` returns an empty list (broker doesn't support it).

**Backward compatibility:** all 242 pre-existing tests passed unchanged,
by construction — `PaperBroker`'s default synthetic volume is a constant
every candle, and a constant weight applied to every term cancels out of
`sum(p*v)/sum(v)`, mathematically reducing to the exact old equal-weight
average. This was verified explicitly with a dedicated test
(`test_equal_volumes_reduce_to_equal_weight_average`), not assumed.

**New tests:** `tests/test_premium_vwap_volume.py` (6 tests — the core
volume-weighting formula, zero/negative-volume fallback, backward-
compatible equal-volume case) and
`tests/test_volume_weighted_vwap_integration.py` (2 tests — real per-candle
volume flowing end-to-end through the Orchestrator, and the no-
get_option_candles-support fallback path).

**Documentation updated:** `docs/ARCHITECTURE.md`, `docs/OPERATIONS_RUNBOOK.md`.

**Not yet re-run:** the two real-data backtests recorded earlier in this
session (July 13-17) used the OLD equal-weight tracker throughout — a
fresh re-run against real captured volume would need `RealDataBroker`
(the ad hoc backtest script, not part of the production package) extended
with a `get_option_candles()` implementation serving the real captured
volume data, which was not done as part of this pass.

## Pass 11 — Market Intelligence Core: Regime Brain (new platform layer)

**Not a bug fix, not a production change — a new, standalone OBSERVATION
layer above production**, per the Market Intelligence Core research
design from this session. `bujji/intelligence/` never places an order,
sizes a trade, or calls into the Capital Management Engine, Execution
Engine, Orchestrator, or Trade Manager -- it reads real market data BUJJI
already has and emits structured, evidence-carrying readings only.

**Built:** the Regime Brain -- classifies a trading session as TRENDING /
RANGING / VOLATILE / COMPRESSED / TRANSITIONING / UNKNOWN using Kaufman's
Efficiency Ratio and realized-volatility statistics on real NIFTY spot
candles. Deliberately not ML (insufficient real history to honestly
validate one). Every reading carries its raw evidence for independent
verification -- same explainability discipline as `SizingDecision.render()`.

**Real-data validated, not just unit tested:** ran the brain against all
13 real NIFTY trading days fetched live earlier this session
(2026-07-01 to 2026-07-17). 12 of 13 genuinely quiet days (day-range
0.45%-1.11%) correctly classified RANGING; the one day with a materially
larger real move (07-08: 2.04% range, -1.60% net) was correctly excluded
from the RANGING bucket. Full detail in `docs/MARKET_INTELLIGENCE_CORE.md`.

**Calibration finding, documented not silently fixed:** 07-08's
efficiency ratio (0.391) fell short of the TRENDING threshold (0.60)
despite being the clearest directional day in the sample -- flagged as a
first-pass threshold that may need revisiting once more real history
accumulates, not adjusted based on a 13-day sample.

**Tests:** `tests/test_regime_brain.py` -- 11 tests (data-quality gate,
one synthetic case per regime at realistic NIFTY price scale -- the first
draft used unrealistic 5-10% synthetic candle swings and every case
tripped the volatility check first; caught and fixed by rescaling to real
NIFTY magnitudes rather than loosening the threshold to fit the bad test
data -- plus a real-data regression fixture).

**Explicitly not done:** not wired into the dashboard, journal, or any
decision path. Standalone and inert with respect to production, exactly
as the MIC's first principle requires.

## Pass 12 — Market Intelligence Core: Volatility Brain

**Second brain in the MIC, same observation-only discipline as Pass 11**
(Regime Brain) -- `bujji/intelligence/volatility_brain.py` never places an
order, sizes a trade, or touches the Capital Management Engine, Execution
Engine, Orchestrator, or Trade Manager.

**Built:** solves implied volatility per-leg (CE/PE) from real market
premiums via Newton-Raphson on Black-Scholes (bisection fallback),
computes realized volatility from real spot candles, and classifies
richness = IV/RV. Emits expected move to expiry. IV rank/percentile
always reported as None -- FYERS serves no historical data for expired
option contracts (same finding as this session's backtesting work),
so rather than fabricate a "typical" range this brain states the
limitation explicitly.

**Solver validated first, independent of any market data:** synthetic
round-trip check across 6 known sigmas (0.08-0.50) -- Black-Scholes price
in, exact sigma recovered out, before trusting the solver on anything
real.

**Real-data validated:** ran against all 5 real trading days with real
CE/PE premiums (2026-07-13 to 2026-07-17). All 5 classified IV_RICH,
IV 10.5%-15.4%, IV/RV ratio 1.16-1.70 -- consistent with the well-known
volatility risk premium and with the direction a premium-selling
strategy needs to have any edge.

**Two bugs found via testing, not inspection:**
1. Real bug: `iv_average` was computed from whichever of iv_ce/iv_pe
   solved, even with only one leg -- a straddle's IV silently treated as
   known from half the position. Fixed to require both legs before
   computing iv_average/richness; a single-leg solve failure now
   degrades the whole reading to UNKNOWN/INSUFFICIENT.
2. Test bug (not the brain): the 2026-07-13 real-data regression test
   used a hand-picked, down-sampled approximation of the real spot
   closes instead of the exact sequence, which produced a wrong
   classification in the test itself. Fixed by re-fetching and embedding
   the exact real 16-candle close sequence.

**Tests:** `tests/test_volatility_brain.py` -- 17 tests (solver
correctness incl. puts, below-intrinsic/non-positive refusal,
data-quality gate, partial-leg-solve degradation, richness
classification, expected-move sanity, rendering, real-data regression).
All passing. Full suite: 278/278 passing, no regressions.

**Explicitly not done:** not wired into the dashboard, journal, or any
decision path -- standalone and inert with respect to production, same
as the Regime Brain.

## Pass 13 — Market Intelligence Core: Premium Brain

**Third brain in the MIC, same observation-only discipline as Pass 11/12**
-- `bujji/intelligence/premium_brain.py` never places an order, sizes a
trade, or touches the Capital Management Engine, Execution Engine,
Orchestrator, or Trade Manager.

**Built:** isolates how much of the combined straddle premium's change
since entry is explained by pure time decay (theta) alone, vs. everything
else (real spot movement, real IV changes). Holds spot and IV fixed at
their entry values, reprices the straddle at the current time via
Black-Scholes (reusing the exact `_bs_price` pricer already validated in
Pass 12's solver round-trip test), and compares that theoretical
theta-only baseline against the real current combined premium.
Classifies `DECAYING_FASTER_THAN_THETA` / `DECAYING_AS_EXPECTED` /
`RISING_AGAINST_THETA`. Also reports premium-captured % and time-elapsed
%.

**Data-quality discipline:** requires a known entry IV -- refuses to
fabricate a theta-only baseline without one, returning
`UNKNOWN`/`INSUFFICIENT` instead. Also gated on non-positive premiums,
`now` before entry, and at/past-expiry.

**Synthetic correctness check:** fed the brain a "current" premium
computed from its own Black-Scholes formula with only time moved forward
-- ratio came back exactly 1.0, confirming the theta-only baseline
computation itself is correct before trusting it on real data.

**Real-data validated:** 2026-07-13, real combined premium rose from
378.20 (09:20 entry) to 418.35 (10:30) as spot rallied +127 points. The
theta-only baseline barely moved (378.20 -> 377.05, correctly, given how
little time had passed relative to ~8 days to expiry) -- ratio 1.11,
correctly attributing the premium rise to the real spot move rather than
misleadingly flagging it as decay-defying premium behavior.

**Tests:** `tests/test_premium_brain.py` -- 11 tests (data-quality
gates, exact synthetic baseline correctness, classification edge cases,
derived-field sanity, rendering, real-data regression). All passing.
Full suite: 289/289 passing, no regressions.

**Explicitly not done:** not wired into the dashboard, journal, or any
decision path -- standalone and inert with respect to production, same
as the Regime and Volatility Brains.

## Pass 14 — Market Intelligence Core: Greeks Brain

**Fourth brain in the MIC, same observation-only discipline as Pass
11/12/13** -- `bujji/intelligence/greeks_brain.py` never places an
order, sizes a trade, or touches the Capital Management Engine,
Execution Engine, Orchestrator, or Trade Manager.

**Built:** standard closed-form Black-Scholes delta/gamma/theta per leg
(CE, PE), combined into POSITION Greeks for the actual SHORT straddle
(sign-flipped, summed). Reuses `_norm_cdf`/`_norm_pdf`/`_bs_price`/
`_bs_vega` from the already-validated Volatility Brain; only
delta/gamma/theta are new formulas here. Theta reported per calendar
day, vega per 1% IV change. Classifies net directional exposure
(`DELTA_NEUTRAL` / `NET_LONG_EXPOSURE` / `NET_SHORT_EXPOSURE`) from
position delta.

**Formula correctness proven via finite-difference cross-checks, not
just visual inspection:** delta and vega matched finite differences of
the trusted pricer to 1e-3/1e-2. Theta's first finite-difference check
(1-day step) showed ~5% discrepancy against the analytic formula --
investigated and found to be ordinary discretization error from a step
too large relative to the 5-day-to-expiry window, not a bug; a much
smaller time step (1e-5 years) converged to the analytic value within
0.05/day, confirming the formula. Gamma matched the finite difference of
delta itself.

**Known-value sanity:** ATM CE delta ~+0.52, ATM PE delta ~-0.48 (both
near textbook +-0.5); per-leg theta negative (long options decay);
**position theta positive**, confirming the seller structurally earns
from time decay -- the core premise of this strategy.

**Real-data validated:** the real 24000-strike straddle at the real
09:20 entry on 2026-07-13, with the real solved entry IVs (CE 11.68%, PE
14.39%), came back DELTA_NEUTRAL with positive position theta -- the
expected shape for an ATM entry.

**Tests:** `tests/test_greeks_brain.py` -- 19 tests (finite-difference
formula correctness for all four Greeks, ATM sanity, exposure
classification, data-quality gates, rendering, real-data regression).
All passing. Full suite: 308/308 passing, no regressions.

**Explicitly not done:** not wired into the dashboard, journal, or any
decision path -- standalone and inert with respect to production, same
as the other three brains.

## Pass 15 — Market Intelligence Core: Liquidity Brain (with live data-access certification)

**Fifth brain in the MIC, same observation-only discipline as Pass
11-14** -- `bujji/intelligence/liquidity_brain.py` never places an
order, sizes a trade, or touches the Capital Management Engine,
Execution Engine, Orchestrator, or Trade Manager.

**Data access certified live before any brain logic was written** --
Liquidity was previously listed in this doc's status table as
"designed, data access unverified." Closed out with the same discipline
as the span_margin certification earlier this session: found the VPS's
FYERS access token had expired and refresh_token exchange is disabled
(SEBI rule) -- same situation as before. User re-ran the manual login
flow to refresh the token, then a live `quotes` call against real NIFTY
weekly ATM CE/PE symbols confirmed the response includes genuine
per-symbol `bid`, `ask`, and `spread` fields, with `spread == ask - bid`
holding exactly on both legs (CE 82.4/82.6, PE 68.0/68.05) -- real,
usable top-of-book data.

**Deliberately excluded, not guessed:** the same response's `volume`
field returned 400M+ for a single option symbol -- implausible as a
per-symbol traded quantity and not corroborated elsewhere. Rather than
assume what it represents, the brain does not consume it. Multi-level
depth beyond top-of-book bid/ask was not observed in the response either
and is not assumed available.

**Built:** combined top-of-book spread (both legs' ask minus both legs'
bid, the real cost of exiting a short straddle at the current ask),
reported in points and as % of combined mid. Classifies TIGHT / NORMAL /
WIDE (first-pass thresholds, documented as uncalibrated like every other
brain's thresholds).

**Data-quality gates:** non-positive bid/ask on either leg, or a crossed
market (ask below bid) -> UNKNOWN/INSUFFICIENT. Raw quote values still
reported even on a bad reading.

**Integration note:** no current broker method exposes bid/ask (only
`get_ltp()`, last price only) -- this brain takes bid/ask as plain
arguments, same pattern as the Volatility/Premium/Greeks Brains taking
premiums/IV directly. Wiring live bid/ask into production is a separate,
not-yet-done integration step.

**Real-data validated:** the live-captured 24250-strike quote (CE
82.4/82.6, PE 68.0/68.05) produced a combined spread of 0.25 points
(0.166% of mid) -- correctly classified TIGHT.

**Tests:** `tests/test_liquidity_brain.py` -- 16 tests (exact spread-math
correctness, classification edge cases, data-quality gates including
crossed-market detection on either leg, raw-value visibility on bad
readings, rendering, real-data regression). All passing. Full suite:
324/324 passing, no regressions.

**Explicitly not done:** not wired into the dashboard, journal, or any
decision path -- standalone and inert with respect to production, same
as the other four brains. Bid/ask not yet plumbed into the broker
interface for live production use. Multi-level depth and the `volume`
field's true meaning remain open items requiring their own live
verification.

## Pass 16 — Market Intelligence Core: Structure Brain (with live OI data-access certification)

**Sixth brain in the MIC, same observation-only discipline as Pass
11-15** -- `bujji/intelligence/structure_brain.py` never places an
order, sizes a trade, or touches the Capital Management Engine,
Execution Engine, Orchestrator, or Trade Manager.

**Data access certified live before any brain logic was written.**
Structure's OI-wall half was previously listed as "designed, data access
unverified." Before assuming FYERS exposes open interest at all,
inspected the installed `fyers_apiv3` SDK and found a dedicated
`optionchain` endpoint distinct from the plain `quotes` call every other
brain uses. Called it live against real NIFTY strikes: response includes
per-strike `oi`, `prev_oi`, `oich` for both CE and PE, with
`oich == oi - prev_oi` holding exactly on every strike checked (e.g.
24100 PE: oi=17,299,295, prev_oi=10,241,300, oich=7,057,995 -- exact
match) -- genuine, internally consistent open interest data.

**Built:** resistance = strike ABOVE spot with highest CE OI ("call
wall"); support = strike BELOW spot with highest PE OI ("put wall").
Put/Call OI ratio reported as evidence only, not yet classified (not
enough real history to calibrate a PCR signal honestly). Classifies
NEAR_RESISTANCE_WALL / NEAR_SUPPORT_WALL (whichever wall is strictly
closer, within ~one strike-width) / MID_RANGE.

**Data-quality gates:** non-positive spot, no strikes with usable
non-negative OI, or no strikes on either side of spot ->
UNKNOWN/INSUFFICIENT. One-sided data (only strikes above or below spot)
produces a partial, honest reading rather than a fabricated symmetric
one.

**Real-data validated:** the live-captured NIFTY option chain (spot
24243.1, strikes 24100-24250) put spot almost exactly under the 24250
call wall (CE OI 12.5M) -- distance 0.0285%, correctly classified
NEAR_RESISTANCE_WALL. The 24200 strike had the largest PE OI (25.9M)
and was correctly identified as support, with the classifier correctly
picking the strictly-nearer wall (24250) over the higher-OI one (24200)
when both were within threshold.

**Tests:** `tests/test_structure_brain.py` -- 16 tests (wall-selection
correctness with opposite-side-OI-must-not-leak checks, proximity
classification including the nearer-wall-wins tiebreak, data-quality
gates, one-sided data handling, rendering, real-data regression). All
passing. Full suite: 340/340 passing, no regressions.

**Explicitly not done:** not wired into the dashboard, journal, or any
decision path -- standalone and inert with respect to production, same
as the other five brains. PCR is reported but not classified. The live
verification pulled only 4 strikes around spot; production use would
need a wider strike range to find true walls further out, a separate
integration concern.

## Pass 17 — Market Intelligence Core: Event Brain (narrowed scope, live VIX certification)

**Seventh brain in the MIC, same observation-only discipline as Pass
11-16** -- `bujji/intelligence/event_brain.py` never places an order,
sizes a trade, or touches the Capital Management Engine, Execution
Engine, Orchestrator, or Trade Manager.

**Scope deliberately narrowed, stated up front.** The MIC's original
"Event Brain" concept included a calendar half (FOMC, RBI policy, Union
Budget, similar scheduled macro events). No FYERS endpoint used anywhere
in this codebase (`quotes`, `historical`, `optionchain`) provides an
economic calendar, and there is no other integration for one. Rather
than fabricate event dates from general knowledge -- which would
silently go stale and could be wrong for any given contract cycle --
that half is explicitly NOT built, same open-item status as the
standalone OI brain.

**What was built, on two genuinely verifiable sources:**
1. Expiry proximity -- pure date arithmetic against the straddle's own
   real expiry date, needing no external data at all.
2. VIX regime -- live-verified `NSE:INDIAVIX-INDEX` quote via the same
   `quotes` endpoint the Liquidity Brain's certification used
   (`lp=13.02, prev_close_price=13.15, chp=-0.99` at capture, a
   plausible real level). Classifies LOW/MODERATE/ELEVATED (first-pass
   bands, documented as uncalibrated).

**Deliberately not synthesized into one risk score** -- the two
dimensions are reported independently; inventing a merge formula would
be unvalidated synthesis this codebase avoids elsewhere.

**Data-quality handling:** a missing/invalid VIX must not block the
always-computable expiry reading and vice versa (independent
dimensions) -- same partial-but-honest pattern as the Structure Brain's
one-sided wall data. Fully INSUFFICIENT only when both fail;
`today > expiry_date` is treated as an inconsistent input and refused
rather than silently producing a negative days-to-expiry.

**Real-data validated:** real VIX (13.02 vs prev close 13.15) correctly
classified MODERATE with vix_change_pct=-0.989% (FYERS's own real
number); today (2026-07-20) against the real 2026-07-21 expiry correctly
classified EXPIRY_EVE.

**Tests:** `tests/test_event_brain.py` -- 16 tests (expiry-date
arithmetic including inconsistent-date refusal, VIX regime
classification, independent-dimension partial-data honesty,
data-quality gates, rendering including the explicit "calendar events
not covered" note, real-data regression). All passing. Full suite:
356/356 passing, no regressions.

**Explicitly not done:** not wired into the dashboard, journal, or any
decision path -- standalone and inert with respect to production, same
as the other six brains. Economic-calendar events remain entirely
unbuilt -- adding a calendar vendor integration is a separate, much
larger undertaking, not attempted here.

## Pass 18 — Market Intelligence Core: Behaviour Brain (built inert by design, data gap confirmed)

**Eighth brain in the MIC, same observation-only discipline as Pass
11-17** -- `bujji/intelligence/behaviour_brain.py` never places an
order, sizes a trade, or touches the Capital Management Engine,
Execution Engine, Orchestrator, or Trade Manager.

**Data reality checked before writing any logic, and it changed the
plan.** Queried the real trade journal (`data/bujji.db`): zero completed
live trades. Checked this session's real-data backtest artifacts under
`/tmp/bujji_backtest_real_*`: only ~5 correlated real trading days on
file (2026-07-13 to 2026-07-17, re-run several times, not independent
additional history). This confirms and is worse than the MIC status
table's earlier "blocked on history" note for this brain.

**Stopped and asked the user before building anything** rather than
either fabricating patterns from 5 data points or silently building a
brain that could never say anything real. User chose: build the full
interface now, but make it inert until real data exists.

**Built:** win_rate, avg_pnl, current_streak (signed, consecutive
same-direction outcomes from the most recent trade backward),
streak_signal (WINNING_STREAK/LOSING_STREAK at |streak| >= 3, else
NORMAL), and exit_reason_breakdown (count/win-rate/avg-pnl per real
recorded exit reason) -- ALL gated behind a hard
`MIN_TRADES_REQUIRED = 30` floor (documented first-pass rule-of-thumb,
not a rigorous derivation for this strategy's specific variance). Below
the floor, every field is UNKNOWN/None -- not a partial reading, not a
low-confidence guess.

**No real-data validation section exists for this brain, unlike every
other brain built this session.** There is no real data to validate
against yet. That section gets written honestly once real trades
accumulate.

**Tests:** `tests/test_behaviour_brain.py` -- 13 tests (the hard gate at
zero trades, the actual ~5-trade real count, one-below-threshold,
exactly-at-threshold; arithmetic correctness on synthetic sequences
sized above the floor; streak counting in both directions; exit-reason
grouping; rendering). All passing. Full suite: 369/369 passing, no
regressions.

**Explicitly not done:** not wired into the dashboard, journal, or any
decision path -- standalone and inert with respect to production, same
as the other seven brains, and ALSO inert with respect to its own
output until real trade history exists. The 30-trade floor is a
placeholder pending real-data calibration.

## Pass 19 — Market Intelligence Core: wired all eight brains into the dashboard (read-only)

**Not a new brain -- integration of the eight already-built brains
(Pass 11-18) into the live dashboard**, strictly as a read-only
observation panel. No brain output feeds back into any trading
decision, order, or sizing at any point in this wiring.

**Built:** `bujji/intelligence/runner.py` (new) -- the single place
production code touches `bujji.intelligence`. Computes all eight
brains' readings from whatever real data is on hand each cycle;
position-dependent brains (Volatility, Premium, Greeks) are simply
ABSENT from the result when there's no real open position with real
current premiums, never a fabricated placeholder. `RuntimeStatus.intelligence`
(new field) carries the result to the dashboard, same pattern as the
existing `capital_health`/`market_data_health` fields.
`Orchestrator._update_intelligence()` runs at the very end of
`on_candle()`, after the VWAP audit, wrapped in try/except so a failure
here can never affect the state machine or an open position. A bounded
80-candle real spot history feeds the Regime Brain; never read by any
trading logic.

**Coverage stated honestly, not glossed over:** Regime, Behaviour, and
Event's expiry-half always run (their inputs -- spot candle history, the
real trade journal, pure date arithmetic -- are always available).
Volatility/Premium/Greeks run only with a real open position and real
current premiums. Liquidity, Structure, and Event's VIX half were
live-certified as real data sources in Pass 15-17, but the actual broker
calls were never wired into the live trading loop's data path -- that
remains a separate, explicitly deferred integration step. Their
dashboard cards show "NOT AVAILABLE" with an honest reason until that
happens.

**Three real bugs caught during this pass, before shipping:**
1. The natural approach to feeding the Premium Brain an entry IV was to
   split `Position.entry_price` (the only entry premium the journal
   records -- COMBINED, never per-leg) 50/50 across CE/PE. Caught and
   rejected during review -- CE and PE premiums are rarely close to
   equal; that split would silently feed a wrong IV into downstream
   theta math. Fixed to pass `entry_iv=None` honestly; Premium Brain's
   own gate reports UNKNOWN with reason `no_entry_iv` instead.
2. The first draft used `position.entry_spot` as "current spot" for live
   Greeks/IV -- silently stale the moment spot moved after entry. Fixed
   to use the real current spot from the latest candle, with a
   regression test that fails if this regresses.
3. The dashboard's generated JavaScript had a nested-single-quote syntax
   error that would have broken the page's entire script tag in a real
   browser. Caught by extracting and syntax-checking the generated JS
   with `node --check` (not just visual inspection), then verifying full
   end-to-end rendering against the real live API response using a real
   DOM (`jsdom`) -- confirmed all eight brain cards render correctly
   with zero runtime JS errors.

**Tests:** `tests/test_intelligence_runner.py` -- 13 tests (no-crash on
empty input, position-dependent brains correctly absent under various
missing-data conditions, all three populate with a real position, the
entry-IV-never-fabricated regression, the current-spot-not-entry-spot
regression, Behaviour trade-row reordering, null-safety of the
not-yet-wired brains). All passing. Full suite: 382/382 passing, no
regressions.

**Explicitly not done:** Liquidity/Structure/Event-VIX still have no
real production data feed -- extending the broker interface with live
quote/optionchain/VIX calls in the trading loop is separate future work.
The journal doesn't record separate CE/PE entry premiums, so Premium
Brain will show UNKNOWN for every real position until that's added. No
dashboard control lets a human interact with or override any brain --
by design, this stays observation only.

## Pass 20 — Wired Liquidity and Structure into the live trading loop's data path

**Closes the gap Pass 19 explicitly deferred**: Liquidity and Structure
were rendering "NOT AVAILABLE" on the dashboard because no real bid/ask
or option-chain OI was ever fetched in the live trading loop, even
though both data sources were live-certified back in Pass 15/16.

**Built:** `Broker.get_quote(contract)` and
`Broker.get_option_chain(underlying, spot, strike_count)` added to the
broker interface (default `None`, same pattern as `get_funds`).
Implemented for real in `FyersBroker` using the exact endpoints verified
live in Pass 15/16. Delegated through `HybridPaperBroker` (the mode the
live service actually runs) exactly like `get_ltp`. Wrapped in
`ExecutionEngine.get_quote`/`get_option_chain` as best-effort,
single-attempt, never-raising calls -- deliberately not run through the
retry/backoff path used for critical trading calls, since observational
data going stale for one cycle is harmless and burning the retry
schedule on it is not. `Orchestrator._update_intelligence` (made async)
now fetches the open straddle's real CE/PE bid/ask when a position
exists, and a real 5-strike option chain around spot every cycle
regardless of position.

**A real bug caught before shipping:** `get_option_chain` initially read
`data["optionsChain"]` directly and always returned zero strikes on real
data. Rather than accept "zero strikes" as a plausible answer, re-ran
the live check and found the payload is actually nested under a
top-level `"data"` key -- the same pattern already known from
`get_funds`/`get_order_margin` earlier this session but missed on this
endpoint's first pass. Fixed and locked in with a regression test using
the exact real response shape.

**End-to-end proof, not just unit tests:** ran the full real pipeline
live -- `FyersBroker.get_quote`/`get_option_chain` against the real
account straight into `run_intelligence(...)` -- confirmed both brains
return SUFFICIENT with real numbers (Structure correctly identified a
real support wall at 24200 with spot 0.159% away, correctly classified
NEAR_SUPPORT_WALL).

**Tests:** 5 new tests in `tests/test_fyers_transport_mapping.py`
(quote parsing incl. crossed/zero-quote refusal, the nested-`"data"`-key
regression, empty-chain handling), 1 in `tests/test_hybrid_paper_broker.py`
(delegation through the live leg), 3 in `tests/test_execution_and_e2e.py`
(never-raises-on-failure, clean pass-through). All passing. Full suite:
391/391 passing, no regressions.

**Deployed:** `bujji.service` restarted with these changes live (no open
position at restart time, confirmed via `session_state.json` before
restarting).

**Explicitly not done:** Event's VIX half still has no live feed wired
-- out of scope for this request, remains a separate step. Option-chain
fetch uses a fixed `strike_count=5`; making that configurable is a
future refinement.

## Pass 21 — Wired Event Brain's VIX half into the live trading loop's data path

**Closes the last data-coverage gap from Pass 19/20**: after Pass 20
wired Liquidity and Structure in, Event's VIX half remained the only
MIC data source still not fetched anywhere in the live trading loop.

**Built:** `Broker.get_vix()` added to the interface (default `None`,
same pattern as `get_quote`/`get_option_chain`), implemented for real in
`FyersBroker` reusing the exact `NSE:INDIAVIX-INDEX` quote shape
verified live in Pass 17. Delegated through `HybridPaperBroker`, wrapped
in `ExecutionEngine.get_vix` as a best-effort, single-attempt,
never-raising call, identical discipline to the Pass 20 wrappers.
`Orchestrator._update_intelligence` fetches real VIX every cycle
(market-wide, not tied to position state) and feeds it into
`run_intelligence(...)`.

**No new parsing bug this time** -- `get_vix` reuses the exact `ltp`
action/response shape already verified and locked in by `get_quote`, so
there was no new surface to get wrong (unlike Pass 20's `optionchain`
nesting mistake).

**End-to-end proof against the real account:** `FyersBroker.get_vix()`
returned the real current India VIX (12.98, down from a real prior
close of 13.15) straight into `run_intelligence(...)`, correctly
classified LOW with SUFFICIENT data quality.

**Tests:** 4 new tests in `tests/test_fyers_transport_mapping.py`
(verified-shape parsing, missing symbol, non-positive-level refusal,
prev_close omission), the existing hybrid-delegation test extended to
cover `get_vix`, and 1 new test in `tests/test_execution_and_e2e.py`
confirming the never-raises discipline. Full suite: 396/396 passing, no
regressions.

**Coverage milestone:** all eight brains now have their live production
data feed wired in. Every data source flagged across Pass 15-19 as "not
yet wired into production" is now live. Remaining gaps are structural,
not integration debt: Behaviour is deliberately inert pending real trade
history, the economic-calendar half of Event has no data source at all,
and OI as a standalone brain concept was never separately built (its
function is covered by Structure).

**Deployed:** `bujji.service` restarted with these changes live (no open
position at restart time, confirmed before restarting).

## LIVE CERTIFICATION REQUIRED (cannot be closed by this audit)

- FYERS fill-response field mapping (`filledQty`, `tradedPrice`, status
  codes, `orderTag` echo) — item 7 above. Blocks small-live-capital
  certification until one real order confirms the response shape.
