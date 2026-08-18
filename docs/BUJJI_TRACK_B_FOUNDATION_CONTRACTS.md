# Bujji — Track B: Foundation Contracts (design only)

**Design and audit only. No implementation. Nothing here depends on live-feed evidence.**

Frozen and untouched: Market Data Fabric · TickStore · storage selection · universe · watermark · ingestion topology.

---

## 1. Data lineage contract

**Problem (measured):** 7 of 10 lineage requirements fail today. `calc_version` appears in **0 files**. `config_version` exists in 7 files but is not tied to any decision.

### The contract

```
Lineage = (
    source_event_ids : tuple[str, ...]   # authoritative events consumed
    instrument_id    : str
    event_time_range : (start, end)      # EXCHANGE time, not arrival
    as_of            : timestamp         # the instant this was computed "as if"
    timeframe        : str | None
    calc_version     : str               # THIS calculation's identity
    feature_version  : str | None
    state_version    : str | None
    config_version   : str               # config that governed it
    code_version     : str               # git sha / build id
)
```

### Binding rules

| Rule | Rationale |
|---|---|
| Every **derived** object carries `Lineage`. No exceptions. | A derived value without lineage is unreproducible and therefore untrustworthy. |
| Every **authoritative** object carries its own `event_id` | It is the *terminus* of a lineage chain. |
| `calc_version` changes whenever the formula changes | Otherwise two incompatible definitions silently coexist in one series. |
| Versions are **never** back-filled onto old rows | Retrofitting a version is a lie about provenance. |
| `as_of` is a **required keyword** on every store read | Look-ahead becomes a `TypeError`, not a code-review miss. |

### Versioning discipline

`calc_version` is a **content hash of the calculation definition**, not a hand-maintained integer — hand-maintained versions drift the moment someone edits a formula and forgets to bump. Suggested: `sha256(source_of_pure_function + parameters)[:12]`.

**Consequence to accept now:** changing a formula creates a *new* series, it does not amend the old one. Longitudinal research must group by `calc_version`, or explicitly opt into mixing.

### Where it attaches (no code yet)

| Object | Status |
|---|---|
| `Candle` (15Q) | Has `window_start/end`, `tick_count`. **Needs** `source_tick_range`, `calc_version`, `derived_from` |
| `Feature` | Does not exist |
| `PositionLifecycle` (15G) | Has `position_id`, `source_cycle_id` — closest thing to compliant today |
| `OutcomeMemoryRecord` (15N) | Has `memory_id` + verbatim snapshots — **already lineage-complete in spirit** |

**15N is the model to copy.** It stores verbatim `lifecycle_snapshot` and `attribution_snapshot` rather than re-summarising — exactly the "cannot drift from source" property lineage needs.

---

## 2. Uncertainty framework

**Problem (measured):** 20 packages define confidence constants across 6+ scales. **No composition semantics exist anywhere** — uncertainty resets at every layer boundary.

### Canonical model

```
EpistemicState ∈ {
    KNOWN                 evidence resolved
    UNKNOWN               evidence should exist, does not
    NOT_AVAILABLE         predates the feature/schema that would capture it
    NOT_APPLICABLE        legitimately no such evidence for this object
    INSUFFICIENT_HISTORY  needs N observations, has < N  (self-heals)
    STALE                 resolved, but older than its freshness bound
    GAP                   source window has a hole
    DEGRADED              resolved from partially-trustworthy inputs
}

Confidence ∈ {HIGH, MODERATE, LOW, NONE}      meaningful only when KNOWN

Uncertainty = (state, confidence, limiting_factor, criticality_map,
               freshness_s, completeness_pct, provenance)
```

### Composition rules

| Rule | Semantics |
|---|---|
| **MIN** | `confidence(out) ≤ min(confidence(critical inputs))` |
| **UNKNOWN absorption** | any *critical* input UNKNOWN → output UNKNOWN, no value emitted |
| **Non-critical UNKNOWN** | output stays KNOWN, confidence drops one band, `completeness` reduced, `limiting_factor` names it |
| **GAP taint** | any GAP in the source window → state carries GAP, confidence capped LOW |
| **STALE taint** | propagates; consumers decide, but **risk and execution must reject STALE** |
| **NOT_APPLICABLE isolation** | does *not* degrade siblings |
| **INSUFFICIENT_HISTORY ≠ low confidence** | produces **no value at all** |
| **`limiting_factor` mandatory** | whenever output < max, name the capping input |

### The five required answers

