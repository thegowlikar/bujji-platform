"""Capital Management Engine — health report publication.

Takes a SizingDecision (engine.py's output) and publishes it to every sink
the mandate requires: logs (engine.py already does this via log_event),
dashboard (RuntimeStatus), and journal/replay (the caller attaches
`to_dashboard()`'s dict to a TradeRecord or an equivalent capital-health
log). This module owns ONLY the "where does it go," never the decision
itself — that's engine.py's job.
"""
from __future__ import annotations

from ..core.runtime_status import RuntimeStatus
from .models import SizingDecision


def publish_to_dashboard(status: RuntimeStatus, decision: SizingDecision) -> None:
    """Attach the latest capital health snapshot to the shared RuntimeStatus
    so the dashboard can render it — purely observational, never influences
    any decision (same non-invasive pattern as the existing VWAP audit
    trail)."""
    status.capital_health = decision.to_dashboard()
    status.touch()
