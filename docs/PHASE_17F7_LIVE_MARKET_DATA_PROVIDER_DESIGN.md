# Phase 17F.7 — Live MarketDataProvider Design Audit

**Status: AUDIT ONLY. No code. No wiring.**

Follows the operator's decision (post-17F.6.2) to prioritize a live
`MarketDataProvider` implementation. Audits `MarketDataProvider`'s actual
interface, its actual real caller (`bujji_options_os_runner.py`, not
assumed), the chain-row shape it must produce, and — the question this
document exists to answer — whether that live implementation needs
`FyersTickFeed`/websockets at all. Every claim below is grounded in code
read this session, not inferred from a docstring's own description.

---

## Part 0 — The finding that reframes the whole premise

**A live `MarketDataProvider` does not need `FyersTickFeed` or any
websocket at all.** The 17F.6.2 design doc's websocket-adapter chain is
not on the critical path for this work.

Traced by reading `bujji_options_os_runner.py` (`OptionsOSRunner`) in
full — the *only* real caller of `MarketDataProvider` in the active
architecture (grep confirms zero other non-test callers exist):

- `get_option_chain(as_of_date)` is called **exactly twice per session**:
  once in `_pre_market_check()` (a fail-closed readiness probe — the
  session refuses to start if this raises) and once in `_entry_window()`
  (to actually source the chain for trade construction). `ReplayChainProvider`
  itself caches after the first real load (`_ensure_loaded`'s `if
  self._chain is not None: return` guard), so the second call is free.
- `get_spot()` is called **exactly once**, immediately after the second
  `get_option_chain()` call, in `_entry_window()`.
- **There is no polling loop, no continuous consumption, anywhere in this
  runner.** `_position_management()` and `_eod_close()` each call
  `_run_one_management_pass()` **exactly once** — a **single pass**,
  explicitly labeled in the runner's own log line: *"POSITION_MANAGEMENT
  -- single-pass (Phase-1 has no intraday tick feed)."*
- The entire runner is an 8-stage state machine (`STARTUP` →
  `PRE_MARKET_CHECK` → `MARKET_SESSION` → `ENTRY_WINDOW` →
  `POSITION_MANAGEMENT` → `EOD_CLOSE` → `SESSION_ARCHIVE` → `SHUTDOWN`)
  run **once per invocation, once per trading day** — architecturally a
  single-shot batch process, not a long-running live loop a websocket
  feed would even have anywhere to plug into.

**Consequence:** `get_option_chain()`/`get_spot()` are satisfied by a
plain, synchronous **REST request-response** call, made twice a day.
Nothing about this consumption pattern needs a persistent socket,
callback hooks, reconnect logic, or `TickSilenceWatchdog`. Building a
live provider on `FyersTickFeed` would be strictly more machinery than
this interface's actual, current usage requires.

**Where a websocket genuinely would matter, and is explicitly NOT this
document's scope:** the runner's own docstring discloses, unprompted:
*"unrealized P&L will read as flat until a live/replay TICK feed (a
genuinely different, larger piece of work) is added."* Intraday position
revaluation during `POSITION_MANAGEMENT` is the one place continuous
ticks would matter — and it is explicitly named as separate, larger,
currently-unscoped future work, not part of `MarketDataProvider`'s own
interface or this phase's scope. **The 17F.6.2 websocket-adapter design
remains correct and will matter — for that future TICK-feed work, not
for this one.**

---

## Part 1 — The interface, verified

`bujji/production_runtime/market_data_provider.py` (106 lines, read in
full):

```
MarketDataProvider(ABC):
    get_option_chain(as_of_date: str) -> Sequence
    get_spot() -> Optional[float]
```

That is the entire contract. No futures data, no depth, no VIX, no
breadth — this interface is scoped narrowly to exactly what
`TradingSessionGovernor.attempt_entry`/`construct_trade` need: a chain
and a spot price. `MarketDataUnavailableError` is the one failure mode —
fail closed, never a stale/synthetic substitute.

**`ReplayChainProvider`** (Phase-1's only concrete implementation) loads
one historical NSE bhavcopy file via the already-real, already-tested
`bujji.options_observation.runner.ingest_all_option_series_from_bhavcopy`,
takes the last observation per series as the day's chain, and reads
`underlying_price` off any row for spot. **A live provider's job is to
produce the SAME shape from a live source** — not a new contract.

---

## Part 2 — The chain-row shape, verified (and a real gap found)

A chain row is `bujji.options_observation.models.OptionObservation`:
identity (`strike`, `expiry`, `option_type`, `underlying`,
`instrument_symbol`) plus a wrapped MOC `Observation` carrying payload
fields via `ALL_OPTIONS_OBSERVATION_FIELDS`: `open`, `high`, `low`,
`close`, `settlement`, `volume`, `open_interest`,
`change_in_open_interest`, `underlying_price`.

**`construct_trade()`** (`msi_trade_construction/engine.py`) fails closed
immediately (`REJECT_INCONSISTENT_CHAIN`) if `not chain or spot is None
or spot <= 0` — so a live provider returning an empty or malformed chain
degrades safely, not silently.

**Gap found: the existing `FyersBroker.get_option_chain()` method
(`bujji/broker/fyers.py`, audited in Phase 17B and reused since) returns
`List[Tuple[strike, ce_oi, pe_oi]]` — bare tuples, OI-only, no LTP, no
OHLC, no settlement, no per-contract identity.** This is structurally
insufficient to build a real `OptionObservation` — there is no premium
price anywhere in that return shape, and `construct_trade`/downstream
risk sizing almost certainly needs a real premium to price legs against
(not traced exhaustively in this pass — a live-provider implementation
phase would need to confirm exactly which fields `construct_trade` and
its downstream margin/risk calls actually read, field by field, before
writing code). **A live `MarketDataProvider` cannot be built on top of
`get_option_chain()` as it exists today without either extending that
broker method or sourcing per-strike quotes a different way.**

---

## Part 3 — Certification state, checked against what a live provider actually needs

`data_certification/fyers_option_chain_certification.json` certifies
`NIFTY_OPTION_CE` — **one single, live-resolved ATM contract**
(`NSE:NIFTY2681824350CE`, resolved from spot at cert time), not the full
multi-strike, both-sides (CE+PE) chain a real `MarketDataProvider` must
return. **A live provider needs the FULL chain certified — every strike
in the constructed range, both option types — which has not been done.**
This is a real, additional certification gap beyond what 17F.5 already
found, specific to this phase.

`NIFTY_SPOT` is certified (`CERTIFIED_AVAILABLE`) and sufficient for
`get_spot()` as-is — no additional certification work needed there.

---

## Part 4 — Wiring gap: there is no provider factory

`config/options_os_shadow.yaml`-shaped config (see `tests/test_options_os_runner.py`'s
`base_config`) already carries `providers.market_data.type: "replay_chain"`
— but **`OptionsOSRunner._startup()` never reads that `type` key.** It
hardcodes `self._market_data_provider = ReplayChainProvider(...)`
directly. The `"type"` field in config is presently **dead configuration**
— present, plausible-looking, and not actually dispatched on anywhere.

**Consequence:** adding a live provider is not "register a new type" —
it requires either (a) adding the dispatch logic `_startup()` currently
lacks (a small, real change to the runner itself, in scope for an
eventual implementation phase, not this audit), or (b) a parallel
composition path. Naming this now so it isn't assumed away later.

---

## Part 5 — What a live `MarketDataProvider` implementation actually needs

In priority order, all **not started**:

1. **A real per-strike quote/premium source.** Either extend
   `FyersBroker` with a method returning full per-strike OHLC/LTP/OI (not
   just OI), or call `get_quote()`-equivalent per contract symbol across
   the constructed strike range. Needs its own live-verification pass
   (this codebase's standing discipline — every broker field claim here
   has been "LIVE-VERIFIED" with a date and a real observed response;
   this would be no different).
2. **Full-chain certification** (Part 3) — every strike in range, both
   CE and PE, not just one ATM contract.
3. **A live implementation of `MarketDataProvider`** itself: `LiveChainProvider`
   or similarly named, satisfying the exact same two-method interface,
   constructing real `OptionObservation` rows from the source in item 1.
   Given Part 0's finding, this can be **plain synchronous REST**, called
   twice per session — no async, no socket, no reconnect logic needed for
   this specific interface.
4. **Provider dispatch** (Part 4) — `_startup()` needs to actually read
   `providers.market_data.type` and choose between `ReplayChainProvider`
   and the new live provider, rather than hardcoding one.
5. **Decide where certification enters this path.** `ReplayChainProvider`
   never checks a certification gate at all (bhavcopy files are files, not
   a live broker call) — a live provider is the first place in this
   runner's actual call chain where `market_reality.certification`-style
   fail-closed gating would matter. Whether it reuses
   `market_reality.CertificationGate` directly, or the existing
   `data_certification/*.json` artifacts read a different way, is an open
   design question for the implementation phase, not resolved here.

**Explicitly NOT needed for this phase**, per Part 0: `FyersTickFeed`,
`TickSilenceWatchdog`, the 17F.6.2 capture-lifecycle adapter, or any
websocket machinery. That work remains correctly designed and waiting —
for the separate, larger, still-unscoped intraday TICK-feed phase the
runner's own docstring already names, not for this one.

---

## Part 6 — Recommended next step

Given Part 0's finding, the natural next audit is narrower than
originally framed: **not** "how does a live provider sit on top of the
websocket lifecycle" (Part 2A/adapter-decision work already answered
that for the *other* future phase), but **"what does FYERS actually
return for a full option chain with real premiums, and does the existing
`get_option_chain()` need to change or be supplemented."** That is a
live-verification question (Part 5, item 1) — the same posture as
`scripts/discover_depth_response_shape.py` from 17F.1.2/17F.5: a
read-only, market-hours-gated discovery script, run once by the
operator, before any implementation code is written.

Proposed: **Phase 17F.7.1 — Option Chain Premium Field Discovery**,
mirroring `discover_depth_response_shape.py`'s exact discipline (raw
capture, no normalization, structural report only) but for a real
multi-strike chain quote call, to establish what fields are actually
available before `LiveChainProvider` is designed further.

Not started by this document.
