"""bujji.msi_decision_auditor — Decision Auditor & Learning Observatory
(Series 99).

BUJJI's permanent black-box flight recorder. Records exactly what
BUJJI believed (a `DecisionRecord`, assembled from the real, already-
computed outputs of every stage from MSI through Execution Planning)
and, separately, what actually happened (an `OutcomeRecord`, from real
same-day/next-day market data), linked deterministically by a
`DecisionOutcomePair`. This package NEVER changes a decision, NEVER
scores or ranks anything, NEVER adds ML or reinforcement learning --
it only records. Every prior MSI module (Series 77-98) is consumed
read-only via its own real, already-produced assessment objects;
none of them are modified."""
