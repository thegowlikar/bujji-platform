"""Phase 20.17 -- Risk Governor Bridge.

Interface Map row: "Decision Brain -> Risk Governor Bridge -> Execution
Intelligence." Connects Cycle 1's own evidence-driven decision chain
(Phase 20.1-20.15.1) to the real, already-built, already-tested MSI/
Trading Brain risk governor pipeline (`bujji.trading_brain.risk_governor`)
-- an organ that already exists, is never rebuilt or duplicated here.

STEP 1 AUDIT SUMMARY (see docs/PHASE_20_17_RISK_GOVERNOR_BRIDGE_REPORT.md
for the full audit):

- `bujji.trading_brain.risk_governor.risk_governor_pipeline` (D.1-D.6,
  `run_risk_governor_pipeline`) -- A) reusable directly for the real
  per-stage governor functions D.1-D.3 (capital_safety_governor,
  portfolio_risk_aggregator, risk_budget_governor). The pipeline's own
  D.4 (position_lifecycle_intelligence) requires a real, already-open
  position (non-Optional quantity/lifecycle_state) Cycle 1 does not
  have; its D.5 (adaptive_risk_memory/adaptive_risk_governor) requires
  real historical risk-memory entries Cycle 1 also does not have.
  Fabricating either would be dishonest, so this bridge calls D.1-D.3
  directly rather than the full `run_risk_governor_pipeline` orchestrator,
  and explicitly, disclosedly skips D.4/D.5 (see `models.RiskGovernorAssessment`).
- `bujji.broker.fyers.FyersBroker.get_funds()` -- A) reusable directly,
  read-only, LIVE-CERTIFIED, already structurally unreachable from
  `place_order`/`modify_order`/`cancel_order`/`get_open_positions`/
  `get_order` via the existing `disable_live_execution()` guard.
- `bujji.decision_orchestration.FinalDecision` (Phase 20.10) -- A)
  reusable directly as this bridge's sole input; never recomputed,
  never modified.

This package NEVER places an order, computes a real position size for
execution, calculates margin for an actual trade, or deploys capital.
Every quantity/margin/max_loss value it passes into the real governor
is a disclosed, fixed, trivial "notional probe" (see
`models.PROBE_DISCLOSURE`) used only to exercise the real governor's
own admit/reject logic -- the informative output is the governor's own
real, currently-available risk ceiling, never the probe itself.
"""
from .bridge import evaluate_risk_governor
from .builder import build_capital_snapshot_from_real_funds
from .explain import explain_risk_governor_assessment
from .models import (
    ALL_FINAL_STATUSES, NOTIONAL_PROBE_DESIRED_QUANTITY, NOTIONAL_PROBE_MARGIN,
    NOTIONAL_PROBE_MAX_LOSS, PROBE_DISCLOSURE, RiskGovernorAssessment,
    STATUS_ADMITTED, STATUS_BLOCKED, STATUS_NOT_EVALUATED,
)

__all__ = [
    "evaluate_risk_governor", "build_capital_snapshot_from_real_funds",
    "explain_risk_governor_assessment", "RiskGovernorAssessment",
    "ALL_FINAL_STATUSES", "STATUS_ADMITTED", "STATUS_BLOCKED", "STATUS_NOT_EVALUATED",
    "NOTIONAL_PROBE_MARGIN", "NOTIONAL_PROBE_MAX_LOSS", "NOTIONAL_PROBE_DESIRED_QUANTITY",
    "PROBE_DISCLOSURE",
]
