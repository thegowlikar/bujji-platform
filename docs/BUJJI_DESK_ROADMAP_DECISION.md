# BUJJI Desk Roadmap Decision
## The reasoning-stack-vs-live-system question, made explicit

**Status:** A decision document, not an engineering series. No code changes. Purpose: force the implicit question this project has deferred across Series 73A–83 into an explicit, written decision, so the next series can be scoped against a real answer instead of continuing to add reasoning layers on top of an unresolved premise.

---

## 1. The question that has been implicitly deferred

Every series from 73A through 83 has built toward "BUJJI should eventually reason about the market like a desk." None of them have asked: **when this reasoning stack is ready, does it replace the live hardcoded straddle bot, run alongside it, or stay a research system that never touches live capital?**

This is not a detail to leave for later. It changes what "done" means for every remaining series:

- If the stack will **replace** the live bot: Strategy Selection, Strike Selection, Position Construction, and Execution Planning need to be built to the same rigor as the capital/margin engine already has (fail-closed on uncertainty, real broker verification, no guessing) — because they'll eventually place real orders.
- If the stack will **run alongside** the live bot (shadow mode, informing a human, or feeding a second, smaller live allocation): the bar is lower short-term, but a real decision is needed about *when* and *how* it graduates from shadow to live, or it shadows forever.
- If the stack is a **research system only**: several remaining series (Strike Selection, Execution Planning, live order-side work) may not be worth building at production rigor at all, and effort should redirect toward validating the reasoning against real historical outcomes instead.

## 2. What's actually built, and what it proves

| Layer | Series | What it proves |
|---|---|---|
| Sensing (Observation → Event → Episode) | 73A–76 | Deterministic, replay/live-parity-proven transport of market facts. |
| Decision Synthesis | 77 | Multiple domain signals fuse into one opportunity read, contradictions preserved, never averaged away. |
| Price Structure, Market Structure | 78, 79 | Two orthogonal, genuinely reasoning brains — proven to compose with 77 and each other, byte-identical on replay. |
| Consensus | 81 | Measures whether the brains actually agree — a real, separate signal from "the opportunity read looks confident." |
| Strategy Eligibility | 82 | Narrows to a *set* of admissible strategy families, using Consensus in a way Decision Synthesis alone structurally cannot. |
| Trade Intent | 83 | Converts one (placeholder-selected) family into described exposure/bias/invalidation conditions. |

This is a real, working, well-tested pipeline as far as it goes. The engineering discipline (determinism, replay/live parity, contradiction-preservation, evidence lineage, honest `None`-not-fabricated behavior) has been consistently enforced series over series — that's genuinely hard to fake and hasn't been faked.

## 3. What's missing, and why it's not a "next series" problem but a design problem

Three gaps surfaced during this arc are not simply "not built yet" — they are premises the later series were quietly built on top of without being true:

1. **No directional lean anywhere in the stack.** Confirmed directly by Series 83: neither `MarketOpportunityAssessment` nor `ConsensusAssessment` retains a bullish/bearish signal. `opportunity_state` describes a *type* of opportunity, never a *direction*. This means `market_bias` in Trade Intent is currently always `DELTA_NEUTRAL` by honest default, not by market reality. **Strike Selection cannot be built at all until this is fixed** — you cannot choose a put spread vs. a call spread without a direction.
2. **Volatility Structure (a planned "Series 80") was never built.** Every integration proof since Series 81 has used an explicitly-disclosed mock. Real IV/Greeks computation already exists and works correctly (`intelligence/volatility_brain.py`, `intelligence/greeks_brain.py`) — but it's a different, disconnected codebase from the MSI arc, observation-only, feeding a dashboard nobody's strategy consults.
3. **"Strategy Selection" was referenced in Series 83's own task spec as an existing prior stage. It does not exist.** Series 82 (Eligibility) only narrows to a set; nothing picks one for real. A dormant, unrelated `bujji/trading_brain/strategy_selector/` module exists from an earlier, separate arc, explicitly built with *no scoring* by design ("it never asks which would make the most money — that question is forbidden") — which itself was a deliberate choice for a different purpose (qualification/replay), not one made for this MSI arc.

None of these are "the next series will fix it" gaps — they are structural holes that make everything currently downstream of them (Trade Intent's `market_bias`, any future Strike Selection) either honestly-neutral placeholders or impossible to build at all.

## 4. Recommendation

**Decide explicitly now:** this reasoning stack's near-term purpose is to become the actual decision-making system that eventually replaces the hardcoded straddle bot — not a permanent shadow/research artifact — *provisionally*, subject to the reasoning proving itself against real historical outcomes before any live cutover. This is the reading most consistent with the effort invested and the "quant desk" framing, but it should be treated as adopted policy from this point forward, not re-derived implicitly every few series.

**Given that, the priority order for what comes next, ahead of any further MSI-brain series:**

1. **Add a real directional-lean field**, sourced honestly from what already exists — Price Structure's `trend_state` and Market Structure's `structure_location` already contain directional information; the gap is that nothing aggregates and *retains* it through Decision Synthesis/Consensus. This is a schema fix to 77/78/79's outputs (additive field, no redesign), not a new brain.
2. **Bridge, don't rebuild, Volatility Structure.** The real IV/Greeks engine already exists and is correct (`intelligence/volatility_brain.py`, `greeks_brain.py`). Building a from-scratch "Series 80" duplicates real, working code. The right move is a thin adapter translating `intelligence/`'s real live output into an MSI-shaped `VolatilityStructureAssessment`, not a parallel reimplementation.
3. **Build a real Strategy Selector** — with actual scoring, now that real IV/Greeks/direction data will exist to score against — replacing Series 83's disclosed placeholder. This is the first place genuine ranking/optimization logic is appropriate in this arc, and it should explicitly use the real Greeks engine, since scoring "which strategy family fits" without volatility/Greeks input would be hollow.
4. Only after 1–3: Strike Selection, Position Construction, Execution Planning — each held to the capital engine's existing bar (fail-closed, real broker data, no guessing).
5. **Before any live cutover of any piece of this stack**, run it through the same historical-corpus discipline this project already has muscle memory for (Series 65–70's real-data-only, measure-before-optimize standard) — a full backtest of the MSI stack's would-have-been decisions against the real 41/81-day corpora, compared honestly against the live bot's actual results, before anything in this stack is trusted with capital.

**What I'd explicitly NOT do next:** build another descriptive MSI brain (Liquidity, Time Structure, Cross-Asset) before closing the three gaps above — they'd be adding more inputs to a pipeline that still can't tell you which direction to trade or score its own strategy choice.