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
| Tick journal (writer) | `TickJournal` | `bujji.tick_journal.journal` | OWNED |
| Tick journal manifest | `JournalManifest` | `bujji.tick_journal.manifest` | OWNED |
| Replayed tick | `TickRecord` | `bujji.tick_journal.replay` | OWNED |
| Replay result | `ReplayResult` | `bujji.tick_journal.replay` | OWNED |
| Websocket tick feed | `FyersTickFeed` | `bujji.broker.fyers_ws` | OWNED |
| Order status | `OrderStatus` | `bujji.core.enums` | OWNED |
| Position lifecycle state | `PositionLifecycleState` | `bujji.production_runtime.position_lifecycle_runtime` | OWNED |
| Order request | `OrderRequest` | `bujji.core.models` | CONTESTED |
| Spot snapshot | `SpotSnapshot` | `bujji.market_perception.models` | CONTESTED |
| VIX snapshot | `VixSnapshot` | `bujji.market_perception.models` | CONTESTED |
| Market snapshot | `MarketSnapshot` | `bujji.market_perception.models` | CONTESTED |
| Leg quote (readiness) | `ReadinessQuote` | `bujji.production_runtime.leg_readiness` | OWNED |
| Leg state (readiness) | `ReadinessLegState` | `bujji.production_runtime.leg_readiness` | OWNED |
| Decision trace | `DecisionTrace` | `bujji.core.decision_trace` | CONTESTED |
| Session store | `SessionStore` | `bujji.shadow_observatory.session_store` | OWNED |

### Contested entries and the milestone that resolves each

| Type | Competing definitions | Resolution |
| --- | --- | --- |
| `SpotSnapshot`, `VixSnapshot`, `MarketSnapshot` | `bujji.market_reality_snapshot.models`, `bujji.broker.simulation.market_snapshot` | **M1** — one market model family; the perception family is the one on the entry path. |
| `OrderRequest` | `bujji.trading_brain.order_construction.models` | **M3** — resolved with the broker-truth boundary, which is what consumes it. |
| `DecisionTrace` | `bujji.trading_brain.risk_governor.risk_governor_pipeline` | **M6** — resolved with the session evidence package. |

**Resolved.** `LegQuote` and `LegState` were introduced by the market-data
campaign and collided with `execution_reality.models` and
`risk_governor.position_group_fold` — both older, both reachable, both meaning
something different. The newcomers moved to `ReadinessQuote` and
`ReadinessLegState`; the originals were not touched. A recently introduced
duplicate is not left behind an indefinite CONTESTED label.

### Two evidence layers, and why that is not two authorities

`market_reality.store` (Layer 0) and `bujji.tick_journal` both hold market
evidence, and a reader of the table above would reasonably wonder which should
absorb the other. Neither. They answer different questions with different
identity models, and each is authoritative only over its own:

| | Layer 0 | Tick journal |
| --- | --- | --- |
| Owns | the SET of distinct facts observed | the SEQUENCE of arrivals |
| Identity | content hash over identity + value | local ingest sequence |
| A repeat is | the same fact, deduplicated | a second arrival, kept |
| Order | not meaningful | the point |

Layer 0's own docstring states the rule that makes it unsuitable for ticks: an
identical id "means an identical fact", and re-capturing one is a no-op where
"nothing is written, and nothing is lost". That is correct for observations.
For ticks it is fatal -- lite-mode payloads carry only `symbol`, `ltp` and
`type` with no exchange timestamp, so a quiet symbol emits byte-identical
ticks, and collapsing them destroys the rate, gaps and order the journal exists
to record.

**Do not merge them.** Layer 0 reserves `KIND_MARKET_TICK` and has never
produced one; that reservation is a historical artifact, not a claim on this
truth.

**Not conflicts.** `Explanation`, `Contradiction` and `LensOpinion` are defined
once per `msi_*` package by convention — twenty, four and two definitions
respectively. They are per-package value types, not competing authorities, and
are deliberately absent from the table above.

---

## 3. Runtime reachability, and what "test-only" means

`tools/reachability.py` computes, from the eight entry points systemd actually
starts, which modules production can reach. Current measurement:

