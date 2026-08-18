# Phase 19.2.1 — Market Intelligence Boundary Hardening Audit

**Status: AUDIT ONLY. Zero code changes.**

**Headline finding**: the wall-clock defect Phase 19.0.1 found in
`RegimeBrain` is not an isolated bug. **All six brains call `now_ist()`
internally to stamp their own `as_of` field — six for six, confirmed
by direct grep of every module.** This is systemic, not incidental,
and confirms the user's own instinct that this gate belongs before
implementation, not after.

---

## 1. Brain Clock Determinism — Full Audit, All Six

| Brain | Input timestamp source | Internal clock call | Output `as_of` | Historical replay safe? |
|---|---|---|---|---|
| `RegimeBrain` | None — takes `candles` only, no time parameter | `now_ist()` (2 call sites) | Wall-clock, always | **NO** |
| `StructureBrain` | None — takes `spot`, `strikes` only | `now_ist()` (1 call site, used for both success and `_unknown` paths) | Wall-clock, always | **NO** |
| `LiquidityBrain` | None — takes bid/ask floats only | `now_ist()` (1 call site) | Wall-clock, always | **NO** |
| `VolatilityBrain` | None — takes `spot_candles`, `spot`, `strike`, `t_years`, premiums | `now_ist()` (1 call site, `now = now_ist()`) | Wall-clock, always | **NO** |
| `GreeksBrain` | None — takes `spot`, `strike`, `t_years`, `iv_ce`, `iv_pe` | `now_ist()` (1 call site) | Wall-clock, always | **NO** |
| `EventBrain` | **Partial** — `today: date` IS a real, injected parameter, used for the actual `expiry_proximity` classification logic | `now_ist()` (1 call site, for `as_of` only) | Wall-clock for `as_of`; **classification itself is correctly injected** | **PARTIAL** — the real decision (days-to-expiry) is already replay-safe; only the metadata label is wrong |

**The precise failure mode, confirmed identical across all six**: every
brain's actual CLASSIFICATION logic is a pure function of its real
input parameters (candles, premiums, bid/ask, strikes) — none of them
call `now_ist()` to make a DECISION. The wall-clock call exists solely
to stamp the output object's own `as_of` metadata field. **This means
the underlying market read (`RegimeType.TRENDING`, a computed IV
value, etc.) is already correct and reproducible in a historical
replay — but the object LIES about when that reading was true**,
exactly the failure mode the user's own worked example describes: a
2026-08-14 replay run on 2026-08-15 would silently report
`as_of: 2026-08-15`, and any downstream consumer trusting that field
(a `MarketIntelligenceSnapshot`'s own `as_of_time`, a revision-lineage
timestamp, an evidence-freshness check) would be misled about
temporal ordering even though the classification VALUE itself was
never wrong.

**This is the exact reason Phase 19.2's own mandatory clock rule is
correct and non-negotiable — confirmed, not merely asserted.** Every
brain needs one, uniform fix: accept an `as_of: datetime` parameter
(or a `Clock` callable, per `mil_next`'s own already-proven pattern,
Phase 19.1.2) and stamp the output with it instead of calling
`now_ist()`. This is a small, mechanical, six-module fix — not a
redesign of any brain's actual logic.

## 2. Evidence Model Audit

Every brain's real `evidence: dict[str, Any]` field (confirmed present
on `RegimeReading`, `StructureReading`, `LiquidityReading`,
`VolatilityReading`, `GreeksReading`, `EventReading` — all six)
carries **real, machine-readable NUMBERS** — e.g.
`{"efficiency_ratio": 0.62, "realized_vol": 0.0018}` — never bare
prose strings like `"OI increased"`. This is better than the naive
failure mode the user's own prompt names as a risk — **it is not the
"OI increased" problem.**

**What IS genuinely missing, confirmed by re-reading every evidence
dict's real shape**: none of these numbers carry a **lineage
pointer** back to the specific `HistoricalObservation.observation_id`
(or `MarketRealitySnapshot.fingerprint()`) that produced them. A
value like `"realized_vol": 0.0018` is real and precise, but nothing
on the object itself lets a future auditor independently verify
WHICH candles produced that number without re-running the brain and
trusting it agrees. This is a real, moderate gap — not the severe
"free-floating text" problem, but short of the full
machine-verifiable standard Phase 19.1's own evidence-boundary design
already requires (`source_observation_ids`, Phase 18.1/18.3).

