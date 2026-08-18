"""Paper Intelligence Mode — BUJJI Options OS v3, Phase 5.

The missing wire: real IntelligenceCycleRecorder evidence ->
market_thesis -> intelligence_orchestrator -> TradingSessionGovernor's
own real decision function -> a persisted DecisionArtifact. No
execution, structurally (see engine.py's own import list).
"""
from .engine import run_cycle

__all__ = ["run_cycle"]
