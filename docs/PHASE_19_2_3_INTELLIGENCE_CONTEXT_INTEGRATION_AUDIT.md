# Phase 19.2.3 — Intelligence Context Integration Audit

**Audit only. No code changes made in this phase.**

## Objective

Verify Phase 19.2.2's clock injection and evidence lineage changes are actually complete and correctly
wired everywhere — not just in the three caller sites already known and fixed during 19.2.2 itself —
before starting `MarketIntelligenceSnapshot` implementation (Phase 19.3).

## 1. Every intelligence brain caller supplies `IntelligenceContext`

Searched the entire `bujji/` package for `.analyze(` (not just the intelligence package itself, to catch
any caller reaching in from elsewhere):

```
grep -rn '\.analyze(' bujji/ --include='*.py'
```

Real invocation sites found (excluding comments/docstrings, which also turned up in the grep and are
listed separately below for completeness):

| Call site | Brain | `context=` supplied? |
|---|---|---|
| `bujji/intelligence/runner.py:83` | RegimeBrain | ✅ positional `context` |
| `bujji/intelligence/runner.py:91` | VolatilityBrain | ✅ `context=context` |
| `bujji/intelligence/runner.py:95` | PremiumBrain | N/A — out of scope, no `context` param exists |
| `bujji/intelligence/runner.py:102` | GreeksBrain | ✅ `context=context` |
| `bujji/intelligence/runner.py:110` | LiquidityBrain | ✅ `context=context` |
| `bujji/intelligence/runner.py:111` | StructureBrain | ✅ `context=context` |
| `bujji/intelligence/runner.py:119` | EventBrain | ✅ `context=context` |
| `bujji/intelligence/runner.py:123` | BehaviourBrain | N/A — out of scope, no `context` param exists |
| `bujji/market_state/intelligence_cycle_recorder.py:348` | LiquidityBrain | ✅ `context=intelligence_context` (built from `datetime.fromisoformat(snapshot.timestamp)`) |
| `bujji/shadow_runtime/shadow_session_runner.py:267-269` | LiquidityBrain | ✅ `context=liquidity_context` (built from `self._clock()`) |

