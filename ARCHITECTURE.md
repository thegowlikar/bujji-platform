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
python files            1833
  test modules           556
  production modules    1277

REACHABLE                480   (37.6% of production)
orphaned                 797
  test-only              603
  unreferenced           194
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

- ~~No tick is persisted anywhere.~~ **Resolved (M2).** Every callback payload
  is journaled verbatim before any field is read, with a manifest and
  deterministic replay. Still unproven against a live feed: no tick has ever
  arrived in this configuration, so the journal has recorded nothing real.
- **The broker boundary is not a boundary.** `self._broker` is hardcoded to
  `PaperBroker`; every `FyersBroker` is execution-neutered. The three-valued
  UNKNOWN machinery is correct and untestable, because the read it guards
  cannot fail. (M3)
- **`FYERS_POSITION_SCHEMA_VERIFIED = False`** and remains false. No claim in
  this repository may assume the FYERS position schema is verified.
- **Risk state is ephemeral.** Recomputed per cycle, never journaled; a restart
  loses every risk decision and its inputs. (M4)
- **No gate in this document has met a live feed.** All are structurally tested.
