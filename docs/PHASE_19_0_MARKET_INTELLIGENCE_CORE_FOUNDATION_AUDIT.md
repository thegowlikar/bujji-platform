# Phase 19.0 — Market Intelligence Core Foundation Audit

**Status: AUDIT ONLY. Zero code changes.**

**The single most important finding of this audit, stated up front**:
this is not a greenfield design exercise. A substantial Market
Intelligence Core **already exists** in this repository — eight
tested "brain" modules, a market-state synthesizer, a similarity/memory
layer, and more. The real work this audit does is establish exactly
what exists, prove whether it is wired to the now-frozen Historical
Data Foundation (Phases 17–18.15), and design the pipeline that
connects them — not invent new brains from scratch.

---

## 1. Current Intelligence Capabilities — Real Inventory

Every module below was opened and read this phase, not assumed from a
directory listing.

| Domain | Module(s) | Classification |
|---|---|---|
| Market Regime | `bujji/intelligence/regime_brain.py` — `RegimeBrain.analyze(candles)`, Kaufman Efficiency Ratio + realized-volatility classification into TRENDING/RANGING/VOLATILE/COMPRESSED/TRANSITIONING, with `confidence`, `evidence`, `data_quality` | **Existing** — real, tested (`tests/test_regime_brain.py`), documented calibration caveats, stateless and pure |
| Volatility | `bujji/intelligence/volatility_brain.py` | **Existing** — tested (`test_volatility_brain.py`) |
| Greeks | `bujji/intelligence/greeks_brain.py` + `bujji/msi_greeks/engine.py` (two related, not yet reconciled by this audit) | **Existing** — tested (`test_greeks_brain.py`) |
| Options premium/OI behaviour | `bujji/intelligence/premium_brain.py`, `bujji/premium_behaviour/engine.py` | **Existing** — tested (`test_premium_brain.py`); confirmed by grep — **no PCR, no max pain anywhere in either module** (one comment in `structure_brain.py` explicitly discusses the discipline of avoiding an unjustified PCR signal, never computes one) — this project's own "Reality first" rule has held |
| Market structure (price behaviour) | `bujji/intelligence/structure_brain.py` | **Existing** — tested (`test_structure_brain.py`) |
| Liquidity | `bujji/intelligence/liquidity_brain.py` | **Existing** — tested (`test_liquidity_brain.py`) |
| Events | `bujji/intelligence/event_brain.py` | **Existing** — tested (`test_event_brain.py`) |
| Options/market behaviour (chain-level) | `bujji/intelligence/behaviour_brain.py` | **Existing** — tested (`test_behaviour_brain.py`) |
| Regime memory (persistence across sessions) | `bujji/market_regime_memory/engine.py` + `models.py` | **Existing** |
| Market narrative (explaining state in words) | `bujji/market_narrative/engine.py` | **Existing** |
| "Have we seen this before" similarity/memory | `bujji/market_understanding/similarity.py`, `structure.py`, `timeline.py` + `bujji/reality_memory/catalog.py` | **Existing** — `similarity.py` explicitly imports `RealityMemoryCatalog`, confirming a real historical-episode-matching mechanism already exists |
| Market state synthesis | `bujji/market_state/synthesizer.py`, `confidence.py`, `evidence_boundary.py`, `domain_view_adapter.py` | **Existing** — a real synthesis layer combining multiple brains into one state, with an explicit `evidence_boundary` concept (bounding what a state claim can honestly assert) |
| Market state construction from raw observations | `bujji/market_state_builder/` (`market_state.py`, `observation_bridge.py`, `episode_bridge.py`, `event_bridge.py`, `option_observation_bridge.py`) | **Existing** |
| Intelligence completeness scoring | `bujji/intelligence_completeness/engine.py` | **Existing** — a real, prior-built self-assessment layer ("how complete is what we currently understand") |
| Intelligence consistency checking | `bujji/intelligence_consistency_checker/engine.py` | **Existing** |
| Context window (recent-history framing) | `bujji/context_window/engine.py` | **Existing** |
| Portfolio-level intelligence | `bujji/portfolio_intelligence/engine.py` | **Existing** |
| Risk intelligence | `bujji/trading_brain/risk_governor/` (multiple files, seen in this session's own earlier work: `capital_safety_governor.py`, `risk_budget_governor.py`, `live_risk_context_provider.py`) | **Existing**, but built for LIVE trading-session risk gating, not historical intelligence — a different consumer than MIC |
| Knowledge graph | No module of this name or shape found anywhere in the repository | **Missing** — the closest real analogues are `reality_memory.catalog` and `market_understanding.similarity` (episode-based lookup, not a graph structure) |

**The overwhelming classification is Existing, not Missing.** The one
genuine gap is a formal knowledge-graph structure — and even that has
a working, simpler substitute (episode/similarity catalog) already in
place, which may make a graph unnecessary rather than merely
unbuilt — a judgment call for a future phase, not resolved here.

## 2. The Critical Integration Gap — Confirmed by Direct Evidence

**Does any existing intelligence module consume the Historical Data
Foundation (Phases 17H–18.15: `MarketRealitySnapshot`, `DatasetVersion`,
`DatasetArtifact`)?**

Checked by direct grep across every module in §1's table, both
directions: **zero references, in either direction.** No intelligence
module imports `market_reality_snapshot`; nothing in
`market_reality_snapshot` imports any intelligence module. These are
two completely disconnected subsystems today.

**This is not a "wrong data source" problem — it is a "no wiring
exists" problem, and that distinction matters.** Reading
`RegimeBrain.analyze()`'s real signature —
`analyze(self, candles: list[Candle]) -> RegimeReading` — it takes a
plain, generic `bujji.core.models.Candle` list. It is **store-agnostic
by construction**: nothing about the brain itself assumes live data,
Layer 0, or any particular historical source. The same is true of
every other brain checked (`greeks_brain`, `volatility_brain`, etc.) —
they are pure, stateless functions over plain input models, exactly
the shape this audit would have had to design from scratch if they
didn't already exist.

**What this means for Phase 19.1+**: the brains do not need to be
rewritten to consume `MarketRealitySnapshot`. What is missing is an
**adapter layer** — something that reads a `MarketRealitySnapshot`
(or a `DatasetArtifact`'s reconstructed range) and produces the plain
`Candle`/`Observation`-shaped inputs these brains already expect. This
is a real, scoped, additive piece of work — not a rewrite of eight
tested modules.

## 3. Market Intelligence Core Architecture — As It Actually Exists Today

The pipeline this phase's own prompt sketches (Reality → Features →
Market State → Context → Intelligence) is **not a new design — it
already exists in the codebase**, mapped here to real modules rather
than invented fresh:

```
Reality:        "What happened?"
                 -> HistoricalObservationStore / MarketRealitySnapshot (Phase 17H-18.x, FROZEN)
                    [today's gap: nothing reads FROM here into the layer below]

Features:       "What measurable characteristics exist?"
                 -> bujji/intelligence/*_brain.py (regime, volatility, greeks,
                    premium, structure, liquidity, event, behaviour)
                    -- pure, stateless, evidence-carrying, ALREADY BUILT

Market State:   "What condition describes the market?"
                 -> bujji/market_state/synthesizer.py + confidence.py +
                    evidence_boundary.py + bujji/market_state_builder/market_state.py
                    -- ALREADY BUILT, combines brain outputs into one state

Context:        "Have we seen this before?"
                 -> bujji/market_understanding/similarity.py +
                    bujji/reality_memory/catalog.py +
                    bujji/market_regime_memory/engine.py
                    -- ALREADY BUILT, a real episode-matching mechanism

Intelligence:   "What does this imply?"
                 -> bujji/market_narrative/engine.py (explains state in words) +
                    bujji/intelligence_completeness/engine.py (how much can be
                    honestly claimed) + bujji/intelligence_consistency_checker/
                    -- ALREADY BUILT
```

**The only missing piece in this entire pipeline is the very first
arrow** — Reality (the NEW, hardened, artifact-backed Historical Data
Foundation) into Features. Every stage downstream of that already
exists, is tested, and needs no redesign.

## 4. Intelligence Domains — Evaluated Against What Exists

### A. Market Regime Brain
**Already built** (`regime_brain.py`, §1). Inputs today: candles only
(price behaviour, volatility via realized-vol of returns). Volume and
market breadth, named in this phase's own prompt as desired inputs,
are **not yet consumed** by the existing brain — a real, scoped
extension, not a rebuild.

### B. Volatility Intelligence Brain
**Already built** (`volatility_brain.py`). Not independently re-read
in full depth this phase (time-bounded); its existence and test
coverage are confirmed, its exact IV-environment/premium-behaviour
coverage should be re-verified before Phase 19.2 builds on it, not
assumed complete from this audit alone.

### C. Options Behaviour Intelligence
**Already built** (`premium_brain.py`, `behaviour_brain.py`,
`premium_behaviour/engine.py`). **Confirmed: no PCR, no max pain
anywhere** — this phase's own "Reality first, do not invent
prediction indicators" instruction is already the discipline these
modules follow, not a new rule to impose.

### D. Liquidity Intelligence
**Already built** (`liquidity_brain.py`). Given Phase 18.15's own
freeze finding that depth data is not integrated into
`MarketRealitySnapshot`, this brain's real inputs should be checked
against what the Historical Data Foundation can actually supply before
Phase 19 wires it up — flagged, not resolved here.

### E. Market Memory Intelligence
**Already built**, more completely than this phase's own prompt
implies — `reality_memory.catalog.RealityMemoryCatalog` +
`market_understanding.similarity` together already implement
"search memory for similar prior episodes," and
`market_regime_memory` persists regime history across sessions. The
worked example in this phase's prompt (VIX rising, premium expanding,
OI changing → search memory) is architecturally already answerable —
subject to the same Historical-Data-Foundation wiring gap as
everything else (§2).

