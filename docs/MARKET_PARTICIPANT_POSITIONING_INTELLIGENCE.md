# Market Participant Positioning Intelligence (MPPI v1)
## BUJJI Engineering Series 86

**Status:** Real, implemented, tested. Answers only "what are option
market participants collectively signalling through their
positioning?" — never strategy, strike, or expiry choice.

---

## 1. Deliverable 1 — Investigation findings (real, no assumptions)

| Stage | Status | Evidence |
|---|---|---|
| Live option chain / historical option chain | **Already exists** | `bujji/options_observation/runner.py::ingest_all_option_series_from_bhavcopy` — real NSE Bhavcopy ingestion, Series 73C. |
| Option Observation | **Already exists** | `bujji/options_observation/models.py::OptionObservation` — wraps a real `bujji.market_observation.models.Observation`; `.open_interest`/`.change_in_open_interest`/`.underlying_price` are real, populated fields from Bhavcopy. |
| OI transport (Series 64-68 legacy) | **Superseded by 73C** | Series 73C is the deterministic, replay-safe successor to the older `OptionLiquiditySnapshot` (Series 64/65) transport this project built earlier — 73C is what this sprint builds on, not the older transport. |
| Legacy Structure Brain (`bujji/intelligence/structure_brain.py`) | **Partially exists, not directly reusable** | Real, working PCR and wall-proximity computation against LIVE data — but calls `now_ist()` internally (wall-clock read), so it is not a pure/deterministic function and cannot be imported into this replay-safe arc without breaking determinism. Its TECHNIQUE was reimplemented as pure functions in this package's `engine.py`; its CODE was not imported. |
| Intraday OI history | **Absent** | Confirmed by direct inspection: Bhavcopy (Series 73C's only real source) is end-of-day only. No intraday OI time series exists anywhere in this codebase. |
| bid/ask/bid-quantity/ask-quantity | **Absent** | Confirmed already-disclosed in Series 73C's own taxonomy: these fields exist in the schema for a future live source but are always `None` from Bhavcopy. No lens in this module depends on them. |
| Replay support | **Already exists, reused as-is** | This module consumes `OptionObservation` tuples directly (mirroring Series 78/79's direct consumption of `Episode`/`MarketEvent`) — no new replay infrastructure was built. |

## 2. The five lenses, and what each genuinely needs

| Lens | Needs | Confidence ceiling | Why |
|---|---|---|---|
| A — Put/Call OI Ratio | One chain snapshot | MODERATE | Aggregate OI alone cannot distinguish buyer vs. writer — same disclosed caution as legacy `structure_brain.py`'s own PCR treatment. |
| B — OI Concentration (wall proximity) | One chain snapshot + real spot price | MODERATE | Reimplements `structure_brain.py`'s proximity technique as a pure function; same threshold (`NEAR_WALL_THRESHOLD_PCT = 0.25`, "roughly one NIFTY strike-width," disclosed as a first-pass, non-statistically-calibrated sizing). |
| C — OI Migration | TWO chain snapshots | LOW | Daily resolution only — no intraday OI history exists. Honestly `UNKNOWN` when only one snapshot is supplied, not a fabricated estimate. |
| D — OI Expansion/Contraction | TWO chain snapshots | N/A (no directional vote) | This lens has **no directional valence of its own** — it always reports `NEUTRAL_POSITIONING` and instead informs `positioning_strength`, a genuinely different concept from confidence. |
| E — Writer Dominance | One chain snapshot | MODERATE | Uses `change_in_open_interest`, a real single-snapshot field — needs no history. |

## 3. `positioning_bias` vs. `positioning_strength` vs. lens `confidence`

Three genuinely different questions, kept genuinely separate:
- **`confidence`** (per lens): how sure is THIS lens in its own reading.
- **`positioning_bias`** (overall): the reconciled direction across all lenses — `BULLISH_POSITIONING` / `BEARISH_POSITIONING` / `NEUTRAL_POSITIONING` (lenses agree there's no bias) / `MIXED_POSITIONING` (lenses genuinely disagree — never averaged) / `UNKNOWN_POSITIONING` (no lens could form any opinion — absence of signal, not agreement or disagreement). Mirrors Series 85 (MDI)'s own proven three-way discipline, restated independently for this package.
- **`positioning_strength`**: how much participation/conviction is behind whatever bias exists, driven primarily by Lens D's expansion/contraction read — a `STRONG` reading with a `NEUTRAL_POSITIONING` bias is a real, meaningful, honest combination (heavy participation, genuinely no directional lean), not a contradiction.

## 4. Reconciliation and contradiction preservation

`reconcile_lenses` mirrors `bujji.msi_market_direction.engine.reconcile_lenses` exactly: filter to opinionated lenses, if any split bullish/bearish → `MIXED_POSITIONING` with `conflicting_lenses` populated (never averaged, never suppressed), otherwise the averaged signed rank of agreeing lenses. Proven via a real, non-fabricated scenario
(`test_mixed_when_lenses_genuinely_disagree`): far-OTM put OI driving PCR bullish while a near-spot call wall with dominant call-writer activity drives Concentration/Writer-Dominance bearish — a genuine, realistic disagreement, plus a direct, unambiguous constructed-opinion test proving the mechanism unconditionally.

## 5. Relationship to MDI (Series 85) — Deliverable 7

**MDI was not modified.** `test_deliverable_7_mppi_adapts_into_an_mdi_style_lens_opinion` proves, with a synthetic scenario only, that an `MarketParticipantPositioningAssessment` can be translated into a real `bujji.msi_market_direction.models.LensOpinion` using the already-reserved `mdi_taxonomy.OPTIONS_POSITIONING_DIRECTION` lens name (reserved by Series 85 itself, unused until now) — via a disclosed, documented translation mapping (MPPI's 3-way bullish/bearish/neutral + MIXED/UNKNOWN maps directly onto MDI's corresponding plain-band values; MPPI does not claim MDI's STRONG/WEAK sub-bands, since it has no basis to assert that granularity). This is a proof of compatibility, not production wiring — no runtime code path connects the two packages.

## 6. Replay/live parity

Same framing as Series 85's own resolved precedent: the real input shape is a single `(current_chain, optional previous_chain)` pair, not a growing sequence. `runner.assess_positioning` (batch) and `runner.ParticipantPositioningStream.process` (streaming) both delegate to the identical `engine.assess_participant_positioning` function — proven byte-identical by `test_batch_vs_streaming_parity`.

## 7. What MPPI explicitly does NOT infer

- Does not infer whether OI represents a buyer or a writer beyond the disclosed, capped-confidence convention already used by this project's own legacy `structure_brain.py`.
- Does not infer intraday positioning shifts — genuinely absent data, reported as `UNKNOWN`, never approximated.
- Does not infer anything from bid/ask/bid-quantity/ask-quantity — always `None` from Bhavcopy, no lens touches them.
- Does not recommend a strategy, strike, or expiry, and does not estimate probability of profit or predict returns.

## 8. Tests

`tests/test_msi_participant_positioning_intelligence.py` — **19 tests**, all passing: deterministic IDs, batch/streaming parity (including automatic previous-chain carry-forward in streaming mode), real lens derivation from genuine `OptionObservation` data (bullish-leaning and bearish-leaning chains, insufficient-data → `UNKNOWN` for every lens), migration/expansion honestly `UNKNOWN` without a previous snapshot, migration computed correctly with two snapshots, contradiction preservation (both a realistic scenario and a direct unambiguous constructed-opinion test), evidence lineage, the Deliverable 7 MDI-adapter proof, serialization round-trip, append-only journal, query helpers, and AST isolation (no `mic_v2`/Runtime/Trading Brain/Strategy Selector/FYERS-SDK/legacy-`intelligence`/sibling-MSI-package imports, no `uuid4`, no unseeded randomness, no strategy/strike/execution identifiers).

Full BUJJI suite after this sprint: **2411 passed, zero regressions** (2392 baseline + 19 new).

## 9. Deliverable 10 — Readiness Report (honest, not overstated)

**Positioning concepts remaining unavailable:**
- Any positioning read finer than daily resolution.
- Any read distinguishing new-money buying from writing with confidence (would need order-flow or tick-level data, not chain snapshots).
- Any futures-positioning cross-check (this sprint is options-only, per its own scope).

**Which require intraday OI history:** Lens C (Migration) and Lens D (Expansion/Contraction) would both become materially more useful at intraday resolution — currently capped at daily, which is a real but coarse signal.

**Which require bid/ask:** none of the five lenses currently implemented depend on bid/ask — but a future "OI Concentration Quality" refinement (distinguishing a wall built on liquid vs. illiquid strikes) would need it, and it remains absent.

**Which require futures positioning:** none of MPPI's five lenses use futures data by design (this sprint is scoped to options only) — a genuine Futures Positioning Direction lens (already reserved in `mdi_taxonomy.FUTURES_POSITIONING_DIRECTION`) remains a separate, unbuilt future sprint.

**Which require external data:** FII/DII flow data (referenced in an earlier session-level audit as a real gap) is not available from FYERS or Bhavcopy at all — would need NSE's separately-published FII/DII derivatives statistics or a paid data vendor, entirely outside this sprint's scope.

**Recommended next engineering step:** Wire this lens (and MDI itself) into Consensus/Decision Synthesis as a real, non-mock third domain input — this is the first genuinely independent lens Series 85's own readiness report called for, and it is now real, tested, and proven adaptable. The next honest step is proving the combination (Price Structure + Market Structure + Participant Positioning, reconciled through MDI, then through Consensus) against the real 41/81-day historical corpus, per the standing recommendation from `docs/BUJJI_DESK_ROADMAP_DECISION.md` — not adding a sixth lens before this one has been validated end-to-end.
