"""bujji.msi_strategy_expression — Strategy Expression Engine (Series 93).

Sits BETWEEN Trade Thesis (Series 92) and Strategy Selection Foundation
(Series 87) / Strategy Selector (Series 89):

    ... -> Trade Thesis -> Strategy Expression -> Strategy Selection -> Trade Construction

Translates a validated market thesis into the EXPOSURE CHARACTERISTICS a
position should have (directional/delta-neutral, long/short volatility,
defined/undefined risk, theta sign, convexity sign) -- never a strike,
never an expiry, never a concrete strategy choice, never a profitability
score. Strategy Selection Foundation and the Strategy Selector remain
completely unmodified; this package is consumed by Selector integration
additively (Deliverable 5), never by redesigning either."""
