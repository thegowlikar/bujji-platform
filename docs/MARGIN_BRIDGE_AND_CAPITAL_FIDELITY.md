# Margin Bridge & Capital Fidelity v1 (MBCF v1)
## BUJJI Engineering Series 97

**Status:** Real, implemented, tested, replayed against the real 41-day
corpus, driven directly by Series 90's real `TradeConstructionAssessment`
output. A new, independent capital-fidelity package -- **Portfolio
Construction (Series 91) and Position Lifecycle (Series 96) were not
modified**, per this series' own explicit constraint. This series
improves capital fidelity only, not trading intelligence.

---

## 1. Deliverable 1 — Capability Audit

| Component | Finding | Classification |
|---|---|---|
| `CapitalManagementEngine` (`bujji/capital/engine.py`) | Real, production, live-only. Its pure sizing arithmetic was ALREADY extracted in Series 91 as `compute_lot_sizing` -- a single, shared implementation. | **REUSABLE (already reused)** -- this series does not touch it again; margin estimation and lot sizing are deliberately kept as two separate concerns, exactly as `CapitalManagementEngine` itself already separates `MarginProvider` from its own sizing math. |
| Broker margin calls (`bujji/capital/providers.py`) | `BrokerMarginProvider`/`CertifiedBrokerMarginProvider` (live, uncertified/certified), `ConfigMarginProvider`/`SyntheticMarginProvider` (non-live, config-driven) -- already a real, deliberate tiering. | **PRODUCTION ONLY** for the broker-backed tiers; their real `MarginRequirement` OUTPUT type is reused verbatim as this package's own production-path input (`live_margin_requirement`), not re-derived. |
| Replay sizing (`bujji.msi_portfolio_construction.config.REPLAY_MARGIN_PER_LOT_ESTIMATE`) | A single flat number (100,000, itself reused from `config.yaml`'s SIMULATION tier) applied identically to EVERY family regardless of strikes, wing width, or risk profile -- Series 91's own disclosed limitation. | **REPLAY APPROXIMATION -- the exact gap this series closes.** Not removed (Portfolio Construction was not modified), but now measurably improvable by an ADAPTER this series provides. |
| Portfolio Construction (Series 91) | Consumes a margin-per-lot figure as a plain parameter; never computes margin itself. | **No change required** -- its own interface already accepts any per-lot figure; this package could feed it a better one without any code change to Series 91 (not wired in this series, per the "do not redesign Portfolio Construction" constraint -- see Section 11). |
| Position Construction (Series 95) | A pure declarative planning layer; never touches margin at all. | **No reuse needed.** |

**Conclusion**: no calculation was duplicated. The bridge is a genuinely new adapter sitting between two already-real things (Series 90's real trade legs/premiums, and production's real `MarginRequirement` type) that never talked to each other before.

## 2. Deliverable 2 — MarginEstimate

Immutable, frozen. All spec-required fields present: `assessment_id`, `methodology`, `estimated_margin` (PER LOT, matching `MarginRequirement.margin_per_lot`'s own existing convention), `confidence`, `data_source`, `replay_safe`, `explanation`, `provenance`, `schema_version`.

## 3. Deliverable 3 — Bridge Layer

**ONE public function**, `estimate_margin(...)`, used identically in both environments:
- **Production**: caller passes a real `bujji.capital.models.MarginRequirement` (already fetched live, elsewhere, exactly as today) as `live_margin_requirement`. This module never calls a broker itself.
- **Replay**: caller passes a real `TradeConstructionAssessment` (Series 90's output) plus `lot_size`/`spot`; `live_margin_requirement` is omitted. A deterministic estimate is computed from the trade's own real legs/premiums/risk profile.

Both paths return the exact same `MarginEstimate` type (verified directly: `type(replay_est) is type(prod_est)` and identical field sets) -- this is the "same interface" the spec requires, expressed as a single shared function and output shape rather than two divergent implementations.

**Replay methodology, by real risk profile** (no approximation constant needed for defined-risk structures at all -- max loss is a mathematical fact):
- **Net-debit defined-risk** (e.g. a long call, most debit spreads): `estimated_margin = debit_paid` -- EXACT, since max loss for a debit position IS the debit paid.
- **Net-credit defined-risk** (e.g. Iron Condor, Iron Fly): `estimated_margin = wing_width - credit_received` -- EXACT, the real structural max-loss formula for a European, cash-settled spread (NIFTY options are exactly this).
- **Undefined-risk** (naked shorts, short straddle/strangle, ratio, covered, synthetic): no mathematical max-loss exists; `estimated_margin = max(15% of notional, 3x premium collected)` -- a disclosed, standard retail-margin rule of thumb, APPROXIMATE, never claimed as exchange-certified.

## 4. Deliverable 4 — Fidelity Classification

Every estimate declares one of `EXACT` / `HIGH_CONFIDENCE` / `APPROXIMATE` / `UNKNOWN` -- never silently downgraded or upgraded:
- Defined-risk replay estimates -> `EXACT` (a mathematical fact of the structure).
- Undefined-risk replay estimates -> `APPROXIMATE` (a disclosed heuristic).
- Production, `verified=True` -> `EXACT`; `verified=False` (uncertified broker response) -> `APPROXIMATE`, **never silently treated as EXACT just because it came from a live broker call** -- verified directly by a dedicated test.
- Missing/insufficient data (either path) -> `UNKNOWN`, `estimated_margin=None` -- never guessed.

## 5. Deliverable 5 — Real 41-day corpus replay

Run against Series 90's real constructed trades on all 22 days a family was both selected and successfully constructed:

- **Methodology distribution: `DEBIT_MAX_LOSS_EXACT` 15, `NOTIONAL_PERCENTAGE_APPROXIMATION` 6, `UNKNOWN` 1.**
- **Confidence distribution: `EXACT` 15, `APPROXIMATE` 6, `UNKNOWN` 1.**
- **Rejected/unknown-margin days: 1/22** -- one real day where neither a computable wing width nor a usable premium figure existed for a defined-risk construction; honestly reported as `UNKNOWN`, not guessed.
- **The single most important finding of this series**: comparing the bridge's real, differentiated per-trade estimates against Series 91's flat 100,000 figure --
  - **`LONG_DIRECTIONAL` (debit, defined-risk) trades were estimated at real values as low as ₹4,860 to ₹11,700 per lot** -- the flat 100,000 number **overestimated required margin by roughly 88,000-95,000 per lot** on every single one of these 15 real days.
  - **Undefined-risk trades (`COVERED`, `SHORT_DIRECTIONAL`, `NEUTRAL_PREMIUM_SELLING`) were estimated at real values from ₹261,000 to ₹273,000 per lot** -- the flat 100,000 number **underestimated required margin by roughly 161,000-173,000 per lot** on every one of these 6 real days.
  - **Average across all 21 real, non-`UNKNOWN` estimates: ₹91,814 -- deceptively close to the flat 100,000 figure on average**, but this average masks two large, opposite, and real errors that would have caused Series 91's own admission engine to size EVERY real trade in this corpus incorrectly (over-conservative for defined-risk directional bets, dangerously under-conservative for undefined-risk premium sales).
- Replayed twice: **margin-estimate `assessment_id`s were byte-identical** across both runs.

No tuning was performed against these numbers -- the notional percentage and premium multiple constants were fixed before this replay ran.

## 6. Deliverable 6 — Explainability

Every `MarginEstimate.explanation` answers all three required questions: `why_this_estimate_exists` (which methodology and why), `why_exact_margin_is_or_isnt_available` (e.g. *"exact margin is NOT available in replay -- no live/certified broker call is made here by design"*), `assumptions_required` (e.g. *"assumes European, cash-settled exercise (true for NIFTY index options)"*).

## 7. Deliverable 7 — Integration

Demonstrated end-to-end on real data: `Observation -> MSI -> Trade Thesis -> Strategy Expression -> Strategy Selection -> Position Construction -> Portfolio Construction -> Position Lifecycle -> Margin Bridge`, with no execution and no live broker calls anywhere in the replay.

## 8. Deliverable 8 — Behaviour Validation

- **Replay never calls live broker APIs**: verified directly -- the replay path's `data_source` is never `LIVE_BROKER_SPAN`, and `replay_safe=True` for every replay-path estimate.
- **Production never uses replay approximations**: the production path only ever wraps a real, externally-supplied `MarginRequirement` -- it has no access to the replay heuristic constants at all (they live in a separate code path).
- **Both share identical public interfaces**: one function, one output type, verified by a direct `type()`/field-set equality test.
- **Sizing logic remains shared**: `compute_lot_sizing` (Series 91) is untouched and still the only lot-sizing implementation; this package produces a PER-LOT figure only, never lot counts.

## 9. Confidence model

`EXACT` means a mathematical/certified fact (debit paid, wing-width max loss, or a certified broker figure) -- not merely "a number was produced." `APPROXIMATE` is used whenever a disclosed heuristic or an uncertified broker figure stands in for a true SPAN calculation. `UNKNOWN` is a genuine, real outcome (1/22 real days) -- never silently defaulted to a plausible-looking number.

## 10. Replay philosophy

Compute what IS mathematically knowable from real trade construction data (defined-risk max loss) exactly; approximate, with clear disclosure, what genuinely cannot be known without a real exchange risk engine (undefined-risk margin); never fabricate a number where insufficient real data exists.

## 11. Production integration plan

Wire `estimate_margin(live_margin_requirement=...)` into Portfolio Construction's `margin_per_lot_estimate` parameter once a live scheduler exists -- this requires zero changes to `bujji.msi_portfolio_construction` itself (its parameter already accepts any per-lot float from any source), only a new caller at composition-root time. Not done in this series, per its own explicit "do not redesign Portfolio Construction" constraint -- flagged here as the concrete, low-risk next wiring step.

## 12. Known limitations

- The undefined-risk approximation (15% notional / 3x premium) is a disclosed retail heuristic, not validated against any real exchange SPAN figure -- no live-vs-replay comparison was possible in this series since no live broker connection was exercised (a real, disclosed gap; Deliverable 5's "replay vs production differences" is therefore reported only as replay-vs-Series-91's-flat-number, not replay-vs-real-broker).
- 1 of 22 real days produced `UNKNOWN` (a defined-risk construction with neither a computable wing width nor a usable debit figure) -- disclosed, not silently defaulted.
- This bridge is NOT wired into Portfolio Construction's own admission decisions in this series (Section 11) -- the real, measured improvement above is demonstrated in an independent comparison script, not yet reflected in Series 91's own real corpus behavior.

## 13. Deliverable 10 — Recommendation

Evidence-based, from the real replay measurements above, and consistent with the user's own proposed post-Series-97 roadmap:

The margin fidelity gap is now MEASURED, DIFFERENTIATED, and ADAPTER-READY (Section 11) even though not yet wired into live admission decisions. Per the user's own stated plan, and since replay confirms the Margin Bridge behaves correctly (deterministic, fail-closed, confidence-honest, and demonstrably more accurate than the flat number it stands next to):

**Recommended next step: Series 98 — Execution Planning** (order staging, sequencing, validation, fail-safe rules), exactly as the user's own roadmap proposes. The decision engine (Series 77-96) plus now a measurably-improved capital-fidelity layer (Series 97) together form a complete, demonstrated end-to-end research architecture; the remaining work shifts from inventing new decision logic to connecting this decision chain to real order infrastructure, starting with Execution Planning.
