"""bujji.msi_margin_bridge — Margin Bridge & Capital Fidelity (Series 97).

A deterministic margin-estimation bridge, sitting alongside (not
replacing) Portfolio Construction's own flat replay estimate:

    Trade Construction (real premiums/legs) -> Margin Bridge -> MarginEstimate
    Production: live broker MarginRequirement -> Margin Bridge -> MarginEstimate

ONE public function, `estimate_margin`, exposes the exact same
interface in both environments -- given a real `MarginRequirement`
(production, already fetched live elsewhere), it wraps it; given none
(replay), it computes a deterministic estimate from Series 90's own
real `TradeConstructionAssessment` (legs, premiums, risk profile) --
never calling a broker itself, never duplicating
`bujji.capital.engine.compute_lot_sizing`'s sizing arithmetic (this
package only produces a PER-LOT margin figure; lot sizing remains
Portfolio Construction's job, unchanged). This package does not modify
Portfolio Construction or Position Lifecycle -- it is a new,
independent capital-fidelity improvement, callable alongside them.
"""