```
python files            1897
  test modules           579
  production modules    1318

REACHABLE                487   (36.9% of production)
orphaned                 831
  test-only              603
  unreferenced           228
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

## 3a. Forbidden universe patterns, and the ratchet on them

Four patterns let a component decide for itself which contracts exist or what
they are called, instead of deriving that from the canonical universe model.
Two components that each answer "which contracts?" will eventually answer
differently, and the difference surfaces as a strike nobody can price, a symbol
the venue rejects, or a reconciliation that silently matches nothing.

| Pattern | What it is |
| --- | --- |
| `expiry-by-list-order` | Taking a broker response's expiry list by POSITION. Assumes the broker sorts, and that the returned rows belong to whichever entry sits first. |
| `fixed-strike-count` | A hardcoded number of strikes. The master steps by 50 near expiry and 1500 for LEAPS, so "N each side" is a different width per expiry — and a different width from what was subscribed. |
| `broker-symbol-built` | Constructing a venue symbol from parts rather than selecting a real one. A symbol that does not exist cannot be priced, ordered or reconciled, and the failure looks like an absent position. |
| `independent-atm` | Computing an at-the-money strike locally. The ATM the universe was centred on is the only one whose band is actually subscribed. |

`tools/forbidden_patterns.py` reports every occurrence, split by reachability.
Docstrings are stripped before matching — an earlier version reported five false
positives, all of them prose explaining these very defects.

### Quarantine — dormant, and must stay dormant

These modules carry a forbidden pattern but are **not reachable** from any
systemd entry point, so they cannot define what production trades today. They
are NOT deleted and NOT rewritten. They are fenced.

**The ratchet:** `tests/test_architecture_contract.py` fails if any of these
becomes reachable. Wiring one into a production path breaks the build until it
is either migrated (the pattern removed) or explicitly and visibly taken out of
quarantine here. A dormant forbidden pattern cannot re-enter the runtime
silently.

| Module | Patterns | Status |
| --- | --- | --- |
| `bujji.market_timeseries.subscription` | fixed-strike-count, broker-symbol-built | QUARANTINED |
| `bujji.core.orchestrator` | fixed-strike-count | QUARANTINED — legacy stack |
| `run_live_shadow` | fixed-strike-count | QUARANTINED — no unit runs it |
| `bujji.trading_brain.nifty_contract_builder.engine` | independent-atm | QUARANTINED |
| `scripts.certify_fyers_optionchain_reality_access` | fixed-strike-count | QUARANTINED |
| `scripts.gate1.build_universe` | independent-atm | QUARANTINED |
| `scripts.verify_fo_access` | fixed-strike-count | QUARANTINED |

### The legacy stack — retired, and held retired

`bujji.app` is the **deprecated ORB-VWAP ATM Seller**, superseded by Bujji
Options OS. Its unit is `bujji-orb-vwap-legacy.service`: **disabled, no timer,
zero journal entries.** `run_live_shadow` is referenced by no unit at all.
`scripts.run_paper_intelligence_campaign`'s timer is disabled.

Everything reachable only from those three is therefore **not production
code**, and no safety claim may rest on it:

| Module | What it duplicates | Status |
| --- | --- | --- |
| `bujji.core.orchestrator` | its own session FSM, `reconcile()`, recovery path | RETIRED — unreachable |
| `bujji.core.session_state` | `SessionStore`, `trades_taken` | RETIRED — unreachable |
| `bujji.replay.broker` | a Broker implementation | RETIRED — unreachable |
| `bujji.shadow_lifecycle.orchestrator` | contract construction | RETIRED — unreachable |

**This was not visible until 2026-08-22**, because `tools/reachability.py`
declared `bujji.app` as the shadow-decision-campaign's entry point. That
service runs `scripts/run_phase20_13_live_entrypoint.py`. So the whole legacy
stack was counted as production, `bujji.core.orchestrator` sat in the table
below as a violation that "defines what production trades", and the modules the
shadow campaign really reaches were counted as unreachable. Correcting the list
moved reachability from 43.1% to **37.6%** — the earlier figure overstated
production reach by roughly 70 modules.

**THE RATCHET, and it runs in two places.**
`tests/test_reachability_entry_points.py` pins `ENTRY_POINTS` to the enabled
unit files in both directions: a unit's module missing from the list fails, and
a not-enabled module present in it fails. It also asserts each retired module
above stays unreachable. So enabling a unit, or adding a production import that
reaches the legacy stack, breaks the build until the migration is made
deliberately and written down here.

Retired is not deleted. These modules keep working for whoever runs them by
hand; what they may not do is come back into the runtime silently, or be cited
as evidence about the system that trades.

### Known reachable violations — declared, and shrinking

These DO define what production trades. Each is declared with the milestone
that clears it.

**The ratchet, both ways:** the test asserts the set of reachable violations
equals EXACTLY this table. A new violation fails, because it is undeclared. A
fixed violation left declared here also fails, so the table cannot rot in the
safe-looking direction and an entry cannot sit resolved-but-listed forever.

| Module | Pattern | Clears in |
| --- | --- | --- |
| `bujji.shadow_runtime.intelligence_pipeline_adapter` | independent-atm | M1 |
| `bujji.trading_brain.risk_governor.msi_entry_bridge` | broker-symbol-built | M1 |

**`bujji.broker.paper` cleared in M3 (2026-08-22).** It built
`f"{underlying}{strike}{opt.value}"` — `"NIFTY24500CE"` — with no expiry and
no exchange prefix, and stamped the contract's expiry as the literal
`"WEEKLY"`. Two defects in one shape. A strike alone is not a contract: every
ledger the simulator owns keys on the symbol string and the position row has
no expiry field, so a short near leg and a long far leg at the same strike
merged into one row and **netted to no position at all** — a phantom flat
manufactured by the simulator itself, demonstrated live before the fix. And
the string was venue-*shaped* without being a venue symbol (a real one is
`NSE:NIFTY26AUG24500CE`), so any comparison against a real symbol matched
nothing, silently.

Both simulators — `bujji.broker.paper` and `bujji.replay.broker` — now return
the ABSENT sentinel this codebase already owned:
`UNRESOLVED|NIFTY|2026-08-27|24500|CE`, via
`options_observation.taxonomy.unresolved_symbol()`. Pipe-delimited, because no
exchange vocabulary in use here contains a `|`; it carries the expiry, so two
expiries cannot collide; and `option_symbol_resolver` already refuses ABSENT
provenance permanently, so a leak into a real order path is refused loudly
rather than failing silently. A simulator cannot know which contracts are
listed — that answer is in the instrument master, a network download it must
not make — so when no expiry is supplied it says `UNRESOLVED-EXPIRY` instead
of inventing one.

**Scope, stated honestly:** the collision was never reachable. The option
chain is filtered to `min(expiry)` before any strategy sees it
(`market_perception/option_chain_adapter.py`), so no proposal can span two
expiries, and no systemd-run service calls a paper broker's
`resolve_atm_contract` at all — production contracts come from
`SymbolIndex.resolve_leg()`, which returns the chain row's symbol verbatim.
This fix disarms a trap; it did not repair a live failure.

**A detector blind spot found while fixing it.** The `broker-symbol-built`
pattern matched the literal placeholder `{underlying}`, so
`bujji.shadow_lifecycle.orchestrator`'s
`f"{underlying_symbol}{int(leg.strike)}{leg.option_type}"` — the same defect,
one identifier longer — was invisible to it, while that function's docstring
claimed it invented nothing and `leg.expiry` sat unused beside it. The
detector now matches the shape rather than the variable name, and that module
is fixed too. A detector defeated by a rename is defeated by the single most
likely accidental edit.

---

## 4. Session state machine

**RESOLVED IN M4 (2026-08-23). `TradingSessionState` is the single owner of
session and position lifecycle.**

Bujji ran **two** session-scoped machines. Both transitioned to
`POSITION_ACTIVE`, from different call sites, with no defined relationship, and
both gated entry — `RuntimeState` through `_ENTRY_ACCEPTING_STATES`,
`TradingSessionState` through `entry_control.can_enter_trade`. Neither was
journaled, so neither survived a restart.

| Machine | Status after M4 |
| --- | --- |
| `TradingSessionState` (`…trading_session_governor.session_trading_state`) | **OWNER.** Transitions journaled; state derived from the journal reconciled against broker truth. |
| `RuntimeState` (`bujji.production_runtime.runtime_state_machine`) | **RETIRED as lifecycle authority.** Keeps only connectivity and market phase. |

**Why this owner.** Its states map onto facts the durable journal already
holds — `MINTED`/`CONSTRUCTED` means a strategy was locked, `FILL_OBSERVED`
means a position is active, net-zero means it exited. `RuntimeState` mixes
connectivity (`CONNECTING`) and market phase (`PREMARKET`) with position
lifecycle, and those are **process** facts that must not survive a restart:
after a crash you genuinely are connecting again, and journaling that would
mean reconstructing something that has to be re-derived fresh.

**Transitions are journaled; state is derived.** Every transition is appended
to the same `position_group_events` stream as position lifecycle, as a
`SESSION_TRANSITION` carrying session identity, prior state, next state, cause,
timestamp and an evidence reference — under a `SESSION:<id>` identity that
`position_group_scope` excludes from every position-group boundary by explicit
contract, enforced on both the read and the write side. The runner's in-memory
tracker is a **cache**; it may not decide whether Bujji is flat, open, safe to
enter, or finished.

**Disagreement is `UNKNOWN`, and `UNKNOWN` blocks entry and makes the session
unsafe.** The journal says what this process recorded; broker truth says what
the account holds. When they disagree, neither is assumed correct. A missing
history, a broken transition chain, an unreadable position history, and a
broker `UNKNOWN` all reach the same place, for the same reason: a session that
cannot establish what it is may not take new risk.

`UNKNOWN` is deliberately **not** a member of the state enum, so no transition
table can accept it as a target and no caller can transition into it by
mistake.

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
| 4 | One journaled state machine | **MET.** Killing the process mid-session and restarting reconstructs state from the journal, reconciled against broker truth; disagreement, missing history or corruption is `UNKNOWN` and blocks entry. |
| 5 | Event-driven protection | An adverse move between poll intervals triggers protection from the tick path; reconciliation runs with the management loop stopped. |
| 6 | Session evidence package | Every terminal path produces a package; a session that cannot prove closure exits non-zero. |

---

## 5a. The enabled-runtime lifecycle, end to end

One path, named at every hop. Everything here is reached from
`bujji_options_os_runner`, which is what systemd starts. Anything not on this
path is not the running system, whatever its docstring says.

```
FYERS SDK callback
  -> FyersTickFeed.on_message                    bujji.broker.fyers_ws
       -> TickJournal.offer()                    verbatim, FIRST, before any field is read
       -> quote.project()                        typed Quote; the ONE SDK field map
  -> feed._quotes / feed._ltp                    both written under one lock, same callback
  -> WebsocketTickProvider.get_quotes()          monotonic freshness, per-symbol REST fallback
  -> OptionsOSRunner._current_leg_quotes()       gate: assess_quote_fields(["ltp"])
       -> LegPriceView                           prices EMPTY unless valid
  -> _run_one_management_pass()                  revalue only when valid
       -> revalue_all() -> revalue()             current_price from gate-passed quotes
       -> _emergency_brake()                     reached on BOTH the valid and blind paths
       -> session_governor.evaluate_and_enforce_exit()
            -> exit_lifecycle.plan()             exit intent journaled BEFORE placement
            -> TradeLifecycleExecutor._execute_reduce()
            -> settle()                          EXITED only on broker-proven flat
  -> run_eod_closure()                           EOD + abort, same exit lifecycle
  -> session_safety_verdict.evaluate_session_safety()
  -> process exit code                           0 / 3 UNSAFE / 4 PENDING_EVIDENCE