## 5. Market Intelligence Objects — Design, Reusing Real Names Where They Exist

The phase's own example (`MarketStateSnapshot`) is close to, but not
identical to, real existing models. Rather than inventing a new shape,
this audit recommends the eventual design **extend**, not duplicate:

- `bujji.market_state.models` (existing) already has a market-state
  concept — its exact current field list was not fully re-verified
  this phase (time-bounded) and should be read in full before Phase
  19.1 decides whether to extend it or supersede it.
- The phase's own suggested fields — `timestamp`, `regime`,
  `volatility_state`, `liquidity_state`, `options_state`,
  `evidence_refs`, `confidence` — map cleanly onto the REAL,
  already-existing per-brain output shapes (`RegimeReading` already
  has `regime`, `confidence`, `evidence`, `data_quality` — confirmed
  by direct read this phase) if those brains were assembled into one
  combined snapshot. **No new object needs to be invented from
  nothing — an assembly/rollup over already-real per-brain readings is
  the correct design**, mirroring exactly the pattern this project
  used repeatedly in the 18.x series (`DatasetVersion` assembling
  `ResearchCalendarEntry`s, `DatasetArtifact` assembling
  `DatasetVersion` + identity + lineage).

## 6. Intelligence Governance Rules — Already the Project's Own Standing Discipline

