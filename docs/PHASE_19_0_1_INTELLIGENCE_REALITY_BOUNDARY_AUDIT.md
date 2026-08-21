# Phase 19.0.1 — Market Intelligence Reality Boundary Audit

**Status: AUDIT ONLY. Zero code changes.**

**A correction to Phase 19.0, made transparently, not buried**: that
audit's grep for the Intelligence↔Historical-Foundation connection did
not include `bujji/reality_memory/`, and its claim of "zero references
in either direction" was wrong for that module specifically. This
phase's own first job — auditing the boundary properly — caught it.
The corrected picture is more nuanced than either "fully disconnected"
or "fully connected," and is laid out precisely below.

---

## 1. Intelligence Input Boundary — Per Module, Real Signatures

Every `analyze()` signature below was read directly from the real,
deployed source this phase, not inferred.

| Module | Real input signature | Raw fact or derived feature? | Can originate from `MarketRealitySnapshot`? | Classification |
|---|---|---|---|---|
| `regime_brain.RegimeBrain.analyze` | `candles: list[Candle]` | Raw (OHLC) | Yes — `SpotSnapshot`/intraday rows map directly to `Candle` | **READY** |
| `structure_brain.StructureBrain.analyze` | `spot: float, strikes: list[tuple[strike, ce_premium, pe_premium]]` | Raw | Yes — `OptionsSnapshot.contracts` grouped by strike maps directly | **READY** |
| `liquidity_brain.LiquidityBrain.analyze` | `ce_bid, ce_ask, pe_bid, pe_ask: float` | Raw | Yes — `OptionContractSnapshot.bid`/`.ask`, per contract | **READY** (needs a strike-selection rule — which contract's bid/ask to use — a small, real adapter decision, not a data gap) |
| `event_brain.EventBrain.analyze` | `expiry_date, today, vix_level, vix_prev_close` | Raw + one derived cross-reference | Partially — `expiry_date` from option identity, `vix_level` from `VixSnapshot.close`; `vix_prev_close` requires a SEPARATE, prior-day snapshot lookup | **NEEDS ADAPTER** |
| `volatility_brain.VolatilityBrain.analyze` | `spot_candles, spot, strike, t_years, ce_premium, pe_premium` | Raw | Yes, but requires picking ONE representative strike (ATM) out of the full chain — a real selection rule, not currently defined anywhere | **NEEDS ADAPTER** |
| `greeks_brain.GreeksBrain.analyze` | `spot, strike, t_years, iv_ce, iv_pe` | **`iv_ce`/`iv_pe` are NOT Reality facts** | **No** — confirmed: `market_reality.taxonomy.FORBIDDEN_PAYLOAD_FIELDS` structurally forbids storing `iv`/`implied_volatility` anywhere in Reality (re-confirmed this phase), and options capture deliberately never requests Greeks/IV from FYERS (Phase 17I.10/18.0's own confirmed finding) | **ARCHITECTURAL ISSUE** — see §6 |
| `premium_brain.PremiumBrain.analyze` | `entry_combined_premium, current_combined_premium, spot_at_entry, entry_iv, entry_time, now, expiry_time` | Mixed — assumes an OPEN POSITION with a real entry event | Only if a hypothetical "as-of" position is synthesized — this brain's shape is a position-tracking concept, not a stateless point-in-time market read | **ARCHITECTURAL ISSUE** — see §6 |
| `behaviour_brain.BehaviourBrain.analyze` | `trades: list[tuple[pnl, exit_reason]]` | **Not market data at all** — trade outcome history | No — this is Position/Outcome intelligence, not Market intelligence | **ARCHITECTURAL ISSUE** — wrong category for this adapter entirely, see §6 |

**Net result**: 3 of 8 brains are genuinely `READY` for a
straightforward adapter; 2 need a real but bounded adapter (strike
selection, cross-date lookup); 3 surface real architectural questions
this audit found and none of Phase 19.0 surfaced, because Phase 19.0
did not read past each module's file existence and test coverage into
its actual parameter shape.

## 2. Reality Contamination Audit

Checked directly, across `bujji/intelligence/`, `bujji/market_state/`,
`bujji/market_state_builder/`, `bujji/market_understanding/`,
`bujji/market_regime_memory/`, `bujji/reality_memory/`: **zero write
calls, zero imports of any write-capable store method, anywhere.**

Two files DO import Reality-tier store classes —
`market_understanding/structure.py` (`HistoricalObservationStore`) and
`reality_memory/catalog.py` (`HistoricalObservationStore`,
`RawObservationStore`) — both confirmed, by direct read, to hold them
**read-only**: `structure.py`'s `IntradayStructureCatalog` docstring
states plainly *"Read-only. Never writes to any store."*, and its own
`get()` method only ever calls read functions. `reality_memory/catalog.py`
similarly only ever calls `build_market_reality_snapshot()` (itself a
pure read function, Phase 17H.5) — never `store.write(...)`.

**Can any intelligence module create fake observations, alter
historical facts, or influence certification?** No evidence of any of
the three found. No intelligence module imports `CertificationGate` or
any certification-writing path. **Confirmed: the boundary holds today,
provably, not merely by policy.**

## 3. Intelligence Output Classification

Four layers, each mapped to what actually exists:

- **Reality** ("what happened") — `HistoricalObservation`,
  `MarketRealitySnapshot`. Immutable, frozen (Phase 18.15).
- **Features** ("what measurements describe it") — the RAW INPUTS to
  each brain (candle closes, strike/premium tuples, bid/ask) — these
  are still Reality-derived facts, not yet interpretation. No existing
  model formally names this intermediate layer; it exists implicitly
  as "whatever gets passed into `analyze()`."
- **Intelligence** ("what interpretation do we assign") — each
  brain's own `*Reading` output (`RegimeReading`, `StructureReading`,
  etc.) — confirmed to always carry `confidence` and `evidence`
  alongside the classification itself, never a bare label. This is the
  correct, already-enforced shape for this layer.
- **Decision** ("what action may be considered") — does not exist yet
  anywhere in the modules audited this phase (`bujji.trading_brain.risk_governor`
  is a separate, live-trading-only package, not part of this MIC
  chain) — correctly out of scope for both this phase and Phase 19.0's
  own roadmap (Phase 19.5).

**No layer mixing was found.** Every brain's output type is a distinct
`*Reading` dataclass, never a raw float or a mutated input — confirmed
by reading each module's return type.

## 4. `MarketIntelligenceSnapshot` — Design, Not Implemented

```
MarketIntelligenceSnapshot
  timestamp:                  the AS-OF moment this reflects (see §6's
                               own wall-clock finding — must be INJECTED,
                               never read from the system clock)
  market_state:
    regime:                    = RegimeReading (already exists, real shape)
    volatility_state:           = VolatilityReading (already exists)
    options_state:               = StructureReading + LiquidityReading (already exist)
    liquidity_state:              = LiquidityReading (already exists)
  historical_context:             = RealityMemoryEvent / similarity match
                                    (already exists, `reality_memory`/
                                    `market_understanding.similarity`)
  confidence:                      an aggregate over per-brain confidences
                                    (NOT a new arbitrary score -- see §7)
  evidence_refs:                    union of every per-brain `evidence` dict
                                    (already real, per-brain)
  source_reality_snapshot_id:        the `MarketRealitySnapshot.fingerprint()`
                                    (Phase 18.3) this was computed from --
                                    THE field that makes this "reference
                                    Reality, never replace it," per this
                                    phase's own explicit requirement
```

Every field traces to a real, already-existing per-brain output type
or a real Phase 18.x identity primitive (`fingerprint()`). **No field
requires inventing a new concept from nothing** — this is an assembly
object, the same pattern `DatasetArtifact` itself used over
`DatasetVersion` (Phase 18.12).

## 5. Historical Intelligence Replay — Feasibility, With One Real Blocker Found

**The mechanism this needs already exists and is proven**:
`build_market_reality_snapshot(date, resolution=FIVE_MINUTE,
as_of_time=...)` (Phase 18.1, hardened through 18.10) already answers
"what was Reality at this exact historical moment, with no
look-ahead" — proven adversarially, repeatedly, across five phases.

**One real blocker found this phase, not previously identified**:
`RegimeBrain.analyze()`'s own return statement calls `as_of=now_ist()`
— a **live wall-clock read**, confirmed by direct code inspection.
Called during a historical replay of, say, 2026-08-14 10:30 IST, this
brain would stamp its reading with TODAY's real time, not the replayed
moment — silently mislabeling every historically-replayed
`RegimeReading`. This is exactly the class of defect this project has
found and fixed multiple times before (`epistemics.identity.DecisionContext`
was built specifically to eliminate this pattern, per its own
docstring, referencing a prior real incident in
`shadow_trade_construction`). **Not confirmed for the other 7 brains
this phase** (time-bounded) — each must be individually checked for
the same pattern before Phase 19.2 attempts real historical replay,
not assumed clean by association.

**Given that fix (real, small, scoped)**: the worked example in this
phase's own prompt — Reality (price/options/volatility) + Intelligence
(regime classification + confidence + context) at a specific historical
moment, with no future information — is architecturally achievable
using ONLY already-existing, already-proven primitives, once (a) the
adapter from §1 exists and (b) every brain's own `as_of`/timestamp
field is injected rather than read from the clock.

## 6. Adapter Requirements

```
MarketRealitySnapshot
        |
        v
Reality Intelligence Adapter    <- THE genuinely new code this phase's
        |                          audit scopes (not built here)
        v
Existing Brains
```

**Required transformations, each grounded in a real signature gap
found in §1**:

1. **Candle extraction** (regime_brain) — trivial: `SpotSnapshot`/
   intraday rows already carry OHLC; a thin mapping function.
2. **Strike-grouped tuple extraction** (structure_brain) — trivial:
   `OptionsSnapshot.contracts` grouped by `strike`, `ce_premium`/`pe_premium`
   read off matching CE/PE pairs.
3. **Strike selection rule** (volatility_brain, liquidity_brain) — a
   REAL, currently-undefined decision: which strike is "the" one to
   feed a single-strike brain. The obvious candidate (nearest-the-money)
   requires comparing `spot` against every contract's `strike` — simple
   to implement, but the RULE itself does not exist anywhere today and
   must be an explicit, documented adapter decision, not an implicit
   default.
4. **Prior-day cross-reference** (event_brain's `vix_prev_close`) —
   requires querying a SECOND `MarketRealitySnapshot` (the prior
   trading day's) alongside the current one — a real, small extension
   to how the adapter calls the builder, not a new capability.
5. **IV derivation, not IV lookup** (greeks_brain) — the adapter MUST
   NOT attempt to read `iv_ce`/`iv_pe` from Reality (none exists, by
   design). It must instead invert real `ce_premium`/`pe_premium`
   (Black-Scholes IV solve) to DERIVE an implied volatility as an
   Intelligence-layer computation — this is legitimate (deriving a
   number from real facts is exactly what Intelligence is for), but it
   is a real design decision this audit surfaces, not a
   previously-known requirement.
6. **Wall-clock injection** (§5) — every brain's own `as_of`/timestamp
   field must accept an injected value for historical replay to be
   honest.
7. **Governed-path alignment for `reality_memory`/`market_understanding.structure`** —
   both already connect to Reality (§1's own correction), but
   `reality_memory.catalog` calls `build_market_reality_snapshot()`
   with no `resolution`/`as_of_time` arguments — defaulting to
   `RESOLUTION_DAILY`, meaning **it cannot see options data at all
   today** (options only exist at `FIVE_MINUTE` resolution, confirmed
   throughout Phase 17I–18.x) and predates every eligibility/certification
   gate built in Phase 18.12–18.14. This is a real, disclosed gap: the
   "historical context" memory layer is quietly working off a narrower,
   older slice of Reality than what now exists. Recommended for Phase
   19.1: extend its calls to pass `resolution=RESOLUTION_FIVE_MINUTE`/
   `as_of_time_of_day` where a point-in-time (not daily) memory query is
   needed, rather than building a second, parallel memory catalog.

**Unsupported cases, honestly named**: `premium_brain` and
`behaviour_brain` (§1) do not have a clean adapter path at all — they
are shaped for different consumers (an open position's own
entry-vs-current tracking; historical trade-outcome analysis,
respectively) than "what does Reality look like right now." Forcing
them into this adapter would be a real architectural distortion, not a
mapping exercise — recommended: exclude both from Phase 19.1's initial
adapter scope, and let a future phase decide whether they belong to
Position Intelligence (which already exists, per this project's own
`bujji/position_intelligence/` package, seen in this session's earlier
work) instead of Market Intelligence.

## 7. Confidence Governance

**Grounded in what `regime_brain` already, actually does** (read
directly, not designed from scratch): confidence is a function of how
strongly the real evidence clears its own classification thresholds —
e.g., an efficiency ratio far above `ER_TRENDING_THRESHOLD` produces
higher confidence than one just barely across it. This is **already**
"confidence depends on evidence strength," not an arbitrary score —
the discipline this phase's own prompt asks for is already the real
implementation, re-confirmed by code read, not merely by documentation
claim.

**Recommended for the aggregate `MarketIntelligenceSnapshot.confidence`
(§4)**: a combination of (a) each contributing brain's own
already-real, evidence-derived confidence, (b) `DataQuality.INSUFFICIENT`
propagation — if any input brain reports insufficient data, the
aggregate must not silently ignore that and report high confidence
anyway (each brain's own `DataQuality` enum, confirmed real in
`regime_brain.py`, already carries exactly this signal), and (c),
where `reality_memory`/similarity is available, historical-match
strength as a real, additional evidence input, per this phase's own
suggestion — not a new invented factor, an extension of a real,
existing mechanism (§1's correction).

## Final Recommendation

**A) Ready for adapter implementation — with the exact scope this
audit narrowed, not the broader one Phase 19.0 implied.**

The evidence supports proceeding, not further architecture redesign:
the Reality/Intelligence boundary already holds provably (§2), the
output-layer separation is already correct (§3), and 5 of 8 brains
have a clean or near-clean path to real historical data (§1). What
this audit adds beyond Phase 19.0 is **precision**: a corrected,
evidence-based understanding that `reality_memory` was never fully
disconnected (just narrower than it should be), a concrete list of
seven real adapter requirements (§6) instead of one vague "build an
adapter" instruction, one confirmed wall-clock defect that must be
fixed before any historical replay claim is trustworthy (§5), and an
explicit, evidence-based decision to EXCLUDE `premium_brain`/
`behaviour_brain` from this adapter's scope rather than forcing an
awkward fit. Phase 19.1 can proceed directly against this narrowed,
now-precise scope.
