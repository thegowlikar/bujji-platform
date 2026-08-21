"""bujji.live_shadow_runner -- Phase 20.13 (Bujji Trading Intelligence
Roadmap v1.2).

The operational controller that runs the shadow campaign during real
NSE hours. Still SHADOW ONLY: receives market observations, evaluates
intelligence, generates decisions, records explanations, measures
stability. No trading, no simulated trading, no order placement, no
position creation, no realized-outcome calculation, no connection to
any execution system exists anywhere in this package.

NAMING / COLLISION AUDIT (this phase's own mandatory Step 1): none of
`bujji/live_shadow_runner/`, `bujji/shadow_session_runner/`, or
`bujji/campaign_runtime/` existed prior to this phase (a file named
`shadow_session_runner.py` exists INSIDE `bujji/shadow_runtime/` --
not a top-level package at either requested path -- so this is not a
path collision). `bujji/live_shadow_runner/` proceeds under its own
requested name.

ARCHITECTURE DECISION (per this phase's own explicit instruction --
"do NOT duplicate heartbeat, session open/close handling, lifecycle
state, scheduler integration"): `bujji.shadow_runtime.daily_session.
DailySessionRuntime` (Phase 19.11) IS reused directly for all of that.
It is genuinely domain-agnostic: it awaits an injected `capture_fn`
once and an injected `intelligence_fn` once per day, drives
PRE_MARKET -> MARKET_OPEN -> CAPTURING -> INTELLIGENCE_RUNNING ->
SESSION_COMPLETE/FAILED, writes the heartbeat file, and handles
graceful shutdown -- none of which this package reimplements. This
package supplies Cycle 1's own chain as that `intelligence_fn`: the
function this package's own runner exposes (a day-long loop of
`process_cycle()` calls, `start_session()`/`close_session()` around
it) is exactly what a caller wires into `DailySessionRuntime(...,
intelligence_fn=<this package's own day-loop coroutine>)`. `capture_fn`
is intentionally a caller-supplied no-op in Cycle 1's own design --
unlike the older Phase 19 lineage's separate capture scripts, MIC v0
reads real candles/VIX directly inside each cycle (via
`compose_market_state`, Phase 20.1), so there is no separate capture
step for this package to wrap.

Everything below `process_cycle()` composes ONLY already-existing,
already-tested functions from `bujji.mic_v0` (20.1), `bujji.
strategy_intelligence` (20.5), `bujji.opportunity_intelligence` (20.6),
`bujji.opportunity_ranking` (20.7), `bujji.capital_intelligence` (20.8),
`bujji.opportunity_portfolio` (20.9), `bujji.decision_orchestration`
(20.10), `bujji.shadow_decision_runtime` (20.11), and `bujji.
shadow_market_campaign` (20.12) -- nothing here recomputes any of them.

No broker import. No holdings- or realized-outcome vocabulary anywhere
in this package -- enforced structurally by
`tests/test_live_shadow_runner.py`.

PHASE 20.14 ADDITION: `continuity.py` -- the SAME five-way
classification pattern `bujji.shadow_runtime.campaign_continuity`
(Phase 19.17) established, applied to THIS lineage's own artifacts
(never that module's own Phase-19.x-specific stores; disclosed
explicitly in `continuity.py`'s own docstring).
"""
from __future__ import annotations

from .continuity import (
    CampaignContinuityReport, SessionClassification,
    STATUS_INCOMPLETE, STATUS_MISSING, STATUS_NON_TRADING_DAY,
    STATUS_SESSION_COMPLETE, STATUS_SESSION_FAILED,
    build_campaign_continuity_report, classify_session,
)
from .health import evaluate_runtime_health
from .models import (
    HealthReport, ShadowRunConfig, ShadowRunState,
    SHADOW_RUN_CLOSED, SHADOW_RUN_FAILED, SHADOW_RUN_OPEN, SHADOW_RUN_RUNNING,
)
from .persistence import load_campaign_artifacts, load_decision_observations, save_campaign_artifact
from .runner import close_session, process_cycle, start_session

__all__ = [
    "ShadowRunConfig",
    "ShadowRunState",
    "HealthReport",
    "SHADOW_RUN_OPEN",
    "SHADOW_RUN_RUNNING",
    "SHADOW_RUN_CLOSED",
    "SHADOW_RUN_FAILED",
    "start_session",
    "process_cycle",
    "close_session",
    "save_campaign_artifact",
    "load_campaign_artifacts",
    "load_decision_observations",
    "evaluate_runtime_health",
    "CampaignContinuityReport",
    "SessionClassification",
    "STATUS_NON_TRADING_DAY",
    "STATUS_SESSION_COMPLETE",
    "STATUS_INCOMPLETE",
    "STATUS_SESSION_FAILED",
    "STATUS_MISSING",
    "classify_session",
    "build_campaign_continuity_report",
]