**Recommended canonical `EvidenceReference` model, exactly per this
phase's own proposal, refined against what's real**:

```
EvidenceReference
  evidence_id:          content hash (fingerprint_state(), reused a
                         fifth time, not a new mechanism)
  source_type:            "HistoricalObservation" | "MarketRealitySnapshot"
                          (a closed, small vocabulary, not a free string)
  source_reference:         the real observation_id or snapshot fingerprint
  observation_time:           the REAL market timestamp the fact reflects
                          (never wall-clock, per §1's own finding)
  dataset_artifact_id:          Optional[str] — when computed against a
                          published artifact (Phase 18.12), else None
                          (an honest absence, not an error)
  metric_name:                the exact field name (e.g. "efficiency_ratio",
                          "open_interest_change") — reuses each brain's
                          OWN already-real evidence dict keys, not a new
                          naming scheme
  value:                        the real computed number, copied verbatim
  confidence:                     Optional — only set where a SINGLE metric
                          has its own sub-confidence; most evidence
                          entries will leave this None and rely on the
                          brain's own aggregate confidence field
```

**Implementation note (not for this phase, disclosed for the next
one)**: retrofitting this onto all six brains means each brain's
`evidence` dict values become `EvidenceReference` objects instead of
bare floats — a real, mechanical, bounded change per brain, not a
redesign of any classification logic, exactly the same shape of fix
as §1's clock injection.

## 3. Volatility Reference Policy

**Confirmed, directly, not inferred**: `VolatilityBrain.analyze()`'s
real signature (Phase 19.0.1) takes `strike: float` as a caller-
supplied parameter — the brain itself has **zero opinion** on which
strike represents "the" volatility read. No strike-selection rule
exists anywhere in `bujji/intelligence/` today.

**A real, existing, already-proven precedent WAS found elsewhere in
the codebase**, directly validating this phase's own suggested
ATM rule rather than requiring one to be invented from scratch:

```python
# bujji/market_perception/option_chain_adapter.py, real, live code:
atm_strike = min((c.strike for c in contracts), key=lambda s: abs(s - spot))
```

Its own comment states this is *"the mapping used throughout this
project's offline ATM resolution work"* — meaning nearest-strike-to-spot
is already this project's own established convention elsewhere, simply
never wired into `VolatilityBrain`/`GreeksBrain`'s own callers.

**Recommendation, directly building on this real precedent and the
user's own proposed policy**:

```
VolatilityReferencePolicy = VOLATILITY_REFERENCE_ATM_STRADDLE

Definition:
  atm_strike = min(contracts, key=lambda c: abs(c.strike - spot))   [REUSE the
               real, existing function above -- do not reimplement]
  ce_premium = atm_strike's own CE .ltp
  pe_premium = atm_strike's own PE .ltp
  volatility.iv_ce/.iv_pe = VolatilityBrain.analyze(..., strike=atm_strike, ...)
```

**Is IV derivation itself deterministic?** Yes, confirmed by
`VolatilityReading`'s own docstring (Phase 19.1): `iv_ce`/`iv_pe` are
*"solved from the CE/PE premium"* — a Black-Scholes inversion, a pure
mathematical function of `spot`/`strike`/`t_years`/`premium`/
`risk_free_rate`, all of which are real, already-deterministic inputs.
The only non-determinism was ever in the OUTER question (“which
strike”), now closed by adopting the real, existing ATM convention
above.

**One real, disclosed limitation of the ATM rule, not hidden**: ATM
selection is itself SPOT-dependent — a spot price that crosses a
strike boundary between two `as_of_time`s will cause the "ATM" strike
to jump, exactly the "ATM rule cons: jumps around" tradeoff this
phase's own prompt already named. This is accepted as a known,
bounded cost, consistent with `RegimeBrain`'s own precedent of
documenting calibration limitations rather than pretending they don't
exist (Phase 19.0's own confirmed finding about `RegimeBrain`'s
threshold constants).