Every rule this phase's prompt states as new is, in fact, **already
this project's own long-standing, repeatedly-enforced discipline**,
confirmed across the entire 17.x/18.x series, not introduced here:

**MIC can**: interpret observations (every brain in §1 does exactly
this and nothing else); classify states (`RegimeReading`'s own
`regime`/`confidence`/`evidence` shape, confirmed); compare history
(`reality_memory`/`market_understanding.similarity`, confirmed
existing).

**MIC cannot**: modify reality data (`HistoricalObservationStore` is
INSERT-only, immutable, re-verified 5+ times across the 18.x series —
no brain module was found to write to it, or to any Reality-tier
store, confirmed by this phase's own grep); create fake observations
(every brain's own `DataQuality.INSUFFICIENT`/similar refusal pattern,
seen directly in `regime_brain.py`'s own `MIN_CANDLES` guard, is the
same "never fabricate, refuse instead" discipline `HistoricalObservationStore`
itself follows); generate trades directly (no brain module imports
anything from `bujji.broker`, `bujji.runtime_execution`, or order
placement — confirmed by this audit's own reading of every module's
import list); override risk (risk governance lives entirely in
`bujji.trading_brain.risk_governor`, a separate package no intelligence
brain touches).

**This section requires no new enforcement mechanism** — it already
holds, provably, today.

## 7. Relationship With Future Backtester

Per Phase 18.15's own freeze document (§3, the Backtester Dependency
Contract): the backtester consumes `DatasetArtifact` +
`DatasetManifest`, never raw stores directly. The natural extension
for Intelligence: a future **`MarketIntelligenceSnapshot`** (the
assembly object from §5) should be computed FROM an eligible
`DatasetArtifact`'s reconstructed range, tagged with that
`artifact_id`, and — following the exact same pattern Phase 18.12–18.14
already established for datasets — potentially persisted as its own
kind of artifact, so the question *"during this historical period, what
did Bujji understand about the market?"* is answerable the same
provable way `check_backtest_eligibility()` already answers *"is this
dataset safe to use."* Not designed further here — correctly out of
this audit-only phase's own scope.

