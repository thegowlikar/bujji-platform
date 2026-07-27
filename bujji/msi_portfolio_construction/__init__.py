"""bujji.msi_portfolio_construction — Portfolio & Risk Construction
(Series 91). Given a Series 90 `TradeConstructionAssessment`, decides
whether the portfolio should ADMIT the trade (approve/reject/defer),
and if approved, the largest defensible position size given available
(replay-estimated) capital and margin. This is a portfolio-admission
gate only -- no broker calls, no order routing, no trade management,
no rolling/adjustments, no historical-performance optimisation of any
kind (enforced structurally by an AST test, mirroring every prior MSI
package)."""