## 4. Identity Boundary Validation

Re-checked against Phase 19.1.1/19.2's own final taxonomy, with one
real correction to this phase's own prompt:

- `DatasetArtifact.artifact_id` — real, stored field, FROZEN (Phase
  18.15). No collision found.
- `MarketRealitySnapshot` — **has no stored `reality_snapshot_id`
  field at all**, confirmed by direct re-read of
  `market_reality_snapshot/models.py` (Phase 18.3's own deliberate
  design: identity is the RETURN VALUE of the `.fingerprint()` method,
  never a stored attribute). This phase's own prompt lists
  `reality_snapshot_id` as if it were an existing field name — it is
  not, and inventing one now would itself create a new field where a
  method call has always been the real identity mechanism.
  **Recommendation: keep using `.fingerprint()` as the identity
  reference; do not introduce a redundant stored field.**
- `MarketIntelligenceSnapshot.intelligence_snapshot_id` — new, per
  Phase 19.2's own design, deliberately never the bare word
  `snapshot_id` (avoiding the three-way collision Phase 19.1.1 found:
  `mic_adapter`, `mil_next`, `trading_brain.ontology`).
- `DecisionContext` — confirmed (Phase 19.2 §7) to have no single
  `_id` field of its own; identified via `(as_of, session_id)` — a
  real, pre-existing inconsistency, correctly left out of scope again
  this phase (not an Intelligence-layer problem to fix).

**No new collision found. The one correction above is a naming
precision fix to this phase's own prompt, not a new architectural
problem.**

## 5. Historical Replay — Can Bujji Answer "What Did It Know at 10:30 on 2026-08-14"?

**Today: No, provably not, for exactly one reason** — confirmed live
by tracing the real failure: build a `RegimeReading` (or any of the
other five) from real 2026-08-14 10:30 candles, on any day other than
2026-08-14 itself, and its own `as_of` field will read today's real
date, not 10:30 on 2026-08-14. The underlying CLASSIFICATION would be
correct (§1's own finding: the logic itself is already a pure function
of real historical inputs) — but the object's own claim about WHEN
that was true would be false. **This is precisely the gap between "the
market read is right" and "Bujji knows what it knew and when it knew
it"** the user's own framing distinguishes — confirmed real, not
hypothetical, by this audit's own direct code trace.

**After the §1 fix (mandatory `as_of` injection, no exceptions)**: yes
— every other precondition is already real and proven:
`MarketRealitySnapshot`'s own no-look-ahead guarantee (Phase
18.1–18.10, adversarially proven), each brain's own pure-function
determinism (confirmed, all six, this phase), and `fingerprint_state()`'s
own reproducible hashing (proven three times independently — Phase
18.3, `mil_next`, and now this design's own `intelligence_snapshot_id`).

## Final Verdict

**B) Small hardening required — precisely two items, both fully
specified, neither a redesign.**

1. **Clock injection across all six brains** (§1) — mechanical,
   bounded, the exact same fix pattern six times over, with
   `mil_next.snapshot_builder`'s own real `Clock` parameter as a
   proven template (Phase 19.1.2).
2. **Evidence lineage typing** (§2) — retrofit each brain's own
   `evidence` dict values into `EvidenceReference` objects carrying a
   real `source_reference`, reusing `fingerprint_state()` and each
   brain's own already-real metric names — no new data, only a
   wrapper around data that already exists.

Plus one now-closed design decision that was genuinely open before
this audit: **`VolatilityReferencePolicy = VOLATILITY_REFERENCE_ATM_STRADDLE`**,
directly reusing this project's own existing, real, live ATM-selection
code (`option_chain_adapter.py`) rather than inventing a new rule.

**Not (A)**: implementation today would silently ship six
historically-mislabeled `as_of` fields into the very object every
downstream consumer is meant to trust — exactly the risk this phase
set out to catch. **Not (C)**: no architectural redesign is
needed — every fix identified is additive and mechanical, and four of
five audit areas (evidence NUMBERS being real, `mil_next`'s clock
pattern being reusable, the ATM convention already existing elsewhere,
identity boundaries being clean) came back confirming the foundation
is sound, not broken.