**No bypass path found.** All six in-scope brains are called from exactly three production sites, and all
three were already fixed during Phase 19.2.2 (the type mismatch caught in `intelligence_cycle_recorder.py`
is documented in that phase's own report). Every other `.analyze(` grep hit is a comment, docstring, or
prose reference (`liquidity_aggregator.py`, `liquidity_pairing_adapter.py`, `msi_volatility_structure/*`,
`reality_structure_bridge/bridge.py`, `trading_brain/risk_governor/market_regime_adapter.py`) — none of
these files actually call `.analyze()` themselves.

**One stale comment found, not a bug:** `bujji/msi_volatility_structure/taxonomy.py:32-33` still reads
*"VolatilityBrain.analyze()/GreeksBrain.analyze()/RegimeBrain.analyze() all call now_ist() internally —
this bridge NEVER..."* — this claim is now factually outdated (Phase 19.2.2 removed those internal calls).
The underlying design decision the comment documents (this bridge deliberately does not call these brains
directly) is still correct and still a valid reason to keep the bridge separate; only the stated
justification is stale. Recommend a one-line doc correction alongside Phase 19.3, not urgent enough to
justify a standalone phase.

## 2. Evidence lineage coverage

Confirmed via direct grep of `bujji/intelligence/models.py`: `evidence_lineage: Dict[str,
IntelligenceEvidence] = field(default_factory=dict)` is present on exactly the six in-scope classes
(`RegimeReading`, `VolatilityReading`, `GreeksReading`, `LiquidityReading`, `StructureReading`,
`EventReading`) and absent from `PremiumReading`/`BehaviourReading`, matching Phase 19.2.2's own stated
scope.

**Downstream consumer check:** searched every `.evidence` reference across `bujji/` (excluding
`evidence_lineage` itself). Every real consumer — `outcome_attribution/engine.py`,
`market_state/evidence_boundary.py`, `trading_brain/risk_governor/market_regime_adapter.py`,
`production_runtime/health.py`, and each Reading class's own `render()`/`to_log()`/`to_dashboard()` methods
— reads the original `evidence: dict[str, Any]` field, which Phase 19.2.2 deliberately left untouched.
**None of them reference `evidence_lineage`.** This is not a bug (Phase 19.2.2 explicitly wrapped rather
than replaced), but it is a real gap worth naming precisely: `evidence_lineage` is populated on every
Reading but is **not yet surfaced through `to_dashboard()`, `to_log()`, or `render()`** on any of the six
classes. It only reaches a caller who reads `reading.evidence_lineage` directly (as the new
`test_intelligence_determinism.py` tests do). No downstream code currently does that. This is expected —
Phase 19.2.2's own scope was "wrap it," not "wire it into every existing output surface" — and is exactly
the kind of gap `MarketIntelligenceSnapshot` should close in Phase 19.3, since that is the first real
consumer of lineage data.

## 3. Live vs Replay equivalence proof

Constructed one shared `IntelligenceContext.as_of_time = 2026-08-14T10:30:00+05:30` and ran all six
in-scope brains twice against identical inputs — once with `execution_mode=HISTORICAL_REPLAY`, once with
`execution_mode=LIVE` (the two real, defined modes; `context.py` does not define a `LIVE_SIMULATION` mode
— only `LIVE`, `PAPER`, and `HISTORICAL_REPLAY` — so this audit used `LIVE` as the live-side comparator
rather than inventing a fourth mode not defined anywhere in the codebase):

```
RegimeBrain     — regime, confidence, evidence, as_of : IDENTICAL
StructureBrain  — proximity, evidence, as_of           : IDENTICAL
LiquidityBrain  — tightness, evidence, as_of            : IDENTICAL
VolatilityBrain — richness, evidence, as_of             : IDENTICAL
GreeksBrain     — exposure, evidence, as_of             : IDENTICAL
EventBrain      — vix_regime, evidence, as_of           : IDENTICAL
```

All twelve readings (six brains × two modes) carry `as_of == 2026-08-14T10:30:00+05:30` exactly — no
brain diverged, and none silently fell back to a wall-clock value under either mode. This confirms the
`execution_mode` field is genuinely inert with respect to classification output (as designed — no brain
branches on it), and that the only thing distinguishing LIVE from HISTORICAL_REPLAY is the caller's own
metadata, not brain behavior.

This directly proves the property the user's own framing named as "the killer test": **Bujji thinks the
same way whether looking backward or live.**

## 4. Remaining hidden wall-clock dependency search

```
grep -n 'datetime.now()\|datetime.utcnow()\|now_ist(' bujji/intelligence/*.py
```

Result:

```
bujji/intelligence/behaviour_brain.py:64:        as_of = now_ist()
bujji/intelligence/premium_brain.py:68:        as_of = now_ist()
bujji/intelligence/context.py:4:   (docstring reference only, not a call)
bujji/intelligence/runner.py:80:   (comment referencing the old bug, not a call)
```

`behaviour_brain.py` and `premium_brain.py` **do still call `now_ist()` internally.** This is not a
regression or an oversight — both brains are explicitly out of Phase 19.2.2's scope (Position/Learning
intelligence, not Market Intelligence, per Phase 19.0.1/19.1's established exclusion, reaffirmed in Phase
19.2.2's own report). Flagging it here explicitly rather than treating "no wall-clock anywhere in
`bujji/intelligence/`" as already true: it is true only for the six Market Intelligence brains this phase
targeted, not for the two Position/Learning brains that share the same directory. No wall-clock call
exists in any of the six in-scope files (`regime_brain.py`, `structure_brain.py`, `liquidity_brain.py`,
`volatility_brain.py`, `greeks_brain.py`, `event_brain.py`) — confirmed both by this grep and by the
existing structural test `test_no_in_scope_brain_imports_or_calls_now_ist`.

## Final verdict

**A) Ready for `MarketIntelligenceSnapshot` implementation.**

No bypass path, no missing context wiring, no evidence lineage gap beyond the expected/scoped
"not yet surfaced in dashboards" state, and the live/replay equivalence proof holds exactly across all six
in-scope brains with real inputs. The two remaining `now_ist()` call sites are correctly out of this
phase's scope, not a defect.

One non-blocking cleanup item carried forward (not required before 19.3): correct the stale
"`RegimeBrain.analyze()`... calls `now_ist()` internally" claim in
`bujji/msi_volatility_structure/taxonomy.py:32-33`'s docstring.