| Situation | Resolution |
|---|---|
| Feature KNOWN but **stale** | Value retained, confidence → LOW, `limiting_factor="freshness:<age>s"`. Analysis may use it; **risk/execution must not**. |
| **Critical** input UNKNOWN | Output UNKNOWN. Absorption, never averaging. |
| **Non-critical** input UNKNOWN | KNOWN, one band lower, completeness reduced, factor named. |
| Timeframe has a **GAP** | GAP state, confidence LOW. **Range-dependent features (ATR, Bollinger, realised vol) emit no value** — a gap breaks their definition, not merely their quality. |
| Higher timeframe **not yet formed** | `INSUFFICIENT_HISTORY`, *not* UNKNOWN — it self-heals, and must never be learned from as "unknowable". |

### Criticality

**Declared per feature, not globally.** Everything-critical collapses to UNKNOWN constantly; nothing-critical launders uncertainty. A feature definition is incomplete without its criticality map.

### Migration (no deletions)

The 6+ existing vocabularies become **adapters** onto this model:

| Existing | Maps to |
|---|---|
| `CONFIDENCE_{HIGH,MODERATE,LOW,NONE}` (msi) | `Confidence` directly |
| `STATUS_{KNOWN,PARTIAL,UNKNOWN}` (15M) | KNOWN / DEGRADED / UNKNOWN |
| `PNL_{COMPLETE,PARTIAL,UNKNOWN}` (15K) | KNOWN / DEGRADED / UNKNOWN |
| `QUALITY_{EXCELLENT..POOR}` (synthesis) | Confidence bands |
| `STRENGTH_{STRONG,MODERATE,WEAK}` (15J) | Confidence bands |
| 15N epistemic 4-state | Already a subset — **closest existing match** |

**No package is rewritten.** Adapters are additive; each subsystem keeps its own vocabulary internally and exposes `Uncertainty` at its boundary.

---

## 3. Replay migration plan (boundaries only)

**Canonical owner: `replay_engine` (15H).** Nothing deleted.

| Subsystem | Files | Declared role | Migration |
|---|--:|---|---|
| **`replay_engine`** | 4 | **CANONICAL pure replay core** | Receives `TickStore` as its source when it exists |
| `replay/` | 10 | **Corpus adapter** — historical sessions, manifest, validator | Feed the canonical core; stop owning replay semantics |
| `mic_replay` | 7 | **Parity validator** — publication record/replay | Narrow to parity assertion only |
| `msi_counterfactual_replay` | 9 | **Counterfactual research** | Build *on* the canonical core |
| `qualification` | 8 | **Reporting / statistics** | Consumer only |

### Pure/impure boundary (the contract that makes parity real)

```
PURE CORE  (parity asserted here)     IMPURE SHELL (parity NOT claimable)
─────────────────────────────────    ──────────────────────────────────
candle aggregation                    websocket connect/reconnect
feature computation                   timeouts, retries, rate limits
phenomena detection                   clock reads
state synthesis                       disk/network IO
opportunity/selection/intent          subscription management
risk evaluation                       broker calls
```

**Parity tests assert only across the pure core.** Claiming end-to-end identity would be rhetorical — the shell cannot be identical between live and replay.

**Convergence is incremental**: each phase re-points one consumer. No big-bang merge.

---

## 4. Stack A harvest plan

**Confirmed by audit:** `capital/` has 24 consumers and 28 tests — but **every consumer is inside `trading_brain/`**. Stack B has no risk layer at all.

### Harvest targets (only these five)

| Capability | Stack A impl | Consumers/Tests | Stack B target | Adapter boundary | Retirement condition |
|---|---|---|---|---|---|
| **Capital** | `capital/` (engine, policy, providers) | 24 / 28 | L10 service | `CapitalPolicy`, `FundsProvider` | Parity vs Stack A on identical inputs + replay determinism |
| **Margin** | `capital/policy`, `msi_margin_bridge`, `whole_book_margin_provider` | 5 / — | L10 | `MarginProvider` | Estimate-vs-actual measured against broker |
| **Risk governor** | `trading_brain/risk_governor/` | 4 / — | L10 | `RiskDecision(intent, portfolio, capital) → APPROVE/REJECT/RESIZE` | Recorded-decision parity + replay |
| **Position sizing** | `trading_brain/position_sizing/` | — / — | L10 | `Sizer(intent, risk, capital) → quantity` | Sizing parity + margin correctness |
| **Operational safety** | `runtime_safety/` | 5 / 2 | L17 | Health + kill-switch protocol | Kill switch exists and is load-tested |

### Explicitly NOT harvested

`signal/` · `tick/` · `trade/` — ORB-VWAP-specific, **superseded not harvested** (Stack B has `msi_*`, 15I management, 15G lifecycle). They stay untouched until the legacy app retires as a unit.

`broker/` · `authentication/` · `journal/` — **already shared or serving distinct roles.** No migration needed.

### Method

Adapters wrap Stack A implementations behind Stack B interfaces. **Stack A source is not modified.** Parity is established by running both against identical recorded inputs before anything is retired.

