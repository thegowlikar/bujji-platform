"""Multi-Domain Consensus Intelligence (MDCI) — BUJJI Engineering
Series 81.

Lives at `bujji/msi_consensus/`, outside `mic_v2`, `bujji.mic_replay`,
`bujji.production_runtime`, `bujji.trading_brain`,
`bujji.strategy_selector`, and `fyers_apiv3` — same isolation
discipline as every prior series in this arc (73A/73B/73C/74/75/76/
77/78/79). Additionally, and specifically to this package's own
design mandate, it imports NOTHING from `bujji.msi_price_structure`,
`bujji.msi_market_structure`, or `bujji.msi_decision_synthesis` —
MDCI must stay genuinely domain-count/domain-type agnostic (see
`engine.py`'s module docstring and `docs/MSI_CONSENSUS.md` Section 2),
so it never imports a specific brain's real model types. Callers
translate real brain assessments into this package's own generic
`engine.DomainAssessmentView` shape, exactly as Series 79's
Deliverable-10-equivalent demonstration translated
`PriceStructureAssessment`/`MarketStructureAssessment` into Series 77's
`DomainSignal` — that translation lives ONLY in the calling
test/demonstration code, never inside this package.

MDCI sits BETWEEN the MSI brains (Series 78, 79, and any future
brains, e.g. a not-yet-built Series 80 Volatility Structure brain —
see `docs/MSI_CONSENSUS.md` Section "Known limitations" for the
explicit disclosure that Series 80 does not exist yet) and Series 77
Decision Synthesis. It does NOT reinterpret any brain's conclusions.
It measures how coherent/agreeing/well-evidenced the COLLECTION of
brain outputs is, as a distinct quality signal — orthogonal to,
and computed independently of, Series 77's own `confidence_level`/
`opportunity_quality` (see `docs/MSI_CONSENSUS.md` Section
"Decision boundaries" for the full non-overlap reasoning).

This sprint does NOT modify `bujji.msi_decision_synthesis` to consume
`ConsensusAssessment` — that wiring, if ever done, is future work. This
sprint only proves a `ConsensusAssessment` can be computed alongside a
`MarketOpportunityAssessment` from the same underlying domain views,
without touching Series 77's code at all.

Purely descriptive/measurement. No strategy, strike,
direction-prediction, or probability-of-profit vocabulary anywhere in
this package.
"""
