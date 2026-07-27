"""bujji.msi_execution_planning — Execution Planning Engine (Series 98).

Sits after Margin Bridge, before a future real order-placement layer:

    ... -> Margin Bridge -> Execution Planning -> (future) Order Placement

Transforms a real, constructed position (Series 90) into a complete,
deterministic EXECUTION PLAN -- order sequencing, a dependency graph
between stages, validation gates, and failure/recovery/rollback/
timeout policies. This package NEVER calls a broker, NEVER places an
order, and NEVER simulates a fill -- it only produces the plan a real
execution layer would follow. Sequencing philosophy and failure/
recovery concepts are deliberately modeled on the REAL, already-working
production `bujji.execution.engine.ExecutionEngine` (idempotent
placement, verify-before-retry, truthful partial-fill handling,
timeout-then-cancel, reconcile-on-reconnect) -- reused BY PATTERN, not
by code, since that engine is async/live/broker-coupled and this
package must remain a pure, deterministic replay artifact."""