---

## 5. Autonomy boundary enforcement

| Boundary | Mechanism | Status |
|---|---|---|
| **Memory ↛ Decision** | AST test forbids `outcome_memory` importing any decision path | ✅ **enforced (15N)** |
| **Execution ↛ bypass Lifecycle** | 15L bridge is the only path; `paper_bridge` calls only `get_order`/`get_execution_report` | ✅ **enforced (15L)** |
| **Research ↛ Production** | Separate stores + explicit promotion gate | ❌ **not built** |
| **Strategy ↛ bypass Risk** | Intent must pass L10 before any order | ❌ **blocked — L10 absent from Stack B** |
| **Risk ↛ bypass Operational safety** | Health state gates risk approval | ❌ **blocked — L17 absent from Stack B** |

### Designed enforcement for the three unbuilt boundaries

**Research ↛ Production** — research reads production stores; it may **never** write them. Enforcement: research modules import read-only protocols; an AST test forbids any `write`/`append`/`record` call from a research package. Promotion is a manual, versioned artifact (`PromotedKnowledge` with `calc_version` + validation evidence), never a live code path.

**Strategy ↛ bypass Risk** — the only function that can produce an `OrderIntent` takes a `RiskDecision` as a *required* argument. Not a runtime check — a type-level impossibility. AST test asserts nothing constructs `OrderIntent` without one.

**Risk ↛ bypass Operational safety** — `RiskDecision` requires a `FeedHealth` argument, and returns `REJECT` when health is not `HEALTHY`. Same pattern: make the unsafe path unconstructible rather than merely forbidden.

**Existing protections are not weakened by any of this** — all three are additive.

---

## 6. Operational readiness contracts

| Contract | Today | Design |
|---|---|---|
| **Kill switch** | ❌ **0 files** | A single durable flag checked before *every* order path; set by operator, alert, or health degradation. Must be checkable without the process running (file/DB), so a crashed process cannot lose it. |
| **Broker health** | ⚠️ `ops/health_monitor` (Stack A) | Composite: connection + feed + staleness + reconnect count + drop count. **`is_connected` alone is never "healthy"** — the 2026-07-29 incident is the proof. |
| **Feed health states** | ⚠️ partial | `HEALTHY / DEGRADED / STALE / DISCONNECTED / RECOVERING`, per-symbol staleness, published as an observation so decisions can gate on it. |
| **Reconciliation** | ❌ none for Stack B | Three loops: **positions** (broker vs `PositionLifecycle`), **realized P&L** (broker vs 15K), **margin** (estimate vs actual). Divergence is an *incident*, not a log line. |
| **Position truth** | ⚠️ | Broker is authoritative for existence/quantity; Bujji authoritative for *intent and thesis*. Divergence halts new entries. |
| **Margin truth** | ⚠️ Stack A only | Broker authoritative. Bujji's estimate is compared, never substituted. |
| **Failure recovery** | ✅ EventStore hydration (15B–15D) | Extend to fabric stores when they exist. |
| **Audit trail** | ✅ `journal/` (25 files, 42 tests) + EventStore | Already strong. Needs decision lineage attached (§1). |

### Sequencing constraint

**Reconciliation cannot be built before the fabric exists** — there is nothing to reconcile against in Stack B yet. **Kill switch can and should be built independently**; it has zero dependency on market data, and its absence is the single largest gap between "paper system" and "system that could ever be trusted with money."

---

## 7. Track B status

| Area | Deliverable | State |
|---|---|---|
| 1. Data lineage | Contract + binding rules + attachment points | ✅ **designed** |
| 2. Uncertainty | Canonical model + composition + 5 answers + migration | ✅ **designed** |
| 3. Replay | Canonical owner + role assignment + pure/impure boundary | ✅ **designed** |
| 4. Stack A harvest | 5 targets with adapters, parity and retirement conditions | ✅ **designed** |
| 5. Autonomy | 2 enforced, 3 designed as type-level impossibilities | ✅ **designed** |
| 6. Operational | 8 contracts; kill switch identified as independently buildable | ✅ **designed** |

**Nothing implemented. No assumptions converted into code. All frozen items remain frozen.**

### Known facts vs unknown measurements

**Known (evidence-backed):** lineage gaps (7/10 fail) · 20 packages × 6+ confidence scales · 5 replay subsystems · `capital/` is Stack A-only (24 consumers) · kill switch absent (0 files) · 2 of 5 autonomy boundaries enforced.

**Unknown (awaiting Gate 1):** everything about the feed — rate, latency, lateness magnitude, drops, capacity, reconnect fidelity, depth cost.

**No Track B design depends on any Gate 1 unknown.** That is why it was safe to complete now.