```

Selection runs on a second, REST-fed path that has not been migrated:

```
MarketDataAdapter.build_snapshot()   REST; no quote_source is passed today
  -> MarketSnapshot                  health_status, missing_fields
  -> market_data_gate.assess_market_data()
  -> _data_quality_permits_entry()
```

That both paths exist is a known one-authority gap, recorded in section 7.

---

## 5b. Authority per domain

The single question this table answers is "if two parts of Bujji disagree
about X, who is right?".

| Domain | Authority | Enabled caller |
| --- | --- | --- |
| Exchange contracts | `bujji.broker.instrument_master` | composition root, universe builders |
| Capture universe | `bujji.capture_universe.builder` | Gate 1 builder, runtime |
| Raw tick evidence | `bujji.tick_journal.journal` | `FyersTickFeed`, via the runner |
| Tick replay / integrity | `bujji.tick_journal.replay` + `manifest` | `production_runtime.tick_evidence` |
| Typed quote | `bujji.market_perception.quote` | `FyersTickFeed`, `intraday_price_provider` |
| Analytical snapshot | `bujji.market_perception.models` | `MarketDataAdapter` |
| Market-data quality | `production_runtime.market_data_gate` | runner `_assess_data_quality`, `_current_leg_quotes` |
| Price for a decision | `IntradayPriceProvider.get_quotes` | `_current_leg_quotes` |
| Order lifecycle | `bujji.journal.position_group_journal` | execution bridge, exit lifecycle |
| Broker position truth | `bujji.broker_truth` | position registry, EOD closure |
| Session lifecycle | `production_runtime.session_lifecycle` | runner `_startup`, governor `_transition` |
| Exit lifecycle | `production_runtime.exit_lifecycle` | EOD closure, lifecycle executor |
| Orphan exposure | `production_runtime.orphan_exposure` | runner startup gate |
| Session verdict | `production_runtime.session_safety_verdict` | `run()` exit code |

---

## 5c. Deployment model

Three environments, and the separation is the point. Nothing promotes itself.

| | Path | Runs | May trade |
| --- | --- | --- | --- |
| **Branch** | `/opt/bujji/work-m4` | tests only | never |
| **Live checkout** | `/opt/bujji/app` | the enabled systemd units | paper only |
| **Measurement** | `/opt/bujji/gate1-run` | one-shot Gate 1 unit | never; structurally incapable |

Rules that hold today:

- The branch is never executed by systemd. Promotion to the live checkout is a
  deliberate, separate act that this repository does not perform.
- The measurement harness imports the application read-only and is asserted
  incapable of starting an order-capable session
  (`gate1-run/prove_no_trading.py`, positive-controlled).
- Entry-capable units are disabled AND condition-gated; the gate is an unmet
  `ConditionPathExists`, so a manual start does not execute either.
- `FYERS_POSITION_SCHEMA_VERIFIED` is `False` and no claim may assume otherwise.

Real-money activation is not a mode that exists. It would require a separate,
explicitly authorised configuration, and nothing in this repository creates one.

---

## 5d. Evidence gates

A gate is a place the system refuses rather than guesses. Each names what it
refuses on, and every one fails closed.

| Gate | Refuses when | Consequence |
| --- | --- | --- |
| Token pre-flight | token cannot cover the session | units do not start |
| Historical exposure | a prior day's group is unreconciled | entry blocked |
| Orphan exposure | broker holds what no group claims | entry blocked, unsafe |
| Prior fills | the day's strategy already deployed | entry blocked |
| Market-data quality | snapshot health / provenance unknown | entry refused |
| Quote field gate | required field missing, stale, or wrong provenance | price-dependent action refused |
| Strategy lock | one strategy per day, across restarts | second entry refused |
| Position schema | schema unverified | no real-capital claim |
| Emergency brake | loss limit, or sustained blindness with a position | forced closure |
| Closure settle | broker not CONFIRMED_FLAT | not EXITED |
| Session verdict | open risk unproven flat, blind open risk, evidence gaps | exit 3 UNSAFE |

An UNKNOWN answer is never converted to a safe one at any gate.

---

## 5e. Strategy selection: one authority, three implementations

Three strategy selectors exist in this repository. Only one decides anything.

| Module | Reachable from the trading entrypoint? | Role |
|---|---|---|
| `production_runtime/trading_session_governor/strategy_selector.py` | **yes** | **the only trade-decision authority** |
| `trading_brain/strategy_selector/registry.py` (11 `StrategyDefinition`s) | no — not on the import closure at all | built, tested, never called |
| `msi_strategy_selector/engine.py` | yes, but only from `intelligence_cycle_recorder` and `live_shadow_validation` | records what a selector *would* say; places nothing |

Established by `tools/reachability.py` from `bujji_options_os_runner.py`, the
module `bujji-options-os-trading.service` actually starts. The positive
control is that the known caller (`session_governor.select_and_lock_strategy`)
resolves; the negative is that `trading_brain.strategy_selector.registry` does
not appear in a 487-module closure.

**This is why there is no fourth registry.** The instruction to replace
scattered conditionals with one auditable registry describes a system whose
selection logic is spread across the runtime. Bujji's is not: it is one pure
function of two inputs. The real defect was narrower and different — that
function declared nothing about the shapes it chose between, recorded no
rejected candidates, and returned a bare string. Adding a registry would have
made three unreachable ones and left the authority untouched.

### The declarative contract

`STRATEGY_RULES` declares, per shape Bujji may sell: eligible trend and
volatility regimes; vetoing conditions; whether the shape is structurally
defined-risk and hedged; required generic data capabilities; and the named
owner of its margin check, lot-size check, exit policy and EOD behaviour.

`_evaluate_candidates()` scores **every** declared rule on every evaluation
and returns a `StrategyCandidate` for each — status, reason code, detail — so
a shape that was not chosen is accounted for rather than absent. These ride on
`StrategySelectionResult.candidates` and are published to the session journal
by `select_and_lock_strategy`.

**The rules do not decide anything yet, deliberately.** `select_strategy()`'s
branch bodies still produce the outcome; the rules produce an independent
record for the same inputs, and
`tests/test_strategy_rules_match_decision.py` asserts the two agree across the
full cross product of the regime vocabulary in both risk modes. Making the
branches *driven* by the table is a behaviour-preserving refactor that can
only be proven safe once that equivalence test exists — so it is the next
commit, not this one.

### Capability requirements are unconfigured, not satisfied

Rules declare generic capabilities (`QUOTE_LAST_PRICE`,
`QUOTE_TWO_SIDED_MARKET`, `QUOTE_OPEN_INTEREST`) — never FYERS field names,
never thresholds. No live payload has been measured. `available_capabilities`
defaults to `None`, meaning **not evaluated**; it does not mean satisfied, and
nothing in the runtime records that the feed is capable of anything. Once Gate
1 measures which capabilities the feed genuinely supplies, supplying a policy
set makes an unsatisfied requirement a refusal. A test asserts no rule names a
broker-specific field.

### The typed plan already exists downstream

`msi_trade_construction.models.TradeConstructionAssessment` is already a fully
typed plan: legs, expiry decision with reasoning, entry reference prices,
expected credit/debit, risk profile, required margin (or the explicit reason
margin is unavailable), supporting assessment IDs, an `Explanation`, a
provenance string and a schema version. A new `TradePlan` dataclass carrying
legs would be a second plan model.

What is genuinely missing is not a type but a **binding**: nothing today joins
the selection evidence (regime inputs, candidates, universe version,
analytical snapshot references) to the construction assessment that resulted
from it. That binding is the decision-evidence work, not a new model — see §7.

## 5f. Paper-session acceptance, and the two gates around it

A paper session is bracketed by two read-only tools. Neither can deploy,
enable, start, place or modify anything; each prints a verdict an operator
then acts on.

**Before — `tools/paper_session_readiness.py`.** Refuses unless IDENTITY (the
code that would run is the code that was verified), INHIBITION (nothing can
start an order-capable session on its own), CONFIGURATION (`shadow_mode` is
literally `true`) and EVIDENCE (the last package replays) all pass. Every
check is PASS, FAIL or UNKNOWN, and **UNKNOWN is never PASS** — an assessment
that ran no checks at all reports PENDING_EVIDENCE rather than READY, because
a fail-open default in a readiness gate turns an unexamined system into an
endorsed one.

**After — `tools/decision_replay_verifier.py`.** Reports the strongest replay
level the evidence *demonstrates*, by recomputation. Levels are strictly
ordered, so proving eligibility replay while the analytical binding is absent
earns level 1, not 3.

Full criteria and the operator procedure: [docs/PAPER_SESSION_RUNBOOK.md](docs/PAPER_SESSION_RUNBOOK.md).

**Acceptance is not profit.** A session is judged on whether it behaved
correctly and can explain itself. A profitable session that cannot replay has
failed; a zero-trade session that records why it declined every shape has
passed. For a premium seller, zero trades is the common outcome.

### The level-4 ceiling is structural

Level 4 is unreachable today, and not because of a missing feature: the option
chain that strikes were selected from is not part of the evidence package.
Strikes cannot be re-derived from a book nobody wrote down. Whether a
per-entry chain snapshot is worth its size is an operator decision about
evidence volume, recorded here rather than patched quietly.

## 6. Deprecations

| Document | Status | Reason |
| --- | --- | --- |
| `docs/ARCHITECTURE.md` | **DEPRECATED** | Describes the VWAP Premium Straddle Seller generation and declares itself authoritative. Superseded by this file. Retained for historical reference; a header now says so. |
| `docs/CHAOS_TESTING_PLAN.md`, `docs/FYERS_TRANSPORT_READINESS.md`, `docs/PAPER_CAMPAIGN_RUNBOOK.md`, `docs/PAPER_TRADING_LIVE_DATA.md`, `docs/TIER1_CAPITAL_PROTECTION.md` | Historical | Describe the earlier ORB-VWAP breakout strategy, per `docs/ARCHITECTURE.md`'s own note. Strategy-specific sections are background, not current behaviour. |

---

## 7. What is not yet true

Recorded here so no reader has to infer it from silence.

- ~~No tick is persisted anywhere.~~ **Resolved (M2).** Every callback payload
  is journaled verbatim before any field is read, with a manifest and
  deterministic replay. Still unproven against a live feed: no tick has ever
  arrived in this configuration, so the journal has recorded nothing real.
- ~~The runtime keeps only `symbol` + latest LTP.~~ **Resolved on the branch.**
  A typed `Quote` carries every field the projection models, with per-field
  provenance and monotonic freshness. The float store is retained until its
  readers migrate; both are written from one callback under one lock.
- ~~A blind cycle revalues against entry prices.~~ **Resolved.** An entry price
  is execution evidence and can no longer become a market price. A blind cycle
  produces no prices, suspends price-dependent management, records a typed
  reason, and escalates through the existing emergency closure path.
- **The broker boundary is not a boundary.** `self._broker` is hardcoded to
  `PaperBroker`; every `FyersBroker` is execution-neutered. The three-valued
  UNKNOWN machinery is correct and largely untestable, because the read it
  guards cannot fail.
- **`FYERS_POSITION_SCHEMA_VERIFIED = False`** and remains false.
- **Two market-data paths still exist, but they are now comparable.**
  Position pricing runs on the tick path; regime derivation and strike
  selection run on a REST-fed `MarketDataAdapter`. `quote_source` is now
  supplied (2026-08-24), giving `live_quotes()` its first caller anywhere in
  the repository, and `tick_rest_coverage()` records per snapshot how many
  chain symbols the tick path held and where the prices differed.
  **`build_snapshot` is unchanged and still REST-only** -- a test asserts it
  does not consult the quote source.

  **The startup ordering is fixed (2026-08-24).** The first regime derivation
  used to run before the universe was subscribed, so the first snapshot of
  every session was tick-blind by construction. Startup now orders:
  intelligence broker -> tick source -> universe subscribed -> derivation.
  Only the derivation call moved; the tick block still follows the regime
  block, because its dependency was always on `self._intelligence_broker`
  being built there, never on the regime having been derived.
  `tools/reachability.py` aside, this is asserted structurally --
  `tests/test_first_cycle_not_tick_blind.py` fails if the derivation drifts
  back, if the feed is built after it, or if the broker drifts after the feed.

  A startup-time subscription failure is rolled back to UNATTEMPTED rather
  than latching, because `_ensure_universe_subscribed` returns early forever
  once `_universe_error` is set -- correct at entry time, and a whole-session
  entry refusal if a newly-added earlier attempt were allowed to latch.

  **What still blocks the merge.** One reason now, not two: no tick has ever
  arrived in this configuration. Making strike selection depend on the feed
  would rest the highest-consequence input on something unmeasured. Monday
  measures it; `tick_rest_coverage()` on the first snapshot is now capable of
  reporting real coverage rather than a structural zero, which is what makes
  that measurement worth reading.
- ~~Strategy selection is conditionals, not a registry.~~ **Partly resolved,
  and the original framing was wrong.** Selection was never scattered: it is
  one pure function of two inputs. What it lacked was declaration and a record
  of what it declined. `STRATEGY_RULES` now declares per shape its eligible
  regimes, vetoes, risk structure, required generic capabilities and the named
  owner of each downstream obligation, and every evaluation records a verdict
  for every declared shape. **Still true:** the branch bodies, not the rules,
  produce the outcome -- an equivalence test holds them together, but the
  refactor to make the table authoritative has not been done.
- **There is no typed trade plan binding selection to construction.**
  `TradeConstructionAssessment` is already a full typed plan (legs, expiry
  decision, risk profile, margin, explanation, provenance), so the gap is a
  binding rather than a model: nothing joins the selection evidence to the
  construction assessment that resulted from it. Adding a second plan type
  carrying legs would be a duplicate, not a fix.
- **Two strategy registries exist and neither decides anything.**
  `trading_brain/strategy_selector/registry.py` (11 declarations) is absent
  from the 487-module import closure entirely; `msi_strategy_selector` is
  reached only by observation paths that record what a selector *would* say.
  Both are live-looking code that no trade passes through.
- **65 of 72 journals are orphaned.** Only `journal.position_group_journal`,
  the three `tick_journal` modules and `execution_journal_bridge` are
  reachable from the trading entrypoint. Decision evidence reaches disk by a
  different route -- the event bus into `ShadowObservatoryRecorder` -- and the
  22 purpose-built journals under `bujji/journal/` are almost entirely unused
  by the runtime.
- ~~`_current_leg_prices` is a dead second price accessor.~~ **Resolved
  (2026-08-24).** Retired, and the P0 lock moved to the live path. Guarding
  one named dead function was the weaker form of the guarantee: it said
  nothing about a second accessor appearing later under a different name. The
  lock is now stated three ways -- `_current_leg_quotes` cannot return entry
  prices; NO method in the runner may return them as a price mapping; and
  `LegPriceView` structurally discards prices on an invalid view, which had no
  direct test at all despite being what the other two rest on. There is now
  exactly one price accessor on the management path.
- **Risk state is ephemeral.** Recomputed per cycle, never journaled; a restart
  loses every risk decision and its inputs.
- **No FYERS payload field is verified.** `PROVISIONAL_SDK_FIELD_MAP` is
  unmeasured. A wrong key yields UNAVAILABLE, never a wrong number -- but no
  bid/ask/OI/volume/depth logic may be written until Gate 1 measures the shape.
- **No subscription-capacity figure is established.** Zero-gap coverage needs
  ~1,465 concurrent subscriptions; whether the venue serves that is unknown.
- **Decision replay is level 1, measured.** `tools/decision_replay_verifier.py`
  run against the three real sealed packages: every eligibility check passes
  (recorded regimes re-derive recorded selections exactly), and the awarded
  level is still `INPUT_INTEGRITY`, because those packages carry no link from
  a selection to the assessment it acted on. Sessions from `43fa860` carry
  that link; no session has yet been run that does.
- **Level 4 replay is structurally unreachable.** The option chain that
  strikes were selected from is not part of the evidence package, so strikes
  cannot be re-derived. Whether a per-entry chain snapshot is worth its size
  is an operator decision, not a defect to patch quietly.
- **No gate in this document has met a live feed.** All are structurally
  tested. Green tests are not runtime proof, and this file makes no claim that
  they are.
