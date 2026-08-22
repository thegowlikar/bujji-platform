# Bujji Options OS — Architecture

**This file is the canonical architecture document.** `docs/ARCHITECTURE.md` is
deprecated and describes a strategy generation this system replaced; see
[Deprecations](#deprecations).

This document records four things and is expected to change whenever any of
them does: the **safety contract**, the **ownership of truth**, the **state
machine**, and the **acceptance criteria** by which a session is judged.

---

## 1. Safety contract

Bujji may eventually trade real money. It may do so only when it can prove that
it is seeing the market it acts on, knows what it holds at the broker, bounds
risk continuously, records enough evidence to reconstruct every decision, and
fails closed whenever reality is uncertain.

Six rules follow from that, and nothing in this repository may contradict them.

1. **UNKNOWN is not FLAT, and UNKNOWN is not SAFE.** A read that failed, a
   symbol that never ticked, a position query that timed out — none of these
   may resolve to "there is nothing there". Every uncertain state blocks.
2. **Local process state is never broker truth.** A dictionary in this process
   records what we believe. Only the broker can say what is held.
3. **Subscribed is not covered; acknowledged is not filled; a fetch timestamp
   is not a price age.** Evidence of a request is never evidence of a result.
4. **Refusing to trade is cheaper than trading on unverified state.** Where the
   two conflict, refuse.
5. **A claim must name its evidence.** "Verified" from code inspection or from
   a passing test is not verification; it is a hypothesis with a test attached.
6. **Silence is not success.** A session that could not act must say so through
   a channel that reaches an operator, not only through a log line.

---

## 2. Ownership of truth

One concept, one owner. A second module defining an owned type is a defect, not
a convenience — it is how two parts of the system come to disagree about what
is true.

`Status` values:

- **OWNED** — exactly one definition, in the module named. Machine-enforced by
  `tests/test_architecture_contract.py`.
- **CONTESTED** — more than one definition exists on the reachable path. Each
  carries the milestone that resolves it. Not yet enforced, because enforcing a
  rule the code breaks would only mean disabling the test.

| Concept | Type | Owner module | Status |
| --- | --- | --- | --- |
| Exchange contract row | `OptionRow` | `bujji.broker.instrument_master` | OWNED |
| Capture universe | `CaptureUniverse` | `bujji.capture_universe.builder` | OWNED |
| Capture instrument | `CaptureInstrument` | `bujji.capture_universe.builder` | OWNED |
| Eligible selection band | `SelectionBand` | `bujji.production_runtime.selection_band` | OWNED |
| Universe coverage verdict | `CoverageVerdict` | `bujji.production_runtime.universe_coverage` | OWNED |
| Leg readiness verdict | `LegReadiness` | `bujji.production_runtime.leg_readiness` | OWNED |
| Order journal | `PositionGroupJournal` | `bujji.journal.position_group_journal` | OWNED |
| Order journal record | `PositionGroupEvent` | `bujji.journal.position_group_journal` | OWNED |
| Broker position group | `PositionGroupReality` | `bujji.production_runtime.position_reality_registry` | OWNED |
| Session strategy lock | `StrategyLock` | `bujji.production_runtime.trading_session_governor.strategy_lock` | OWNED |
| Option observation | `OptionObservation` | `bujji.options_observation.models` | OWNED |
| Session safety verdict | `SessionSafetyVerdict` | `bujji.production_runtime.session_safety_verdict` | OWNED |
| Websocket tick feed | `FyersTickFeed` | `bujji.broker.fyers_ws` | OWNED |
| Order status | `OrderStatus` | `bujji.core.enums` | OWNED |
| Position lifecycle state | `PositionLifecycleState` | `bujji.production_runtime.position_lifecycle_runtime` | OWNED |
| Order request | `OrderRequest` | `bujji.core.models` | CONTESTED |
| Spot snapshot | `SpotSnapshot` | `bujji.market_perception.models` | CONTESTED |
| VIX snapshot | `VixSnapshot` | `bujji.market_perception.models` | CONTESTED |
| Market snapshot | `MarketSnapshot` | `bujji.market_perception.models` | CONTESTED |
| Leg quote | `LegQuote` | `bujji.production_runtime.leg_readiness` | CONTESTED |
| Leg state | `LegState` | `bujji.production_runtime.leg_readiness` | CONTESTED |
| Decision trace | `DecisionTrace` | `bujji.core.decision_trace` | CONTESTED |
| Session store | `SessionStore` | `bujji.shadow_observatory.session_store` | CONTESTED |

### Contested entries and the milestone that resolves each

| Type | Competing definitions | Resolution |
| --- | --- | --- |
| `LegQuote`, `LegState` | `bujji.execution_reality.models`, `bujji.trading_brain.risk_governor.position_group_fold` | **M0** — introduced by the market-data campaign; the newer names move. |
| `SpotSnapshot`, `VixSnapshot`, `MarketSnapshot` | `bujji.market_reality_snapshot.models`, `bujji.broker.simulation.market_snapshot` | **M1** — one market model family; the perception family is the one on the entry path. |
| `OrderRequest` | `bujji.trading_brain.order_construction.models` | **M3** — resolved with the broker-truth boundary, which is what consumes it. |
| `DecisionTrace`, `SessionStore` | `bujji.trading_brain.risk_governor.risk_governor_pipeline`, `bujji.core.session_state` | **M6** — resolved with the session evidence package. |

**Not conflicts.** `Explanation`, `Contradiction` and `LensOpinion` are defined
once per `msi_*` package by convention — twenty, four and two definitions
respectively. They are per-package value types, not competing authorities, and
are deliberately absent from the table above.

---

## 3. Runtime reachability, and what "test-only" means

`tools/reachability.py` computes, from the eight entry points systemd actually
starts, which modules production can reach. Current measurement:

```
python files            1811
  test modules           549
  production modules    1262

REACHABLE                449   (35.6% of production)
orphaned                 813
  test-only              557
  unreferenced           256
```

**`test-only` is a classification, not a verdict.** It means exactly one thing:
*that module cannot support a claim about production safety.* A passing test
over a test-only module proves the module works. It proves nothing about the
system that trades. Whether such a module should be wired, kept as a library
for future work, or retired is an engineering judgement made per module — this
document does not license deleting any of them, and neither does the tool.

The number matters because it explains a recurring pattern in this repository:
a green suite coexisting with a broken runtime. Most of what the suite
exercises is not what runs.

**Known limit.** The graph is static. Dynamic imports (`importlib`,
`__import__`, a module named in config) are not followed, so the reachable set
is a lower bound. Anything reported reachable is; anything reported orphaned
should be confirmed by grep before being acted on.

---

## 4. Session state machine

Bujji currently runs **two** session-scoped state machines, and this is a known
defect rather than a design:

| Machine | Module | States |
| --- | --- | --- |
| `TradingSessionState` | `…trading_session_governor.session_trading_state` | `ANALYSING_MARKET → STRATEGY_LOCKED → POSITION_ACTIVE → MANAGING → EXITED → SESSION_COMPLETE` |
| `RuntimeState` | `bujji.production_runtime.runtime_state_machine` | includes its own `POSITION_ACTIVE` |

Both transition to `POSITION_ACTIVE` for the same session, from different call
sites, with no defined relationship. **M4 collapses them into one journaled
machine.** Until then, `TradingSessionState` is the machine that gates entry
(`entry_control.can_enter_trade` reads it) and is therefore the one to trust
when they disagree.

### Entry gates, in the order they run

1. `_record_universe_coverage` — grades the wide capture universe. **Records; never blocks.**
2. Position truth — reconciliation must have established what the broker holds.
3. Data quality — the market snapshot must have been graded.
4. `select_and_lock_strategy` — one strategy per session, idempotent on retry.
5. `_band_coverage_permits_entry` — **stage 1.** Every contract in the eligible band, plus spot, must be fresh.
6. `evaluate_leg_readiness` — **stage 2.** The exact legs and hedges, freshness *and* field-completeness, graded independently.
7. `_build_order_requests` — the only place a production `OrderRequest` is built.

While a position exists — **stage 3** — those legs stay mandatory, and
monitoring ends only when the broker proves the account flat. Open legs and
unestablished flatness both continue.

---

## 5. Acceptance criteria

A milestone is complete when its acceptance test passes and failed before.

| # | Milestone | Acceptance |
| --- | --- | --- |
| 0 | Contract written down | A type declared OWNED here that gains a second reachable definition fails `tests/test_architecture_contract.py`. |
| 1 | One instrument/universe model | The chain request's strike count and expiry are derived from the universe; band ⊆ universe holds for every expiry role, including on expiry day. |
| 2 | Durable tick journal + replay | A recorded session replays to an identical decision sequence; a corrupted journal refuses rather than degrades. |
| 3 | One broker-truth boundary | With the broker read forced to fail, no consumer concludes flat; the session refuses and says why. |
| 4 | One journaled state machine | Killing the process mid-session and restarting reconstructs state from the journal. |
| 5 | Event-driven protection | An adverse move between poll intervals triggers protection from the tick path; reconciliation runs with the management loop stopped. |
| 6 | Session evidence package | Every terminal path produces a package; a session that cannot prove closure exits non-zero. |

---

## 6. Deprecations

| Document | Status | Reason |
| --- | --- | --- |
| `docs/ARCHITECTURE.md` | **DEPRECATED** | Describes the VWAP Premium Straddle Seller generation and declares itself authoritative. Superseded by this file. Retained for historical reference; a header now says so. |
| `docs/CHAOS_TESTING_PLAN.md`, `docs/FYERS_TRANSPORT_READINESS.md`, `docs/PAPER_CAMPAIGN_RUNBOOK.md`, `docs/PAPER_TRADING_LIVE_DATA.md`, `docs/TIER1_CAPITAL_PROTECTION.md` | Historical | Describe the earlier ORB-VWAP breakout strategy, per `docs/ARCHITECTURE.md`'s own note. Strategy-specific sections are background, not current behaviour. |

---

## 7. What is not yet true

Recorded here so no reader has to infer it from silence.

- **No tick is persisted anywhere.** No session is replayable. (M2)
- **The broker boundary is not a boundary.** `self._broker` is hardcoded to
  `PaperBroker`; every `FyersBroker` is execution-neutered. The three-valued
  UNKNOWN machinery is correct and untestable, because the read it guards
  cannot fail. (M3)
- **`FYERS_POSITION_SCHEMA_VERIFIED = False`** and remains false. No claim in
  this repository may assume the FYERS position schema is verified.
- **Risk state is ephemeral.** Recomputed per cycle, never journaled; a restart
  loses every risk decision and its inputs. (M4)
- **No gate in this document has met a live feed.** All are structurally tested.
