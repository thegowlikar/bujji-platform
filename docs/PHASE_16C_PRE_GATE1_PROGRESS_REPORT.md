# Phase 16C — Pre-Gate-1 Progress Report

**Regression: 5,137 passed / 0 failed** (from 5,094 — every change explained below.)
**Architecture Lock intact. No Gate-1-dependent decision made.**

---

## 1. What was audited

Absence was **verified**, not assumed — this session has already produced three corrections where I wrongly declared something missing (`tick/`, the symbol cap, `market_calendar.py`). Every build decision below was gated on an explicit search across absolute imports, relative imports, tests, config and scripts.

| Candidate | Search | Result |
|---|---|---|
| **Kill switch** | 8 terms (`kill_switch`, `killswitch`, `KILL_SWITCH`, `emergency_stop`, `panic`, `halt_trading`, `trading_halt`, `disable_trading`) across `bujji/ tests/ config/ scripts/` | **0 hits — genuinely absent** |
| `runtime_safety` | read source | **NOT a kill switch** — a 9-check *pre-flight authorization gate* (`authorize()` → ALLOW/DENY/UNKNOWN). Pure function, no durable operator-settable state. Correction avoided. |
| **Canonical uncertainty** | `epistemic` (3), `uncertainty` (7), `limiting_factor` (**0**), `criticality` (**0**) | **No canonical package.** Composition semantics genuinely absent. |
| **Lineage** | `provenance` (**171 files**), `schema_version` (171), `calc_version` (**0**) | Concept pervasive **as a field**; **contract absent**. |
| `ObservationProvenance` | read source | Closest existing shape — adapted, not replaced. |

**Key nuance:** `provenance` in 171 files meant this was a *consolidation* problem, not a greenfield one. Building a competing model would have been the eighth-subsystem mistake.

---

## 2. What was implemented

### `bujji/epistemics/` — canonical uncertainty + lineage (P2 + P3)

Three modules, **stdlib-only**, zero Bujji dependencies:

**`uncertainty.py`** — 8 epistemic states (`KNOWN`, `UNKNOWN`, `NOT_AVAILABLE`, `NOT_APPLICABLE`, `INSUFFICIENT_HISTORY`, `STALE`, `GAP`, `DEGRADED`), one confidence scale, and the composition algebra that did not exist:

| Rule | Behaviour |
|---|---|
| **MIN** | output confidence ≤ weakest **critical** input |
| **Absorption** | critical `UNKNOWN`/`NOT_AVAILABLE`/`INSUFFICIENT_HISTORY` → output absorbs *that state*, not a flattened UNKNOWN |
| **GAP taint** | capped `LOW`; if `range_dependent=True` (ATR/Bollinger/realised-vol) the gap **breaks the definition** → no value at all |
| **STALE taint** | capped `LOW`; value retained for analysis, `is_actionable=False` so risk/execution refuse it |
| **NOT_APPLICABLE isolation** | ignored entirely — does not degrade siblings |
| **Non-critical degradation** | one band down + `completeness` reduced, never blocking |
| **`limiting_factor`** | **mandatory** whenever confidence was reduced |

**`lineage.py`** — the eight-question contract, with `calc_version` as a **content hash of the calculation** rather than a hand-maintained integer (a hand-bumped version drifts silently the moment someone edits a formula, and a silently-drifted version is worse than none). Includes `is_reproducible`, `missing_fields()` (names what an artifact *cannot* answer), `descends_from()` (accumulates source events without duplicates), and `look_ahead_violation()`.

**`adapters.py`** — maps all six existing vocabularies onto the canonical model. **Adapters map VALUES, not objects**, so adopting the model creates no dependency on 15K/15M/15N/msi internals and rewrites nothing.

### Deliberately NOT built: the kill switch

Genuinely absent and valuable — but Stack B has **no execution path to gate**. Building it now would produce a capability with no consumer: the exact recurring failure this project has hit repeatedly. Contract is designed (Track B doc); implementation waits for a consumer.

---

## 3. What was tested

| Suite | Tests | Result |
|---|--:|---|
| `test_epistemics.py` | 34 | ✅ pass |
| `test_epistemics_safety.py` | 9 | ✅ pass |
| **Full regression** | **5,137** | ✅ **0 failed** |

**The five required answers are proven as tests**, not prose:

1. *KNOWN but stale* → `STALE`, `LOW`, `carries_value=True`, `is_actionable=False`
2. *Critical UNKNOWN* → absorbed, `limiting_factor="iv:UNKNOWN"`
3. *Non-critical UNKNOWN* → `DEGRADED`, one band down, `completeness=0.5`
4. *GAP* → tainted; **range-dependent → UNKNOWN** (definition broken, not merely weakened)
5. *Higher TF not formed* → `INSUFFICIENT_HISTORY`, explicitly **not** `UNKNOWN` (it self-heals)

