"""bujji.msi_shadow_trading — Shadow Trading Engine (Series 100).

Sits at the very end of the decision chain, an EXECUTION ENVIRONMENT,
not a decision engine:

    ... -> Decision Auditor -> Shadow Trading (this package)

For every real day Decision Auditor recorded a `TRADE_APPROVED`
decision, this package creates a `ShadowPosition` using real entry
prices (Series 90's own real `TradeConstructionAssessment`), tracks it
day-over-day by REUSING Position Lifecycle's own `assess_position_lifecycle`
directly (never re-implementing lifecycle logic), reprices it against
REAL same-contract settlement premiums on later real Bhavcopy days
(never a synthetic or estimated mark), and closes it using exactly the
same lifecycle states production will eventually use (never a
shadow-only exit rule). No broker order is ever placed; no
paper-trading API is ever called; no capital is ever at risk."""
