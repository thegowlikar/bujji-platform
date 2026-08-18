"""Phase 20.12 -- reporting. Renders a `CampaignSession` and its
`CampaignMetrics` together -- both already-real, already-computed
objects; this module formats, it never computes."""
from __future__ import annotations

from .models import CampaignMetrics, CampaignSession


def build_campaign_report(session: CampaignSession, metrics: CampaignMetrics) -> str:
    if session.session_date != metrics.session_date:
        raise ValueError(
            f"session_date mismatch: session={session.session_date!r} metrics={metrics.session_date!r} -- "
            "refusing to render a report pairing two different sessions."
        )
    return session.render() + "\n\n" + metrics.render()