Plus an **exhaustive** invariant over every band pair proving derived confidence can never exceed its weakest critical input, and a full-stack propagation test showing a `GAP` at the observation layer is still visible, still named, and still non-actionable at the decision layer with `tick_store` traceable in its provenance.

### Baseline change explained

```
5,094  previous verified baseline
  +34  test_epistemics.py
  + 9  test_epistemics_safety.py
─────
5,137  ✅
```

No existing test modified. No existing package modified.

---

## 4. What remains blocked by Gate 1

| Blocked | Why |
|---|---|
| TickStore implementation | Row shape needs real field envelope + bytes/msg |
| Storage tier | 10× spread until sustained rate is measured |
| Ingestion topology | Needs CPU headroom (G3) |
| **Watermark / lateness** | Needs `LATENESS_MAGNITUDE_ms` (G1) |
| Candle persistence architecture | Downstream of TickStore |
| Retention policy | Downstream of volume |
| Websocket scaling | Needs server-side cap + reconnect fidelity |

**None were touched.**

---

## 5. What can proceed independently

| Item | State |
|---|---|
| Epistemics (P2/P3) | ✅ **done** |
| Replay ownership model (P4) | ✅ designed |
| Stack A harvest plan (P5) | ✅ designed |
| Autonomy boundaries (P6) | ✅ 2 enforced, 3 designed |
| Market intelligence hierarchy (P7) | ✅ designed |
| Kill switch | ⏸ designed; **deliberately unbuilt** (no consumer) |
| Adopting epistemics in existing packages | ⏸ available now via adapters, phase-by-phase |

---

## 6. Updated dependency DAG

```
RAW MARKET EVENTS
   │ ✗ DISCONNECTED  (feed keeps 2 of 23 fields; nothing persists)
AUTHORITATIVE STORAGE
   │ ✗ DISCONNECTED  (no TickStore — BLOCKED BY GATE 1)
MULTI-TF DATA
   │ ⚠ PARTIAL       (5m only, synthetic)
FEATURES
   │ ⚠ PARTIAL       (6 of ~40) ── ✅ NOW HAS: lineage + uncertainty contract
PHENOMENA
   │ ⚠ PARTIAL       ── ✅ NOW HAS: uncertainty composition
STATE
   │ ⚠ PARTIAL       ── ✅ NOW HAS: uncertainty composition
OPPORTUNITY  ✓  STRATEGY  ✓  TRADE INTENT ✓
   │ ⛔ LEGACY        (risk/capital exists only in Stack A)
RISK/CAPITAL
   │ ⚠ PaperBroker only
EXECUTION ✓ → LIFECYCLE ✓ → PORTFOLIO ✓ → OUTCOME ✓ → MEMORY ✓
   │ ✗ DISCONNECTED  (no research promotion path)
RESEARCH
```

**Change since last DAG:** the FEATURES → PHENOMENA → STATE band now has a *shared* uncertainty and lineage contract. Those edges remain PARTIAL because their data substrate is still missing — but they are no longer *semantically* fragmented.

---

## 7. Exact next action after Gate 1

1. Read `LATENESS_MAGNITUDE_ms` → **set the watermark** (first Gate-1-dependent decision)
2. Read sustained rate + bytes/msg → **choose the storage tier**
3. Read CPU/RSS headroom → **choose ingestion topology**
4. Read drops + `harness_was_bottleneck` → confirm single-process viability
5. Read `corpus_self_sufficient` → confirm the replay premise before building on it
6. **Then** 16C-proper: full-fidelity feed adapter + `TickStore`, with `Lineage` and `Uncertainty` attached from the first line rather than retrofitted

---

## 8. Discipline check

| Principle | Held? |
|---|---|
| REUSE → ADAPT → CONSOLIDATE → BUILD | ✅ adapters over 6 vocabularies; nothing rewritten |
| No new subsystem for convenience | ✅ built only after verifying `limiting_factor`/`criticality` = 0 files |
| No parallel confidence vocabulary | ✅ asserted by test |
| No Gate-1 assumption in code | ✅ epistemics is stdlib-only, market-data-free |
| Nothing deleted / merged / refactored | ✅ |
| Stack A untouched | ✅ |
| Baseline maintained or explained | ✅ 5,094 → 5,137, fully accounted |

**Working tree uncommitted at `b148e39`.** Gate 1 still blocked on token refresh + a non-expiry session.
