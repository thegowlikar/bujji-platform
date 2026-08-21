"""Execution Integration Layer -- BUJJI Options OS, Phase-4.

PURPOSE: correlates identities only -- an immutable record linking an
MSI TradeConstructionAssessment's assessment_id to an Execution
Reality TradeLiquidityContext's context_id, by ID string, never by
object reference. This package deliberately does NOT import
`bujji.execution_reality`, `bujji.msi_trade_construction`, or any
other trading/risk/execution system: correlation requires knowing two
ID strings, never the shape of what those IDs identify.

Not to be confused with `bujji/integration/` (a completely different,
unrelated, abandoned "Options OS v3 Series 31-54" generation-2
package, wrapping FyersBroker/ExecutionEngine adapters -- discovered
during this phase's own design review, never reused here).

Phase-4 scope only: one artifact, `TradeIntegrationContext`, and its
construction helper. No LiquidityGate, no MSI wiring, no Governor
wiring -- all explicitly deferred to a future phase.
"""