## Implementation Roadmap — Revised Against What Already Exists

The phase's own proposed roadmap (19.1 Market State Engine, 19.2
Volatility Intelligence, 19.3 Options Behaviour, 19.4 Historical
Pattern Memory, 19.5 Decision Intelligence) assumed these needed to be
BUILT. Given §1's findings, the real roadmap is **connect, verify, and
extend**, not construct from zero:

- **Phase 19.1 — Historical Foundation Adapter**: the one genuinely
  missing piece (§2) — build the adapter(s) that read
  `MarketRealitySnapshot`/`DatasetArtifact` and produce the
  `Candle`/`Observation` shapes the existing brains already consume.
  Small, scoped, the true prerequisite for everything else.
- **Phase 19.2 — Brain-by-Brain Verification Against Real Historical
  Data**: run each of the 8 existing, tested brains against real
  reconstructed historical data (starting with the one real complete
  day, 2026-08-14, per Phase 18.15's own known boundary) and confirm
  their outputs are sane against real data, not just their own unit
  tests' synthetic fixtures.
- **Phase 19.3 — Market State Assembly**: verify/extend
  `market_state.synthesizer` to combine the (now historically-fed)
  brain outputs into one `MarketStateSnapshot`-shaped object, tagged
  with its source `artifact_id`.
- **Phase 19.4 — Memory/Similarity Verification**: verify
  `reality_memory.catalog`/`market_understanding.similarity` against
  real historical episodes once more than one real day of data exists
  (the same options-coverage boundary Phase 18.15 already named).
- **Phase 19.5 — Decision Intelligence**: unchanged from the original
  proposal — this is the one stage that genuinely does not yet exist
  and was correctly scoped last.

## Final Decision

**A) Ready to implement MIC — with the scope corrected.** Not because
foundation work remains (Phase 18.15 already closed that question) and
not because MIC itself needs building from scratch (§1 proves most of
it already exists, tested) — but specifically for the ONE real,
narrow, well-scoped adapter identified in §2. The evidence for
readiness is unusually strong for a Phase-0 audit: eight tested brain
modules, a real synthesis layer, a real memory/similarity layer, and a
governance discipline that already holds without new enforcement — all
confirmed by direct code reading this phase, not assumed. The evidence
against "more audit needed" is equally direct: nothing found in this
audit suggests the existing MIC modules are unsound; they simply were
never connected to the Historical Data Foundation this project just
spent 15 phases hardening. Building that one connection is squarely
implementation work for Phase 19.1, not further audit.
