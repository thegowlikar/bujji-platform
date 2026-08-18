"""bujji.shadow_market_campaign -- Phase 20.12 (Bujji Trading
Intelligence Roadmap v1.2).

"Live market intelligence observation with zero execution authority."
This is NOT paper trading, NOT simulation. The runtime only observes:
market data ingestion, MIC evaluation, opportunity evaluation, decision
generation, decision recording, explanation storage, health monitoring.
No broker calls, no simulated execution of any kind, and no tracking
of holdings or realized outcomes exist anywhere in this package.

NAMING / COLLISION AUDIT (this phase's own mandatory Step 1): none of
`bujji/shadow_market_campaign/`, `bujji/runtime_validation/`, or
`bujji/intelligence_campaign/` existed prior to this phase -- no path
collision.

A broad audit of "shadow campaign", "runtime validation", "session
campaign", "market observation", "live intelligence cycle", "daily
intelligence session", "scheduler", "monitoring", "health checks", and
"artifact storage" across this repository found:

  (C, wrong domain -- REAL simulated-execution runtimes) `bujji.
  production_runtime.trading_brain_runtime.TradingBrainRuntime` and
  `bujji.production_runtime.shadow_session_controller.
  ShadowSessionController` (BUJJI Options OS v3, Gate F.1/F.5) are
  real, terminating in the broker's own simulated order path -- the
  exact execution authority this phase must never have. Neither is
  imported.

  (C, wrong lineage -- the older Phase 19 Intelligence Foundation)
  `bujji.shadow_runtime.live_intelligence_cycle`, `.status`,
  `.completeness` (Phase 19.11-19.17) are real, working session/health/
  completeness tools, but every one operates on the SAME pre-MIC-v0
  lineage already disclosed as non-reusable in Phase 20.10's and
  20.11's own audits (`MarketRealitySnapshot -> ... ->
  DecisionIntelligenceSnapshot`). None has ever seen Cycle 1's own
  chain.

  (B, genuinely reusable, disclosed but NOT imported by this phase)
  `bujji.shadow_runtime.daily_session.DailySessionRuntime` (Phase
  19.11) is real, DOMAIN-AGNOSTIC session lifecycle/heartbeat/
  completeness machinery: it takes an injected `capture_fn`/
  `intelligence_fn` callable and never itself decides what those do.
  It is the natural integration point for a FUTURE live-wired
  entrypoint that calls Cycle 1's own chain as its `intelligence_fn` --
  but wiring a real intraday NSE feed and running that loop live is an
  operational task outside what this phase's own deliverable (a
  metrics/validation/report layer over already-collected observations)
  covers; see the phase report's own Limitations section.

This package therefore observes Cycle 1's own chain exclusively, on
top of Phase 20.11's own `bujji.shadow_decision_runtime.
ShadowDecisionLog` -- reused directly, never reimplemented -- adding
only session-level (market-open/close, health status) and
campaign-level (stability, coverage, explanation-quality) context.

No broker import. No holdings- or realized-outcome-tracking vocabulary
anywhere in this package -- enforced structurally by
`tests/test_shadow_market_campaign.py`.
"""
from __future__ import annotations

from .collector import build_campaign_session, collect_observation
from .models import CampaignMetrics, CampaignSession
from .report import build_campaign_report
from .validator import validate_session_behavior

__all__ = [
    "CampaignSession",
    "CampaignMetrics",
    "collect_observation",
    "build_campaign_session",
    "validate_session_behavior",
    "build_campaign_report",
]
